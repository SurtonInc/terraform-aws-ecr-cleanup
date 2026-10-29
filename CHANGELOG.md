# Changelog

## [Unreleased]

### Added

- Discover current ECS digests, successful deployment history, task recipes and other
  supported AWS image consumers before selecting cleanup candidates.
- Hold an entire service repository when ECS's 90-day history cannot establish the requested rollback window.
- Retain newest tagged images and connected OCI manifest/subject families.
- Add dry-run mode with deletion permission absent from IAM, schedule control and a
  Lambda error alarm. Add offline Python and mocked Terraform regression tests.

### Changed

- Prepare version 0.2.0. Cleanup now defaults to dry-run; set `dry_run = false` after
  reviewing initial output to enable automatic deletion. No manual eligibility tags.
- Use Python 3.13, JSON environment values and repository-scoped permissions.

### Fixed

- Honor wildcard protected tag patterns documented by the module.
- Propagate inventory and per-image deletion failures instead of reporting success.
- Delete index/subject dependencies in ordered batches and recheck inventory before writes.
