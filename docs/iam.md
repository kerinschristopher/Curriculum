# IAM: the CI roles

GitHub Actions reaches AWS by assuming short-lived IAM roles through OpenID Connect (OIDC). No AWS access keys are stored in
GitHub. This page explains who can assume each role, what it can do once assumed, and why each statement exists, so you
can change them without guessing. Which workflow uses which role is in [`docs/ci.md`](ci.md).

| | Plan role | Apply role |
|---|---|---|
| Name | `dev-github-actions-plan` (pattern `<env>-github-actions-plan`) | `dev-github-actions-apply` (pattern `<env>-github-actions-apply`) |
| Capability | Plan only: reads configuration and state, takes no state lock, writes nothing | Applies an approved plan: writes dev's state and the VPC module's resources, nothing else |
| Who can assume it | PR runs, and runs on `main` or `mod*` | Only jobs in the `dev-apply` GitHub Environment, after a required reviewer approves |
| Used by | `terraform-plan.yml` (PR and manual plans) and the `plan` job of `terraform-apply.yml`, both through `terraform-plan-reusable.yml` | The `apply` job of `terraform-apply.yml` |
| Session length | 1 hour (`max_session_duration = 3600`) | 1 hour |

| | |
|---|---|
| Defined in | [`Acme/infra/terraform/modules/iam-roles/main.tf`](../Acme/infra/terraform/modules/iam-roles/main.tf), called from [`Acme/infra/terraform/ci-iam/main.tf`](../Acme/infra/terraform/ci-iam/main.tf) |
| Applied by | A human, from WSL. Never by CI (see [Why a separate `ci-iam` root](#why-a-separate-ci-iam-root)) |
| OIDC provider | [`Acme/infra/terraform/account/main.tf`](../Acme/infra/terraform/account/main.tf) (one per account) |
| Account / region | `401352756330` / `us-east-1` |

## How a role gets assumed

1. The job declares `permissions: id-token: write`. Without it, GitHub won't issue the job an OIDC token.
2. `aws-actions/configure-aws-credentials` asks GitHub for a signed token. GitHub sets its claims, including
   `aud` (who the token is for) and `sub` (which repo, and which trigger or environment, the job runs for).
3. The action calls STS `AssumeRoleWithWebIdentity` with that token. AWS checks the signature against the account's GitHub
   OIDC provider, then evaluates the role's **trust policy** against the claims.
4. If the trust policy matches, STS returns temporary credentials (valid up to 1 hour), and the job runs with the role's
   **permission policies**.

Two separate questions decide what a workflow can do. The trust policy decides **who** can become the role. The permission
policies decide **what** that identity can then do. Each one alone isn't enough: tight trust over broad permissions is
still one compromised branch away from reading the whole account, and tight permissions under open trust can be assumed by anyone who can push.

The `sub` claim GitHub sends depends on how the job was started:

| Job | `sub` |
|---|---|
| `pull_request` run | `repo:kerinschristopher/Curriculum:pull_request` (no branch in it) |
| `push` or `workflow_dispatch` run on branch `B` | `repo:kerinschristopher/Curriculum:ref:refs/heads/B` |
| Any job with `environment: dev-apply` | `repo:kerinschristopher/Curriculum:environment:dev-apply` (replaces the two above) |
| A job in a reusable workflow | The **caller's** value: `terraform-plan-reusable.yml` gets `pull_request` or `ref:refs/heads/main` |

## The plan role

### Trust policy: who can become the plan role

The live policy, as AWS stores it for dev:

```json
{
  "Sid": "GitHubOidc",
  "Effect": "Allow",
  "Principal": {
    "Federated": "arn:aws:iam::401352756330:oidc-provider/token.actions.githubusercontent.com"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
    },
    "StringLike": {
      "token.actions.githubusercontent.com:sub": [
        "repo:kerinschristopher/Curriculum:ref:refs/heads/main",
        "repo:kerinschristopher/Curriculum:ref:refs/heads/mod*",
        "repo:kerinschristopher/Curriculum:pull_request"
      ]
    }
  }
}
```

There is one statement. Each part of it narrows who can get in.

#### `Action: sts:AssumeRoleWithWebIdentity`
The only way in is a federated web-identity token. The policy has no `sts:AssumeRole` statement, so no IAM user, no other role and no
other AWS account can assume this role, even with admin rights elsewhere.

#### `Principal.Federated`: the GitHub OIDC provider
Only tokens signed by GitHub's issuer (`token.actions.githubusercontent.com`) and verified through this account's OIDC provider are
considered. AWS allows one provider per issuer URL per account, so the provider belongs to the account-level stack. Each environment
looks it up with a data source instead of creating it, which keeps environments from fighting over a shared resource.

#### Condition: `aud` must equal `sts.amazonaws.com`
GitHub can mint tokens for any audience a workflow asks for. This condition only accepts tokens minted for AWS STS. Without it, a token
the same repo obtained for a different service could be replayed here.

#### Condition: `sub` must match this repo, and an allowed branch or a pull request
This condition does three jobs:

- **Pins the repository.** Every GitHub repository's workflows can get tokens from the same issuer. Without the `repo:` prefix,
  any repo on GitHub could assume this role. It is the most important line in the policy.
- **Pins the branches** for push and manual runs. Dev allows `main` and `mod*` (`github_branches` in `ci-iam/main.tf`). The operator is
  `StringLike`, so `mod*` matches `mod5`, `mod6` … `mod12` without editing Terraform for each new module branch.
  The module has no default for `github_branches`, and validation rejects any entry starting with `*`. A new environment has to
  name its branches; forgetting the argument fails instead of inheriting something.
- **Admits pull requests** (`trust_pull_requests = true` in `ci-iam/main.tf`, added in Module 6 for PR plans). The value has no wildcard,
  so `StringLike` matches it exactly. It is a deliberate widening: a PR from **any** same-repo branch, protected or not, can assume
  this role. That is accepted because the role is read-only and lockless and the repo has one maintainer (design doc Q7, risk R3).
  PRs from forks never get an OIDC token, so outside contributors can't use it. Revisit this when collaborators are added.

A branch pattern in the trust list is only as narrow as the set of people who can create a branch matching it. That half lives in GitHub,
in the `trusted-branches` ruleset ([`Acme/infra/github/trusted-branches-ruleset.json`](../Acme/infra/github/trusted-branches-ruleset.json)).
It covers `main`, `mod*` and `mod*/**/*`, and only repository admins can create, push to, delete or force-push those branches.
The two systems read `*` differently. IAM's `*` matches `/`, so trust `mod*` accepts `mod/anything`. GitHub matches ruleset patterns
with `fnmatch` in pathname mode, where `*` stops at `/`, and so does `**` unless it's written as `**/`. A ruleset on `mod*` alone, or on `mod**`
(tried, and confirmed with the `rules/branches` API), leaves `mod/anything` open to collaborators while AWS still trusts it.
`mod*/**/*` closes that gap.

Why not allow every branch (`*`)? The workflow file lives in the same repo, so whoever can push a trusted branch can edit the
workflow and run anything with the role. Trusting `*` hands the role to anyone who can push any branch. Narrowing to ruleset-protected
patterns means a manual run on `feature/x` gets a valid GitHub token that AWS rejects. (The `pull_request` value is the one exception,
taken on purpose for a read-only role.)

#### Which runs use the plan role
- **Pull requests:** `terraform-plan.yml` plans dev on every same-repo PR and posts the plan as a PR comment.
- **Manual plans:** `gh workflow run terraform-plan.yml --ref <branch>`, from `main` or `mod*` only. GitHub only offers dispatch for
  workflows whose file exists on the default branch.
- **After a merge:** the `plan` job of `terraform-apply.yml`, on `main`.
- **Pushes to `main`/`mod*`** run `terraform-plan.yml`'s fmt and validate jobs only. They have no `id-token` permission and don't plan.

Don't add an input that checks out a different branch while a run sits on a trusted ref. That would run untrusted code under that ref's trust.

The triggers, the trust list and the ruleset move together. Changing one means checking the other two.

#### What the trust policy deliberately doesn't accept
- **GitHub Environments.** A job bound to an environment has `sub = repo:<repo>:environment:<name>`, which matches nothing here.
  The plan role never needs an environment. Environment-bound jobs use the apply role.
- **Workflow pinning.** The trust policy doesn't check *which workflow file* requested the token (the `job_workflow_ref` claim). Any workflow
  on an allowed branch, or in a PR, can assume the role. That's acceptable for a read-only role.

### Permissions: what the plan role can do

The role has two inline policies and no AWS-managed policies. It used to have `ReadOnlyAccess` attached. That was removed because it grants read
on almost every service, including the *contents* of S3 objects, SSM parameters and Lambda source, across the whole account.
`aws_iam_role_policy_attachments_exclusive` with an empty list now detaches any managed policy that gets attached, on the next `ci-iam` apply.

The current policies follow these rules:
- **Describe/Get/List only.** Nothing here returns stored data, decrypts or writes.
- **Scope by ARN wherever AWS allows it.** ARNs are built from `var.name` and the current account and region, so a stage or prod role
  scopes itself to its own environment automatically.
- **Region-locked.** Regional statements require `aws:RequestedRegion = us-east-1`.
- **Use `*` only where AWS offers no resource-level scoping** for that action. Putting an ARN there would make the statement never match.

#### `terraform-plan-read`
`N` is the environment name (`dev`).

| Sid | Actions | Scope | Why it's there |
|---|---|---|---|
| `Ec2Describe` | `ec2:Describe*` | `*` (no resource-level support), region-locked | Refresh the VPC, subnets, internet/NAT gateways, EIP, route tables, security groups and node launch templates |
| `DenyInstanceUserData` | **Deny** `ec2:DescribeInstanceAttribute` | `*` | This action is inside `ec2:Describe*` and can return instance user data, which often contains secrets. Nothing in this stack manages `aws_instance`, so denying it costs nothing |
| `ElbAsgDescribe` | `elasticloadbalancing:Describe*`, `autoscaling:Describe*` | `*` (no resource-level support), region-locked | The managed node group's Auto Scaling group and any future load balancers |
| `EksClusterRead` | `eks:Describe*`, `eks:List*` | `cluster/N`, `nodegroup/N/*/*`, `addon/N/*/*`, `access-entry/N/*`, region-locked | Refresh this environment's cluster, node group, addons and access entries, and nothing for any other cluster |
| `EksAddonVersions` | `eks:DescribeAddonVersions` | `*` (no resource-level support), region-locked | The EKS module looks up the latest version of each addon |
| `IamRoleRead` | `iam:GetRole`, `GetRolePolicy`, `ListRolePolicies`, `ListAttachedRolePolicies`, `ListInstanceProfilesForRole`, `ListRoleTags` | `role/N-*`, `role/default-eks-node-group-*` | Refresh the cluster role (`N-cluster-*`), this CI role itself (`N-github-actions-plan`, which the EKS module also reads to identify the caller) and the node group role. The EKS module names the node role after the node group key, not the environment, so it needs its own pattern |
| `IamPolicyRead` | `iam:GetPolicy`, `GetPolicyVersion`, `ListPolicyVersions`, `ListPolicyTags` | `policy/N-*` | The cluster's secrets-encryption policy (`N-cluster-ClusterEncryption*`). Attached AWS-managed policies are covered by `ListAttachedRolePolicies` and need no grant |
| `IamOidcRead` | `iam:GetOpenIDConnectProvider`, `ListOpenIDConnectProviderTags` | `oidc-provider/*` in this account | The GitHub provider (looked up by the trust policy's data source) and the cluster's IRSA provider, whose ARN contains a random ID |
| `IamOidcList` | `iam:ListOpenIDConnectProviders` | `*` | Finding the GitHub provider by URL requires listing the providers |
| `KmsKeyRead` | `kms:DescribeKey`, `GetKeyPolicy`, `GetKeyRotationStatus`, `ListResourceTags` | `key/*` **with** `kms:ResourceAliases = alias/eks/N`, region-locked | Refresh the cluster's secrets-encryption key. Key IDs are random, so the alias the module creates is what scopes it. No `Decrypt` or `Encrypt` |
| `KmsAliasList` | `kms:ListAliases` | `*` (no resource-level support), region-locked | Refresh the key's alias |
| `LogsGroupRead` | `logs:ListTagsForResource`, `ListTagsLogGroup` | `log-group:/aws/eks/N/cluster` (and `:*`), region-locked | Refresh the EKS control-plane log group |
| `LogsDescribe` | `logs:DescribeLogGroups` | `*` (no resource-level support), region-locked | Find the log group |
| `EksAmiParam` | `ssm:GetParameter` | `parameter/aws/service/eks/*` (AWS public parameters), region-locked | The node group reads the latest EKS-optimized AMI release from an AWS-published parameter. Your own parameters (`/myapp/...`) aren't covered |

KMS, CloudWatch Logs and SSM were added because the EKS module needs them to refresh. Without them, plan fails with AccessDenied once
the cluster exists.

#### `terraform-state-access`

| Sid | Actions | Scope | Why it's there |
|---|---|---|---|
| `StateBucketList` | `s3:ListBucket` | the state bucket, **with** `s3:prefix` = `N/` or `N/*` | Lets the backend check for this environment's state objects. Without `ListBucket`, S3 reports a missing object as AccessDenied instead of NotFound. The prefix condition hides other environments' keys |
| `StateRead` | `s3:GetObject` | `ckerins-tfstate-12345/N/*` | Read this environment's state. State stores values in plaintext, so `account/`, `ci-iam/` and other environments' state stay unreadable |
| `StateDigestRead` | `dynamodb:GetItem` | the lock table, **with** `dynamodb:LeadingKeys` = `<bucket>/N/terraform.tfstate-md5` | Read this environment's state checksum, which plan verifies the downloaded state against. This is the digest item only, not the lock item |

There is no `s3:PutObject`, so plan never writes state. There is also no `dynamodb:PutItem` or `DeleteItem`, so the role can't take, hold or release any lock.

##### Why CI plans don't lock
Every CI plan runs `terraform plan -lock=false`. Terraform releases a DynamoDB lock by deleting the lock item, so a plan that
locks would need `DeleteItem`. That permission was removed on purpose, because it would let a CI job release a lock held by someone's
`apply` and let a second writer in. IAM can't limit `DeleteItem` to "locks this role created": `LeadingKeys` scopes the item key, and every
dev run uses the same key. So with DynamoDB locking, the choice is either "CI can delete dev's lock" or "CI plans don't lock".

Skipping the lock is safe here because this role never writes state. The worst case is a plan computed against the state from just before
a concurrent apply. S3 writes state as a whole object, so the plan sees the old state or the new one, never a partial one. A rerun fixes it,
and the apply job refuses such a saved plan as stale. Applies still lock as normal.

To lock CI plans again, don't re-add `DeleteItem` on the table. S3 native locking (`use_lockfile = true`, Terraform 1.10+) is the
likely route, but it is not a drop-in switch. Check both points below first:
- **The apply role's Denies block it.** With S3 locking, Terraform takes the lock by writing `N/terraform.tfstate.tflock` and releases
  it by deleting that object. The apply role's `DenyOtherStateWrites` (PutObject on anything but the state key) and `DenyStateDeletes`
  (DeleteObject on `bucket/*`) are explicit Denies, so they beat any Allow. Both must exclude the `.tflock` key, or every apply fails
  at the lock step.
- **It doesn't remove the lock-release trade-off.** The plan role would need `s3:PutObject` and `s3:DeleteObject` on that `.tflock`
  object. Every dev run uses the same key, so IAM still can't limit the delete to "locks this role created". That is the same
  choice as DynamoDB `DeleteItem` above, just on a narrower resource.

This policy assumes each environment's backend key is `"<env>/terraform.tfstate"`, set in `environments/<env>/terraform.tf`.
If a backend key changes, this policy has to change with it.

#### What the plan role deliberately can't do
- Read any other environment's state, or the `account`/`ci-iam` state.
- Take, hold or release any state lock, its own environment's included.
- Read data: S3 objects outside its state prefix, your SSM parameters, Secrets Manager values, DynamoDB rows, Lambda code.
- Decrypt with KMS.
- Read EC2 instance user data.
- Write, create or delete anything in AWS.

#### Accepted residual exposure
AWS gives these actions no resource-level scoping: `ec2:Describe*`, `elasticloadbalancing:Describe*`, `autoscaling:Describe*`,
`eks:DescribeAddonVersions`, `iam:ListOpenIDConnectProviders`, `kms:ListAliases` and `logs:DescribeLogGroups`.
This project uses a single AWS account, so these statements can't be narrowed further. Each environment's plan role can see
the others' network, load balancer and Auto Scaling *configuration*, but never data. That's a deliberate trade-off. Only separate per-environment
accounts would remove it, and that's out of scope here.

## The apply role

The apply role exists so that write credentials only ever appear **after a human has approved a specific plan**, and only for the
minutes the apply takes. The plan is made by the read-only plan role in an ungated job; the apply job is bound to the `dev-apply`
environment and applies exactly that saved plan (see `terraform-apply.yml` in [`docs/ci.md`](ci.md)).

### Trust policy: who can become the apply role

```json
{
  "Sid": "GitHubOidcApplyEnvironment",
  "Effect": "Allow",
  "Principal": {
    "Federated": "arn:aws:iam::401352756330:oidc-provider/token.actions.githubusercontent.com"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:kerinschristopher/Curriculum:environment:dev-apply"
    }
  }
}
```

The action, principal and `aud` condition do the same jobs as in the plan role. The difference is `sub`:

- **`StringEquals`, one value, no wildcard.** Only a token for the `dev-apply` environment matches: not a branch, not a PR, not another
  environment such as `dev-apply-x`.
- **GitHub only issues that token after the environment's protection rules pass.** The `dev-apply` environment
  ([`Acme/infra/github/dev-apply-environment.json`](../Acme/infra/github/dev-apply-environment.json)) requires reviewer `kerinschristopher`
  and allows deployments from `main` only. So a job from any other branch, or one nobody approves, never holds a token this policy accepts.
  The trust policy and the environment are one control in two places: change them together.
- **Self-review is allowed** (`prevent_self_review: false`). With one maintainer, the only possible reviewer is the person who merged.
  The gate still forces a deliberate look at the plan before anything is written (design doc Q6).

### Permissions: what the apply role can do

Three inline policies, no managed policies (enforced the same way as the plan role). It was created on 2026-09-29 with `ReadOnlyAccess`
and `AmazonVPCFullAccess`; both were detached when `ci-iam` adopted it in Module 6.

**Scope today: dev's state plus the VPC module's EC2 resources.** No EKS, KMS, logs or IAM writes. `enable_eks` in
`environments/dev/variables.tf` stays `false` until an EKS-scope expansion lands with a permissions boundary (design doc Q4).

#### `terraform-plan-read`
The same document as the plan role's. Apply refreshes every resource before changing anything, so it needs the same reads.

#### `terraform-state-access`

| Sid | Actions | Scope | Why it's there |
|---|---|---|---|
| `StateBucketList` | `s3:ListBucket` | the state bucket, **with** `s3:prefix` = `N/` or `N/*` | As for the plan role |
| `StateReadWrite` | `s3:GetObject`, `s3:PutObject` | `ckerins-tfstate-12345/N/terraform.tfstate` only | Read and write this environment's state object, and nothing else in the bucket |
| `StateLockAndDigest` | `dynamodb:GetItem`, `PutItem`, `DeleteItem` | the lock table, **with** `dynamodb:LeadingKeys` = `<bucket>/N/terraform.tfstate` or `…-md5` | Take and release dev's lock, and update the state checksum. Applies lock; plans don't |
| `DenyOtherStateWrites` | **Deny** `s3:PutObject` | everything except dev's state object (`NotResource`) | Belt and braces: even if a later change grants broader S3 access, this role can never write `account`, `ci-iam` or another environment's state |
| `DenyStateDeletes` | **Deny** `s3:DeleteObject`, `s3:DeleteObjectVersion` | every object in the state bucket | A deleted state orphans every resource in it. Nothing in CI ever needs to delete state, dev's included |

#### `terraform-apply-vpc`

Every taggable resource in `modules/vpc` carries `Environment = dev`, so the policy is written around that tag.

| Sid | Actions | Scope | Why it's there |
|---|---|---|---|
| `Ec2CreateTagged` | `ec2:CreateVpc`, `CreateSubnet`, `CreateInternetGateway`, `CreateRouteTable`, `CreateNatGateway`, `AllocateAddress` | `*` **with** `aws:RequestTag/Environment = N`, region-locked | Create the VPC module's resources, only if the create request tags them for this environment. An untagged or wrongly tagged create is denied |
| `Ec2TagOnCreate` | `ec2:CreateTags` | `*` **with** `ec2:CreateAction` = one of those six creates, region-locked | Terraform tags resources inside the create call. This allows tagging only as part of those creates, not retagging something that already exists |
| `Ec2ChangeOwned` | modify, delete, attach/detach, route and association actions for the same resource types | `*` **with** `aws:ResourceTag/Environment = N`, region-locked | Change or delete only resources that already carry this environment's tag. Another environment's VPC, or an untagged one, is out of reach |
| `Ec2RetagOwned` | `ec2:CreateTags`, `ec2:DeleteTags` | `*` **with** `aws:ResourceTag/Environment = N`, `aws:TagKeys` present and never `Environment`, region-locked | Change other tags (e.g. `Name`) on owned resources. The `Environment` tag itself can't be changed or removed, so the role can't hand its VPC to another environment. `DeleteTags` with no keys (which removes every tag) is refused too. Terraform sends only the tags that change, so an unchanged `Environment` never appears |
| `DenyCiIdentityChanges` | **Deny** `iam:*` | `role/*-github-actions-*` and the GitHub OIDC provider | The apply role has no IAM write today. This keeps that true for CI identity when EKS (which creates IAM roles) is added: the role can never edit itself, the plan role or the provider |

#### What the apply role deliberately can't do
- Anything before a reviewer approves: no token exists until then.
- Create, change or delete EKS, KMS, CloudWatch Logs or IAM resources (until the EKS-scope expansion).
- Touch EC2 resources not tagged `Environment = dev`, or launch instances.
- Change or remove the `Environment` tag on anything it owns.
- Write or delete any state other than dev's, or delete dev's state.
- Change any CI role or the OIDC provider, even after future expansions (explicit Deny).

### Why a separate `ci-iam` root
If CI applied the Terraform root that contains its own roles, the apply role would need permission to edit IAM roles, including
itself, and anyone who could land a reviewed change could grant CI anything. So both roles live in `Acme/infra/terraform/ci-iam/`,
which a human applies from WSL, and the apply role has an explicit Deny on CI identity. `environments/dev` owns no IAM, and the
OIDC provider stays in `account/`.

### Before EKS is in scope
Turning `enable_eks` on first needs a follow-up change to this role: EKS, KMS, logs and IAM-role writes for `role/dev-*`, with
`iam:CreateRole` and `iam:PutRolePermissionsBoundary` conditioned on `iam:PermissionsBoundary = policy/dev-workload-boundary`, so every
role it creates is capped by a boundary. Until then, a merge creates only VPC resources (and costs nothing: the NAT gateway also follows
`enable_eks`).

## GitHub-side controls

IAM is half of each control; GitHub holds the other half. All of these are captured as JSON in [`Acme/infra/github/`](../Acme/infra/github/).

| Control | What it does | Live state (checked 2026-10-10) |
|---|---|---|
| Ruleset `trusted-branches` (`24627971`) | Only admins can create, update, delete or force-push `main`, `mod*` and `mod*/**/*`. Backs the plan role's branch patterns | `bypass_actors: [{actor_id: 5, RepositoryRole (admin), bypass_mode: always}]` |
| Ruleset `main-merge-gate` (`24832716`) | `main` changes only through a PR whose 8 required checks pass (listed in [`docs/ci.md`](ci.md)) | `bypass_actors: [{actor_id: 5, RepositoryRole (admin), bypass_mode: pull_request}]`: an admin can merge a PR over a failing check, explicitly, but can't push to `main` directly |
| Environment `dev-apply` | Required reviewer `kerinschristopher`, `prevent_self_review: false`, deployment branch `main` only. Backs the apply role's trust | `can_admins_bypass: true`. The only admin is also the only reviewer, so this changes nothing today; set it to `false` when someone else joins |

The bypass actor list can't be seen by people without admin access to the repo, so it's recorded here. Re-check it with
`gh api repos/kerinschristopher/Curriculum/rulesets/<id> --jq .bypass_actors` and update this table when it changes.

## How it's verified

| Check | What it proves | Status |
|---|---|---|
| [`test_iam_policies.py`](../Acme/infra/scripts/tests/test_iam_policies.py) (pytest + boto3): 34 plan-role and 45 apply-role permission cases through the IAM policy simulator, plus 17 trust cases | Permissions: allowed actions are allowed, out-of-scope ones denied (every lock write for the plan role; untagged creates, other state and CI identity for the apply role). Trust: which `sub`/`aud` values each role accepts. Condition values are supplied by hand. A harness failure reports as a pytest ERROR, not a policy FAIL | Passing, 96/96 against the live roles (2026-10-10). Before the `pull_request` trust was applied, exactly one case failed (that one); before `Ec2RetagOwned` was applied, exactly the 4 new retag denials failed |
| `SIM_PLAN_JSON=<plan JSON>` run of the same tests | The *planned* `ci-iam` policies, before they're applied | Used before every `ci-iam` apply in Module 6 (73/73, then 90/90, then 96/96) |
| Real plan on a PR (PR #9, run `38032703703`) | The real `pull_request` trust match: `assumed-role/dev-github-actions-plan/...`, plan of dev with no AccessDenied | Passed |
| Real manual plan on `mod6` (run `38032295497`) | The real branch trust match and condition values with `-lock=false` | Passed |
| Negative trust test, dispatch from throwaway branch `trust-check` (run `37571573254`) | A branch outside `main`/`mod*` gets `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Passed, branch deleted |
| Ruleset coverage: `gh api repos/kerinschristopher/Curriculum/rules/branches/<name>` (encode `/` as `%2F`) | `main`, `mod5`, `modern`, `mod/evil` and `mod/a/b` get the `trusted-branches` rules, and `trust-check` and `feature/mod5` get none | Passed |
| Ruleset enforcement: push `HEAD:mod/evil` with the `trusted-branches` bypass temporarily set to "pull requests only" (Module 5) | A push that doesn't bypass is rejected | Passed: `GH013 ... Cannot create ref due to creations being restricted`; bypass restored to Always |
| Direct push to `main` (2026-10-10) | `main-merge-gate` refuses it even for an admin | Passed: `GH013 ... Changes must be made through a pull request` |
| Full refresh: deploy dev (VPC + EKS), run the CI plan, expect `No changes` | The EKS, KMS (alias condition), logs and cluster-IAM read statements against real resources | Passed in Module 5: 58 resources refreshed with no AccessDenied (`eb34972`) |
| First CI apply after the Module 6 merge | The apply role against real AWS, including actions that authorize against two resources (for example `AssociateRouteTable` on the subnet and the route table), and the environment gate | **Not yet run** |

Run the simulator tests from WSL with AWS credentials that can call `iam:SimulatePrincipalPolicy`
(and `iam:SimulateCustomPolicy` for planned policies). **They can't run in CI, and that is deliberate:** neither CI role has
those permissions, so automating them would need a second, more privileged principal in CI. A pull request that changes IAM
therefore has to be tested by a human before `ci-iam` is applied. Setup and usage are in the file's docstring:

```bash
~/.venvs/iam-tests/bin/pytest Acme/infra/scripts/tests                     # live dev roles
SIM_PLAN_JSON=plan.json ~/.venvs/iam-tests/bin/pytest Acme/infra/scripts/tests   # planned ci-iam policies
SIM_ENV=stage ~/.venvs/iam-tests/bin/pytest Acme/infra/scripts/tests       # once stage roles exist
```

## Changing the roles

Every change here is a **human `ci-iam` apply**: plan, read it, run the tests with `SIM_PLAN_JSON`, apply the saved plan file,
re-plan (expect `No changes`) and re-run the tests against the live roles.

- **Allow another branch.** Add it to `github_branches` in `ci-iam/main.tf` **and** to the `trusted-branches` ruleset's
  include list, then re-apply the ruleset (`gh api -X PUT repos/kerinschristopher/Curriculum/rulesets/24627971 --input Acme/infra/github/trusted-branches-ruleset.json`).
  A trusted branch that the ruleset doesn't cover is open to every collaborator. New `modN` branches already match both and need no change.
- **Add stage or prod.** Add another module call in `ci-iam/main.tf` with `name = "stage"` or `"prod"`. Tighten trust as you go:
  stage gets `["main"]`, prod gets `["main"]`, and leave `trust_pull_requests` off unless their PR plans are wanted. Each gets its own
  `<env>-apply` environment with required reviewers. Before you do this, give the node group role an environment-scoped name. The full
  checklist is in the future-environments note at the top of [`modules/iam-roles/main.tf`](../Acme/infra/terraform/modules/iam-roles/main.tf).
- **Stop trusting pull requests.** Set `trust_pull_requests = false` (or remove it). PR plans will then fail at the assume-role step.
- **Lock CI plans.** Switch the backend to S3 native locking, after carving `.tflock` out of the apply role's state Denies (see [Why CI plans don't lock](#why-ci-plans-dont-lock)). Don't re-add DynamoDB `DeleteItem`.
- **Let the apply role manage EKS.** See [Before EKS is in scope](#before-eks-is-in-scope). Add the new apply-role cases to the tests first.
- **A plan or apply fails with AccessDenied.** The error names the action and the resource. Add the action to the statement for that
  service, with the narrowest scope the action supports. Check the service's IAM reference before falling back to `*`. Then add a test
  case for it and run the tests.
