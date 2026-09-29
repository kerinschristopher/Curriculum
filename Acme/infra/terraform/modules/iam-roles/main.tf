data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  oidc_host  = "token.actions.githubusercontent.com"
  github_url = "https://${local.oidc_host}"

  oidc_provider_arn = (
    var.create_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  )

  state_bucket_arn = "arn:aws:s3:::${var.state_bucket}"
  lock_table_arn   = "arn:aws:dynamodb:*:${local.account_id}:table/${var.lock_table}"
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

# =====================================================================
# PLAN ROLE: assumed by pull-request runs. Read-only.
# =====================================================================

# WHO: any pull_request run in this repo. GitHub sets the token's `sub`
# claim to "repo:<owner>/<repo>:pull_request" for that event. Pull requests
# from forks never receive an OIDC token, so outsiders cannot use this.
data "aws_iam_policy_document" "plan_trust" {
  statement {
    sid     = "GitHubOidcPullRequests"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repo}:pull_request"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "${var.name}-github-actions-plan"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600

  tags = var.tags
}

# WHAT: read everything, so `terraform plan` can refresh any resource type.
resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# Plan reads state and takes the lock, but never writes state.
data "aws_iam_policy_document" "state_read" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "StateRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket_arn}/*"]
  }

  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [local.lock_table_arn]
  }
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "terraform-state-access"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.state_read.json
}

# =====================================================================
# APPLY ROLE: assumed only by a job that passed the environment gate.
# =====================================================================

# WHO: only jobs that declare `environment: <apply_environment>`. GitHub sets
# `sub` to "repo:<owner>/<repo>:environment:<name>" for those jobs, and the
# job cannot start until a required reviewer approves it.
data "aws_iam_policy_document" "apply_trust" {
  statement {
    sid     = "GitHubOidcApplyEnvironment"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repo}:environment:${var.apply_environment}"]
    }
  }
}

resource "aws_iam_role" "apply" {
  name                 = "${var.name}-github-actions-apply"
  assume_role_policy   = data.aws_iam_policy_document.apply_trust.json
  max_session_duration = 3600

  tags = var.tags
}

# WHAT: read everything (for refresh) ...
resource "aws_iam_role_policy_attachment" "apply_read_only" {
  role       = aws_iam_role.apply.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# ... and create/change/delete VPC networking. Deliberately NO IAM permissions:
# this role cannot create or edit any IAM role, including itself.
# When EKS is enabled in a later module this must be widened (EKS creates IAM roles).
resource "aws_iam_role_policy_attachment" "apply_vpc" {
  role       = aws_iam_role.apply.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonVPCFullAccess"
}

# Apply reads any state but may WRITE only the dev environment's state file.
# It cannot overwrite this config's own state (ci-iam/...).
data "aws_iam_policy_document" "state_write" {
  source_policy_documents = [data.aws_iam_policy_document.state_read.json]

  statement {
    sid       = "StateWriteDevOnly"
    actions   = ["s3:PutObject"]
    resources = ["${local.state_bucket_arn}/${var.state_key}"]
  }
}

resource "aws_iam_role_policy" "apply_state" {
  name   = "terraform-state-access"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.state_write.json
}
