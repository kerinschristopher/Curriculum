# Who may reach the cluster's public Kubernetes API (kubectl). Deliberately not committed: a home IP
# in a public repo says where the admin lives. Locally, set it in dev.auto.tfvars next to this file
# (gitignored by *.tfvars), e.g.  eks_public_access_cidrs = ["203.0.113.7/32"]
# If your IP changes, kubectl times out: update the file and apply. Terraform itself is unaffected,
# because it goes through the AWS EKS API, not this endpoint.
# CI needs it only once enable_eks is true: a repository variable passed as TF_VAR_eks_public_access_cidrs.
variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the dev cluster's public Kubernetes API endpoint. Required when enable_eks is true; never committed (set it in a gitignored dev.auto.tfvars)"
  type        = list(string)
  default     = []
  sensitive   = true # plan output and PR plan comments show (sensitive value)

  validation {
    condition     = !local.enable_eks || length(var.eks_public_access_cidrs) > 0
    error_message = "enable_eks = true needs eks_public_access_cidrs (e.g. your IP as \"x.x.x.x/32\" in dev.auto.tfvars)."
  }
}
