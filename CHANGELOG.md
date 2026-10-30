# Changelog

## [Unreleased]

### Added

- Test the module and examples with Terraform 1.14.5 and OpenTofu 1.13.2 in CI.
- Add runnable basic and shared-repository examples, registry installation guidance
  and a manual publication checklist for both registries.

### Fixed

- Use portable mocked-provider tests instead of Terraform-only `override_during`.
  AWS remains mocked; tests evaluate computed values after local mock application.

## [0.2.0] - 2026-10-30

### Added

- Discover current ECS digests, successful deployment history, task recipes and other
  supported AWS image consumers before selecting cleanup candidates.
- Hold an entire service repository when ECS's 90-day history cannot establish the requested rollback window.
- Retain newest tagged images and connected OCI manifest/subject families.
- Add dry-run mode with deletion permission absent from IAM, schedule control and a
  Lambda error alarm. Add offline Python and mocked Terraform regression tests.

### Changed

- Cleanup now defaults to dry-run; set `dry_run = false` after
  reviewing initial output to enable automatic deletion. No manual eligibility tags.
- Use Python 3.13, JSON environment values and repository-scoped permissions.

### Fixed

- Normalize standard, FIPS and dual-stack ECR image references to the same repository.
- Ignore non-ECS schedule payloads while validating ECS universal target inputs.
- Retain queued/in-flight Batch job images after definition deregistration; hold the
  repository for mutable job image tags whose resolved digest is unavailable.
- Honor wildcard protected tag patterns documented by the module.
- Propagate inventory and per-image deletion failures instead of reporting success.
- Delete index/subject dependencies in ordered batches and recheck inventory before writes.

[Unreleased]: https://github.com/SurtonInc/terraform-aws-ecr-cleanup/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/SurtonInc/terraform-aws-ecr-cleanup/releases/tag/v0.2.0
