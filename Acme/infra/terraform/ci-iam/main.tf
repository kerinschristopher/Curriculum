# CI identity: the GitHub OIDC provider and the roles GitHub Actions assumes.
#
# Applied by a HUMAN with admin credentials, never by CI. If CI applied this,
# the apply role would need permission to edit IAM roles -- including its own --
# and could grant itself anything.

provider "aws" {
  region = "us-east-1"
}

module "iam_roles" {
  source = "../modules/iam-roles"

  name              = "dev"
  github_repo       = "kerinschristopher/Curriculum"
  apply_environment = "dev-apply"
  state_bucket      = "ckerins-tfstate-12345"
  state_key         = "dev/terraform.tfstate"
  lock_table        = "terraform-locks"

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

output "plan_role_arn" {
  value = module.iam_roles.plan_role_arn
}

output "apply_role_arn" {
  value = module.iam_roles.apply_role_arn
}
