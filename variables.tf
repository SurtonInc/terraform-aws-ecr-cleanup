variable "repository_names" {
  description = "Existing ECR repositories to clean in this account and region."
  type        = list(string)
  validation {
    condition     = length(var.repository_names) > 0 && length(distinct(var.repository_names)) == length(var.repository_names)
    error_message = "Specify at least one unique repository name."
  }
}
variable "retention_days" {
  description = "Minimum image push age in days before an unprotected image is eligible."
  type        = number
  default     = 60
  validation {
    condition     = var.retention_days >= 1 && floor(var.retention_days) == var.retention_days
    error_message = "retention_days must be a positive whole number."
  }
}
variable "protected_tags" {
  description = "Case-sensitive shell patterns for tags to retain indefinitely."
  type        = list(string)
  default     = ["latest", "dev", "qa", "stg", "staging", "demo", "prd", "prd-*"]
  validation {
    condition     = alltrue([for tag in var.protected_tags : length(tag) > 0])
    error_message = "Protected tag patterns must not be empty."
  }
}
variable "protected_images" {
  description = "Additional immutable image URIs or tags, e.g. explicit recovery pins."
  type        = list(string)
  default     = []
}
variable "minimum_images" {
  description = "Minimum number of newest tagged images retained per repository, plus their manifest families."
  type        = number
  default     = 10
  validation {
    condition     = var.minimum_images >= 1 && floor(var.minimum_images) == var.minimum_images
    error_message = "minimum_images must be a positive whole number."
  }
}
variable "rollback_deployments" {
  description = "Distinct successful ECS image sets retained per service, including the current release."
  type        = number
  default     = 4
  validation {
    condition     = var.rollback_deployments >= 1 && floor(var.rollback_deployments) == var.rollback_deployments
    error_message = "rollback_deployments must be a positive whole number."
  }
}
variable "task_definition_revisions" {
  description = "Newest task definition revisions retained per family for bootstrap/migration recipes."
  type        = number
  default     = 4
  validation {
    condition     = var.task_definition_revisions >= 1 && floor(var.task_definition_revisions) == var.task_definition_revisions
    error_message = "task_definition_revisions must be a positive whole number."
  }
}
variable "scan_regions" {
  description = "Account-local workload regions. Empty discovers all enabled EC2 regions."
  type        = list(string)
  default     = []
}
variable "dry_run" {
  description = "Plan and log cleanup without deleting. Also removes ECR deletion permission from the Lambda role."
  type        = bool
  default     = true
}
variable "schedule_enabled" {
  description = "Whether the cleanup schedule is enabled."
  type        = bool
  default     = true
}
variable "schedule_expression" {
  description = "EventBridge schedule expression."
  type        = string
  default     = "cron(0 1 * * ? *)"
}
variable "lambda_timeout" {
  description = "Maximum execution seconds; discovery finishes before deletion starts."
  type        = number
  default     = 600
  validation {
    condition     = var.lambda_timeout >= 60 && var.lambda_timeout <= 900
    error_message = "lambda_timeout must be between 60 and 900 seconds."
  }
}
variable "lambda_memory_size" {
  description = "Lambda memory in MB."
  type        = number
  default     = 256
}
variable "name_prefix" {
  description = "Unique prefix per module instance, including region when sharing an account."
  type        = string
  default     = ""
}
variable "alarm_actions" {
  description = "Optional existing SNS alarm action ARNs for cleanup errors."
  type        = list(string)
  default     = []
}
variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
