variable "enable_eks" {
  description = <<-EOT
    Create the EKS cluster, its node group, and the NAT gateway its nodes need.
    COSTS MONEY while it exists: roughly $0.10/hr control plane + $0.045/hr NAT
    + node EC2 instances. Leave false unless a module needs the cluster, and
    destroy the same day.
  EOT
  type        = bool
  default     = false
}
