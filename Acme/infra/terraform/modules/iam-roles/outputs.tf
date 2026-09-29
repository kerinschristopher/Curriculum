output "plan_role_arn" {
  description = "Role ARN the terraform-plan workflow assumes"
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Role ARN the terraform-apply workflow assumes"
  value       = aws_iam_role.apply.arn
}

output "oidc_provider_arn" {
  value = local.oidc_provider_arn
}
