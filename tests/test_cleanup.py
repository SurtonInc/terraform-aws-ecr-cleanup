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


if __name__ == "__main__":
    unittest.main()
