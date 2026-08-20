# SQS, the dead-letter queue, and the SNS topic Textract publishes completion to.
#
# Standard queues, not FIFO (C6). FIFO buys ordering and exactly-once, and this
# system needs neither: documents are independent, and idempotency is enforced by
# `pipeline_jobs.idempotency_key` rather than by the transport. FIFO would cap
# throughput and add a partition key for no benefit.
#
# Completion arrives on SNS rather than by polling `GetDocumentAnalysis` in a loop
# (bottleneck B2). A poll loop blocks a worker for minutes per document and
# throttles under concurrency; a notification lets the worker submit and move on.

variable "name_prefix" {
  type = string
}

variable "visibility_timeout_seconds" {
  description = "Must exceed the slowest stage or a still-running job is redelivered."
  type        = number
  default     = 900
}

variable "max_receive_count" {
  description = "Deliveries before a message goes to the DLQ."
  type        = number
  default     = 3
}

variable "message_retention_seconds" {
  description = "14 days on the DLQ: a failure discovered on Monday must still be diagnosable."
  type        = number
  default     = 1209600
}

resource "aws_sqs_queue" "document_ready_dlq" {
  name                      = "${var.name_prefix}-document-ready-dlq"
  message_retention_seconds = var.message_retention_seconds
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "document_ready" {
  name                       = "${var.name_prefix}-document-ready"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.document_ready_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Lets the DLQ be redriven back to the main queue with
# `aws sqs start-message-move-task`, which is what ops/runbooks/dlq-drain.md does.
# Without it the only way back is a hand-rolled receive-and-resend script.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.document_ready_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.document_ready.arn]
  })
}

resource "aws_sqs_queue" "ocr_complete_dlq" {
  name                      = "${var.name_prefix}-ocr-complete-dlq"
  message_retention_seconds = var.message_retention_seconds
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "ocr_complete" {
  name                       = "${var.name_prefix}-ocr-complete"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ocr_complete_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "ocr_dlq" {
  queue_url = aws_sqs_queue.ocr_complete_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ocr_complete.arn]
  })
}

# ── Textract completion notifications ────────────────────────────────────────

resource "aws_sns_topic" "textract_complete" {
  name = "${var.name_prefix}-textract-complete"
}

resource "aws_sns_topic_subscription" "textract_to_sqs" {
  topic_arn = aws_sns_topic.textract_complete.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.ocr_complete.arn

  # The worker parses the Textract notification directly rather than unwrapping an
  # SNS envelope around it.
  raw_message_delivery = true
}

resource "aws_sqs_queue_policy" "allow_sns" {
  queue_url = aws_sqs_queue.ocr_complete.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ocr_complete.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_sns_topic.textract_complete.arn }
      }
    }]
  })
}

# The role Textract assumes to publish to the topic. Passed as
# `NotificationChannel.RoleArn` on every StartDocument* call.
resource "aws_iam_role" "textract_publish" {
  name = "${var.name_prefix}-textract-publish"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "textract.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "textract_publish" {
  name = "publish-completion"
  role = aws_iam_role.textract_publish.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = aws_sns_topic.textract_complete.arn
    }]
  })
}

output "document_ready_queue_url" {
  value = aws_sqs_queue.document_ready.id
}

output "document_ready_queue_arn" {
  value = aws_sqs_queue.document_ready.arn
}

output "document_ready_dlq_arn" {
  value = aws_sqs_queue.document_ready_dlq.arn
}

output "document_ready_dlq_name" {
  value = aws_sqs_queue.document_ready_dlq.name
}

output "ocr_complete_queue_arn" {
  value = aws_sqs_queue.ocr_complete.arn
}

output "ocr_complete_dlq_name" {
  value = aws_sqs_queue.ocr_complete_dlq.name
}

output "document_ready_queue_name" {
  value = aws_sqs_queue.document_ready.name
}

output "textract_topic_arn" {
  value = aws_sns_topic.textract_complete.arn
}

output "textract_publish_role_arn" {
  value = aws_iam_role.textract_publish.arn
}

output "queue_arns" {
  description = "Every queue, for scoping the worker IAM policy to exactly these."
  value = [
    aws_sqs_queue.document_ready.arn,
    aws_sqs_queue.document_ready_dlq.arn,
    aws_sqs_queue.ocr_complete.arn,
    aws_sqs_queue.ocr_complete_dlq.arn,
  ]
}
