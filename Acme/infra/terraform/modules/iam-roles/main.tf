# --- Future environments (known follow-ups) ---
#
# - One plan role per environment: call this module again with name = "stage" / "prod".
#   Every scoped ARN below derives from var.name, so each role scopes itself.
# - Trust tightens dev -> stage -> prod: dev ["main", "mod*"], stage ["main"], prod ["main"].
#   Plan roles never trust an environment sub. Gated applies use a separate <env>-apply role that
#   trusts only "repo:<repo>:environment:<env>-apply" (as dev-apply does; docs/iam.md).
# - pull_request-triggered plans send sub "repo:<repo>:pull_request", not a branch ref; trust_pull_requests
#   adds that value. Keep it off for stage/prod plan roles unless their PR plans are wanted too.
# - Before creating stage/prod, set the node group's iam_role_name = "${var.name}-node" in modules/eks
#   and drop the "default-eks-node-group-*" pattern below; otherwise every env's role matches it.
# - The "*" statements (ec2/elb/autoscaling Describe*, eks:DescribeAddonVersions,
#   iam:ListOpenIDConnectProviders, kms:ListAliases, logs:DescribeLogGroups) have no resource-level
#   scoping in AWS. This project lives in a single AWS account, so they cannot be made any narrower:
#   each environment's plan role can see the other environments' network/LB/ASG configuration
#   (configuration only, never data). This is an accepted residual exposure; only separate
#   per-environment accounts would remove it, and that is out of scope.
# - The apply role (below, Module 6) follows the same per-environment pattern. Cluster-admin and KMS key admin
#   are an explicit cluster_admin_arn (modules/eks), so applying as that role won't hand it the cluster.

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

  apply = var.apply_environment != null
}

# --- Identity provider: AWS trusts GitHub as a token issuer ---

# One per account, owned by the account stack (infra/terraform/account); look it up here
data "aws_iam_openid_connect_provider" "github" {
  url = local.github_url
}

# --- Plan role trust: WHO may become the plan role ---

data "aws_iam_policy_document" "plan_trust" {
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

    # Branch refs for push and workflow_dispatch runs, plus (optionally) the branchless PR value.
    # The PR value has no wildcard, so StringLike matches it exactly.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = concat(
        [for b in var.github_branches : "repo:${var.github_repo}:ref:refs/heads/${b}"],
        var.trust_pull_requests ? ["repo:${var.github_repo}:pull_request"] : [],
      )
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "${var.name}-github-actions-plan"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600

  tags = var.tags
}

# --- Plan role permissions: WHAT it can do once assumed (plan-only) ---

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
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_read.json
}

# Remote state access: plan reads this environment's state and checksum; it takes no lock and never writes state
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

  # State checksum (digest item) that plan verifies the state against.
  # CI plans run with -lock=false (see .github/workflows/terraform-plan.yml), so no PutItem/DeleteItem:
  # the role can't take, hold or release any lock.
  statement {
    sid       = "StateDigestRead"
    actions   = ["dynamodb:GetItem"]
    resources = ["arn:aws:dynamodb:${local.region}:${local.account_id}:table/${var.lock_table}"]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "dynamodb:LeadingKeys"
      values   = ["${var.state_bucket}/${local.state_key}-md5"]
    }
  }
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "terraform-state-access"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.state.json
}

# --- Managed policies: none, enforced ---

# Both roles carry only their inline policies. "Exclusive" means Terraform detaches any managed
# policy not listed, so an attached ReadOnlyAccess (as on the 2026-09-29 roles) is removed and
# can't quietly come back.
resource "aws_iam_role_policy_attachments_exclusive" "plan" {
  role_name   = aws_iam_role.plan.name
  policy_arns = []
}

# --- Apply role: only when apply_environment is set ---
#
# Assumed only by jobs running in the GitHub Environment `apply_environment`, which requires a
# reviewer, so write credentials exist only after a human approves a specific plan.
#
# Scope (Module 6): this environment's state, plus the VPC module's EC2 resources tagged
# Environment = var.name. No EKS, KMS, logs or IAM writes yet: enable_eks stays false in the
# environment until that expansion lands with a permissions boundary (design doc Q4).

data "aws_iam_policy_document" "apply_trust" {
  count = local.apply ? 1 : 0

  statement {
    sid     = "GitHubOidcApplyEnvironment"
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

    # A job that names `environment:` gets this sub instead of its branch ref, and GitHub only
    # issues it after the environment's protection rules (required reviewer) pass.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:${var.apply_environment}"]
    }
  }
}

resource "aws_iam_role" "apply" {
  count = local.apply ? 1 : 0

  name                 = "${var.name}-github-actions-apply"
  assume_role_policy   = data.aws_iam_policy_document.apply_trust[0].json
  max_session_duration = 3600

  tags = var.tags
}

# Apply refreshes before it changes anything, so it needs the same reads as plan.
resource "aws_iam_role_policy" "apply_read" {
  count = local.apply ? 1 : 0

  name   = "terraform-plan-read"
  role   = aws_iam_role.apply[0].id
  policy = data.aws_iam_policy_document.plan_read.json
}

# This environment's state: read and write the state object, take and release its lock, update its digest
data "aws_iam_policy_document" "apply_state" {
  count = local.apply ? 1 : 0

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
    sid       = "StateReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/${local.state_key}"]
  }

  statement {
    sid       = "StateLockAndDigest"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
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

  # Belt and braces for when this role gains more S3 access: no writes to any other state, ever,
  # and no deleting any state, including its own (a deleted state orphans everything in it).
  statement {
    sid           = "DenyOtherStateWrites"
    effect        = "Deny"
    actions       = ["s3:PutObject"]
    not_resources = ["arn:aws:s3:::${var.state_bucket}/${local.state_key}"]
  }

  statement {
    sid       = "DenyStateDeletes"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["arn:aws:s3:::${var.state_bucket}/*"]
  }
}

resource "aws_iam_role_policy" "apply_state" {
  count = local.apply ? 1 : 0

  name   = "terraform-state-access"
  role   = aws_iam_role.apply[0].id
  policy = data.aws_iam_policy_document.apply_state[0].json
}

# VPC module writes (modules/vpc): VPC, subnets, IGW, EIP, NAT gateway, route tables, routes.
# Every taggable resource there is tagged Environment = var.name, so creates must carry that tag
# and changes or deletes must target a resource that already has it.
data "aws_iam_policy_document" "apply_vpc" {
  count = local.apply ? 1 : 0

  statement {
    sid = "Ec2CreateTagged"
    actions = [
      "ec2:CreateVpc",
      "ec2:CreateSubnet",
      "ec2:CreateInternetGateway",
      "ec2:CreateRouteTable",
      "ec2:CreateNatGateway",
      "ec2:AllocateAddress",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Environment"
      values   = [var.name]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # Tags applied as part of those creates (TagSpecifications)
  statement {
    sid       = "Ec2TagOnCreate"
    actions   = ["ec2:CreateTags"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values = [
        "CreateVpc",
        "CreateSubnet",
        "CreateInternetGateway",
        "CreateRouteTable",
        "CreateNatGateway",
        "AllocateAddress",
      ]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  statement {
    sid = "Ec2ChangeOwned"
    actions = [
      "ec2:ModifyVpcAttribute",
      "ec2:DeleteVpc",
      "ec2:ModifySubnetAttribute",
      "ec2:DeleteSubnet",
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:DeleteInternetGateway",
      "ec2:CreateRoute",
      "ec2:ReplaceRoute",
      "ec2:DeleteRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:ReplaceRouteTableAssociation",
      "ec2:DeleteRouteTable",
      "ec2:DeleteNatGateway",
      "ec2:DisassociateAddress",
      "ec2:ReleaseAddress",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [var.name]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # Retag owned resources, but never the Environment tag itself: otherwise this role could hand
  # its VPC to another environment (Environment = prod) or drop the tag. Terraform only sends the
  # keys that change, so an unchanged Environment tag never appears here. A DeleteTags call with
  # no keys deletes every tag, and an empty key set passes ForAllValues, hence the Null check.
  statement {
    sid       = "Ec2RetagOwned"
    actions   = ["ec2:CreateTags", "ec2:DeleteTags"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [var.name]
    }

    condition {
      test     = "ForAllValues:StringNotEquals"
      variable = "aws:TagKeys"
      values   = ["Environment"]
    }

    condition {
      test     = "Null"
      variable = "aws:TagKeys"
      values   = ["false"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [local.region]
    }
  }

  # The apply role must never change CI identity: itself, the plan role, or the OIDC provider.
  # It has no IAM write today; this deny keeps that true when EKS (which creates IAM roles) is added.
  statement {
    sid     = "DenyCiIdentityChanges"
    effect  = "Deny"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/*-github-actions-*",
      "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }
}

resource "aws_iam_role_policy" "apply_vpc" {
  count = local.apply ? 1 : 0

  name   = "terraform-apply-vpc"
  role   = aws_iam_role.apply[0].id
  policy = data.aws_iam_policy_document.apply_vpc[0].json
}

resource "aws_iam_role_policy_attachments_exclusive" "apply" {
  count = local.apply ? 1 : 0

  role_name   = aws_iam_role.apply[0].name
  policy_arns = []
}
