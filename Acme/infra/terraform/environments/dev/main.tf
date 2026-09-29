provider "aws" {
  region = "us-east-1"
}

module "vpc" {
  source = "../../modules/vpc"

  name                 = "dev"
  cidr                 = "10.0.0.0/16"
  azs                  = ["us-east-1a", "us-east-1b"]
  public_subnet_cidrs  = ["10.0.101.0/24", "10.0.102.0/24"]
  private_subnet_cidrs = ["10.0.32.0/19", "10.0.64.0/19"]

  # Only EKS nodes in the private subnets need outbound internet, so the
  # (billed-by-the-hour) NAT gateway exists only when EKS does.
  enable_nat_gateway = var.enable_eks

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

module "eks" {
  source = "../../modules/eks"
  count  = var.enable_eks ? 1 : 0

  name               = "dev"
  kubernetes_version = "1.36"
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# The CI IAM roles moved to ../../ci-iam, which a human applies locally.
# Stop tracking them here WITHOUT deleting them from AWS.
removed {
  from = module.iam_roles

  lifecycle {
    destroy = false
  }
}
