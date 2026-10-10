# null while enable_eks = false
output "cluster_name" {
  value = one(module.eks[*].cluster_name)
}

output "cluster_endpoint" {
  value = one(module.eks[*].cluster_endpoint)
}
