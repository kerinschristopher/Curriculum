module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.25"

  name               = var.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids

  # Public API endpoint so your laptop and GitHub's runners can reach it.
  # Tighten with endpoint_public_access_cidrs later.
  endpoint_public_access = true

  # Cluster admin is a named principal, not "whoever runs Terraform".
  # enable_cluster_creator_admin_permissions (and the KMS default below) derive the admin from the
  # caller's identity, so the same code plans differently for each caller: a local apply as the human
  # admin and a CI plan as dev-github-actions-plan would each try to hand cluster-admin and KMS key
  # admin to themselves. CI could never show "No changes", and a future CI apply role would silently
  # take over the cluster. Naming the admin makes the result independent of who runs Terraform.
  enable_cluster_creator_admin_permissions = false

  # Key "cluster_creator" / policy key "admin" deliberately match the module's built-in entry, so
  # existing clusters keep the same resource addresses (no replace) when switching to this.
  access_entries = {
    cluster_creator = {
      principal_arn = var.cluster_admin_arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }

  # Same reason: the module defaults the KMS key administrator to the caller's identity
  kms_key_administrators = [var.cluster_admin_arn]

  addons = {
    coredns                = {}
    kube-proxy             = {}
    vpc-cni                = { before_compute = true }
    eks-pod-identity-agent = { before_compute = true }
  }

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      desired_size   = var.node_desired_size
      max_size       = var.node_max_size
    }
  }

  tags = var.tags
}