# Terratest fixture: the vpc module as dev uses it (NAT off), under a unique name, with local
# state in a temporary copy of this folder. Never applied by CI. See ../../README.md.
terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "name" {
  description = "Unique name for this test run (set by the test)"
  type        = string
}

variable "region" {
  description = "Region to build the test VPC in"
  type        = string
  default     = "us-east-1"
}

provider "aws" {
  region = var.region
}

module "vpc" {
  source = "../../../modules/vpc"

  name                 = var.name
  cidr                 = "10.99.0.0/16"
  azs                  = ["${var.region}a", "${var.region}b"]
  public_subnet_cidrs  = ["10.99.101.0/24", "10.99.102.0/24"]
  private_subnet_cidrs = ["10.99.32.0/19", "10.99.64.0/19"]

  # As in dev with EKS off: nothing billed by the hour.
  enable_nat_gateway = false

  tags = {
    Environment = "terratest"
    ManagedBy   = "terratest"
    TestRun     = var.name
  }
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}
