output "plan_role_arn" {
  description = "Role ARN the plan workflows assume"
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Role ARN the approved apply job assumes (null when apply_environment is null)"
  value       = one(aws_iam_role.apply[*].arn)
}

output "oidc_provider_arn" {
  value = local.oidc_provider_arn
}
