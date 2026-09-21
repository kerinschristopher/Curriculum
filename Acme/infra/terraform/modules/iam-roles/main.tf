data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  github_url = "https://token.actions.githubusercontent.com"

  oidc_provider_arn = (
    var.create_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  )
}

# --- Identity provider: AWS trusts GitHub as a token issuer ---

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = local.github_url
  client_id_list = ["sts.amazonaws.com"]

  tags = var.tags
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1

  url = local.github_url
}

# --- Trust policy: WHO may become this role ---

data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "GitHubOidc"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for b in var.github_branches : "repo:${var.github_repo}:ref:refs/heads/${b}"]
    }
  }
}

resource "aws_iam_role" "ci" {
  name                 = "${var.name}-github-actions-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600

  tags = var.tags
}

# --- Permissions: WHAT the role can do once assumed (plan-only) ---

# Read-only view of the account so `terraform plan` can refresh every resource type
resource "aws_iam_role_policy_attachment" "read_only" {
  role       = aws_iam_role.ci.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# Remote state access: plan reads state and takes a lock, but never writes state
data "aws_iam_policy_document" "state" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]
  }

  statement {
    sid       = "StateRead"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/*"]
  }

  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = ["arn:aws:dynamodb:*:${local.account_id}:table/${var.lock_table}"]
  }
}

resource "aws_iam_role_policy" "state" {
  name   = "terraform-state-access"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.state.json
}