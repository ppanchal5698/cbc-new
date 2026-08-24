# Bedrock and Textract access, and the SSM parameters the application reads.
#
# ── The Bedrock resource ARN (fixes defect B18) ──────────────────────────────
#
# The previous policy pinned `foundation-model/anthropic.claude-3-5-sonnet*`. That
# breaks C5 twice over.
#
# First, model IDs are resolved at deploy time from ListFoundationModels /
# ListInferenceProfiles and pinned in SSM — never hardcoded, because a run that
# cannot name its exact model version cannot be audited (NFR-3). An IAM policy
# naming one model family means changing the model requires a Terraform apply,
# which is exactly the coupling C5 exists to remove.
#
# Second, and more immediately: the current Claude models are only reachable
# through **inference profiles**, whose ARNs are `inference-profile/us.anthropic.…`
# and which in turn invoke `foundation-model/…` in each backing region. A policy
# covering only the foundation-model ARN in one region cannot call them at all.
#
# So the grant is: any Anthropic foundation model, in any region (cross-region
# inference profiles route to several), plus the inference profiles themselves in
# this account. That is scoped to a vendor, not to the whole of Bedrock.

variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "source_bucket_arn" {
  type = string
}

variable "derived_bucket_arn" {
  type = string
}

variable "queue_arns" {
  type = list(string)
}

variable "textract_topic_arn" {
  type = string
}

variable "textract_publish_role_arn" {
  type = string
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "django_secret_key" {
  type      = string
  sensitive = true
}

variable "config_parameters" {
  description = "Non-secret configuration written to SSM as String parameters."
  type        = map(string)
  default     = {}
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  ssm_prefix = "/cbc-copilot/${var.environment}"
  account    = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  bedrock_resources = [
    # Any region: a cross-region inference profile fans out to several, and the
    # call fails if the backing regions are not covered.
    "arn:${local.partition}:bedrock:*::foundation-model/anthropic.*",
    "arn:${local.partition}:bedrock:*:${local.account}:inference-profile/*",
    "arn:${local.partition}:bedrock:*:${local.account}:application-inference-profile/*",
  ]
}

# ── IAM policy documents ─────────────────────────────────────────────────────

data "aws_iam_policy_document" "worker" {
  # The worker writes derived artefacts and reads source documents. It must NOT be
  # able to write to source: the source bucket is written exactly once, at intake,
  # by the API host (§3.3 step 1).
  statement {
    sid     = "ReadSource"
    actions = ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket"]
    resources = [
      var.source_bucket_arn,
      "${var.source_bucket_arn}/*",
    ]
  }

  # GetObject here is load-bearing beyond the worker's own reads: Textract fetches
  # the document under the *caller's* credentials, and what it is handed is the
  # routed-page subset PDF in derived, not the source document (§4.4, B1).
  # Narrowing this to write-only would break OCR, not just tidy a policy.
  statement {
    sid = "ReadWriteDerived"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      var.derived_bucket_arn,
      "${var.derived_bucket_arn}/*",
    ]
  }

  statement {
    sid = "ConsumeQueues"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:SendMessage",
    ]
    # Scoped to these queues. The previous policy used "*" with a comment saying
    # to restrict it in production, which is a comment, not a control.
    resources = var.queue_arns
  }

  statement {
    sid = "Textract"
    actions = [
      "textract:StartDocumentAnalysis",
      "textract:GetDocumentAnalysis",
      "textract:StartDocumentTextDetection",
      "textract:GetDocumentTextDetection",
    ]
    resources = ["*"] # Textract has no resource-level permissions.
  }

  # Textract assumes this role to publish completion. Without iam:PassRole the
  # NotificationChannel on StartDocument* is rejected.
  statement {
    sid       = "PassTextractPublishRole"
    actions   = ["iam:PassRole"]
    resources = [var.textract_publish_role_arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["textract.amazonaws.com"]
    }
  }

  statement {
    sid       = "SubscribeTextractTopic"
    actions   = ["sns:GetTopicAttributes"]
    resources = [var.textract_topic_arn]
  }

  statement {
    sid = "Bedrock"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = local.bedrock_resources
  }

  # Read-only discovery, used by ops/scripts/resolve_bedrock_models.py to resolve
  # a model ID at deploy time instead of hardcoding one (C5/D12).
  statement {
    sid = "BedrockDiscovery"
    actions = [
      "bedrock:ListFoundationModels",
      "bedrock:GetFoundationModel",
      "bedrock:ListInferenceProfiles",
      "bedrock:GetInferenceProfile",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "ReadConfig"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:${local.partition}:ssm:*:${local.account}:parameter${local.ssm_prefix}/*"]
  }

  statement {
    sid       = "WriteLogsAndMetrics"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:${local.partition}:logs:*:${local.account}:log-group:/cbc-copilot/${var.environment}/*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

data "aws_iam_policy_document" "api" {
  # ── Fixes defect B17 ──
  # The previous policy granted GetObject and ListBucket only. The API host is
  # what performs intake — it writes the uploaded document to the source bucket
  # (§3.3 step 1, §11.2). Without PutObject, upload could never have worked.
  statement {
    sid = "IntakeWriteSource"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListBucket",
    ]
    resources = [
      var.source_bucket_arn,
      "${var.source_bucket_arn}/*",
    ]
  }

  # Presigning a viewer raster needs GetObject; the API never writes derived.
  statement {
    sid       = "ReadDerived"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [var.derived_bucket_arn, "${var.derived_bucket_arn}/*"]
  }

  # Enqueue only. The API must not be able to consume — that is the worker's job,
  # and a shared consumer is how messages go missing.
  statement {
    sid       = "EnqueueDocumentReady"
    actions   = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
    resources = var.queue_arns
  }

  statement {
    sid       = "ReadConfig"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:${local.partition}:ssm:*:${local.account}:parameter${local.ssm_prefix}/*"]
  }

  statement {
    sid       = "WriteLogsAndMetrics"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:${local.partition}:logs:*:${local.account}:log-group:/cbc-copilot/${var.environment}/*"]
  }
}

# ── Break-glass (§11.3) ──────────────────────────────────────────────────────
#
# GOVERNANCE mode is only meaningfully different from COMPLIANCE if someone can
# actually override it — for a genuine mistaken upload, or a deletion request.
# That capability is a separate assumable role, not a permission on the app roles,
# so using it is a deliberate act that shows up in CloudTrail. The observability
# module alarms on it.

resource "aws_iam_role" "break_glass" {
  name        = "${var.name_prefix}-break-glass-object-lock"
  description = "Override S3 Object Lock GOVERNANCE retention. Every use is alarmed. See ops/runbooks/."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = "arn:${local.partition}:iam::${local.account}:root" }
      Condition = {
        # MFA is the point. A leaked long-lived credential must not be able to
        # delete a locked source document.
        Bool = { "aws:MultiFactorAuthPresent" = "true" }
      }
    }]
  })

  max_session_duration = 3600
}

resource "aws_iam_role_policy" "break_glass" {
  name = "bypass-governance-retention"
  role = aws_iam_role.break_glass.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:BypassGovernanceRetention",
        "s3:DeleteObjectVersion",
        "s3:PutObjectRetention",
        "s3:GetObjectRetention",
      ]
      Resource = "${var.source_bucket_arn}/*"
    }]
  })
}

# ── SSM ──────────────────────────────────────────────────────────────────────

resource "aws_ssm_parameter" "database_url" {
  name   = "${local.ssm_prefix}/DATABASE_URL"
  type   = "SecureString"
  value  = var.database_url
  tier   = "Standard"
  key_id = "alias/aws/ssm"
}

resource "aws_ssm_parameter" "secret_key" {
  name   = "${local.ssm_prefix}/SECRET_KEY"
  type   = "SecureString"
  value  = var.django_secret_key
  key_id = "alias/aws/ssm"

  lifecycle {
    # Rotated out of band (ops/runbooks/rotate-secrets.md). Terraform must not
    # revert a rotation on the next unrelated apply.
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "config" {
  for_each = var.config_parameters

  name  = "${local.ssm_prefix}/${each.key}"
  type  = "String"
  value = each.value
}

# BEDROCK_MODEL_ID and BEDROCK_MODEL_ID_CHEAP are deliberately NOT declared here.
# They are resolved at deploy time by ops/scripts/resolve_bedrock_models.py and
# written to SSM by it (C5/D12). Terraform managing them would mean a hardcoded
# model ID in version control, which is the thing C5 forbids.

output "worker_policy_json" {
  value = data.aws_iam_policy_document.worker.json
}

output "api_policy_json" {
  value = data.aws_iam_policy_document.api.json
}

output "ssm_prefix" {
  value = local.ssm_prefix
}

output "break_glass_role_arn" {
  value = aws_iam_role.break_glass.arn
}
