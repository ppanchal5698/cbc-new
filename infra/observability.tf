# ------------------------------------------------------------------------------
# CLOUDWATCH LOGS (Bottleneck B16 fix)
# ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "api_logs" {
  name              = "/ecs/cbc-copilot-api-${var.environment}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "pipeline_logs" {
  name              = "/ecs/cbc-copilot-pipeline-${var.environment}"
  retention_in_days = 30
  
  # Section 10.3 #6: Use Infrequent Access class for verbose pipeline logs
  log_group_class   = "INFREQUENT_ACCESS"
}

# ------------------------------------------------------------------------------
# AWS BUDGETS (Guardrails - Section 10.3 #8)
# ------------------------------------------------------------------------------

resource "aws_budgets_budget" "monthly_cost" {
  name              = "cbc-copilot-monthly-budget-${var.environment}"
  budget_type       = "COST"
  limit_amount      = "250.0"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["alerts@example.com"] # Replace with CBC IT
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["alerts@example.com"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["alerts@example.com"]
  }
}
