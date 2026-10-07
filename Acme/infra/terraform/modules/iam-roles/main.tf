# --- Future environments (known follow-ups) ---
#
# - One plan role per environment: call this module again with name = "stage" / "prod".
#   Every scoped ARN below derives from var.name, so each role scopes itself.
# - Trust tightens dev -> stage -> prod: dev ["main", "mod*"], stage ["main"], prod ["main"],
#   then prod moves to a GitHub Environment with required reviewers in Module 6
#   (needs a "repo:<repo>:environment:prod" sub value in the trust policy).
# - pull_request-triggered plans (Module 6) send sub "repo:<repo>:pull_request", not a branch ref;
#   add that value to the trust policy when those workflows exist.
# - Before creating stage/prod, set the node group's iam_role_name = "${var.name}-node" in modules/eks
#   and drop the "default-eks-node-group-*" pattern below; otherwise every env's role matches it.
# - The "*" statements (ec2/elb/autoscaling Describe*, eks:DescribeAddonVersions,
#   iam:ListOpenIDConnectProviders, kms:ListAliases, logs:DescribeLogGroups) have no resource-level
#   scoping in AWS. This project lives in a single AWS account, so they cannot be made any narrower:
#   each environment's plan role can see the other environments' network/LB/ASG configuration
#   (configuration only, never data). This is an accepted residual exposure; only separate
#   per-environment accounts would remove it, and that is out of scope.
# - The apply role (Module 6) follows the same per-environment pattern. Note that the EKS module's
#   enable_cluster_creator_admin_permissions grants whoever applies cluster-admin.

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  github_url = "https://token.actions.githubusercontent.com"

  oidc_provider_arn = data.aws_iam_openid_connect_provider.github.arn

  # Backend key convention: environments/<name>/terraform.tf uses key "<name>/terraform.tfstate".
  # If an environment's backend key changes, the state policy below must change with it.
  state_key = "${var.name}/terraform.tfstate"
}

# --- Identity provider: AWS trusts GitHub as a token issuer ---

# One per account, owned by the account stack (infra/terraform/account); look it up here
data "aws_iam_openid_connect_provider" "github" {
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
      test     = "StringLike"
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

# Plan-only: Describe/Get/List on exactly the resources this environment manages;
# "*" only where AWS offers no resource-level scoping. Nothing here returns stored data or decrypts.
data "aws_iam_policy_document" "plan_read" {
  # VPC module + EKS module: VPC, subnets, IGW, NAT, EIP, route tables, security groups, launch templates
  statement {
    sid       = "Ec2Describe"
    actions   = ["ec2:Describe*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # Can return instance user data (often holds secrets); nothing in this stack manages aws_instance
  statement {
    sid       = "DenyInstanceUserData"
    effect    = "Deny"
    actions   = ["ec2:DescribeInstanceAttribute"]
    resources = ["*"]
  }

  # Node group autoscaling group and future load balancers
  statement {
    sid       = "ElbAsgDescribe"
    actions   = ["elasticloadbalancing:Describe*", "autoscaling:Describe*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # This environment's cluster, node groups, addons and access entries
  statement {
    sid     = "EksClusterRead"
    actions = ["eks:Describe*", "eks:List*"]
    resources = [
      "arn:aws:eks:${local.region}:${local.account_id}:cluster/${var.name}",
      "arn:aws:eks:${local.region}:${local.account_id}:nodegroup/${var.name}/*/*",
      "arn:aws:eks:${local.region}:${local.account_id}:addon/${var.name}/*/*",
      "arn:aws:eks:${local.region}:${local.account_id}:access-entry/${var.name}/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # aws_eks_addon_version data source in the EKS module
  statement {
    sid       = "EksAddonVersions"
    actions   = ["eks:DescribeAddonVersions"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # Cluster role ("<name>-cluster-*"), this CI role ("<name>-github-actions-plan"),
  # and the managed node group role, which the EKS module names after the node group key
  # ("default-eks-node-group-*"), not the environment. See the future-environments note above.
  statement {
    sid = "IamRoleRead"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRoleTags",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${var.name}-*",
      "arn:aws:iam::${local.account_id}:role/default-eks-node-group-*",
    ]
  }

  # Cluster encryption policy ("<name>-cluster-ClusterEncryption*")
  statement {
    sid = "IamPolicyRead"
    actions = [
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:ListPolicyTags",
    ]
    resources = ["arn:aws:iam::${local.account_id}:policy/${var.name}-*"]
  }

  # GitHub OIDC provider (data source above) and the cluster's IRSA provider (random ID in its ARN)
  statement {
    sid       = "IamOidcRead"
    actions   = ["iam:GetOpenIDConnectProvider", "iam:ListOpenIDConnectProviderTags"]
    resources = ["arn:aws:iam::${local.account_id}:oidc-provider/*"]
  }

  statement {
    sid       = "IamOidcList"
    actions   = ["iam:ListOpenIDConnectProviders"]
    resources = ["*"]
  }

  # Cluster secrets-encryption key; key IDs are random, so scope by the module's "eks/<name>" alias.
  # No Decrypt/Encrypt.
  statement {
    sid = "KmsKeyRead"
    actions = [
      "kms:DescribeKey",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:ListResourceTags",
    ]
    resources = ["arn:aws:kms:${local.region}:${local.account_id}:key/*"]

    condition {
      test     = "ForAnyValue:StringEquals"
      variable = "kms:ResourceAliases"
      values   = ["alias/eks/${var.name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  statement {
    sid       = "KmsAliasList"
    actions   = ["kms:ListAliases"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # EKS control-plane log group
  statement {
    sid     = "LogsGroupRead"
    actions = ["logs:ListTagsForResource", "logs:ListTagsLogGroup"]
    resources = [
      "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/eks/${var.name}/cluster",
      "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/eks/${var.name}/cluster:*",
    ]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # Node group looks up the latest EKS-optimized AMI release from AWS public parameters (no account ID in ARN)
  statement {
    sid       = "EksAmiParam"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${local.region}::parameter/aws/service/eks/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }
}

resource "aws_iam_role_policy" "plan_read" {
  name   = "terraform-plan-read"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.plan_read.json
}

# Remote state access: plan reads this environment's state and takes its lock, but never writes state
data "aws_iam_policy_document" "state" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.name}/", "${var.name}/*"]
    }
  }

  statement {
    sid       = "StateRead"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/${var.name}/*"]
  }

  # Lock item and the digest item plan reads; nothing for other environments' state
  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = ["arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.lock_table}"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values = [
        "${var.state_bucket}/${local.state_key}",
        "${var.state_bucket}/${local.state_key}-md5",
      ]
    }
  }
}

resource "aws_iam_role_policy" "state" {
  name   = "terraform-state-access"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.state.json
}
