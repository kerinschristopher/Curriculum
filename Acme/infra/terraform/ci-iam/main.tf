# CI identity: the roles GitHub Actions assumes, for every environment.
#
# Applied by a HUMAN from WSL, never by CI. If CI applied this, the apply role would need
# permission to edit IAM roles (including its own) and could grant itself anything. The apply
# role also has an explicit Deny on CI roles (modules/iam-roles).
#
# The GitHub OIDC provider is account-wide and lives in ../account; the roles look it up.

provider "aws" {
  region = "us-east-1"
}

module "dev" {
  source = "../modules/iam-roles"

  name              = "dev"
  github_repo       = "kerinschristopher/Curriculum"
  state_bucket      = "ckerins-tfstate-12345"
  lock_table        = "terraform-locks"
  apply_environment = "dev-apply"

  # "mod*" is only safe because the trusted-branches ruleset (Acme/infra/github/) limits who can
  # create, push to or delete mod* and mod*/**/*. main is guarded by main-protect and
  # main-merge-gate: changes only through a PR with passing checks, which any collaborator can
  # merge (docs/iam.md, "GitHub-side controls"). Change this list and the rulesets together.
  github_branches = ["main", "mod*"]

  # PR plans (.github/workflows/terraform-plan.yml). Widens the read-only plan role to PRs from any
  # same-repo branch, accepted while the repo is solo (design doc Q7, risk R3).
  trust_pull_requests = true

  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

output "dev_plan_role_arn" {
  value = module.dev.plan_role_arn
}

output "dev_apply_role_arn" {
  value = module.dev.apply_role_arn
}
