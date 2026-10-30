# Registry publication

The same public GitHub repository can back Terraform Registry and OpenTofu Registry.
Repository name: `SurtonInc/terraform-aws-ecr-cleanup`.
Module address after registration: `SurtonInc/ecr-cleanup/aws`.

CI validates and tests changes only. It has read-only repository permissions and
does not create tags, GitHub releases, registry submissions or cloud resources.
Publishing is a separate maintainer action.

## Release preparation

1. Review and merge the intended code. Require green Python, Terraform and OpenTofu
   checks, including example validation, at the exact release commit.
2. Choose a semantic version and update `VERSION`. Keep an `Unreleased` changelog
   section above dated, linked releases. The current preparation targets **0.2.2**;
   existing release tags must not be moved.
3. Create and push an immutable `vX.Y.Z` tag for the reviewed release commit when
   release authorization is given. `VERSION` alone does not publish a version.
   Future fixes receive a new version rather than replacing a published tag.

## Terraform Registry

Follow the [official module publishing requirements](https://developer.hashicorp.com/terraform/registry/modules/publish):

- Keep the repository public and named `terraform-aws-ecr-cleanup`.
- Set a repository description such as "Reference-aware ECR image retention with
  automatic workload and rollback protection."
- Retain the MIT license, root Terraform files, README and described inputs/outputs.
- Sign in with a GitHub identity permitted to publish this repository and authorize
  the Registry integration/webhook. Select the repository and publish the module.
- Confirm the expected version is indexed. Subsequent semantic-version tags are
  detected through the registry's webhook.

## OpenTofu Registry

Use the [official registry submission instructions](https://github.com/opentofu/registry#adding-providers-modules-or-gpg-keys-to-the-opentofu-registry).
Submit `SurtonInc/terraform-aws-ecr-cleanup` using the **Submit new Module** GitHub
issue form through the web UI, then wait for approval and version indexing. The
registry explicitly requires that form; API/CLI-created issues and direct data PRs
are not processed. A Terraform Registry entry does not register the OpenTofu entry.

## Verify installation

After both registries index the release, initialize a fresh consumer with each tool:

```hcl
module "ecr_cleanup" {
  source  = "SurtonInc/ecr-cleanup/aws"
  version = "0.2.2"

  name_prefix      = "application-us-west-2"
  repository_names = ["application-api"]
  dry_run          = true
}
```

Run `terraform init -backend=false` and `tofu init -backend=false` in separate clean
directories with an AWS provider configuration. Confirm each downloads the expected
version from its own default registry. Initialization/validation needs no AWS apply.
Consumers can then replace vendored copies through their own reviewed PRs while
preserving module call names and the existing dry-run/retention configuration.

## Contributor checks

Run the Python tests and the shared mocked-provider suite with either CLI:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
terraform init -backend=false
terraform test
# Or: tofu init -backend=false && tofu test
```

CI uses Terraform 1.14.5 and OpenTofu 1.13.2. Positive module tests apply mocked AWS
resources so computed values can be checked consistently; only the local archive
provider executes. The tests perform no AWS mutations. CI also initializes and
validates both local examples without cloud credentials or an AWS plan.
