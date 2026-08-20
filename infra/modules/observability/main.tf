# Log groups, alarms, budget, and cost anomaly detection (§11.5, §10.3).
#
# Every alarm here corresponds to a failure that is otherwise silent. That is the
# selection criterion: a CPU alarm on a host nobody is watching tells you nothing
# you would not eventually notice, whereas a message in the DLQ means a bid set
# was uploaded and will never be processed, and nobody finds out until a customer
# asks where their quote is.

variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "alert_emails" {
  description = "Who gets paged. An empty list means the alarms exist and notify nobody, which is worse than no alarms."
  type        = list(string)
  default     = []
}

variable "app_log_retention_days" {
  type    = number
  default = 30
}

variable "debug_log_retention_days" {
  type    = number
  default = 7
}

variable "audit_log_retention_days" {
  description = "Quote approvals and provenance. Long, because NFR-3 asks a quote to be explainable long after it was sent."
  type        = number
  default     = 365
}

variable "monthly_budget_usd" {
  type = number
}

variable "instance_ids" {
  type    = list(string)
  default = []
}

variable "db_instance_identifier" {
  type    = string
  default = ""
}

variable "document_ready_queue_name" {
  type = string
}

variable "document_ready_dlq_name" {
  type = string
}

variable "ocr_complete_dlq_name" {
  type = string
}

locals {
  prefix = "/cbc-copilot/${var.environment}"
}

# ── log groups ───────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "api" {
  name              = "${local.prefix}/api"
  retention_in_days = var.app_log_retention_days
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "${local.prefix}/pipeline"
  retention_in_days = var.app_log_retention_days
}

resource "aws_cloudwatch_log_group" "debug" {
  name              = "${local.prefix}/debug"
  retention_in_days = var.debug_log_retention_days
  # Verbose and rarely queried. Infrequent Access is roughly half the ingest cost;
  # the trade is no Live Tail and no metric filters, neither of which apply here.
  log_group_class = "INFREQUENT_ACCESS"
}

resource "aws_cloudwatch_log_group" "audit" {
  name              = "${local.prefix}/audit"
  retention_in_days = var.audit_log_retention_days
}

# ── notification ─────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# ── alarms ───────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = toset([var.document_ready_dlq_name, var.ocr_complete_dlq_name])

  alarm_name        = "${var.name_prefix}-dlq-depth-${each.value}"
  alarm_description = "A message in the DLQ means a bid set was uploaded and never processed. Runbook: ops/runbooks/dlq-drain.md"

  namespace   = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions  = { QueueName = each.value }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  # A DLQ that reports nothing is normal and must not read as insufficient data.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name        = "${var.name_prefix}-queue-age"
  alarm_description = "Oldest message over 15 minutes: the worker is down, wedged, or too slow. NFR-6 is a draft in minutes."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateAgeOfOldestMessage"
  dimensions  = { QueueName = var.document_ready_queue_name }

  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 900
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "cpu_credits" {
  for_each = toset(var.instance_ids)

  alarm_name = "${var.name_prefix}-cpu-credits-${each.value}"
  alarm_description = join(" ", [
    "CPUCreditBalance is nearly exhausted. A burstable instance out of credits is",
    "throttled to its baseline and looks exactly like a hung host — this alarm is",
    "the difference between diagnosing it in a minute and in an afternoon.",
  ])

  namespace   = "AWS/EC2"
  metric_name = "CPUCreditBalance"
  dimensions  = { InstanceId = each.value }

  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 20
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu_credits" {
  count = var.db_instance_identifier == "" ? 0 : 1

  alarm_name        = "${var.name_prefix}-db-cpu-credits"
  alarm_description = "RDS burstable credits nearly exhausted. Runbook: ops/runbooks/restore-from-snapshot.md rules this out before restoring."

  namespace   = "AWS/RDS"
  metric_name = "CPUCreditBalance"
  dimensions  = { DBInstanceIdentifier = var.db_instance_identifier }

  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 20
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  count = var.db_instance_identifier == "" ? 0 : 1

  alarm_name        = "${var.name_prefix}-db-free-storage"
  alarm_description = "Under 2 GiB free. doc_elements grows tens of thousands of rows per bid set; a full disk takes the database read-only."

  namespace   = "AWS/RDS"
  metric_name = "FreeStorageSpace"
  dimensions  = { DBInstanceIdentifier = var.db_instance_identifier }

  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2147483648
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# ── quality: the headline signal from §11.5 ──────────────────────────────────
#
# CitationRejectionRate is emitted by the worker as CloudWatch EMF
# (pipeline/observability/metrics.py). A rise means a prompt edit or a model
# version bump has degraded extraction — it moves before accuracy does, because
# the §5.6 gate catches ungrounded values a human reviewer might wave through.
resource "aws_cloudwatch_metric_alarm" "citation_rejection_rate" {
  alarm_name        = "${var.name_prefix}-citation-rejection-rate"
  alarm_description = "Extraction quality has drifted. Check the prompt version and model id on recent extraction_runs before processing more bid sets."

  namespace   = "CBCCopilot"
  metric_name = "CitationRejectionRate"
  dimensions  = { stage = "extract" }

  statistic           = "Average"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  # Missing data means nothing was extracted this hour, which is not a problem.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# ── break-glass usage (§11.3) ────────────────────────────────────────────────
#
# GOVERNANCE mode is only a real control if using the override is noticed. This
# turns a CloudTrail event into an alarm.

resource "aws_cloudwatch_log_metric_filter" "bypass_governance" {
  name           = "${var.name_prefix}-bypass-governance-retention"
  log_group_name = aws_cloudwatch_log_group.audit.name
  pattern        = "{ $.requestParameters.x-amz-bypass-governance-retention = \"true\" }"

  metric_transformation {
    name          = "BypassGovernanceRetention"
    namespace     = "CBCCopilot"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "bypass_governance" {
  alarm_name        = "${var.name_prefix}-bypass-governance-retention"
  alarm_description = "Someone overrode Object Lock on an immutable source document. This should be rare, deliberate, and already known to you."

  namespace   = "CBCCopilot"
  metric_name = "BypassGovernanceRetention"

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# ── cost ─────────────────────────────────────────────────────────────────────

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.alert_emails
      subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
    }
  }

  # Forecast, not just actual. A budget that only fires on actual spend fires
  # after the money is gone.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
    subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
  }
}

# Catches the shape a budget cannot: a sudden spike inside an otherwise normal
# month. A 3,000-page document submitted three times is exactly that.
resource "aws_ce_anomaly_monitor" "ai_services" {
  name              = "${var.name_prefix}-ai-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "ai_services" {
  count = length(var.alert_emails) == 0 ? 0 : 1

  name             = "${var.name_prefix}-ai-services"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.ai_services.arn]

  dynamic "subscriber" {
    for_each = toset(var.alert_emails)
    content {
      type    = "EMAIL"
      address = subscriber.value
    }
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["10"]
    }
  }
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "log_group_names" {
  value = {
    api      = aws_cloudwatch_log_group.api.name
    pipeline = aws_cloudwatch_log_group.pipeline.name
    debug    = aws_cloudwatch_log_group.debug.name
    audit    = aws_cloudwatch_log_group.audit.name
  }
}
