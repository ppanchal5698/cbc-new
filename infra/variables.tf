variable "aws_region" {
  description = "The AWS region to deploy to (Explicitly chosen as us-east-1 per Q1 to ensure Bedrock Claude 3.5 Sonnet and Textract Async availability)"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "The deployment environment (e.g., dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}
