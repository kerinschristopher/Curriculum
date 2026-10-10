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
  # create, push to or delete main, mod* and mod*/**/*. Change this list and the ruleset together.
  github_branches = ["main", "mod*"]

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

# --- One-time handover (Module 6). Safe to delete once applied. ---
#
# This state key was first applied on 2026-09-29 from an earlier attempt (branch depmod6). That
# state still lists the plan role, the apply role, their ReadOnlyAccess/AmazonVPCFullAccess
# attachments and the OIDC provider under module.iam_roles. Forget all of it without deleting
# anything in AWS; the live roles are re-adopted below at their new addresses, and the provider
# stays with ../account.
removed {
  from = module.iam_roles

  lifecycle {
    destroy = false
  }
}

# The plan role and its two inline policies, until now owned by environments/dev (which forgets
# them with its own removed block). Imported unchanged: the plan should show no diff for them.
import {
  to = module.dev.aws_iam_role.plan
  id = "dev-github-actions-plan"
}

import {
  to = module.dev.aws_iam_role_policy.plan_read
  id = "dev-github-actions-plan:terraform-plan-read"
}

import {
  to = module.dev.aws_iam_role_policy.plan_state
  id = "dev-github-actions-plan:terraform-state-access"
}

# The apply role already exists (created 2026-09-29, never used), so creating it would fail with
# EntityAlreadyExists. Adopt it and its state policy; the apply then rewrites that policy, adds the
# scoped read and VPC policies, and detaches its managed policies (attachments_exclusive).
import {
  to = module.dev.aws_iam_role.apply[0]
  id = "dev-github-actions-apply"
}

import {
  to = module.dev.aws_iam_role_policy.apply_state[0]
  id = "dev-github-actions-apply:terraform-state-access"
}
