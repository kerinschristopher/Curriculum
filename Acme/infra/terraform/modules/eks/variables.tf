variable "name" {
  description = "EKS cluster name"
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version. Pick one still in STANDARD support (see the AWS EKS versions page)."
  type        = string
  default     = "1.36"
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the control plane ENIs and nodes"
  type        = list(string)
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.small"]
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 3
}

variable "cluster_admin_arn" {
  description = "IAM principal ARN granted EKS cluster-admin and KMS key administration (explicit, not the Terraform caller)"
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}