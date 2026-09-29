variable "name" {
  description = "Prefix for role names"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo as owner/name, exact case"
  type        = string
}

variable "apply_environment" {
  description = "GitHub environment whose jobs may assume the apply role (must have a required reviewer)"
  type        = string
}

variable "state_bucket" {
  description = "Name of the S3 bucket holding Terraform state"
  type        = string
}

variable "state_key" {
  description = "State object key the apply role may write, e.g. dev/terraform.tfstate"
  type        = string
}

variable "lock_table" {
  description = "Name of the DynamoDB lock table"
  type        = string
}

variable "create_oidc_provider" {
  description = "false if this account already has a GitHub OIDC provider managed elsewhere"
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
