output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}
output "ci_role_arn" {
  value = module.iam_roles.ci_role_arn
}