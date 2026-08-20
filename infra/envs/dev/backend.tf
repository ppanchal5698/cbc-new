# Remote state in S3 with a DynamoDB lock.
#
# Local state is fine until the second person runs an apply, at which point two
# people hold divergent pictures of the same infrastructure and the loser's
# resources become orphans nobody can see. The lock table prevents concurrent
# applies; versioning on the bucket means a corrupted state file is recoverable.
#
# Chicken and egg: the bucket and table cannot be created by the configuration
# that stores its state in them. Create them once, by hand, before the first init.
# docs/aws-setup.md has the two commands.
#
# Values are supplied at init rather than hardcoded, because the bucket name has
# to be globally unique and therefore account-specific:
#
#   terraform init \
#     -backend-config="bucket=cbc-copilot-tfstate-<account-id>" \
#     -backend-config="key=dev/terraform.tfstate" \
#     -backend-config="region=us-east-1" \
#     -backend-config="dynamodb_table=cbc-copilot-tflock" \
#     -backend-config="encrypt=true"
#
# Or keep those five lines in a gitignored backend.hcl and use -backend-config=backend.hcl.

terraform {
  backend "s3" {}
}
