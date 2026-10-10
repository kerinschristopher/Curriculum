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

  # Only the EKS nodes in the private subnets need outbound internet, so the NAT gateway and its
  # Elastic IP (both billed by the hour) exist only with EKS. With EKS off, dev costs nothing.
  enable_nat_gateway = var.enable_eks

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# See variables.tf for why this is off by default and what turning it on requires.
module "eks" {
  source = "../../modules/eks"
  count  = var.enable_eks ? 1 : 0

  name               = "dev"
  kubernetes_version = "1.36"
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids

  # Not committed; see variables.tf
  endpoint_public_access_cidrs = var.eks_public_access_cidrs

  # Human admin; see modules/eks/main.tf for why this isn't "whoever runs Terraform"
  cluster_admin_arn = "arn:aws:iam::401352756330:user/ckerins"

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }

  # depends_on = [module.vpc]
}

# CI roles moved to ../../ci-iam (human-applied), which imports them. Forget dev's copies without
# deleting them from AWS. (The GitHub OIDC provider already moved to ../../account in Module 5.)
removed {
  from = module.iam_roles

  lifecycle {
    destroy = false
  }
}
