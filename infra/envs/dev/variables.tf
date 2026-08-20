variable "aws_region" {
  description = "us-east-1: Bedrock Claude availability and Textract async are both assumed here (§14.1 Q1)."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "vpc_cidr" {
  type    = string
  default = "10.10.0.0/16"
}

variable "object_lock_retention_days" {
  description = <<-EOT
    Deliberately null. §11.3 requires the retention period to be signed off in
    writing before Object Lock retention is enabled, and the period cannot be
    shortened for objects already written under it.
  EOT
  type        = number
  default     = null
}

variable "alert_emails" {
  description = "Alarm and budget recipients. Empty means the alarms notify nobody."
  type        = list(string)
  default     = []
}

variable "monthly_budget_usd" {
  description = "Hard attention threshold for the dev account. Alerts at 50/80/100% plus a forecast alert."
  type        = number
  default     = 10
}

variable "max_ocr_cost_per_document_usd" {
  description = "Application-level guard, read from SSM. Catches a mistaken 3,000-page upload BEFORE the money is gone."
  type        = string
  default     = "2.00"
}

variable "admin_ingress_cidrs" {
  description = "CIDRs allowed to reach the API host over HTTPS. Shell access is SSM Session Manager, which needs no ingress at all."
  type        = list(string)
  default     = []
}
