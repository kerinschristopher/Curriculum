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
  enable_nat_gateway   = true

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

module "eks" {
  source = "../../modules/eks"

  name               = "dev"
  kubernetes_version = "1.36"
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }

  # depends_on = [module.vpc]
}
module "iam_roles" {
  source = "../../modules/iam-roles"

  name         = "dev"
  github_repo  = "kerinschristopher/Curriculum"
  state_bucket = "ckerins-tfstate-12345"
  lock_table   = "terraform-locks"

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# The GitHub OIDC provider now belongs to the account stack (../../account).
# Forget dev's copy in state without deleting it from AWS.
removed {
  from = module.iam_roles.aws_iam_openid_connect_provider.github

  lifecycle {
    destroy = false
  }
}
