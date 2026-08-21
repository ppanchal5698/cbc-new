# Connecting an AWS account

Written for someone who has never used AWS. If you already know IAM, skip to
[Part 1 step 3](#3-create-a-user-for-this-project).

**Part 1** is what you need today, to run one bid set through extraction. It creates
no servers, no databases, and nothing that bills by the hour.
**Part 2** is for later, when the system is actually deployed.

> **Never paste an access key into a chat, a ticket, a commit, or a screenshot.**
> Nobody helping you needs it. The only check anyone needs is
> `aws sts get-caller-identity`, which prints an account number and a name and no
> secrets. If a key is ever exposed, deactivate it in the console first and ask
> questions afterwards — deleting a key takes ten seconds and costs nothing.

---

## The five words you need

| Word | What it means |
|---|---|
| **Account** | Your whole AWS world, identified by a 12-digit number. One bill, one set of resources. |
| **Region** | A physical location, like `us-east-1` (Northern Virginia). Resources live in *one* region, and a thing created in one region is invisible from another. **We use `us-east-1` throughout.** Getting the region wrong is the single most common first-week confusion. |
| **Root user** | The email you signed up with. It can do anything, including close the account and delete every backup. You use it twice — to sign up, and to lock it down — and then effectively never again. |
| **IAM user** | A named identity with only the permissions you grant it. This is what you actually work as. |
| **Access key** | A username/password pair for programs instead of people: an *access key ID* (public-ish, starts `AKIA…`) and a *secret access key* (shown exactly once, ever). |

---

# Part 1 — What you need today

## 1. Create the account

Skip if you already have one.

1. Go to <https://aws.amazon.com/> → **Create an AWS Account**.
2. Email, password, and an account name.
3. **A credit or debit card is required**, even on the free tier. AWS places a
   small temporary authorisation (around $1) and refunds it.
4. Phone verification.
5. Choose the **Basic support plan** — it is free. The paid plans are not needed.

Sign-up takes a few minutes; account activation is usually instant but can take
a few hours.

## 2. Lock down the root user

Do this before creating anything else. The root user can do irreversible things,
so the goal is to make it hard to misuse and then stop using it.

Sign in as root, then click your **account name, top right → Security credentials**.

1. **Turn on MFA.** "Multi-factor authentication" means a second proof of identity
   beyond the password — a code from an app on your phone. Choose *Authenticator
   app*, scan the QR code with Google Authenticator, Microsoft Authenticator, or
   1Password, and enter two consecutive codes.

   This is the single most valuable thing on this page. A leaked root password
   without MFA is a lost account.

2. **Delete any root access keys.** Scroll to *Access keys*. There should be none.
   If there are, delete them. Root should never have programmatic keys — anything
   holding one can do anything, forever.

3. **Set an account alias.** *IAM → Dashboard → Account Alias → Create*. Use
   something like `cbc-copilot`. It just makes the sign-in page show a name
   instead of twelve digits.

4. **Turn on billing alerts.** *Account name → Billing and Cost Management →
   Billing preferences* → tick **Receive AWS Free Tier Alerts** and enter an email
   you actually read.

Now sign out of root. Everything below is done as the IAM user you are about to
create.

## 3. Create a user for this project

Working as root is like doing your day job logged in as the domain admin: it works
right up until it doesn't.

**IAM → Users → Create user**

1. Name: `cbc-copilot-deploy`
2. **Leave "Provide user access to the AWS Management Console" unticked.** This
   user is for the command line only.
3. **Next → Attach policies directly → Create policy** (opens a new tab).
4. Choose the **JSON** tab, replace everything with this, and continue:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockForExtraction",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel",
        "bedrock:ListInferenceProfiles",
        "bedrock:GetInferenceProfile",
        "bedrock:GetFoundationModelAvailability"
      ],
      "Resource": "*"
    },
    {
      "Sid": "MarketplaceSubscriptionForModelAccess",
      "Effect": "Allow",
      "Action": [
        "aws-marketplace:ViewSubscriptions",
        "aws-marketplace:Subscribe"
      ],
      "Resource": "*"
    }
  ]
}
```

> **The Marketplace block is not optional.** Current Claude models are delivered
> through an AWS Marketplace subscription, and the first `InvokeModel` call against
> an unsubscribed model tries to create that subscription as *you*. Without these
> two actions the call fails with a message that reads like a Bedrock problem:
>
> > *Model access is denied due to IAM user or service role is not authorized to
> > perform the required AWS Marketplace actions (aws-marketplace:ViewSubscriptions,
> > aws-marketplace:Subscribe) to enable access to this model.*
>
> `aws-marketplace:Unsubscribe` is deliberately **not** granted — this user should
> never be able to remove access, only use it.

Name it `cbc-copilot-bedrock`. It grants **only** the ability to list and call
Anthropic models and to subscribe to them. It cannot create servers, read your
bill, or touch storage. That is deliberate: today's run needs nothing else, and a
key that can only do one thing can only cause one kind of problem.

5. Back in the user tab, refresh the policy list, tick `cbc-copilot-bedrock`,
   **Next → Create user**.

> When you later deploy the system for real, this user needs far more permission —
> see [Part 2](#part-2--later-when-you-deploy).

## 4. Create an access key

Open the user you just made → **Security credentials** tab → **Create access key**.

1. Use case: **Command Line Interface (CLI)**.
2. Tick the confirmation box, **Next**, **Create access key**.
3. You now see the **Access key ID** and the **Secret access key**.

   **The secret is shown on this screen once and never again.** Leave the page
   open until the next step is finished. If you lose it, delete the key and make a
   new one — that is normal and costs nothing.

Also turn on MFA for this user while you are here.

## 5. Install the AWS CLI

The CLI is a program that talks to AWS from your terminal.

**Windows** — in PowerShell:

```powershell
winget install --id Amazon.AWSCLI -e
```

**macOS:**

```bash
brew install awscli
```

**Linux:**

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
```

**Close and reopen your terminal**, then confirm it is installed:

```bash
aws --version
```

Expect something like `aws-cli/2.x.x`. "Command not found" almost always means the
terminal was opened before the install finished — reopen it.

## 6. Connect the CLI to your account

```bash
aws configure --profile cbc
```

It asks four questions:

| Prompt | Answer |
|---|---|
| `AWS Access Key ID` | paste from step 4 |
| `AWS Secret Access Key` | paste from step 4 (it will not echo — that is normal) |
| `Default region name` | `us-east-1` |
| `Default output format` | `json` |

`--profile cbc` gives this a name, so these credentials never become the default
for anything else on your machine. The values are stored in `~/.aws/credentials`.

Check it worked:

```bash
aws sts get-caller-identity --profile cbc
```

```json
{
  "UserId": "AIDA...",
  "Account": "123456789012",
  "Arn": "arn:aws:iam::123456789012:user/cbc-copilot-deploy"
}
```

An account number and a name, and no secrets — this is the output that is safe to
share. `make aws-whoami` runs the same command.

If you get `InvalidClientTokenId`, the key was mistyped or has been deleted. Run
`aws configure --profile cbc` again.

## 7. Turn on the Claude models

**This step cannot be automated. AWS requires a human to request model access, and
Terraform has no way to do it.** Until it is done, every extraction call fails with
`AccessDeniedException`.

1. Sign in to the console. **Not as `cbc-copilot-deploy`** — that user has no
   console access by design (step 3). Use the root user, or an admin user with
   console access. Whoever grants the subscription needs Marketplace permissions,
   and root has them.
2. **Check the region selector in the top-right reads `N. Virginia (us-east-1)`.**
   Model access granted in one region does not apply in another, and this is where
   most first attempts go wrong.
3. Search for **Bedrock** → open it.
4. Left sidebar, near the bottom: **Model access** → **Modify model access**
   (older console: *Manage model access*).
5. Tick the **Anthropic** models:
   - a **Claude Haiku** — the cheap first pass that finds which tables are schedules
   - the **strongest Claude available** — the pass that extracts values and must cite
     its sources
6. **Next → Submit.**

Anthropic models are usually granted within a minute. The status column changes to
**Access granted**.

Verify from the terminal:

```bash
aws bedrock list-inference-profiles --region us-east-1 --profile cbc \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `anthropic`)].inferenceProfileId'
```

A non-empty list means the profiles **exist**. It does *not* mean you can call
them — listing and invoking are different permissions, and the profiles are listed
whether or not the subscription completed. Prove the grant by invoking:

```bash
aws bedrock-runtime converse --region us-east-1 --profile cbc   --model-id us.anthropic.claude-sonnet-4-6   --messages '[{"role":"user","content":[{"text":"hi"}]}]'   --inference-config '{"maxTokens":1,"temperature":0}'
```

A JSON reply means model access is real. `AccessDeniedException` means the console
grant did not complete, or the region is wrong.

`make bedrock-resolve` does exactly this check before pinning anything, walking
down the preference order and stopping at the first model it can actually invoke —
a pinned-but-uninvocable model ID is a deploy that looks configured and is not.

> **Why "inference profiles" and not model names?** Current Claude models are not
> callable by a plain model name — they are reached through an *inference profile*,
> which routes the request across several regions for capacity. That is why the
> code resolves model IDs at deploy time rather than hardcoding one, and why the
> IAM policy covers both shapes.

## 8. Hand back

In the **same terminal you run `docker compose` from**, pull the two values out of
the profile so you never retype or paste them:

**Git Bash / macOS / Linux:**

```bash
export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile cbc)
export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile cbc)
```

**PowerShell:**

```powershell
$env:AWS_ACCESS_KEY_ID = (aws configure get aws_access_key_id --profile cbc)
$env:AWS_SECRET_ACCESS_KEY = (aws configure get aws_secret_access_key --profile cbc)
```

These live only in that terminal window and vanish when you close it. Nothing is
written to the repository. `docker-compose.yml` reads them if they are set and
falls back to placeholders if they are not, so the offline loop keeps working
either way.

Then restart the stack so the containers pick them up:

```bash
docker compose up -d api pipeline
```

That is everything. The remaining work — resolving the model IDs, re-running the
bid set, and reporting what it cost — happens from here.

## What Part 1 costs

| | |
|---|---|
| Creating the account, the user, and the key | **$0** |
| Turning on model access | **$0** — you pay per call, not for access |
| Running the bid set once | **Cents.** Bedrock has no free tier, but this is one document |
| Everything else | **$0** — no servers, no database, nothing hourly |

Textract is not called at all: the local run replays OCR from the PDF's own text
layer (`FAKE_OCR=1`).

The thing to watch is not this run, it is *repetition*. There is no automatic
Bedrock spend cap until the budget in Part 2 exists, so the number gets reported
after the first run before anything is repeated.

---

# Part 2 — Later, when you deploy

Not needed to run a bid set. This is for standing up real infrastructure, and it
does create resources that bill.

The scoped Bedrock policy from step 3 is **not** enough here. Terraform creates IAM
roles, S3 buckets, RDS, VPC endpoints, CloudFront, and Budgets. Attach
`AdministratorAccess` to `cbc-copilot-deploy` for the initial bootstrap, then
narrow it from the access-advisor data once `dev` applies cleanly. Track that
narrowing as a real task — "we'll tighten it later" is how accounts stay wide open.

## Bootstrap Terraform state

Terraform records what it created in a *state file*. It lives in S3 with a
DynamoDB lock so two people cannot apply at once. That bucket and table cannot be
created by the configuration that stores its state in them, so create them once by
hand:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text --profile cbc)

aws s3api create-bucket --bucket "cbc-copilot-tfstate-$ACCOUNT" --region us-east-1

aws s3api put-bucket-versioning --bucket "cbc-copilot-tfstate-$ACCOUNT" \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block --bucket "cbc-copilot-tfstate-$ACCOUNT" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws dynamodb create-table --table-name cbc-copilot-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region us-east-1
```

Versioning on that bucket is not optional — it is the only way back from a
corrupted state file.

## Plan and apply dev

```bash
cd infra/envs/dev
cp terraform.tfvars.example terraform.tfvars   # then edit: put a real email in alert_emails

terraform init \
  -backend-config="bucket=cbc-copilot-tfstate-$ACCOUNT" \
  -backend-config="key=dev/terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="dynamodb_table=cbc-copilot-tflock" \
  -backend-config="encrypt=true"

terraform validate
terraform plan -out=dev.tfplan
```

**Read the plan before applying.** Confirm the instance types are `t3.micro` and
`db.t3.micro`, that no NAT gateway appears, and that the budget is $10.

```bash
terraform apply dev.tfplan
```

Roughly 15 minutes, most of it RDS.

Afterwards, `make bedrock-resolve` pins the model IDs into SSM, and the SNS
subscription email needs confirming — until someone clicks that link, every alarm
fires into nothing:

```bash
aws sns list-subscriptions-by-topic --profile cbc \
  --topic-arn "$(terraform output -raw alerts_topic_arn)"
```

`PendingConfirmation` means nobody clicked.

> **Applying dev gives you running hardware and no application.** The compute
> module creates bare EC2 instances — there is no user-data, container runtime, or
> systemd unit yet. Deploying the software is a separate piece of work.

## Object Lock retention is deliberately unset

`terraform output object_lock_retention_configured` returns **false** on purpose.

The source bucket is created with Object Lock *enabled* — that flag cannot be added
after creation, so deferring it was never an option — but **no retention period is
set**. §11.3 requires that period agreed in writing first, and once objects are
written under a retention period it cannot be shortened for them. A plausible
default here is either a decade of storage nobody agreed to pay for or a
legal-hold posture nobody agreed to adopt.

When CBC gives a number, set `object_lock_retention_days` in `terraform.tfvars`
and apply. Nothing else changes.

## Cost guards, all in before the first Textract call

| Guard | Where |
|---|---|
| $10 monthly budget, alerts at 50/80/100% plus a forecast alert | `modules/observability` |
| Cost Anomaly Detection, $10 absolute impact | `modules/observability` |
| `MAX_OCR_COST_PER_DOCUMENT_USD` = $2.00, checked **before** any OCR call | SSM, read by `shared/config.py` |
| Page triage — 65 pages for $0.12 instead of $0.98, measured | `config/ocr_routes.json` |

Textract's free tier covers 1,000 `AnalyzeDocument` pages per month for the first
three months of a new account. Bedrock has none.

## Tearing dev down

```bash
cd infra/envs/dev && terraform destroy
```

The source bucket has `prevent_destroy` and will refuse — intentionally, because it
holds client documents. Empty it deliberately, or remove it from state and delete
it by hand once you are certain.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `aws: command not found` | Terminal was open before the install finished. Reopen it. |
| `InvalidClientTokenId` | Key mistyped or deleted. Re-run `aws configure --profile cbc`. |
| `AccessDeniedException` from Bedrock | Step 7 not done, or done in the wrong region. Check the region selector reads N. Virginia. |
| `...not authorized to perform the required AWS Marketplace actions` | The model is not subscribed and this user cannot subscribe. Grant access in the console as root, or add the Marketplace block from step 3 to the policy. |
| Model listed by `list-inference-profiles` but `converse` denies it | Listing and invoking are separate. The profile exists in the region; the subscription has not completed. |
| `resolve_bedrock_models.py` finds nothing | Same cause. Run it with `--list` to see what the account can actually see. |
| `ExpiredToken` | You are using temporary credentials. `docker-compose.yml` does not pass `AWS_SESSION_TOKEN` — see the comment there. |
| Extraction says model IDs "are deliberately not defaulted" | Expected until `make bedrock-resolve` runs, or the two IDs are set in `.env`. |
| `InvalidBucketState` on the source bucket | Object Lock needs versioning. The storage module sets both; this only appears on a hand-made bucket. |
| Terraform hangs on `Acquiring state lock` | A previous run died. Confirm nobody else is applying, then `terraform force-unlock <id>`. |
| Instance unreachable, no SSH | By design — there is no SSH ingress rule. Use `aws ssm start-session --target <instance-id>`. |
