# Shared repository with external consumers

Retain every tagged image in an existing `shared/runtime` repository, including its
connected manifest families. This is conservative protection for cross-account or
EC2-hosted consumers, which the module's account-local discovery cannot inventory.
Only old, unreferenced, untagged images outside retained families can become candidates.

Every externally used image must have a durable tag or be listed in
`protected_images`. A tag pointing at a different image does not protect an older
unrelated digest. See [scope and limitations](https://github.com/SurtonInc/terraform-aws-ecr-cleanup#safety-and-scope).

Edit the repository name, region and unique name prefix. From this directory,
initialize and validate with either tool without AWS credentials:

```sh
tofu init -backend=false
tofu validate
# Or: terraform init -backend=false && terraform validate
```

The local module source validates the checkout. For a separate consumer project,
use a version-pinned registry source from the
[installation instructions](https://github.com/SurtonInc/terraform-aws-ecr-cleanup#installation).

Leave the initial deployment in dry-run mode and review its candidate logs before a
reviewed apply enables deletion. This example does not create repositories or assume
roles into consumer accounts; see the [basic example](https://github.com/SurtonInc/terraform-aws-ecr-cleanup/tree/main/examples/basic) for deployment steps.
