output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "oidc_provider_arn" {
  description = "The cluster's OIDC provider, used for IRSA trust policies"
  value       = module.eks.oidc_provider_arn
}