# EC2 hosts, security groups, and instance profiles.
#
# EC2 rather than ECS or Lambda, per §3.1. Lambda is out because Textract on a
# 200-page set plus extraction exceeds the 15-minute ceiling, and because the
# worker holds a PDF in memory. ECS adds an orchestration layer that ten users do
# not need.
#
# Two hosts, so a bid set being processed cannot make the review UI unresponsive:
# the worker is CPU and memory hungry in bursts, and the API host must stay
# answering.

variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "api_instance_type" {
  type    = string
  default = "t3.micro"
}

variable "worker_instance_type" {
  type    = string
  default = "t3.micro"
}

variable "api_root_volume_gb" {
  type    = number
  default = 20
}

variable "worker_root_volume_gb" {
  description = "The worker rasterises 200-page sets to local disk before upload."
  type        = number
  default     = 40
}

variable "worker_policy_json" {
  type = string
}

variable "api_policy_json" {
  type = string
}

variable "admin_ingress_cidrs" {
  description = "CIDRs allowed to reach the API host on 443/80. Defaults to nothing — set it deliberately."
  type        = list(string)
  default     = []
}

variable "architecture" {
  description = "x86_64 for t3, arm64 for t4g. Must match the instance family or the AMI will not boot."
  type        = string
  default     = "x86_64"
}

data "aws_ssm_parameter" "al2023" {
  # Resolved at apply time so a new instance picks up a patched AMI, rather than a
  # hardcoded ID that ages into an unpatched host.
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${var.architecture}"
}

# ── security groups ──────────────────────────────────────────────────────────

resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-app"
  description = "Application hosts: API and pipeline worker"
  vpc_id      = var.vpc_id

  egress {
    description = "Outbound to AWS APIs. S3 leaves via the free gateway endpoint."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-app" }
}

# No SSH ingress rule anywhere in this module. Access is SSM Session Manager,
# which needs no open port, no key pair, and no bastion, and which logs the
# session. An SSH rule here would be the widest hole in the account.
resource "aws_vpc_security_group_ingress_rule" "api_https" {
  for_each = toset(var.admin_ingress_cidrs)

  security_group_id = aws_security_group.app.id
  description       = "HTTPS from an approved network"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# ── instance profiles ────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = var.api_policy_json
}

# Session Manager: how anyone gets a shell, in place of SSH.
resource "aws_iam_role_policy_attachment" "api_ssm" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "api" {
  name = "${var.name_prefix}-api"
  role = aws_iam_role.api.name
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "worker"
  role   = aws_iam_role.worker.id
  policy = var.worker_policy_json
}

resource "aws_iam_role_policy_attachment" "worker_ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.name_prefix}-worker"
  role = aws_iam_role.worker.name
}

# ── instances ────────────────────────────────────────────────────────────────

resource "aws_instance" "api" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.api_instance_type
  subnet_id              = var.public_subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.api.name

  root_block_device {
    volume_size = var.api_root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    # IMDSv2 required. IMDSv1 lets any SSRF in the application read the instance
    # role's credentials with a single GET.
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2 # containers need one extra hop
  }

  tags = {
    Name = "${var.name_prefix}-api"
    Role = "api"
  }
}

resource "aws_instance" "worker" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.worker_instance_type
  subnet_id              = var.public_subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.worker.name

  root_block_device {
    volume_size = var.worker_root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }

  tags = {
    Name = "${var.name_prefix}-worker"
    Role = "worker"
  }
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}

output "api_instance_id" {
  value = aws_instance.api.id
}

output "worker_instance_id" {
  value = aws_instance.worker.id
}

output "api_public_ip" {
  value = aws_instance.api.public_ip
}

output "instance_ids" {
  value = [aws_instance.api.id, aws_instance.worker.id]
}
