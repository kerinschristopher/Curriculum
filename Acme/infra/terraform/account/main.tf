provider "aws" {
  region = "us-east-1"
}

# One GitHub OIDC provider per AWS account. Environments look it up; they never create it.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  tags = {
    Scope     = "account"
    ManagedBy = "terraform"
  }
}

# Adopt the provider dev originally created (one-time; can be deleted after apply)
import {
  to = aws_iam_openid_connect_provider.github
  id = "arn:aws:iam::401352756330:oidc-provider/token.actions.githubusercontent.com"
}
