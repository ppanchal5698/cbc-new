# RDS Postgres 17 (D2).
#
# Private subnets, no public access, no route to the internet gateway. The only
# thing that can reach it is the application security group.

variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "app_security_group_id" {
  description = "The only source allowed to reach 5432."
  type        = string
}

variable "instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "allocated_storage_gb" {
  type    = number
  default = 20
}

variable "max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling. doc_elements grows fast per bid set, and running out of disk takes the database read-only."
  type        = number
  default     = 100
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = true
}

resource "aws_db_subnet_group" "main" {
  name       = var.name_prefix
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "db" {
  name        = "${var.name_prefix}-db"
  description = "Postgres, reachable only from the application hosts"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from the application security group"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.app_security_group_id]
  }

  # No egress rules. RDS initiates nothing.

  tags = { Name = "${var.name_prefix}-db" }
}

resource "aws_db_parameter_group" "main" {
  name   = var.name_prefix
  family = "postgres17"

  # Log anything slower than a second. The COPY path writes tens of thousands of
  # rows per bid set and is the first thing to degrade under load; without this
  # the symptom is "the pipeline feels slow" with nothing to point at.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }
}

resource "random_password" "master" {
  length  = 32
  special = false # RDS rejects several specials, and the URL has to be encodable.
}

resource "aws_db_instance" "main" {
  identifier     = var.name_prefix
  engine         = "postgres"
  engine_version = "17"
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "cbccopilot"
  username = "cbc_app"
  password = random_password.master.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false

  multi_az                = var.multi_az
  backup_retention_period = var.backup_retention_days
  backup_window           = "07:00-08:00" # ~02:00 ET, outside estimator hours
  maintenance_window      = "sun:08:00-sun:09:00"

  auto_minor_version_upgrade = true
  deletion_protection        = var.deletion_protection
  # A final snapshot is the difference between a mistaken destroy and a lost
  # database. Named with a timestamp because the identifier must be unique.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name_prefix}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  # Free tier and enough to see a CPU-credit exhaustion coming on a burstable class.
  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  enabled_cloudwatch_logs_exports       = ["postgresql"]

  lifecycle {
    # timestamp() changes on every plan; without this the instance shows as
    # needing replacement forever.
    ignore_changes = [final_snapshot_identifier]
  }
}

output "endpoint" {
  value = aws_db_instance.main.address
}

output "port" {
  value = aws_db_instance.main.port
}

output "identifier" {
  value = aws_db_instance.main.identifier
}

output "database_url" {
  description = "Written to SSM as a SecureString. Never rendered into a task definition or an AMI."
  value       = "postgresql://cbc_app:${random_password.master.result}@${aws_db_instance.main.address}:${aws_db_instance.main.port}/cbccopilot"
  sensitive   = true
}

output "security_group_id" {
  value = aws_security_group.db.id
}
