#!/usr/bin/env bash
# Simulates an environment's CI plan role (modules/iam-roles) against expected allow/deny cases
# using the IAM policy simulator. Read-only; needs iam:SimulatePrincipalPolicy.
#
# Usage: infra/scripts/tests/simulate-plan-role.sh [env]   (default: dev)
#
# Condition values (region, kms:ResourceAliases, s3:prefix, dynamodb:LeadingKeys) are supplied by
# hand, so this proves the policy logic, not what AWS actually sends at runtime. A real
# `terraform plan` under the role is the final check. Exits non-zero on any failed case.
set -uo pipefail

ACCT=$(aws sts get-caller-identity --query Account --output text)
ENV=${1:-dev}
R=us-east-1
BUCKET=ckerins-tfstate-12345
TABLE=terraform-locks
ROLE="arn:aws:iam::${ACCT}:role/${ENV}-github-actions-plan"

pass=0; fail=0

# sim <expected> <action> <resource> [context-entry ...]
sim() {
  local expected=$1 action=$2 resource=$3; shift 3
  local args=(--policy-source-arn "$ROLE" --action-names "$action" --resource-arns "$resource")
  [ $# -gt 0 ] && args+=(--context-entries "$@")
  local got
  got=$(aws iam simulate-principal-policy "${args[@]}" \
        --query 'EvaluationResults[0].EvalDecision' --output text 2>&1)
  if [ "$got" = "$expected" ]; then
    pass=$((pass+1)); printf 'PASS  %-13s %-32s %s\n' "$got" "$action" "$resource"
  else
    fail=$((fail+1)); printf 'FAIL  expected %s, got %s  %s  %s\n' "$expected" "$got" "$action" "$resource"
  fi
}

REGION="ContextKeyName=aws:RequestedRegion,ContextKeyValues=$R,ContextKeyType=string"
WRONG_REGION="ContextKeyName=aws:RequestedRegion,ContextKeyValues=us-west-2,ContextKeyType=string"

echo "== Should be allowed =="
sim allowed ec2:DescribeVpcs "*" "$REGION"
sim allowed ec2:DescribeLaunchTemplateVersions "*" "$REGION"
sim allowed autoscaling:DescribeAutoScalingGroups "*" "$REGION"
sim allowed eks:DescribeCluster "arn:aws:eks:$R:$ACCT:cluster/$ENV" "$REGION"
sim allowed eks:ListAddons "arn:aws:eks:$R:$ACCT:cluster/$ENV" "$REGION"
sim allowed eks:DescribeAddonVersions "*" "$REGION"
sim allowed iam:GetRole "arn:aws:iam::$ACCT:role/$ENV-cluster-20260101" "$REGION"
sim allowed iam:GetRole "arn:aws:iam::$ACCT:role/default-eks-node-group-20260101" "$REGION"
sim allowed iam:GetRole "$ROLE" "$REGION"
sim allowed iam:GetPolicy "arn:aws:iam::$ACCT:policy/$ENV-cluster-ClusterEncryption2026" "$REGION"
sim allowed iam:ListOpenIDConnectProviders "*" "$REGION"
sim allowed kms:DescribeKey "arn:aws:kms:$R:$ACCT:key/11111111-2222-3333-4444-555555555555" "$REGION" \
  "ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/eks/$ENV,ContextKeyType=stringList"
sim allowed logs:ListTagsForResource "arn:aws:logs:$R:$ACCT:log-group:/aws/eks/$ENV/cluster" "$REGION"
sim allowed ssm:GetParameter "arn:aws:ssm:$R::parameter/aws/service/eks/optimized-ami/1.36/amazon-linux-2023/x86_64/standard/recommended/release_version" "$REGION"
sim allowed s3:GetObject "arn:aws:s3:::$BUCKET/$ENV/terraform.tfstate"
sim allowed s3:ListBucket "arn:aws:s3:::$BUCKET" "ContextKeyName=s3:prefix,ContextKeyValues=$ENV/,ContextKeyType=string"
sim allowed dynamodb:PutItem "arn:aws:dynamodb:$R:$ACCT:table/$TABLE" \
  "ContextKeyName=dynamodb:LeadingKeys,ContextKeyValues=$BUCKET/$ENV/terraform.tfstate,ContextKeyType=stringList"
sim allowed dynamodb:GetItem "arn:aws:dynamodb:$R:$ACCT:table/$TABLE" \
  "ContextKeyName=dynamodb:LeadingKeys,ContextKeyValues=$BUCKET/$ENV/terraform.tfstate-md5,ContextKeyType=stringList"

echo
echo "== Should be denied (implicit) =="
sim implicitDeny ec2:DescribeVpcs "*" "$WRONG_REGION"
sim implicitDeny eks:DescribeCluster "arn:aws:eks:$R:$ACCT:cluster/not-$ENV" "$REGION"
sim implicitDeny iam:GetRole "arn:aws:iam::$ACCT:role/SomeOtherRole" "$REGION"
sim implicitDeny iam:GetAccountAuthorizationDetails "*" "$REGION"
sim implicitDeny kms:DescribeKey "arn:aws:kms:$R:$ACCT:key/11111111-2222-3333-4444-555555555555" "$REGION" \
  "ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/other,ContextKeyType=stringList"
sim implicitDeny kms:Decrypt "arn:aws:kms:$R:$ACCT:key/11111111-2222-3333-4444-555555555555" "$REGION" \
  "ContextKeyName=kms:ResourceAliases,ContextKeyValues=alias/eks/$ENV,ContextKeyType=stringList"
sim implicitDeny ssm:GetParameter "arn:aws:ssm:$R:$ACCT:parameter/myapp/secret" "$REGION"
sim implicitDeny lambda:GetFunction "arn:aws:lambda:$R:$ACCT:function:anything" "$REGION"
sim implicitDeny s3:GetObject "arn:aws:s3:::$BUCKET/account/terraform.tfstate"
sim implicitDeny s3:GetObject "arn:aws:s3:::some-other-bucket/file"
sim implicitDeny s3:PutObject "arn:aws:s3:::$BUCKET/$ENV/terraform.tfstate"
sim implicitDeny s3:ListBucket "arn:aws:s3:::$BUCKET" "ContextKeyName=s3:prefix,ContextKeyValues=account/,ContextKeyType=string"
sim implicitDeny dynamodb:PutItem "arn:aws:dynamodb:$R:$ACCT:table/$TABLE" \
  "ContextKeyName=dynamodb:LeadingKeys,ContextKeyValues=$BUCKET/account/terraform.tfstate,ContextKeyType=stringList"

echo
echo "== Should be denied (explicit) =="
sim explicitDeny ec2:DescribeInstanceAttribute "*" "$REGION"

echo
echo "Passed: $pass   Failed: $fail"
[ "$fail" -eq 0 ]
