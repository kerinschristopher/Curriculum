"""IAM policy simulator tests for an environment's CI roles (modules/iam-roles).

These prove the policy *logic*: condition values (region, kms:ResourceAliases, s3:prefix,
dynamodb:LeadingKeys, tags) are supplied by hand, so they don't prove what AWS sends at
runtime. A real `terraform plan`/`apply` under the role is the final check.

They can't run in CI: the simulator needs iam:SimulatePrincipalPolicy (live roles) or
iam:SimulateCustomPolicy (planned policies), which the CI roles deliberately lack. Run them
from WSL as a principal that has those permissions (docs/iam.md).

    python3 -m venv --without-pip ~/.venvs/iam-tests     # python3-venv isn't installed in WSL
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | ~/.venvs/iam-tests/bin/python
    ~/.venvs/iam-tests/bin/pip install -r Acme/infra/scripts/tests/requirements.txt

    ~/.venvs/iam-tests/bin/pytest Acme/infra/scripts/tests              # the live roles
    SIM_PLAN_JSON=plan.json ~/.venvs/iam-tests/bin/pytest ...            # policies from a plan

SIM_PLAN_JSON is `terraform show -json <planfile>` of the root that owns the roles
(Acme/infra/terraform/ci-iam). It tests the planned inline policies before they're applied.
SIM_ENV picks the environment (default dev).

Harness problems (no credentials, AccessDenied on the simulator call itself, a policy missing
from the plan) raise inside the session fixture, so pytest reports them as ERROR, never as
a policy FAIL. The bash version captured stderr into the value it compared, so a broken
harness looked like a policy mismatch.
"""

import json
import os
from collections import defaultdict
from dataclasses import dataclass

import boto3
import pytest

ENV = os.environ.get("SIM_ENV", "dev")
REGION = "us-east-1"
BUCKET = "ckerins-tfstate-12345"
TABLE = "terraform-locks"
PLAN_JSON = os.environ.get("SIM_PLAN_JSON")

ALLOWED, IMPLICIT, EXPLICIT = "allowed", "implicitDeny", "explicitDeny"


@dataclass(frozen=True)
class Case:
    role: str  # "plan" or "apply": <env>-github-actions-<role>
    expected: str
    action: str
    resource: str
    context: tuple = ()  # ((key, type, (values...)), ...); hashable so cases can be grouped

    @property
    def id(self):
        return f"{self.role}:{self.expected}:{self.action}:{self.resource.rsplit(':', 1)[-1]}"


def region(r=REGION):
    return ("aws:RequestedRegion", "string", (r,))


def entry(key, value, kind="string"):
    values = tuple(value) if isinstance(value, (list, tuple)) else (value,)
    return (key, kind, values)


def cases(acct):
    E = ENV
    plan_role = f"arn:aws:iam::{acct}:role/{E}-github-actions-plan"
    key = "11111111-2222-3333-4444-555555555555"
    kms_key = f"arn:aws:kms:{REGION}:{acct}:key/{key}"
    table = f"arn:aws:dynamodb:{REGION}:{acct}:table/{TABLE}"
    R = (region(),)

    def plan(expected, action, resource, *context):
        return Case("plan", expected, action, resource, tuple(context))

    return [
        # --- plan role: allowed ---
        plan(ALLOWED, "ec2:DescribeVpcs", "*", *R),
        plan(ALLOWED, "ec2:DescribeLaunchTemplateVersions", "*", *R),
        plan(ALLOWED, "autoscaling:DescribeAutoScalingGroups", "*", *R),
        plan(ALLOWED, "eks:DescribeCluster", f"arn:aws:eks:{REGION}:{acct}:cluster/{E}", *R),
        plan(ALLOWED, "eks:ListAddons", f"arn:aws:eks:{REGION}:{acct}:cluster/{E}", *R),
        plan(ALLOWED, "eks:DescribeAddonVersions", "*", *R),
        plan(ALLOWED, "iam:GetRole", f"arn:aws:iam::{acct}:role/{E}-cluster-20260101", *R),
        plan(ALLOWED, "iam:GetRole", f"arn:aws:iam::{acct}:role/default-eks-node-group-20260101", *R),
        plan(ALLOWED, "iam:GetRole", plan_role, *R),
        plan(ALLOWED, "iam:GetPolicy", f"arn:aws:iam::{acct}:policy/{E}-cluster-ClusterEncryption2026", *R),
        plan(ALLOWED, "iam:ListOpenIDConnectProviders", "*", *R),
        plan(ALLOWED, "kms:DescribeKey", kms_key, *R, entry("kms:ResourceAliases", [f"alias/eks/{E}"], "stringList")),
        plan(ALLOWED, "logs:ListTagsForResource", f"arn:aws:logs:{REGION}:{acct}:log-group:/aws/eks/{E}/cluster", *R),
        plan(ALLOWED, "ssm:GetParameter",
             f"arn:aws:ssm:{REGION}::parameter/aws/service/eks/optimized-ami/1.36/amazon-linux-2023/x86_64/standard/recommended/release_version",
             *R),
        plan(ALLOWED, "s3:GetObject", f"arn:aws:s3:::{BUCKET}/{E}/terraform.tfstate"),
        plan(ALLOWED, "s3:ListBucket", f"arn:aws:s3:::{BUCKET}", entry("s3:prefix", f"{E}/")),
        plan(ALLOWED, "dynamodb:GetItem", table,
             entry("dynamodb:LeadingKeys", [f"{BUCKET}/{E}/terraform.tfstate-md5"], "stringList")),
        # --- plan role: implicitly denied ---
        plan(IMPLICIT, "ec2:DescribeVpcs", "*", region("us-west-2")),
        plan(IMPLICIT, "eks:DescribeCluster", f"arn:aws:eks:{REGION}:{acct}:cluster/not-{E}", *R),
        plan(IMPLICIT, "iam:GetRole", f"arn:aws:iam::{acct}:role/SomeOtherRole", *R),
        plan(IMPLICIT, "iam:GetAccountAuthorizationDetails", "*", *R),
        plan(IMPLICIT, "kms:DescribeKey", kms_key, *R, entry("kms:ResourceAliases", ["alias/other"], "stringList")),
        plan(IMPLICIT, "kms:Decrypt", kms_key, *R, entry("kms:ResourceAliases", [f"alias/eks/{E}"], "stringList")),
        plan(IMPLICIT, "ssm:GetParameter", f"arn:aws:ssm:{REGION}:{acct}:parameter/myapp/secret", *R),
        plan(IMPLICIT, "lambda:GetFunction", f"arn:aws:lambda:{REGION}:{acct}:function:anything", *R),
        plan(IMPLICIT, "s3:GetObject", f"arn:aws:s3:::{BUCKET}/account/terraform.tfstate"),
        plan(IMPLICIT, "s3:GetObject", "arn:aws:s3:::some-other-bucket/file"),
        plan(IMPLICIT, "s3:PutObject", f"arn:aws:s3:::{BUCKET}/{E}/terraform.tfstate"),
        plan(IMPLICIT, "s3:ListBucket", f"arn:aws:s3:::{BUCKET}", entry("s3:prefix", "account/")),
        plan(IMPLICIT, "dynamodb:PutItem", table,
             entry("dynamodb:LeadingKeys", [f"{BUCKET}/account/terraform.tfstate"], "stringList")),
        # CI plans run with -lock=false: the role can't take, release or read its own lock either
        plan(IMPLICIT, "dynamodb:PutItem", table,
             entry("dynamodb:LeadingKeys", [f"{BUCKET}/{E}/terraform.tfstate"], "stringList")),
        plan(IMPLICIT, "dynamodb:DeleteItem", table,
             entry("dynamodb:LeadingKeys", [f"{BUCKET}/{E}/terraform.tfstate"], "stringList")),
        plan(IMPLICIT, "dynamodb:GetItem", table,
             entry("dynamodb:LeadingKeys", [f"{BUCKET}/{E}/terraform.tfstate"], "stringList")),
        # --- plan role: explicitly denied ---
        plan(EXPLICIT, "ec2:DescribeInstanceAttribute", "*", *R),
    ]


def planned_inline_policies(plan_json_path, role_name):
    """Inline policy documents a plan gives role_name (aws_iam_role_policy resources)."""
    with open(plan_json_path) as f:
        plan = json.load(f)

    def walk(module):
        yield from module.get("resources", [])
        for child in module.get("child_modules", []):
            yield from walk(child)

    docs = [
        r["values"]["policy"]
        for r in walk(plan["planned_values"]["root_module"])
        if r["type"] == "aws_iam_role_policy" and r["values"].get("role") == role_name
    ]
    if not docs:
        raise RuntimeError(f"{plan_json_path} plans no inline policies for {role_name}")
    return docs


def to_context_entries(context):
    return [{"ContextKeyName": k, "ContextKeyType": t, "ContextKeyValues": list(v)} for k, t, v in context]


@pytest.fixture(scope="session")
def acct():
    return boto3.client("sts").get_caller_identity()["Account"]


@pytest.fixture(scope="session")
def decisions(acct):
    """Simulate every case, batching all actions that share a role, resource and context into
    one call (34 serial calls in the bash version). Returns {case: decision}."""
    iam = boto3.client("iam")
    groups = defaultdict(list)
    for c in cases(acct):
        groups[(c.role, c.resource, c.context)].append(c)

    policy_docs = {}
    out = {}
    for (role, resource, context), group in groups.items():
        role_name = f"{ENV}-github-actions-{role}"
        actions = sorted({c.action for c in group})
        kwargs = {"ActionNames": actions, "ResourceArns": [resource]}
        if context:
            kwargs["ContextEntries"] = to_context_entries(context)
        if PLAN_JSON:
            if role_name not in policy_docs:
                policy_docs[role_name] = planned_inline_policies(PLAN_JSON, role_name)
            resp = iam.simulate_custom_policy(PolicyInputList=policy_docs[role_name], **kwargs)
        else:
            resp = iam.simulate_principal_policy(
                PolicySourceArn=f"arn:aws:iam::{acct}:role/{role_name}", **kwargs
            )
        by_action = {r["EvalActionName"]: r["EvalDecision"] for r in resp["EvaluationResults"]}
        for c in group:
            out[c] = by_action[c.action]
    return out


# Account ID is only known at run time; build ids from a placeholder so collection needs no AWS call.
@pytest.mark.parametrize("case", cases("000000000000"), ids=lambda c: c.id)
def test_policy_decision(case, decisions, acct):
    real = Case(
        case.role,
        case.expected,
        case.action,
        case.resource.replace("000000000000", acct),
        tuple((k, t, tuple(v.replace("000000000000", acct) for v in vals)) for k, t, vals in case.context),
    )
    assert decisions[real] == case.expected
