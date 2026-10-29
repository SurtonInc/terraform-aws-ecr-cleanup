"""Reference-aware ECR cleanup. Inventory must succeed before any deletion."""
import datetime as dt
import fnmatch
import json
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import boto3

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
IMAGE_CONFIG_TYPES = {
    None,
    "application/vnd.docker.container.image.v1+json",
    "application/vnd.oci.image.config.v1+json",
}


def pages(client, operation, key, **kwargs):
    """Exhaust pagination, including APIs without SDK paginator definitions."""
    if client.can_paginate(operation):
        return [item for page in client.get_paginator(operation).paginate(**kwargs)
                for item in page.get(key, [])]
    result = []
    while True:
        response = getattr(client, operation)(**kwargs)
        result.extend(response.get(key, []))
        token_key = next((k for k in ("nextToken", "NextToken") if response.get(k)), None)
        if not token_key:
            return result
        kwargs[token_key] = response[token_key]


def checked(response, key):
    if response.get("failures"):
        raise RuntimeError(f"Incomplete {key} inventory: {response['failures']}")
    return response.get(key, [])


def image_reference(uri):
    """Return (registry host, repository, digest-or-tag), or None for other registries."""
    if not isinstance(uri, str) or ".dkr.ecr." not in uri:
        return None
    host, separator, path = uri.partition("/")
    if not separator or not (host.endswith(".amazonaws.com") or host.endswith(".amazonaws.com.cn")):
        return None
    if "@" in path:
        repository, value = path.rsplit("@", 1)
    else:
        repository, separator, value = path.rpartition(":")
        if not separator:
            repository, value = path, "latest"
    return host, repository, value


def resolved_uri(uri, digest):
    reference = image_reference(uri)
    return f"{reference[0]}/{reference[1]}@{digest}" if reference and digest else uri


def images_in_object(value):
    """Batch supports containerProperties, nodeProperties and ecsProperties."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("image", "Image") and isinstance(child, str):
                yield child
            else:
                yield from images_in_object(child)
    elif isinstance(value, list):
        for child in value:
            yield from images_in_object(child)


def inventory_region(region, rollback_count, definition_count, client):
    """Collect only references; do not log configuration/env/secret values."""
    references = set()
    ecs = client("ecs", region)
    definitions = set()
    for cluster in pages(ecs, "list_clusters", "clusterArns"):
        services = pages(ecs, "list_services", "serviceArns", cluster=cluster)
        for start in range(0, len(services), 10):
            for service in checked(ecs.describe_services(
                    cluster=cluster, services=services[start:start + 10]), "services"):
                definitions.add(service["taskDefinition"])
                definitions.update(d["taskDefinition"] for d in service.get("deployments", []))
                definitions.update(t["taskDefinition"] for t in service.get("taskSets", []))
                deployments = pages(ecs, "list_service_deployments", "serviceDeployments",
                                    cluster=cluster, service=service["serviceArn"])
                successful = sorted(
                    [d for d in deployments if d["status"] == "SUCCESSFUL"],
                    key=lambda d: d.get("createdAt", dt.datetime.min.replace(tzinfo=dt.timezone.utc)),
                    reverse=True)
                retained_releases = set()
                for offset in range(0, len(successful), 20):
                    requested = [d["targetServiceRevisionArn"] for d in successful[offset:offset + 20]]
                    revisions = checked(ecs.describe_service_revisions(serviceRevisionArns=requested), "serviceRevisions")
                    by_arn = {r["serviceRevisionArn"]: r for r in revisions}
                    if set(by_arn) != set(requested):
                        raise RuntimeError("Incomplete ECS service revision inventory")
                    for arn in requested:
                        revision = by_arn[arn]
                        release_images = set()
                        for image in revision.get("containerImages", []):
                            ref = image_reference(image["image"])
                            if ref and not ref[2].startswith("sha256:") and not image.get("imageDigest"):
                                raise RuntimeError("Missing immutable digest in successful ECS release")
                            release_images.add(resolved_uri(image["image"], image.get("imageDigest")))
                        fingerprint = tuple(sorted(release_images)) or (revision["taskDefinition"],)
                        if fingerprint in retained_releases:
                            continue
                        retained_releases.add(fingerprint)
                        definitions.add(revision["taskDefinition"])
                        references.update(release_images)
                        if len(retained_releases) >= rollback_count:
                            break
                    if len(retained_releases) >= rollback_count:
                        break
        # Desired RUNNING includes tasks whose actual status is PENDING.
        tasks = pages(ecs, "list_tasks", "taskArns", cluster=cluster, desiredStatus="RUNNING")
        for start in range(0, len(tasks), 100):
            for task in checked(ecs.describe_tasks(cluster=cluster, tasks=tasks[start:start + 100]), "tasks"):
                definitions.add(task["taskDefinitionArn"])
                for container in task.get("containers", []):
                    if container.get("image"):
                        references.add(resolved_uri(container["image"], container.get("imageDigest")))
    families = defaultdict(list)
    for status in ("ACTIVE", "INACTIVE"):
        for arn in pages(ecs, "list_task_definitions", "taskDefinitionArns", status=status):
            families[arn.rsplit(":", 1)[0]].append(arn)
    for arns in families.values():
        definitions.update(sorted(arns, key=lambda a: int(a.rsplit(":", 1)[1]), reverse=True)[:definition_count])

    events = client("events", region)
    for bus in pages(events, "list_event_buses", "EventBuses"):
        for rule in pages(events, "list_rules", "Rules", EventBusName=bus["Name"]):
            for target in pages(events, "list_targets_by_rule", "Targets", Rule=rule["Name"], EventBusName=bus["Name"]):
                if target.get("EcsParameters", {}).get("TaskDefinitionArn"):
                    definitions.add(target["EcsParameters"]["TaskDefinitionArn"])
                # Universal targets can embed an ECS RunTask request.
                payload = json.loads(target.get("Input") or "{}")
                if isinstance(payload, dict) and (payload.get("taskDefinition") or payload.get("TaskDefinition")):
                    definitions.add(payload.get("taskDefinition") or payload["TaskDefinition"])
    scheduler = client("scheduler", region)
    for group in pages(scheduler, "list_schedule_groups", "ScheduleGroups"):
        for schedule in pages(scheduler, "list_schedules", "Schedules", GroupName=group["Name"]):
            target = scheduler.get_schedule(Name=schedule["Name"], GroupName=group["Name"])["Target"]
            if target.get("EcsParameters", {}).get("TaskDefinitionArn"):
                definitions.add(target["EcsParameters"]["TaskDefinitionArn"])
            payload = json.loads(target.get("Input") or "{}")
            if isinstance(payload, dict) and (payload.get("taskDefinition") or payload.get("TaskDefinition")):
                definitions.add(payload.get("taskDefinition") or payload["TaskDefinition"])
    for arn in definitions:
        definition = ecs.describe_task_definition(taskDefinition=arn)["taskDefinition"]
        references.update(c["image"] for c in definition["containerDefinitions"])

    lamb = client("lambda", region)
    for function in pages(lamb, "list_functions", "Functions"):
        if function.get("PackageType") != "Image":
            continue
        for version in pages(lamb, "list_versions_by_function", "Versions", FunctionName=function["FunctionName"]):
            code = lamb.get_function(FunctionName=function["FunctionName"], Qualifier=version["Version"])["Code"]
            references.add(code.get("ResolvedImageUri") or code["ImageUri"])
    batch = client("batch", region)
    for definition in pages(batch, "describe_job_definitions", "jobDefinitions", status="ACTIVE"):
        references.update(images_in_object(definition))
    build = client("codebuild", region)
    projects = pages(build, "list_projects", "projects")
    for start in range(0, len(projects), 100):
        response = build.batch_get_projects(names=projects[start:start + 100])
        if response.get("projectsNotFound"):
            raise RuntimeError("Incomplete CodeBuild project inventory")
        for project in response["projects"]:
            references.add(project["environment"]["image"])
    # Kubernetes pods require Kubernetes authorization; never guess their image use.
    if pages(client("eks", region), "list_clusters", "clusters"):
        raise RuntimeError(f"EKS cluster present in {region}; refusing cleanup without pod inventory")
    return references


def inventory_references(regions, rollback_count, definition_count, client, apprunner_regions):
    def collect(region):
        refs = inventory_region(region, rollback_count, definition_count, client)
        if region in apprunner_regions:
            runner = client("apprunner", region)
            for service in pages(runner, "list_services", "ServiceSummaryList"):
                config = runner.describe_service(ServiceArn=service["ServiceArn"])["Service"]["SourceConfiguration"]
                if config.get("ImageRepository"):
                    refs.add(config["ImageRepository"]["ImageIdentifier"])
        return refs
    with ThreadPoolExecutor(max_workers=4) as executor:
        return set().union(*executor.map(collect, regions))


def inventory_repository(ecr, repository):
    images = {i["imageDigest"]: i for i in pages(
        ecr, "describe_images", "imageDetails", repositoryName=repository,
        filter={"imageStatus": "ANY", "tagStatus": "ANY"})}
    # Fetch index and artifact manifests, not layer/config blobs.
    manifest_ids = [digest for digest, image in images.items()
                    if image.get("imageManifestMediaType") in INDEX_TYPES | {"application/vnd.oci.image.manifest.v1+json"}
                    or image.get("artifactMediaType") not in IMAGE_CONFIG_TYPES]
    edges = set()
    for start in range(0, len(manifest_ids), 100):
        response = ecr.batch_get_image(repositoryName=repository,
                                      imageIds=[{"imageDigest": d} for d in manifest_ids[start:start + 100]])
        manifests = checked(response, "images")
        if {i["imageId"]["imageDigest"] for i in manifests} != set(manifest_ids[start:start + 100]):
            raise RuntimeError("Incomplete image manifest inventory")
        for image in manifests:
            digest = image["imageId"]["imageDigest"]
            manifest = json.loads(image["imageManifest"])
            for child in manifest.get("manifests", []):
                edges.add((digest, child["digest"]))
            if manifest.get("subject"):
                edges.add((digest, manifest["subject"]["digest"]))
    return images, edges


def retention_plan(images, edges, references, repository_uri, now, retention_days,
                   minimum_images, protected_tags):
    tags = {tag: digest for digest, image in images.items() for tag in image.get("imageTags", [])}
    protected = set()
    host, repository = repository_uri.split("/", 1)
    for uri in references:
        ref = image_reference(uri)
        if not ref or ref[:2] != (host, repository):
            continue
        digest = ref[2] if ref[2].startswith("sha256:") else tags.get(ref[2])
        if digest not in images:
            raise RuntimeError(f"Required image reference is absent from {repository_uri}: {uri}")
        protected.add(digest)
    cutoff = now - dt.timedelta(days=retention_days)
    for digest, image in images.items():
        if (image["imagePushedAt"] >= cutoff
                or image.get("imageStatus", "ACTIVE") != "ACTIVE"
                or any(fnmatch.fnmatchcase(tag, pattern)
                       for tag in image.get("imageTags", []) for pattern in protected_tags)):
            protected.add(digest)
    # Count actual tagged releases, not their untagged manifest/attestation children.
    tagged = sorted([i for i in images.values() if i.get("imageTags")],
                    key=lambda i: (i["imagePushedAt"], i["imageDigest"]), reverse=True)
    protected.update(i["imageDigest"] for i in tagged[:minimum_images])
    changed = True
    while changed:
        changed = False
        for parent, child in edges:
            if parent in protected or child in protected:
                for digest in (parent, child):
                    if digest not in protected:
                        protected.add(digest)
                        changed = True
    return set(images) - protected


def deletion_levels(candidates, edges):
    """Delete indices/artifacts before their children/subjects in separate API calls."""
    remaining = set(candidates)
    while remaining:
        children = {child for parent, child in edges if parent in remaining and child in remaining}
        roots = sorted(remaining - children)
        if not roots:
            raise RuntimeError("Cycle in image manifest graph; refusing deletion")
        yield roots
        remaining.difference_update(roots)


def cleanup(ecr, repositories, references, retention_days, minimum_images,
            protected_tags, dry_run=True, refresh_references=None, context=None):
    now = dt.datetime.now(dt.timezone.utc)
    inventories = {repo["repositoryName"]: inventory_repository(ecr, repo["repositoryName"])
                   for repo in repositories}
    plans = {repo["repositoryName"]: retention_plan(
        *inventories[repo["repositoryName"]], references, repo["repositoryUri"], now,
        retention_days, minimum_images, protected_tags) for repo in repositories}
    stats = {"dry_run": dry_run, "candidate_count": sum(map(len, plans.values())),
             "deleted_count": 0, "repositories": {name: sorted(digests) for name, digests in plans.items()}}
    LOG.info("Cleanup plan %s", json.dumps(stats))
    if dry_run:
        return stats
    if refresh_references is None:
        raise RuntimeError("Deletion requires a fresh workload inventory")
    if set(refresh_references()) != set(references):
        raise RuntimeError("Workload references changed during inventory; retry on the next schedule")
    # Re-read every repository before the first delete. New pins/tags must be respected.
    for repo in repositories:
        name = repo["repositoryName"]
        fresh = inventory_repository(ecr, name)
        fresh_candidates = retention_plan(*fresh, references, repo["repositoryUri"], now,
                                         retention_days, minimum_images, protected_tags)
        plans[name].intersection_update(fresh_candidates)
        inventories[name] = fresh
    stats["revalidated_candidate_count"] = sum(map(len, plans.values()))
    stats["cancelled_count"] = stats["candidate_count"] - stats["revalidated_candidate_count"]
    levels = {name: list(deletion_levels(plans[name], inventories[name][1])) for name in plans}
    for repo in repositories:
        name = repo["repositoryName"]
        for level in levels[name]:
            for start in range(0, len(level), 100):
                if context and context.get_remaining_time_in_millis() < 10000:
                    raise RuntimeError("Insufficient execution time for another deletion batch")
                response = ecr.batch_delete_image(repositoryName=name,
                                                 imageIds=[{"imageDigest": d} for d in level[start:start + 100]])
                deleted = {i["imageDigest"] for i in response.get("imageIds", [])}
                stats["deleted_count"] += len(deleted)
                if response.get("failures"):
                    LOG.error("Partial deletion failure after %s successful deletes: %s",
                              stats["deleted_count"], response["failures"])
                    raise RuntimeError("ECR rejected image deletions; inspect per-image failures in logs")
                if deleted != set(level[start:start + 100]):
                    raise RuntimeError("ECR returned an incomplete deletion result")
    LOG.info("Cleanup complete %s", json.dumps(stats))
    return stats


def handler(event, context):
    region = os.environ["AWS_REGION"]
    # Clients created in worker threads use independent SDK sessions.
    def client(service, target_region):
        return boto3.Session(region_name=target_region).client(service)
    ecr = client("ecr", region)
    names = json.loads(os.environ["REPOSITORIES_JSON"])
    repositories = []
    for start in range(0, len(names), 100):
        repositories.extend(ecr.describe_repositories(repositoryNames=names[start:start + 100])["repositories"])
    regions = json.loads(os.environ.get("SCAN_REGIONS_JSON", "[]"))
    if not regions:
        regions = [r["RegionName"] for r in client("ec2", region).describe_regions()["Regions"]]
    rollback_count = int(os.environ["ROLLBACK_DEPLOYMENTS"])
    definition_count = int(os.environ["TASK_DEFINITION_REVISIONS"])
    runner_regions = set(boto3.Session().get_available_regions("apprunner"))
    def discover():
        return inventory_references(regions, rollback_count, definition_count, client, runner_regions)
    references = discover() | set(json.loads(os.environ.get("PROTECTED_IMAGES_JSON", "[]")))
    configured_dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    requested = event.get("dry_run", False)
    if not isinstance(requested, bool):
        raise ValueError("dry_run event override must be boolean")
    return cleanup(ecr, repositories, references, int(os.environ["RETENTION_DAYS"]),
                   int(os.environ["MINIMUM_IMAGES"]), json.loads(os.environ["PROTECTED_TAGS_JSON"]),
                   configured_dry_run or requested,
                   lambda: discover() | set(json.loads(os.environ.get("PROTECTED_IMAGES_JSON", "[]"))), context)
