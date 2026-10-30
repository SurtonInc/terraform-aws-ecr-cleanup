terraform {
  required_version = ">= 1.3.0"
  required_providers {
    aws = { source = "hashicorp/aws" }
  }
}

provider "aws" {
  region = "us-west-2"
}

module "ecr_cleanup" {
  source = "../.."

  name_prefix      = "shared-runtime-us-west-2"
  repository_names = ["shared/runtime"]
  retention_days   = 90
  protected_tags   = ["*"]
  dry_run          = true
}

output "cleanup_function_name" {
  description = "Scheduled cleaner whose logs contain the dry-run candidate plan."
  value       = module.ecr_cleanup.lambda_function_name
}
