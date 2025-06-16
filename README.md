# ECR Cleanup Terraform Module

This Terraform module provides an automated solution for cleaning up old Amazon Elastic Container Registry (ECR) images based on configurable retention policies. It helps manage ECR storage costs by automatically deleting images older than a specified number of days while preserving images with protected tags.

## Features

- 🗑️ **Automated Cleanup**: Scheduled Lambda function to delete old ECR images
- 🏷️ **Tag Protection**: Preserves images with specific tags (e.g., `latest`, `dev`, `qa`, `stg`, `prd`)
- ⏰ **Configurable Schedule**: Customizable cleanup frequency using cron expressions
- 📊 **Multi-Repository Support**: Manage multiple ECR repositories simultaneously
- 📝 **Comprehensive Logging**: CloudWatch logs for audit and troubleshooting
- 🔒 **Secure**: Follows least-privilege IAM principles

## Architecture

The module creates the following AWS resources:

- **Lambda Function**: Python function that performs the image cleanup
- **CloudWatch Event Rule**: Triggers the Lambda function on schedule
- **IAM Role & Policy**: Grants necessary permissions to the Lambda function
- **CloudWatch Log Group**: Stores execution logs

## Usage

### Basic Example

```hcl
module "ecr_cleanup" {
  source = "./ecr-cleanup"

  repository_names = ["my-app", "my-service"]
  retention_days   = 30
}
```

### Advanced Example

```hcl
module "ecr_cleanup" {
  source = "./ecr-cleanup"

  name_prefix = "prod-"
  
  repository_names = [
    "frontend-app",
    "backend-api",
    "worker-service"
  ]
  
  retention_days = 90
  
  protected_tags = [
    "latest",
    "stable",
    "release-*",
    "v*"
  ]
  
  schedule_expression = "cron(0 3 * * ? *)"  # Run at 3 AM UTC daily
  
  lambda_timeout      = 600
  lambda_memory_size  = 256
  
  tags = {
    Environment = "production"
    Team        = "devops"
    CostCenter  = "engineering"
  }
}
```

## Configuration

### Required Variables

| Name | Description | Type |
|------|-------------|------|
| `repository_names` | List of ECR repository names to clean up | `list(string)` |

### Optional Variables

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `retention_days` | Number of days to retain images | `number` | `60` |
| `protected_tags` | List of tags that prevent image deletion | `list(string)` | `["latest", "dev", "qa", "stg", "prd"]` |
| `schedule_expression` | CloudWatch Events schedule expression | `string` | `"cron(0 1 * * ? *)"` |
| `lambda_timeout` | Lambda function timeout in seconds | `number` | `300` |
| `lambda_memory_size` | Lambda function memory size in MB | `number` | `128` |
| `name_prefix` | Prefix for all created resource names | `string` | `""` |
| `tags` | Tags to apply to all resources | `map(string)` | `{}` |

### Schedule Expression Examples

- `"cron(0 1 * * ? *)"` - Daily at 1:00 AM UTC
- `"cron(0 0 * * MON *)"` - Weekly on Mondays at midnight UTC
- `"cron(0 2 1 * ? *)"` - Monthly on the 1st at 2:00 AM UTC
- `"rate(6 hours)"` - Every 6 hours

## Outputs

| Name | Description |
|------|-------------|
| `lambda_function_name` | Name of the created Lambda function |
| `lambda_function_arn` | ARN of the Lambda function |
| `lambda_role_arn` | ARN of the Lambda execution role |
| `cloudwatch_event_rule_arn` | ARN of the CloudWatch Event Rule |

## How It Works

1. **Scheduled Trigger**: CloudWatch Events triggers the Lambda function according to the schedule
2. **Repository Scan**: Lambda iterates through each configured ECR repository
3. **Image Analysis**: For each image, the function:
   - Checks if it has any protected tags
   - Calculates the image age
   - Marks images older than retention period for deletion
4. **Batch Deletion**: Deletes eligible images in batches of 100 (AWS API limit)
5. **Logging**: Records all actions to CloudWatch Logs

## Lambda Function Details

The Lambda function (`lambda/index.py`) uses:
- **Runtime**: Python 3.9
- **AWS SDK**: boto3 for ECR operations
- **Environment Variables**:
  - `REPOSITORY_NAMES`: Comma-separated list of repositories
  - `RETENTION_DAYS`: Number of days to retain images
  - `PROTECTED_TAGS`: Comma-separated list of protected tags

## IAM Permissions

The Lambda function is granted minimal permissions:
- `ecr:DescribeRepositories` on specified repositories
- `ecr:ListImages` on specified repositories
- `ecr:DescribeImages` on specified repositories
- `ecr:BatchDeleteImage` on specified repositories
- CloudWatch Logs permissions for its log group

## Monitoring and Troubleshooting

### CloudWatch Logs

All Lambda executions are logged to CloudWatch Logs. The log group name follows the pattern:
```
/aws/lambda/{name_prefix}ecr-cleanup
```

### Common Log Messages

- `"Starting ECR cleanup for repository: {name}"` - Cleanup process initiated
- `"Found {count} images in repository {name}"` - Image discovery complete
- `"Deleting {count} images older than {days} days"` - Deletion in progress
- `"Deleted batch of {count} images"` - Batch deletion successful
- `"Skipping image with protected tag: {tag}"` - Protected image preserved

### Troubleshooting

1. **Lambda Timeout**: Increase `lambda_timeout` for repositories with many images
2. **Permission Errors**: Ensure ECR repositories exist and are in the same region
3. **No Images Deleted**: Check retention period and protected tags configuration

## Cost Considerations

- **Lambda**: Minimal cost due to short execution time and low memory usage
- **CloudWatch Logs**: 14-day retention minimizes storage costs
- **ECR Storage**: Significant savings by removing old, unused images

## Security Best Practices

1. **Least Privilege**: Lambda only has permissions for specified repositories
2. **No Network Access**: Lambda doesn't require VPC configuration
3. **Audit Trail**: All actions are logged to CloudWatch
4. **Tag Protection**: Prevents accidental deletion of important images

## Contributing

When contributing to this module:

1. Update the Lambda function code in `lambda/index.py`
2. Add new variables to `variables.tf` with appropriate defaults
3. Update IAM permissions in `main.tf` if needed
4. Add outputs to `outputs.tf` for new resources
5. Update this README with any new features or changes

## License

This module is provided as-is under the MIT License.

## Support

For issues or questions:
1. Check CloudWatch Logs for error messages
2. Verify IAM permissions and repository names
3. Ensure ECR repositories exist in the correct region
4. Review the Lambda function code for debugging