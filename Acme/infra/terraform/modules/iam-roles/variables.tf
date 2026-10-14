variable "name" {
  description = "Prefix for role names"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo as owner/name, exact case"
  type        = string
}

# Required on purpose: a caller that forgets it should fail, not inherit a value.
variable "github_branches" {
  description = "Branch patterns (StringLike, e.g. \"mod*\") whose workflows may assume the role. StringLike's * also matches /, so every pattern needs a matching GitHub ruleset target (mod* -> mod* and mod*/**/*)"
  type        = list(string)

  validation {
    condition     = length(var.github_branches) > 0
    error_message = "github_branches must name at least one branch."
  }

  validation {
    condition     = alltrue([for b in var.github_branches : !startswith(b, "*")])
    error_message = "github_branches entries can't start with *. Name branches explicitly, or use a prefix pattern (e.g. \"mod*\") backed by a GitHub ruleset."
  }
}

variable "trust_pull_requests" {
  description = "Let pull_request-triggered workflows assume the plan role. Their OIDC sub is \"repo:<repo>:pull_request\" with no branch in it, so this admits a PR from any same-repo branch (fork PRs get no OIDC token). The plan role is read-only and lockless; never set this on a role that can write"
  type        = bool
  default     = false
}

variable "state_bucket" {
  description = "Name of the S3 bucket holding Terraform state"
  type        = string
}

variable "lock_table" {
  description = "Name of the DynamoDB lock table"
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "apply_environment" {
  description = "GitHub Environment whose jobs may assume the apply role; it must have a required reviewer. null creates no apply role (plan only). The apply role's EC2 writes require tags Environment = name, so tags must include that"
  type        = string
  default     = null
}