output "ci_role_arn" {
  description = "Role ARN the GitHub workflow assumes"
  value       = aws_iam_role.ci.arn
}

output "oidc_provider_arn" {
  value = local.oidc_provider_arn
}