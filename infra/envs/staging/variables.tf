variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "staging"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "object_lock_retention_days" {
  description = "Null until CBC signs the retention period off in writing (§11.3)."
  type        = number
  default     = null
}

variable "alert_emails" {
  type    = list(string)
  default = []
}

variable "monthly_budget_usd" {
  description = "Production-shaped sizing at staging duty cycle. See §10 for the derivation."
  type        = number
  default     = 250
}

variable "max_ocr_cost_per_document_usd" {
  type    = string
  default = "2.00"
}

variable "admin_ingress_cidrs" {
  type    = list(string)
  default = []
}
