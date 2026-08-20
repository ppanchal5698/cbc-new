# VPC, subnets, and the S3 gateway endpoint (§10.3 item 4).
#
# There is deliberately **no NAT gateway**. A NAT gateway is $33/month plus data
# processing, and the only thing in a private subnet is RDS, which never makes
# outbound calls. The application hosts sit in public subnets with security groups
# as the boundary; S3 traffic leaves through the gateway endpoint, which is free.
#
# That is the right trade at this size. It is also the first thing to revisit if
# the hosts ever need outbound internet for something other than S3.

variable "name_prefix" {
  description = "Resource name prefix, e.g. cbc-copilot-dev."
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "az_count" {
  description = "Availability zones to span. RDS requires a subnet group across at least two, even for a single-AZ instance."
  type        = number
  default     = 2
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = var.name_prefix }
}

resource "aws_subnet" "public" {
  count                   = var.az_count
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.name_prefix}-public-${count.index}" }
}

# RDS only. No route to the internet gateway, so the database is unreachable from
# outside the VPC regardless of what a security group later says.
resource "aws_subnet" "private" {
  count             = var.az_count
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + var.az_count)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.name_prefix}-private-${count.index}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = var.name_prefix }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.name_prefix}-public" }
}

resource "aws_route_table_association" "public" {
  count          = var.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private subnets get their own table with no default route. Without this they
# would inherit the VPC main route table, and whatever ends up on that later.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name_prefix}-private" }
}

resource "aws_route_table_association" "private" {
  count          = var.az_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"

  # Both tables: the worker in public subnets uses it to avoid egress charges, and
  # attaching it to private keeps the option open for moving hosts there later.
  route_table_ids = [aws_route_table.public.id, aws_route_table.private.id]

  tags = { Name = "${var.name_prefix}-s3" }
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "vpc_cidr" {
  value = aws_vpc.main.cidr_block
}
