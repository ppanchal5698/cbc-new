# ------------------------------------------------------------------------------
# API ROLE (CopilotApiRole)
# Section 11.2: S3 read/presign only. No cross-talk.
# ------------------------------------------------------------------------------

resource "aws_iam_role" "api_role" {
  name = "CopilotApiRole-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "api_s3_policy" {
  name = "CopilotApiS3Policy"
  role = aws_iam_role.api_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.source_bucket.arn,
          "${aws_s3_bucket.source_bucket.arn}/*",
          aws_s3_bucket.derived_bucket.arn,
          "${aws_s3_bucket.derived_bucket.arn}/*"
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# WORKER ROLE (CopilotWorkerRole)
# Section 11.2: S3 R/W, SQS read/delete, Bedrock invoke, Textract start/get.
# ------------------------------------------------------------------------------

resource "aws_iam_role" "worker_role" {
  name = "CopilotWorkerRole-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "worker_policy" {
  name = "CopilotWorkerPolicy"
  role = aws_iam_role.worker_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # S3 R/W
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.source_bucket.arn,
          "${aws_s3_bucket.source_bucket.arn}/*",
          aws_s3_bucket.derived_bucket.arn,
          "${aws_s3_bucket.derived_bucket.arn}/*"
        ]
      },
      {
        # SQS Read/Delete
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl"
        ]
        Resource = "*" # Restrict this to the specific queues in production
      },
      {
        # Textract
        Effect = "Allow"
        Action = [
          "textract:StartDocumentAnalysis",
          "textract:GetDocumentAnalysis"
        ]
        Resource = "*"
      },
      {
        # Bedrock Invoke (Claude 3.5 Sonnet only)
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-sonnet*"
      }
    ]
  })
}
