locals {
  lambda_function_name = "${var.name_prefix}-ecr-image-cleanup"
  lambda_zip_filename  = "${path.root}/.terraform/${local.lambda_function_name}.zip"
  repository_arns      = [for repo in var.repository_names : "arn:${data.aws_partition.current.partition}:ecr:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:repository/${repo}"]
}
data "aws_partition" "current" {}
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = local.lambda_zip_filename
  excludes    = ["__pycache__", "__pycache__/*", "*.pyc"]
}
resource "aws_iam_role" "lambda_role" {
  name = "${local.lambda_function_name}-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
  tags = var.tags
}
resource "aws_iam_policy" "lambda_policy" {
  name        = "${local.lambda_function_name}-policy"
  description = "ECR retention with read-only workload discovery and explicitly enabled repository deletion"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchGetImage"]
        Resource = local.repository_arns
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeRegions",
          "ecs:ListClusters", "ecs:ListServices", "ecs:DescribeServices", "ecs:ListTasks", "ecs:DescribeTasks",
          "ecs:ListTaskDefinitions", "ecs:DescribeTaskDefinition", "ecs:ListServiceDeployments", "ecs:DescribeServiceRevisions",
          "events:ListEventBuses", "events:ListRules", "events:ListTargetsByRule",
          "scheduler:ListScheduleGroups", "scheduler:ListSchedules", "scheduler:GetSchedule",
          "lambda:ListFunctions", "lambda:ListVersionsByFunction", "lambda:GetFunction",
          "batch:DescribeJobDefinitions", "codebuild:ListProjects", "codebuild:BatchGetProjects", "eks:ListClusters"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda_logs.arn}:*"
      }
      ], var.dry_run ? [] : [{
        Effect   = "Allow"
        Action   = ["ecr:BatchDeleteImage"]
        Resource = local.repository_arns
    }])
  })
  tags = var.tags
}
resource "aws_iam_role_policy_attachment" "lambda_policy_attachment" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${local.lambda_function_name}"
  retention_in_days = 14
  tags              = var.tags
}
resource "aws_lambda_function" "ecr_cleanup" {
  function_name                  = local.lambda_function_name
  filename                       = local.lambda_zip_filename
  source_code_hash               = data.archive_file.lambda_zip.output_base64sha256
  role                           = aws_iam_role.lambda_role.arn
  handler                        = "index.handler"
  runtime                        = "python3.13"
  timeout                        = var.lambda_timeout
  memory_size                    = var.lambda_memory_size
  reserved_concurrent_executions = 1
  environment {
    variables = {
      RETENTION_DAYS            = tostring(var.retention_days)
      PROTECTED_TAGS_JSON       = jsonencode(var.protected_tags)
      PROTECTED_IMAGES_JSON     = jsonencode(var.protected_images)
      REPOSITORIES_JSON         = jsonencode(var.repository_names)
      SCAN_REGIONS_JSON         = jsonencode(var.scan_regions)
      MINIMUM_IMAGES            = tostring(var.minimum_images)
      ROLLBACK_DEPLOYMENTS      = tostring(var.rollback_deployments)
      TASK_DEFINITION_REVISIONS = tostring(var.task_definition_revisions)
      DRY_RUN                   = tostring(var.dry_run)
    }
  }
  depends_on = [aws_iam_role_policy_attachment.lambda_policy_attachment, aws_cloudwatch_log_group.lambda_logs]
  tags       = var.tags
}
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${local.lambda_function_name}-schedule"
  description         = "Reference-aware ECR image retention"
  schedule_expression = var.schedule_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"
  tags                = var.tags
}
resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "InvokeECRCleanup"
  arn       = aws_lambda_function.ecr_cleanup.arn
}
resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ecr_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.lambda_function_name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ecr_cleanup.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  tags                = var.tags
}
