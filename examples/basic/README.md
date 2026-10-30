# Basic retention

Run a read-only scheduled cleaner for an **existing** `application-api` repository
in us-west-2. Edit the repository name, region and unique name prefix for your account.
The module discovers account-local workload references across enabled regions and
retains recent images, newest tagged releases, deployed/rollback digests and related
manifest families according to the [module policy](../../README.md).

From this directory, validate without AWS credentials:

```sh
terraform init -backend=false
terraform validate
# Or: tofu init -backend=false && tofu validate
```

The local source tests the checked-out module before registry publication. When
copying this example into another project, use the released Git source or the
version-pinned registry source described in [installation](../../README.md#installation).

Deployment requires your AWS credentials and a reviewed plan. Leave `dry_run = true`
for initial rollout; the Lambda role has no image-deletion permission. Review its
successful candidate logs before enabling writes through a later reviewed apply.
The module does not create the ECR repository.
