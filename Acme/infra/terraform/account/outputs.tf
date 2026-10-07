output "github_oidc_provider_arn" {
  description = "ARN of the account's GitHub OIDC provider"
  value       = aws_iam_openid_connect_provider.github.arn
}
