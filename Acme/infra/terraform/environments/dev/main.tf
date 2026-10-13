provider "aws" {
  region = "us-east-1"
}

# Off on purpose (design doc Q4). The CI apply role (ci-iam, modules/iam-roles) can only
# write VPC resources: the smallest blast radius while apply-from-CI is new. Turning EKS on first
# needs the EKS-scope apply-role expansion (EKS, KMS, logs and IAM-role writes, with every created
# role bound to the dev-workload-boundary permissions boundary), a human ci-iam apply, and a
# conscious decision about cost: dev costs about $0.20/h (about $145/month) while EKS is on.
#
# A local, not a variable, so nothing outside the code (tfvars, -var, TF_VAR_) can override it:
# CI and a local run always agree. To turn EKS on, change it here in a reviewed PR.
# Above is true as of 10-13-26
locals {
  enable_eks = false
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
  enable_nat_gateway = local.enable_eks

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# See the enable_eks local above for why this is off by default and what turning it on requires.
module "eks" {
  source = "../../modules/eks"
  count  = local.enable_eks ? 1 : 0

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
