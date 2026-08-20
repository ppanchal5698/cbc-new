terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  
  # Section 10.3 #8: Enforce default tags on all resources
  default_tags {
    tags = {
      env       = var.environment
      service   = "cbc-copilot"
      component = "infrastructure"
      managedBy = "terraform"
    }
  }
}
