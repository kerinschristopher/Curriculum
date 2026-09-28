variable "name" {
  description = "Prefix for role names"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo as owner/name, exact case"
  type        = string
}

variable "github_branches" {
  description = "Branches whose workflows may assume the role"
  type        = list(string)
}

variable "state_bucket" {
  description = "Name of the S3 bucket holding Terraform state"
  type        = string
}

variable "lock_table" {
  description = "Name of the DynamoDB lock table"
  type        = string
}

variable "create_oidc_provider" {
  description = "false if this account already has a GitHub OIDC provider"
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}