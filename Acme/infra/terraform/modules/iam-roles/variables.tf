variable "name" {
  description = "Prefix for role names"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo as owner/name, exact case"
  type        = string
}

variable "github_branches" {
  description = "Branch patterns (StringLike, e.g. \"mod*\") whose workflows may assume the role"
  type        = list(string)
  default     = ["main"]
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