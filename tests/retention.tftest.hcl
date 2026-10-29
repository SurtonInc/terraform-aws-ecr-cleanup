mock_provider "aws" {
  override_during = plan
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_region" { defaults = { name = "us-west-2" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/cleanup" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:us-west-2:123456789012:log-group:/aws/lambda/test-cleanup" } }
}
variables {
  name_prefix      = "test-us-west-2"
  repository_names = ["example-api"]
}
run "dry_run_has_no_delete_permission" {
  command = plan
  assert {
    condition     = !strcontains(aws_iam_policy.lambda_policy.policy, "ecr:BatchDeleteImage") && aws_lambda_function.ecr_cleanup.environment[0].variables["DRY_RUN"] == "true"
    error_message = "Dry-run must prevent deletion both in code configuration and IAM."
  }
  assert {
    condition     = strcontains(aws_iam_policy.lambda_policy.policy, "apprunner:ListServices") && strcontains(aws_iam_policy.lambda_policy.policy, "apprunner:DescribeService")
    error_message = "App Runner discovery must work with the deployed role, including accounts with no services."
  }
  assert {
    condition     = aws_lambda_function.ecr_cleanup.runtime == "python3.13" && aws_lambda_function.ecr_cleanup.reserved_concurrent_executions == 1
    error_message = "Use a supported runtime and avoid concurrent cleaners."
  }
}
run "enabled_deletion_is_scoped_to_requested_repositories" {
  command = plan
  variables { dry_run = false }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.lambda_policy.policy).Statement :
      !contains(statement.Action, "ecr:BatchDeleteImage") || statement.Resource == ["arn:aws:ecr:us-west-2:123456789012:repository/example-api"]
    ])
    error_message = "Deletion permission must not escape the configured repository set."
  }
  assert {
    condition     = strcontains(aws_iam_policy.lambda_policy.policy, "ecs:DescribeServiceRevisions") && strcontains(aws_iam_policy.lambda_policy.policy, "ecr:BatchGetImage")
    error_message = "Rollback digest and manifest discovery need their read permissions."
  }
}
run "invalid_retention_fails" {
  command = plan
  variables { retention_days = 0 }
  expect_failures = [var.retention_days]
}
run "invalid_repository_list_fails" {
  command = plan
  variables { repository_names = [] }
  expect_failures = [var.repository_names]
}
run "schedule_can_be_disabled" {
  command = plan
  variables { schedule_enabled = false }
  assert {
    condition     = aws_cloudwatch_event_rule.schedule.state == "DISABLED"
    error_message = "The schedule disable switch must be honored."
  }
}
