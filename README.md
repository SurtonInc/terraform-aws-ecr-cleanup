# ECR cleanup for AWS

Automatically remove unused ECR images while retaining deployed images, rollback
releases and their manifest families. **Dry-run by default:** it logs candidates
and has no image-deletion IAM permission until you enable writes.

## Installation

```hcl
provider "aws" {
  region = "us-west-2"
}

module "ecr_cleanup" {
  source  = "SurtonInc/ecr-cleanup/aws"
  version = "~> 0.2.1"

  name_prefix      = "application-us-west-2"
  repository_names = ["application-api"]
  retention_days   = 90
  dry_run          = true
}
```

Use existing repository names and a unique `name_prefix` for each account/region.
The module creates the scheduled Lambda, IAM role, log group and error alarm; it
leaves repository ownership with your existing infrastructure.

OpenTofu uses its own default registry. If the module is not listed there, use
`git::https://github.com/SurtonInc/terraform-aws-ecr-cleanup.git?ref=v0.2.1` as the
source and omit `version`. The module is tested with both tools.

## Enable deletion

1. Deploy with `dry_run = true` and review the Lambda's successful candidate logs.
2. Resolve inventory errors and protect any external image consumers described below.
3. Set `dry_run = false` through a reviewed apply to enable scheduled deletion.

Set `schedule_enabled = false` to stop new scheduled runs. Connect `alarm_actions`
to an existing SNS destination if you want notifications when cleanup fails.

## What is kept

An image becomes eligible only after its push-age window has passed and none of
these protections applies:

- **Recent builds:** images pushed within `retention_days` (default **60 days**;
  the installation example uses 90).
- **Release floor:** the newest **10 tagged images** per repository by default.
- **Deployed images:** current ECS service definitions, deployments, task sets and
  running/pending task digests, including services scaled to zero.
- **Rollback releases:** the latest **four distinct successful ECS image sets** per
  service, including the current release, plus four recent task-definition recipes.
- **Other consumers:** scheduled ECS tasks, container Lambda versions, active Batch
  definitions and nonterminal jobs, CodeBuild and App Runner image references.
- **Explicit pins:** `protected_tags` patterns and additional `protected_images` URIs.
- **Related images:** connected OCI indices, architecture manifests and subject
  artifacts whenever any member is retained. Archived/activating images are excluded.

Image age never overrides a current workload reference. ECS exposes only 90 days of
history; when the requested rollback history is unavailable, the cleaner holds the
service's repositories. A young or rarely deployed service can therefore retain more
images than the age/count settings suggest. Mutable Batch job tags also hold their
repository because Batch does not expose the resolved image digest.

## Safety and scope

- **Account-local discovery.** By default, all enabled regions are scanned. Cross-account
  consumers, EC2-hosted containers and custom workflow references need durable protected
  tags or explicit image pins. An EKS cluster stops cleanup because pod inventory is
  unsupported. The [shared-repository example](https://github.com/SurtonInc/terraform-aws-ecr-cleanup/tree/main/examples/shared-repository)
  conservatively retains all tagged versions for external consumers.
- **Conflicting native policies.** Remove native ECR lifecycle rules that can expire
  images this module would retain before enabling deletion.
- **Inventory errors stop deletion.** Required inventory must complete successfully.
  Write-enabled runs recheck workload references and repository metadata before deletion;
  incomplete reads or per-image deletion failures are reported as errors.
- **No deployment lock.** AWS reads and deletes are separate calls. A manual deployment
  started after the final scan can race cleanup. Rebuild or explicitly protect an older
  image before restoring a release outside the retained history.

Workload discovery permissions are read-only. ECR access is scoped to the configured
repositories, and dry-run removes deletion permission from the role entirely.

## Reference and examples

The Registry's [Inputs](https://registry.terraform.io/modules/SurtonInc/ecr-cleanup/aws/latest?tab=inputs),
[Outputs](https://registry.terraform.io/modules/SurtonInc/ecr-cleanup/aws/latest?tab=outputs)
and [Dependencies](https://registry.terraform.io/modules/SurtonInc/ecr-cleanup/aws/latest?tab=dependencies)
tabs provide the full configuration reference.

- [Basic retention example](https://github.com/SurtonInc/terraform-aws-ecr-cleanup/tree/main/examples/basic)
- [Shared repository with external consumers](https://github.com/SurtonInc/terraform-aws-ecr-cleanup/tree/main/examples/shared-repository)

Requires Terraform `>= 1.3.0`, AWS provider `>= 5.85.0, < 7.0.0` and archive provider
`>= 2.4.0, < 3.0.0`. CI tests Terraform 1.14.5 and OpenTofu 1.13.2. The Python 3.13
Lambda source is bundled and packaged locally by the archive provider.

For contributors: [testing and release guidance](https://github.com/SurtonInc/terraform-aws-ecr-cleanup/blob/main/docs/publishing.md).
