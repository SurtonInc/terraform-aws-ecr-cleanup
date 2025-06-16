import datetime
import os

import boto3
from botocore.exceptions import ClientError


def handler(event, context):
    ecr_client = boto3.client("ecr")
    retention_days = int(os.environ["RETENTION_DAYS"])
    protected_tags = os.environ["PROTECTED_TAGS"].split(",")
    repositories = os.environ["REPOSITORIES"].split(",")

    cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=retention_days
    )

    for repository_name in repositories:
        try:
            print(f"Processing repository: {repository_name}")

            # Get all images in the repository
            paginator = ecr_client.get_paginator("list_images")
            image_ids_to_delete = []

            for page in paginator.paginate(repositoryName=repository_name):
                # Get more details for each image
                if not page["imageIds"]:
                    continue

                response = ecr_client.describe_images(
                    repositoryName=repository_name,
                    imageIds=[
                        {"imageDigest": img["imageDigest"]}
                        for img in page["imageIds"]
                        if "imageDigest" in img
                    ],
                )

                for image in response["imageDetails"]:
                    # Skip images that have protected tags
                    image_tags = image.get("imageTags", [])
                    has_protected_tag = any(tag in protected_tags for tag in image_tags)

                    if has_protected_tag:
                        print(
                            f"Skipping image {image['imageDigest'][:12]} with protected tags: {', '.join(image_tags)}"
                        )
                        continue

                    # Delete images older than retention period
                    pushed_at = image["imagePushedAt"]
                    if pushed_at < cutoff_date:
                        image_id = {"imageDigest": image["imageDigest"]}
                        image_ids_to_delete.append(image_id)
                        print(
                            f"Marking for deletion: {image['imageDigest'][:12]} pushed at {pushed_at}"
                        )

            # Delete the images in batches of 100 (AWS limit)
            if image_ids_to_delete:
                print(
                    f"Deleting {len(image_ids_to_delete)} images from {repository_name}"
                )
                # AWS limits batch deletion to 100 images
                for i in range(0, len(image_ids_to_delete), 100):
                    batch = image_ids_to_delete[i : i + 100]
                    ecr_client.batch_delete_image(
                        repositoryName=repository_name, imageIds=batch
                    )
                print(
                    f"Successfully deleted {len(image_ids_to_delete)} images from {repository_name}"
                )
            else:
                print(f"No images to delete in {repository_name}")

        except ClientError as e:
            print(f"Error processing repository {repository_name}: {e}")

    return {"statusCode": 200, "body": "ECR cleanup completed"}
