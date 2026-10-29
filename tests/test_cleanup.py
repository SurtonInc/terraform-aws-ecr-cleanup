import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

# Tests use no credentials, network or boto3 installation.
sys.modules.setdefault("boto3", types.ModuleType("boto3"))
spec = importlib.util.spec_from_file_location(
    "cleanup", Path(__file__).parents[1] / "lambda/index.py"
)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
NOW = dt.datetime.now(dt.timezone.utc)
HOST = "123456789012.dkr.ecr.us-west-2.amazonaws.com"
URI = HOST + "/app"
REPO = {"repositoryName": "app", "repositoryUri": URI}


def image(digest, days=120, tags=(), **extra):
    return {
        "imageDigest": digest,
        "imagePushedAt": NOW - dt.timedelta(days=days),
        "imageTags": list(tags),
        **extra,
    }


class Client:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def can_paginate(self, op):
        return False

    def __getattr__(self, op):
        def call(**kwargs):
            self.calls.append((op, kwargs))
            value = self.responses.get(op, {})
            if isinstance(value, Exception):
                raise value
            return value(**kwargs) if callable(value) else value

        return call


class RetentionTests(unittest.TestCase):
    def plan(self, items, refs=(), edges=(), minimum=1, patterns=()):
        return cleanup.retention_plan(
            {i["imageDigest"]: i for i in items},
            set(edges),
            set(refs),
            URI,
            NOW,
            90,
            minimum,
            patterns,
        )

    def test_incomplete_rollback_history_holds_whole_repository(self):
        self.assertFalse(
            self.plan(
                [image("sha256:old-unused"), image("sha256:old-rollback")],
                ["retain-all:" + URI],
            )
        )

    def test_repository_hold_does_not_mask_missing_required_image(self):
        with self.assertRaisesRegex(RuntimeError, "Required image"):
            self.plan(
                [image("sha256:old")], ["retain-all:" + URI, URI + "@sha256:missing"]
            )

    def test_old_running_digest_survives(self):
        self.assertEqual(
            self.plan(
                [image("sha256:old"), image("sha256:new", 1, ["new"])],
                [URI + "@sha256:old"],
            ),
            set(),
        )

    def test_old_tag_reference_survives_without_recent_deploy(self):
        self.assertEqual(
            self.plan(
                [
                    image("sha256:old", 365, ["deployed"]),
                    image("sha256:new", 1, ["new"]),
                ],
                [URI + ":deployed"],
            ),
            set(),
        )

    def test_wildcards_really_protect_releases(self):
        self.assertEqual(
            self.plan(
                [
                    image("sha256:a", tags=["release-abc"]),
                    image("sha256:b", 1, ["new"]),
                ],
                patterns=["release-*"],
            ),
            set(),
        )

    def test_default_tag_pattern_can_preserve_environment_variants(self):
        self.assertFalse(
            self.plan([image("sha256:a", tags=["prd-harder"])], patterns=["prd-*"])
        )

    def test_old_unreferenced_build_is_candidate(self):
        self.assertEqual(
            self.plan(
                [image("sha256:a", tags=["build-old"]), image("sha256:b", 1, ["new"])]
            ),
            {"sha256:a"},
        )

    def test_newest_tagged_image_floor(self):
        self.assertEqual(
            self.plan([image("sha256:a", 200, ["a"]), image("sha256:b", 150, ["b"])]),
            {"sha256:a"},
        )

    def test_untagged_child_does_not_consume_release_floor(self):
        self.assertEqual(
            self.plan([image("sha256:a", 200, ["a"]), image("sha256:b", 150)]),
            {"sha256:b"},
        )

    def test_recent_untagged_build_is_retained(self):
        self.assertFalse(self.plan([image("sha256:a", 1)]))

    def test_archived_image_is_retained(self):
        self.assertFalse(self.plan([image("sha256:a", imageStatus="ARCHIVED")]))

    def test_missing_required_reference_aborts(self):
        with self.assertRaisesRegex(RuntimeError, "Required image"):
            self.plan([image("sha256:a")], [URI + "@sha256:missing"])

    def test_cross_repository_reference_is_not_misresolved(self):
        self.assertEqual(
            self.plan([image("sha256:a")], [HOST + "/other@sha256:a"]), {"sha256:a"}
        )

    def test_running_child_retains_parent_and_other_architectures(self):
        items = [
            image(d)
            for d in ["sha256:index", "sha256:amd", "sha256:arm", "sha256:attestation"]
        ]
        edges = [
            ("sha256:index", "sha256:amd"),
            ("sha256:index", "sha256:arm"),
            ("sha256:attestation", "sha256:amd"),
        ]
        self.assertFalse(self.plan(items, [URI + "@sha256:amd"], edges))

    def test_tag_pin_retains_untagged_index_children(self):
        self.assertFalse(
            self.plan(
                [image("sha256:index", tags=["prd"]), image("sha256:child")],
                edges=[("sha256:index", "sha256:child")],
                patterns=["prd"],
            )
        )

    def test_current_and_rollback_digest_are_both_retained(self):
        self.assertFalse(
            self.plan(
                [image("sha256:current"), image("sha256:rollback")],
                [URI + "@sha256:current", URI + "@sha256:rollback"],
            )
        )


class ExecutionTests(unittest.TestCase):
    def ecr(self, fail=False):
        items = [image("sha256:old"), image("sha256:new", 1, ["new"])]
        return Client(
            {
                "describe_images": {"imageDetails": items},
                "batch_delete_image": lambda **kw: (
                    {"failures": [{"failureCode": "ImageReferencedByManifestList"}]}
                    if fail
                    else {"imageIds": kw["imageIds"]}
                ),
            }
        )

    def run_cleanup(self, ecr, dry_run=False, refresh=lambda: set()):
        return cleanup.cleanup(ecr, [REPO], set(), 90, 1, [], dry_run, refresh)

    def test_dry_run_performs_no_mutations(self):
        ecr = self.ecr()
        result = self.run_cleanup(ecr, True)
        self.assertEqual(result["candidate_count"], 1)
        self.assertFalse(any(op == "batch_delete_image" for op, _ in ecr.calls))

    def test_changed_workloads_abort_before_deleting(self):
        ecr = self.ecr()
        with self.assertRaisesRegex(RuntimeError, "references changed"):
            self.run_cleanup(ecr, refresh=lambda: {URI + "@sha256:old"})
        self.assertFalse(any(op == "batch_delete_image" for op, _ in ecr.calls))

    def test_inventory_failure_aborts_before_deleting(self):
        ecr = self.ecr()
        ecr.responses["describe_images"] = PermissionError("Denied")
        with self.assertRaises(PermissionError):
            self.run_cleanup(ecr)
        self.assertFalse(any(op == "batch_delete_image" for op, _ in ecr.calls))

    def test_partial_delete_failure_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "rejected image deletions"):
            self.run_cleanup(self.ecr(True))

    def test_successful_deletes_are_counted(self):
        self.assertEqual(self.run_cleanup(self.ecr())["deleted_count"], 1)

    def test_new_protected_tag_in_second_inventory_cancels_candidate(self):
        ecr = self.ecr()
        count = 0

        def changed(**kwargs):
            nonlocal count
            count += 1
            return {
                "imageDetails": [
                    image("sha256:old", tags=["prd"] if count > 1 else []),
                    image("sha256:new", 1, ["new"]),
                ]
            }

        ecr.responses["describe_images"] = changed
        result = cleanup.cleanup(
            ecr, [REPO], set(), 90, 1, ["prd"], False, lambda: set()
        )
        self.assertEqual(result["deleted_count"], 0)

    def test_manifest_levels_delete_parent_before_child(self):
        self.assertEqual(
            list(cleanup.deletion_levels({"index", "child"}, {("index", "child")})),
            [["index"], ["child"]],
        )

    def test_cycles_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Cycle"):
            list(cleanup.deletion_levels({"a", "b"}, {("a", "b"), ("b", "a")}))

    def test_inventory_uses_runtime_sdk_compatible_active_image_filter(self):
        ecr = Client({"describe_images": {"imageDetails": []}})
        cleanup.inventory_repository(ecr, "app")
        self.assertEqual(ecr.calls[0][1]["filter"], {"tagStatus": "ANY"})

    def test_oci_image_subject_is_discovered_without_artifact_type(self):
        ecr = Client(
            {
                "describe_images": {
                    "imageDetails": [
                        image(
                            "sha256:signature",
                            imageManifestMediaType="application/vnd.oci.image.manifest.v1+json",
                        )
                    ]
                },
                "batch_get_image": {
                    "images": [
                        {
                            "imageId": {"imageDigest": "sha256:signature"},
                            "imageManifest": json.dumps(
                                {"subject": {"digest": "sha256:subject"}}
                            ),
                        }
                    ]
                },
            }
        )
        _, edges = cleanup.inventory_repository(ecr, "app")
        self.assertEqual(edges, {("sha256:signature", "sha256:subject")})

    def test_incomplete_manifest_response_aborts(self):
        ecr = Client(
            {
                "describe_images": {
                    "imageDetails": [
                        image(
                            "sha256:index",
                            imageManifestMediaType=next(iter(cleanup.INDEX_TYPES)),
                        )
                    ]
                },
                "batch_get_image": {"images": []},
            }
        )
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            cleanup.inventory_repository(ecr, "app")

    def test_inventory_exhausts_next_token_without_sdk_paginator(self):
        client = Client(
            {
                "list_services": lambda **kw: (
                    {"serviceArns": ["two"]}
                    if kw.get("nextToken")
                    else {"serviceArns": ["one"], "nextToken": "page2"}
                )
            }
        )
        self.assertEqual(
            cleanup.pages(client, "list_services", "serviceArns"), ["one", "two"]
        )

    def test_dry_run_cannot_be_disabled_by_event(self):
        ecr = self.ecr()
        ecr.responses["describe_repositories"] = {"repositories": [REPO]}
        session = types.SimpleNamespace(
            client=lambda service: ecr, get_available_regions=lambda service: []
        )
        env = {
            "AWS_REGION": "us-west-2",
            "REPOSITORIES_JSON": '["app"]',
            "SCAN_REGIONS_JSON": '["us-west-2"]',
            "ROLLBACK_DEPLOYMENTS": "4",
            "TASK_DEFINITION_REVISIONS": "4",
            "RETENTION_DAYS": "90",
            "MINIMUM_IMAGES": "1",
            "PROTECTED_TAGS_JSON": "[]",
            "DRY_RUN": "true",
        }
        with (
            patch.object(cleanup.boto3, "Session", create=True, return_value=session),
            patch.object(cleanup, "inventory_references", return_value=set()),
            patch.dict(os.environ, env),
        ):
            result = cleanup.handler({"dry_run": False}, None)
        self.assertTrue(result["dry_run"])
        self.assertFalse(any(op == "batch_delete_image" for op, _ in ecr.calls))


class DiscoveryTests(unittest.TestCase):
    def clients(self, ecs):
        return lambda service, region: ecs if service == "ecs" else Client()

    def test_unresolved_running_ecr_digest_stops_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, "Unresolved"):
            cleanup.resolved_uri(URI + ":prd", None)
        self.assertEqual(
            cleanup.resolved_uri(URI + "@sha256:pinned", None), URI + "@sha256:pinned"
        )

    def test_current_and_successful_revision_resolved_digests(self):
        ecs = Client(
            {
                "list_clusters": {"clusterArns": ["cluster"]},
                "list_services": {"serviceArns": ["service"]},
                "describe_services": {
                    "services": [{"serviceArn": "service", "taskDefinition": "td:1"}]
                },
                "list_service_deployments": {
                    "serviceDeployments": [
                        {"status": "SUCCESSFUL", "targetServiceRevisionArn": "rev"}
                    ]
                },
                "describe_service_revisions": {
                    "serviceRevisions": [
                        {
                            "serviceRevisionArn": "rev",
                            "taskDefinition": "td:1",
                            "containerImages": [
                                {
                                    "image": URI + ":prd",
                                    "imageDigest": "sha256:rollback",
                                }
                            ],
                        }
                    ]
                },
                "list_tasks": {"taskArns": ["pending"]},
                "describe_tasks": {
                    "tasks": [
                        {
                            "taskDefinitionArn": "td:1",
                            "containers": [
                                {"image": URI + ":prd", "imageDigest": "sha256:running"}
                            ],
                        }
                    ]
                },
                "describe_task_definition": {
                    "taskDefinition": {
                        "containerDefinitions": [{"image": URI + ":prd"}]
                    }
                },
            }
        )
        refs = cleanup.inventory_region("us-west-2", 4, 4, self.clients(ecs))
        self.assertTrue(
            {URI + "@sha256:running", URI + "@sha256:rollback", URI + ":prd"} <= refs
        )

    def test_duplicate_restarts_do_not_consume_rollback_slots(self):
        history = [
            {
                "status": "SUCCESSFUL",
                "targetServiceRevisionArn": str(i),
                "createdAt": NOW - dt.timedelta(days=i),
            }
            for i in range(6)
        ]
        digests = ["current", "current", "previous1", "previous2", "previous3", "older"]
        ecs = Client(
            {
                "list_clusters": {"clusterArns": ["cluster"]},
                "list_services": {"serviceArns": ["service"]},
                "describe_services": {
                    "services": [{"serviceArn": "service", "taskDefinition": "td:1"}]
                },
                "list_service_deployments": {"serviceDeployments": history},
                "describe_service_revisions": lambda **kw: {
                    "serviceRevisions": [
                        {
                            "serviceRevisionArn": arn,
                            "taskDefinition": "td:1",
                            "containerImages": [
                                {
                                    "image": URI + ":prd",
                                    "imageDigest": "sha256:" + digests[int(arn)],
                                }
                            ],
                        }
                        for arn in kw["serviceRevisionArns"]
                    ]
                },
                "describe_task_definition": {
                    "taskDefinition": {
                        "containerDefinitions": [{"image": URI + ":prd"}]
                    }
                },
            }
        )
        refs = cleanup.inventory_region("us-west-2", 4, 4, self.clients(ecs))
        for name in ["current", "previous1", "previous2", "previous3"]:
            self.assertIn(URI + "@sha256:" + name, refs)
        self.assertNotIn(URI + "@sha256:older", refs)

    def test_missing_ecs_history_retains_entire_service_repository(self):
        ecs = Client(
            {
                "list_clusters": {"clusterArns": ["cluster"]},
                "list_services": {"serviceArns": ["service"]},
                "describe_services": {
                    "services": [{"serviceArn": "service", "taskDefinition": "td:1"}]
                },
                "describe_task_definition": {
                    "taskDefinition": {
                        "containerDefinitions": [{"image": URI + ":prd"}]
                    }
                },
            }
        )
        refs = cleanup.inventory_region("us-west-2", 4, 4, self.clients(ecs))
        self.assertIn("retain-all:" + URI, refs)

    def test_ecs_partial_inventory_failure_aborts(self):
        ecs = Client(
            {
                "list_clusters": {"clusterArns": ["cluster"]},
                "list_services": {"serviceArns": ["service"]},
                "describe_services": {"failures": [{"reason": "MISSING"}]},
            }
        )
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            cleanup.inventory_region("us-west-2", 4, 4, self.clients(ecs))

    def test_eks_without_pod_inventory_aborts(self):
        with self.assertRaisesRegex(RuntimeError, "EKS cluster"):
            cleanup.inventory_region(
                "us-west-2",
                4,
                4,
                lambda service, region: (
                    Client({"list_clusters": {"clusters": ["eks"]}})
                    if service == "eks"
                    else Client()
                ),
            )

    def test_latest_definition_revisions_keep_migration_recipe_images(self):
        seen = []

        def definition(**kwargs):
            seen.append(kwargs["taskDefinition"])
            return {
                "taskDefinition": {
                    "containerDefinitions": [{"image": URI + ":migration"}]
                }
            }

        ecs = Client(
            {
                "list_task_definitions": lambda **kw: (
                    {
                        "taskDefinitionArns": [
                            "migration:1",
                            "migration:5",
                            "migration:3",
                        ]
                    }
                    if kw["status"] == "ACTIVE"
                    else {}
                ),
                "describe_task_definition": definition,
            }
        )
        cleanup.inventory_region("us-west-2", 4, 2, self.clients(ecs))
        self.assertEqual(set(seen), {"migration:3", "migration:5"})

    def test_batch_ecs_properties_and_node_images_are_found(self):
        self.assertEqual(
            set(
                cleanup.images_in_object(
                    {
                        "ecsProperties": {
                            "taskProperties": [{"containers": [{"image": "one"}]}]
                        },
                        "nodeProperties": {
                            "nodeRangeProperties": [{"container": {"image": "two"}}]
                        },
                    }
                )
            ),
            {"one", "two"},
        )


class EndpointTests(unittest.TestCase):
    def test_dual_stack_service_history_hold_uses_canonical_repository(self):
        alias = "123456789012.dkr-ecr.us-west-2.on.aws/app"
        ecs = Client(
            {
                "list_clusters": {"clusterArns": ["cluster"]},
                "list_services": {"serviceArns": ["service"]},
                "describe_services": {
                    "services": [{"serviceArn": "service", "taskDefinition": "td:1"}]
                },
                "describe_task_definition": {
                    "taskDefinition": {
                        "containerDefinitions": [{"image": alias + ":prd"}]
                    }
                },
            }
        )
        refs = cleanup.inventory_region(
            "us-west-2", 4, 4, lambda name, region: ecs if name == "ecs" else Client()
        )
        images = {
            "sha256:old-rollback": image("sha256:old-rollback"),
            "sha256:current": image("sha256:current", 1, ["prd"]),
        }
        self.assertFalse(
            cleanup.retention_plan(images, set(), refs, URI, NOW, 90, 1, [])
        )

    def test_endpoint_aliases_protect_the_canonical_repository(self):
        for host in (
            HOST,
            "123456789012.dkr.ecr-fips.us-west-2.amazonaws.com",
            "123456789012.dkr-ecr.us-west-2.on.aws",
            "123456789012.dkr-ecr-fips.us-west-2.on.aws",
        ):
            with self.subTest(host=host):
                images = {"sha256:running": image("sha256:running")}
                refs = {host + "/app@sha256:running"}
                self.assertFalse(
                    cleanup.retention_plan(images, set(), refs, URI, NOW, 90, 1, [])
                )
                self.assertEqual(
                    cleanup.resolved_uri(host + "/app:prd", "sha256:running"),
                    URI + "@sha256:running",
                )

    def test_china_registry_partition_is_preserved(self):
        for host in (
            "123456789012.dkr.ecr.cn-north-1.amazonaws.com.cn",
            "123456789012.dkr-ecr.cn-north-1.on.amazonwebservices.com.cn",
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    cleanup.image_reference(host + "/app:prd"),
                    ("123456789012.dkr.ecr.cn-north-1.amazonaws.com.cn", "app", "prd"),
                )

    def test_unresolved_alternate_endpoint_task_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Unresolved"):
            cleanup.resolved_uri("123456789012.dkr-ecr.us-west-2.on.aws/app:prd", None)

    def test_other_registries_are_not_treated_as_private_ecr(self):
        for uri in (
            "public.ecr.aws/app:prd",
            "docker.io/app:prd",
            "https://example.com/app:prd",
            None,
        ):
            with self.subTest(uri=uri):
                self.assertIsNone(cleanup.image_reference(uri))


class ScheduledTargetTests(unittest.TestCase):
    def discover(self, service, target):
        if service == "events":
            source = Client(
                {
                    "list_event_buses": {"EventBuses": [{"Name": "default"}]},
                    "list_rules": {"Rules": [{"Name": "rule"}]},
                    "list_targets_by_rule": {"Targets": [target]},
                }
            )
        else:
            source = Client(
                {
                    "list_schedule_groups": {"ScheduleGroups": [{"Name": "default"}]},
                    "list_schedules": {"Schedules": [{"Name": "schedule"}]},
                    "get_schedule": {"Target": target},
                }
            )
        ecs = Client(
            {
                "describe_task_definition": {
                    "taskDefinition": {
                        "containerDefinitions": [{"image": URI + ":scheduled"}]
                    }
                }
            }
        )
        clients = {service: source, "ecs": ecs}
        return cleanup.inventory_region(
            "us-west-2", 4, 4, lambda name, region: clients.get(name, Client())
        )

    def test_unrelated_plaintext_targets_do_not_stop_inventory(self):
        for service in ("events", "scheduler"):
            with self.subTest(service=service):
                self.assertFalse(
                    self.discover(
                        service,
                        {
                            "Arn": "arn:aws:sqs:us-west-2:123456789012:queue",
                            "Input": "plain text",
                        },
                    )
                )

    def test_templated_ecs_target_reads_its_definition_without_parsing_input(self):
        for service in ("events", "scheduler"):
            with self.subTest(service=service):
                refs = self.discover(
                    service,
                    {
                        "EcsParameters": {"TaskDefinitionArn": "td:1"},
                        "Input": "plain text",
                    },
                )
                self.assertIn(URI + ":scheduled", refs)

    def test_universal_ecs_target_reads_task_definition(self):
        for action, key in (
            ("runTask", "taskDefinition"),
            ("startTask", "TaskDefinition"),
            ("createService", "taskDefinition"),
        ):
            with self.subTest(action=action, key=key):
                refs = self.discover(
                    "scheduler",
                    {
                        "Arn": "arn:aws:scheduler:::aws-sdk:ecs:" + action,
                        "Input": json.dumps({key: "td:1"}),
                    },
                )
                self.assertIn(URI + ":scheduled", refs)

    def test_malformed_universal_ecs_input_still_stops_inventory(self):
        with self.assertRaises(ValueError):
            self.discover(
                "scheduler",
                {"Arn": "arn:aws:scheduler:::aws-sdk:ecs:runTask", "Input": "bad json"},
            )

    def test_missing_universal_ecs_definition_stops_inventory(self):
        for payload in ({}, [], {"taskDefinition": ""}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.discover(
                    "scheduler",
                    {
                        "Arn": "arn:aws:scheduler:::aws-sdk:ecs:runTask",
                        "Input": json.dumps(payload),
                    },
                )


class BatchJobTests(unittest.TestCase):
    def discover(self, batch):
        return cleanup.inventory_region(
            "us-west-2",
            4,
            4,
            lambda name, region: batch if name == "batch" else Client(),
        )

    def batch(self, uri):
        return Client(
            {
                "describe_job_queues": {"jobQueues": [{"jobQueueArn": "queue"}]},
                "list_jobs": lambda **kw: (
                    {"jobSummaryList": [{"jobId": "queued"}]}
                    if kw["jobStatus"] == "RUNNABLE"
                    else {}
                ),
                "describe_jobs": {
                    "jobs": [
                        {
                            "jobId": "queued",
                            "jobDefinition": "deregistered:1",
                            "container": {"image": uri},
                        }
                    ]
                },
            }
        )

    def test_queued_job_retains_image_after_definition_deregistration(self):
        refs = self.discover(self.batch(URI + "@sha256:queued"))
        self.assertIn(URI + "@sha256:queued", refs)

    def test_mutable_job_reference_holds_repository(self):
        refs = self.discover(self.batch(URI + ":prd"))
        images = {
            "sha256:old-job": image("sha256:old-job"),
            "sha256:moved-tag": image("sha256:moved-tag", 1, ["prd"]),
        }
        self.assertFalse(
            cleanup.retention_plan(images, set(), refs, URI, NOW, 90, 1, [])
        )

    def test_batch_holds_are_reported_separately_from_ecs_history(self):
        refs = self.discover(self.batch(URI + ":prd"))
        ecr = Client(
            {"describe_images": {"imageDetails": [image("sha256:new", 1, ["prd"])]}}
        )
        result = cleanup.cleanup(ecr, [REPO], refs, 90, 1, [])
        self.assertEqual(result["held_for_incomplete_history"], [])
        self.assertEqual(result["held_for_unresolved_batch_images"], ["app"])

    def test_paginated_jobs_are_described_in_bounded_batches(self):
        batch = self.batch(URI + "@sha256:queued")

        def jobs(**kwargs):
            if kwargs["jobStatus"] != "RUNNABLE":
                return {}
            if kwargs.get("nextToken"):
                return {"jobSummaryList": [{"jobId": "last"}]}
            return {
                "jobSummaryList": [{"jobId": str(i)} for i in range(100)],
                "nextToken": "page2",
            }

        batch.responses["list_jobs"] = jobs
        batch.responses["describe_jobs"] = lambda **kw: {
            "jobs": [
                {"jobId": job_id, "container": {"image": URI + "@sha256:" + job_id}}
                for job_id in kw["jobs"]
            ]
        }
        refs = self.discover(batch)
        self.assertIn(URI + "@sha256:last", refs)
        self.assertEqual(len(refs), 101)
        self.assertEqual(
            [
                len(kwargs["jobs"])
                for op, kwargs in batch.calls
                if op == "describe_jobs"
            ],
            [100, 1],
        )

    def test_incomplete_job_response_stops_cleanup(self):
        batch = self.batch(URI + "@sha256:queued")
        batch.responses["describe_jobs"] = {"jobs": []}
        with self.assertRaisesRegex(RuntimeError, "Incomplete Batch"):
            self.discover(batch)

    def test_every_nonterminal_job_status_is_inventoried(self):
        batch = self.batch(URI + "@sha256:queued")
        self.discover(batch)
        self.assertEqual(
            {kwargs["jobStatus"] for op, kwargs in batch.calls if op == "list_jobs"},
            {"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"},
        )


if __name__ == "__main__":
    unittest.main()
