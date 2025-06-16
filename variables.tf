variable "repository_names" {
  description = "List of ECR repository names to apply cleanup policy"
  type        = list(string)
}

variable "retention_days" {
  description = "Number of days to retain untagged images"
  type        = number
  default     = 60
}

variable "protected_tags" {
  description = "List of tags to protect from cleanup"
  type        = list(string)
  default     = ["latest", "dev", "qa", "stg", "prd"]
}

variable "schedule_expression" {
  description = "CloudWatch Events schedule expression for when to run cleanup"
  type        = string
  default     = "cron(0 1 * * ? *)" # Run at 1:00 AM UTC every day
}

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds"
  type        = number
  default     = 300
}

variable "lambda_memory_size" {
  description = "Lambda function memory size in MB"
  type        = number
  default     = 128
}

variable "name_prefix" {
  description = "Prefix to add to resource names to ensure uniqueness between regions"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags to apply to created resources"
  type        = map(string)
  default     = {}
}
