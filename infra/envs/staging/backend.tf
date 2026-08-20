# Remote state in S3 with a DynamoDB lock. See envs/dev/backend.tf for why, and
# docs/aws-setup.md for the one-time bootstrap of the bucket and table.
#
#   terraform init #     -backend-config="bucket=cbc-copilot-tfstate-<account-id>" #     -backend-config="key=staging/terraform.tfstate" #     -backend-config="region=us-east-1" #     -backend-config="dynamodb_table=cbc-copilot-tflock" #     -backend-config="encrypt=true"

terraform {
  backend "s3" {}
}
