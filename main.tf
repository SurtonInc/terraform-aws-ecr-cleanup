locals {
  lambda_function_name = "${var.name_prefix}-ecr-image-cleanup"
  lambda_zip_filename  = "${path.module}/lambda_function.zip"
  lambda_source_dir    = "${path.module}/lambda"
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# Archive the Lambda function code
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = local.lambda_source_dir
  output_path = local.lambda_zip_filename
}

# IAM role for the Lambda function
resource "aws_iam_role" "lambda_role" {
  name = "${local.lambda_function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action = "sts:AssumeRole",
      Effect = "Allow",
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = var.tags
}

# IAM policy for Lambda to access ECR and CloudWatch Logs
resource "aws_iam_policy" "lambda_policy" {
  name        = "${local.lambda_function_name}-policy"
  description = "Policy for ECR cleanup Lambda function"

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "ecr:ListImages",
          "ecr:DescribeImages",
          "ecr:BatchDeleteImage"
        ],
        Resource = [
          for repo in var.repository_names :
          "arn:aws:ecr:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:repository/${repo}"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.lambda_function_name}*:*"
      }
    ]
  })
}

# Attach policy to role
resource "aws_iam_role_policy_attachment" "lambda_policy_attachment" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# Lambda function
resource "aws_lambda_function" "ecr_cleanup" {
  function_name    = local.lambda_function_name
  filename         = local.lambda_zip_filename
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  role             = aws_iam_role.lambda_role.arn
  handler          = "index.handler"
  runtime          = "python3.9"
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory_size

  environment {
    variables = {
      RETENTION_DAYS = var.retention_days
      PROTECTED_TAGS = join(",", var.protected_tags)
      REPOSITORIES   = join(",", var.repository_names)
    }
  }

  tags = var.tags
}

# CloudWatch Log Group for Lambda
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${local.lambda_function_name}"
  retention_in_days = 14

  tags = var.tags
}

# CloudWatch Event Rule to trigger Lambda
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${local.lambda_function_name}-schedule"
  description         = "Schedule for ECR image cleanup"
  schedule_expression = var.schedule_expression

  tags = var.tags
}

# CloudWatch Event Target pointing to the Lambda function
resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "InvokeECRCleanup"
  arn       = aws_lambda_function.ecr_cleanup.arn
}

# Permission for CloudWatch Events to invoke Lambda
resource "aws_lambda_permission" "allow_cloudwatch" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ecr_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
