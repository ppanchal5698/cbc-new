# Connecting an AWS account

Everything here is yours to run. **Never paste a key, a secret, or a session token
into a chat, a ticket, or a commit** — the only verification anyone needs is
`aws sts get-caller-identity`, which prints an account id and an ARN and no
credentials.

Prerequisites: an AWS account you control, and Terraform ≥ 1.9.

---

## 1. Install the AWS CLI

**Windows**

```powershell
winget install --id Amazon.AWSCLI -e
```

**macOS**

```bash
brew install awscli
```

**Linux**

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
```

```bash
aws --version
```

## 2. Secure the root account

Do this before creating anything. The root user can close the account, remove
Object Lock protections, and see every bill.

1. Sign in as root → **Security credentials**.
2. **Enable MFA.** A hardware key or an authenticator app.
3. **Delete any root access keys.** Root should have none. If one exists, it is
   the single largest risk in the account.
4. **Account** → set an **account alias**, so the sign-in page shows a name rather
   than a twelve-digit number.
5. **Billing preferences** → turn on **Receive Free Tier Alerts** with a real
   email address.

Then stop using root. Everything below is an IAM user.

## 3. Create the deploy user

Console → **IAM** → **Users** → **Create user**.

- Name: `cbc-copilot-deploy`
- **Do not** check "Provide user access to the AWS Management Console".
- Attach `AdministratorAccess` **for the initial bootstrap only**.

> Admin is the honest starting point: Terraform creates IAM roles, S3 buckets with
> Object Lock, RDS, VPC endpoints, CloudFront, and Budgets, and hand-deriving a
> least-privilege policy for that up front produces a long list of
> `AccessDenied` retries and a policy nobody trusts. Once `dev` applies cleanly,
> generate a scoped policy from the access advisor data and swap it in. Track that
> as a real task, not an intention.

Then **Security credentials** → **Create access key** → *Command Line Interface*.
Leave the page open for the next step; the secret is shown once.

Also enable MFA on this user.

## 4. Configure the CLI

```bash
aws configure --profile cbc
```

Paste the key id and secret at the prompts, region `us-east-1`, output `json`.
Nothing is echoed anywhere else.

```bash
export AWS_PROFILE=cbc          # PowerShell: $env:AWS_PROFILE = "cbc"
aws sts get-caller-identity
```

Expected — an account id and the user ARN, and no secrets:

```json
{
  "UserId": "AIDA...",
  "Account": "123456789012",
  "Arn": "arn:aws:iam::123456789012:user/cbc-copilot-deploy"
}
```

`make aws-whoami` runs exactly this.

## 5. Grant Bedrock model access

**This is a manual console step. AWS provides no API for it and Terraform cannot
do it.** Until it is done, every extraction call fails with `AccessDeniedException`
and `resolve_bedrock_models.py` finds nothing (§14.1 Q1).

1. Console → **Amazon Bedrock**, region **us-east-1**.
2. Left nav → **Model access** → **Modify model access**.
3. Enable the **Anthropic** models: a Claude Haiku (the cheap locate pass) and the
   strongest available Claude (the extraction pass).
4. Submit. Anthropic models are usually granted immediately; occasionally there is
   a short review.

Verify:

```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `anthropic`)].inferenceProfileId'
```

A non-empty list means access is live. **Inference profiles are what matters** —
current Claude models are not invocable by bare foundation-model id, which is why
the IAM policy in `infra/modules/ai` grants both ARN shapes.

Bedrock has **no free tier**. Processing the reference bid set costs cents, not
dollars, but not zero.

## 6. Bootstrap Terraform state

State lives in S3 with a DynamoDB lock. That bucket and table cannot be created by
the configuration that stores its state in them, so create them once by hand.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

aws s3api create-bucket \
  --bucket "cbc-copilot-tfstate-$ACCOUNT" --region us-east-1

aws s3api put-bucket-versioning \
  --bucket "cbc-copilot-tfstate-$ACCOUNT" \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket "cbc-copilot-tfstate-$ACCOUNT" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws dynamodb create-table \
  --table-name cbc-copilot-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region us-east-1
```

Versioning on the state bucket is not optional: it is the only way back from a
corrupted or truncated state file.

## 7. Plan the dev environment

```bash
cd infra/envs/dev
cp terraform.tfvars.example terraform.tfvars   # then edit it
```

Set `alert_emails` to a real address. Leave `object_lock_retention_days`
commented out — see step 9.

```bash
terraform init \
  -backend-config="bucket=cbc-copilot-tfstate-$ACCOUNT" \
  -backend-config="key=dev/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=cbc-copilot-tflock" \
  -backend-config="encrypt=true"

terraform validate
terraform plan -out=dev.tfplan
```

**Read the plan.** Confirm the instance types are `t3.micro` and `db.t3.micro`,
that there is no NAT gateway, and that the budget is $10.

```bash
terraform apply dev.tfplan
```

Roughly 15 minutes, most of it RDS.

## 8. After the first apply

```bash
make bedrock-resolve            # pins model IDs in SSM (C5)
```

Confirm the SNS subscription email, or the alarms fire into nothing.

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(terraform -chdir=infra/envs/dev output -raw alerts_topic_arn 2>/dev/null)"
```

`PendingConfirmation` means nobody clicked the link.

## 9. Object Lock retention — still open

`terraform output object_lock_retention_configured` returns **false**, on purpose.

The source bucket is created with Object Lock **enabled** — that flag cannot be
set after creation, so deferring it was never an option — but **no default
retention period is applied**. §11.3 requires the period signed off in writing
first, and once objects are written under a retention period it cannot be
shortened for them. A plausible-looking default is a decade of storage nobody
agreed to pay for, or a legal-hold posture nobody agreed to adopt.

When CBC gives a number, set `object_lock_retention_days` in `terraform.tfvars`
and apply. Nothing else changes.

## 10. Cost guards, all in before the first Textract call

| Guard | Where |
|---|---|
| $10 monthly budget, 50/80/100% plus a forecast alert | `modules/observability` |
| Cost Anomaly Detection, $10 absolute impact | `modules/observability` |
| `MAX_OCR_COST_PER_DOCUMENT_USD` = $2.00, checked **before** any OCR call | SSM, read by `shared/config.py` |
| Page triage: 65 pages for $0.12 instead of $0.98 | `config/ocr_routes.json` |

Textract's free tier covers 1,000 `AnalyzeDocument` pages per month for the first
three months. Bedrock has none.

## 11. Tearing dev down

```bash
cd infra/envs/dev && terraform destroy
```

The source bucket has `prevent_destroy` and will refuse. That is intentional — it
holds client documents. Empty it deliberately, or remove it from state and delete
it by hand once you are certain.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `AccessDeniedException` from Bedrock | Step 5 not done, or done in a different region. |
| `resolve_bedrock_models.py` finds nothing | Same. Run it with `--list` to see what the account can actually see. |
| `InvalidBucketState` on the source bucket | Object Lock needs versioning. The storage module sets both; this only appears if a bucket was created by hand. |
| Terraform hangs on `Acquiring state lock` | A previous run died. Confirm nobody else is applying, then `terraform force-unlock <id>`. |
| RDS apply takes 15+ minutes | Normal. |
| Instance unreachable, no SSH | By design: there is no SSH ingress rule. Use `aws ssm start-session --target <instance-id>`. |
