# Off by default on purpose (design doc Q4). The CI apply role (ci-iam, modules/iam-roles) can only
# write VPC resources: the smallest blast radius while apply-from-CI is new. Turning EKS on first
# needs the EKS-scope apply-role expansion (EKS, KMS, logs and IAM-role writes, with every created
# role bound to the dev-workload-boundary permissions boundary), a human ci-iam apply, and a
# conscious decision about cost: dev costs about $0.20/h (about $145/month) while EKS is on.
variable "enable_eks" {
  description = "Create the dev EKS cluster and the NAT gateway its nodes need. Default false: the CI apply role is VPC-only on purpose (design doc Q4), so true first needs the EKS-scope apply-role expansion with the dev-workload-boundary permissions boundary. Dev costs about $0.20/h while this is true"
  type        = bool
  default     = false
}

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
    condition     = !var.enable_eks || length(var.eks_public_access_cidrs) > 0
    error_message = "enable_eks = true needs eks_public_access_cidrs (e.g. your IP as \"x.x.x.x/32\" in dev.auto.tfvars)."
  }
}
