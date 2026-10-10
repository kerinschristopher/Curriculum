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
