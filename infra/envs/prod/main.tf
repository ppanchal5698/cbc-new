terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      env        = var.environment
      service    = "cbc-copilot"
      managedBy  = "terraform"
      costCenter = "estimating"
    }
  }
}

# Production, sized to §3.1. Written and validated, NOT applied.
#
# Differences from staging, and only these: Multi-AZ RDS, a 35-day backup window,
# and a larger budget. Everything else is identical on purpose — a staging
# environment that differs from production in some other dimension is not a
# staging environment.
#
# Before this is ever applied, three things are outstanding:
#   * NFR-4 sign-off naming AWS as the approved environment.
#   * The Object Lock retention period, in writing (§11.3).
#   * A Compute Savings Plan. §10.3 puts one-year no-upfront at roughly 28% off
#     the two EC2 hosts. Buy it only once the shape has held for a month — a
#     Savings Plan on the wrong instance family is a year of paying for capacity
#     nobody uses.

locals {
  name_prefix = "cbc-copilot-${var.environment}"
}

module "network" {
  source      = "../../modules/network"
  name_prefix = local.name_prefix
  vpc_cidr    = var.vpc_cidr
}

module "storage" {
  source                     = "../../modules/storage"
  name_prefix                = local.name_prefix
  object_lock_retention_days = var.object_lock_retention_days
}

module "queue" {
  source      = "../../modules/queue"
  name_prefix = local.name_prefix
}

module "compute" {
  source      = "../../modules/compute"
  name_prefix = local.name_prefix

  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids

  # Graviton (§10.3): roughly 19% cheaper for equivalent capacity. The Dockerfiles
  # build linux/arm64 for this reason.
  api_instance_type    = "t4g.large"  # Django + web
  worker_instance_type = "t4g.medium" # separate host — the §9 B1 change
  architecture         = "arm64"

  worker_root_volume_gb = 80

  api_policy_json     = module.ai.api_policy_json
  worker_policy_json  = module.ai.worker_policy_json
  admin_ingress_cidrs = var.admin_ingress_cidrs
}

module "database" {
  source      = "../../modules/database"
  name_prefix = local.name_prefix

  vpc_id                = module.network.vpc_id
  private_subnet_ids    = module.network.private_subnet_ids
  app_security_group_id = module.compute.app_security_group_id

  instance_class        = "db.t4g.medium"
  allocated_storage_gb  = 100
  multi_az              = true
  backup_retention_days = 35
  deletion_protection   = true
}

module "ai" {
  source      = "../../modules/ai"
  name_prefix = local.name_prefix
  environment = var.environment

  source_bucket_arn         = module.storage.source_bucket_arn
  derived_bucket_arn        = module.storage.derived_bucket_arn
  queue_arns                = module.queue.queue_arns
  textract_topic_arn        = module.queue.textract_topic_arn
  textract_publish_role_arn = module.queue.textract_publish_role_arn

  database_url      = module.database.database_url
  django_secret_key = random_password.django_secret.result

  config_parameters = {
    ENVIRONMENT                   = var.environment
    S3_SOURCE_BUCKET              = module.storage.source_bucket
    S3_DERIVED_BUCKET             = module.storage.derived_bucket
    DOCUMENT_READY_QUEUE          = module.queue.document_ready_queue_name
    DOCUMENT_READY_DLQ            = module.queue.document_ready_dlq_name
    OCR_COMPLETE_QUEUE            = module.queue.ocr_complete_dlq_name
    TEXTRACT_SNS_TOPIC_ARN        = module.queue.textract_topic_arn
    TEXTRACT_SNS_ROLE_ARN         = module.queue.textract_publish_role_arn
    CLOUDFRONT_DOMAIN             = module.cdn.domain_name
    MAX_OCR_COST_PER_DOCUMENT_USD = var.max_ocr_cost_per_document_usd
    OCR_ROUTE_CONFIG              = "config/ocr_routes.json"
    LOG_FORMAT                    = "json"
    LOG_LEVEL                     = "INFO"
  }
}

module "cdn" {
  source      = "../../modules/cdn"
  name_prefix = local.name_prefix

  derived_bucket_id                   = module.storage.derived_bucket
  derived_bucket_arn                  = module.storage.derived_bucket_arn
  derived_bucket_regional_domain_name = module.storage.derived_bucket_regional_domain_name
}

module "observability" {
  source      = "../../modules/observability"
  name_prefix = local.name_prefix
  environment = var.environment

  alert_emails       = var.alert_emails
  monthly_budget_usd = var.monthly_budget_usd

  instance_ids           = module.compute.instance_ids
  db_instance_identifier = module.database.identifier

  document_ready_queue_name = module.queue.document_ready_queue_name
  document_ready_dlq_name   = module.queue.document_ready_dlq_name
  ocr_complete_dlq_name     = module.queue.ocr_complete_dlq_name
}

resource "random_password" "django_secret" {
  length  = 64
  special = false
}

output "cloudfront_domain" {
  value = module.cdn.domain_name
}

output "object_lock_retention_configured" {
  value = module.storage.object_lock_retention_configured
}

output "ssm_prefix" {
  value = module.ai.ssm_prefix
}
