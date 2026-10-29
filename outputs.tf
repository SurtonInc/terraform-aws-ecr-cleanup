output "lambda_function_name" {
  description = "Name of the ECR cleanup Lambda function"
  value       = aws_lambda_function.ecr_cleanup.function_name
}

output "lambda_function_arn" {
  description = "ARN of the ECR cleanup Lambda function"
  value       = aws_lambda_function.ecr_cleanup.arn
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role"
  value       = aws_iam_role.lambda_role.arn
}

output "cloudwatch_event_rule_arn" {
  description = "ARN of the CloudWatch Event Rule"
  value       = aws_cloudwatch_event_rule.schedule.arn
}

output "configuration" {
  description = "Retention configuration for rollout review and caller tests."
  value = {
    repository_names     = var.repository_names
    retention_days       = var.retention_days
    minimum_images       = var.minimum_images
    rollback_deployments = var.rollback_deployments
    dry_run              = var.dry_run
  }
}
