# ECR cleanup

Scheduled, reference-aware retention for existing ECR repositories. Version 0.2.0
starts in **dry-run**: it logs a candidate plan and its IAM role cannot delete images.
No manual cleanup-approval tags are required.

```hcl
module "ecr_cleanup" {
  source = "git::https://github.com/SurtonInc/terraform-ecr-cleanup.git?ref=<reviewed-commit>"

  name_prefix          = "application-us-west-2"
  repository_names     = ["application-api"]
  retention_days       = 90
  minimum_images       = 10
  rollback_deployments = 4
  dry_run              = true
}
```

Review the dry-run plan after deployment, then change `dry_run` to `false` through
Terraform to enable the scheduled cleanup. This is a rollout switch, not a recurring
manual approval step. `schedule_enabled = false` stops new scheduled invocations.

## What is kept

- Images pushed within `retention_days` and the newest `minimum_images` tagged images
  in each repository (defaults: 60 days and 10 tagged images).
- Any image with a matching `protected_tags` shell pattern, regardless of age.
  Matching is case-sensitive; `release-*` and `v*` work. Defaults include `latest`,
  `dev`, `qa`, `stg`, `staging`, `demo`, `prd`, and `prd-*`.
- Current ECS service definitions, active deployments and task sets, and actual
  running/pending task digests, including services currently scaled to zero.
- The last `rollback_deployments` **distinct successful ECS image sets per service** (default
  four, including the current successful release). Service revision resolved digests
  are used even when a task definition contains a mutable tag.
- The newest `task_definition_revisions` definitions per family (default four),
  including bootstrap/migration recipes; EventBridge and Scheduler ECS task targets;
  active Batch definitions; all container Lambda versions; CodeBuild and App Runner
  image references.
- OCI/multi-architecture manifest families and related subject artifacts whenever any
  member is retained. Archived/activating images are not deletion candidates.
- Additional image URIs in `protected_images`, for explicit recovery requirements.

A site that has not been deployed for three months still has its current ECS digest
retained. Image age does not override a current workload reference.

## Safety and scope

The Lambda completes all configured repository and workload inventory before any
mutation. Inventory/API failures and missing required image references raise errors.
An EKS cluster stops cleanup because this module does not have Kubernetes pod access.
Actual deletion requires a second workload inventory; if references changed, the run
stops. Repository metadata is refreshed again before the first deletion so new tag
pins are respected. Indices/artifacts are deleted before their children/subjects in
separate batches; per-image API failures raise a Lambda error instead of claiming success.

Discovery is **account-local**. Empty `scan_regions` discovers every enabled region;
set a list only when the consumer region scope is known. Cross-account deployments,
EC2-hosted containers, Kubernetes pods and arbitrary references embedded in custom
workflow code are not automatically inventoried. Shared repositories with these
consumers must use durable protected tags/patterns or explicit recovery URIs; for a
small shared release repository, `protected_tags = ["*"]` conservatively retains all
its tagged versions. Do not use unprotected external image consumers with write mode.

AWS inventory and deletion are not a transaction. The second scan detects changes
between passes, but cannot lock a manual deployment that starts after that scan.
Normal new builds are protected by the age window and recent-image floor, while the
retained successful releases cover ordinary rollbacks. Rebuild or explicitly protect
an older image before restoring a release outside the retained history. This module
must not be described as a global deployment lock.

Existing native ECR lifecycle policies are independent and can delete images this
module would retain. Remove conflicting policies in their owning Terraform states
before enabling this cleaner. The module does not create or take ownership of ECR
repositories or native lifecycle policies.

Use one module instance per account/registry region. Give each instance a unique
`name_prefix`, including region, because its IAM role/policy names are account-global.
Private upstream modules require Git access during Terraform initialization. Vendored
snapshots should retain the MIT license, upstream commit and file hashes.

## Configuration

| Input | Default | Purpose |
|---|---|---|
| `repository_names` | required | Existing repositories in the provider account/region |
| `retention_days` | `60` | Minimum push age for unprotected images |
| `minimum_images` | `10` | Newest tagged images retained per repository |
| `rollback_deployments` | `4` | Distinct successful ECS image sets retained per service |
| `task_definition_revisions` | `4` | Newest definitions retained per family |
| `protected_tags` | environment patterns | Exact or glob tag protection |
| `protected_images` | `[]` | Extra image URIs to retain |
| `scan_regions` | `[]` | Empty discovers all enabled regions |
| `dry_run` | `true` | No deletes and no deletion IAM permission |
| `schedule_enabled` | `true` | Enable scheduled execution |
| `schedule_expression` | `cron(0 1 * * ? *)` | Daily, 01:00 UTC |
| `lambda_timeout` | `600` | 60–900 seconds |
| `lambda_memory_size` | `256` | Lambda memory in MB |
| `name_prefix` | `""` | Unique resource name prefix |
| `alarm_actions` | `[]` | Optional existing SNS alarm action ARNs |
| `tags` | `{}` | Resource tags |

JSON environment configuration preserves comma-containing tag patterns. Invocation
input `{"dry_run": true}` can request a dry-run, but cannot enable writes when the
configured dry-run switch is on. CloudWatch logs contain plans and results, not service
environment/secret values. A Lambda Errors alarm is created; wire `alarm_actions` to an
existing operational destination if notifications are required.

The Lambda uses Python 3.13 and the runtime-provided boto3 SDK. ECR read/write access
is scoped to configured repositories. Workload discovery permissions are read-only;
several AWS list/describe APIs require `Resource = "*"`.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
terraform init -backend=false
terraform test
```

Unit tests mock every AWS call and require no credentials or boto3 installation.
Terraform tests use a mocked AWS provider and the local archive provider. No test
performs cloud deletion.
