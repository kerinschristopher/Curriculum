# IAM: the CI plan role

GitHub Actions runs `terraform plan` against AWS by assuming a short-lived IAM role through OpenID Connect (OIDC).
No AWS access keys are stored in GitHub. This page explains who can assume that role, what it can do once assumed, and
why each statement exists, so you can change it without guessing.

| | |
|---|---|
| Role | `dev-github-actions-plan` (one per environment: `<env>-github-actions-plan`) |
| Defined in | [`Acme/infra/terraform/modules/iam-roles/main.tf`](../Acme/infra/terraform/modules/iam-roles/main.tf), called from [`environments/dev/main.tf`](../Acme/infra/terraform/environments/dev/main.tf) |
| Used by | [`.github/workflows/terraform-plan.yml`](../.github/workflows/terraform-plan.yml) |
| Capability | Plan only: reads configuration and state, takes no state lock, writes nothing |
| Session length | 1 hour (`max_session_duration = 3600`) |
| Account / region | `401352756330` / `us-east-1` |

## How the role gets assumed

1. The workflow declares `permissions: id-token: write`. Without it, GitHub won't issue the job an OIDC token.
2. `aws-actions/configure-aws-credentials` asks GitHub for a signed token. GitHub sets its claims, including
   `aud` (who the token is for) and `sub` (which repo and ref the job runs from).
3. The action calls STS `AssumeRoleWithWebIdentity` with that token. AWS checks the signature against the account's GitHub
   OIDC provider, then evaluates the role's **trust policy** against the claims.
4. If the trust policy matches, STS returns temporary credentials (valid up to 1 hour), and the job runs with the role's
   **permission policies**.

Two separate questions decide what a workflow can do. The trust policy decides **who** can become the role. The permission
policies decide **what** that identity can then do. Each one alone isn't enough: tight trust over broad permissions is
still one compromised branch away from reading the whole account, and tight permissions under open trust can be assumed by anyone who can push.

## Trust policy: who can become the role

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
        "repo:kerinschristopher/Curriculum:ref:refs/heads/mod*"
      ]
    }
  }
}
```

There is one statement. Each part of it narrows who can get in.

### `Action: sts:AssumeRoleWithWebIdentity`
The only way in is a federated web-identity token. The policy has no `sts:AssumeRole` statement, so no IAM user, no other role and no
other AWS account can assume this role, even with admin rights elsewhere.

### `Principal.Federated`: the GitHub OIDC provider
Only tokens signed by GitHub's issuer (`token.actions.githubusercontent.com`) and verified through this account's OIDC provider are
considered. AWS allows one provider per issuer URL per account, so the provider belongs to the account-level stack
([`Acme/infra/terraform/account/main.tf`](../Acme/infra/terraform/account/main.tf)). Each environment looks it up with a data source
instead of creating it, which keeps environments from fighting over a shared resource.

### Condition: `aud` must equal `sts.amazonaws.com`
GitHub can mint tokens for any audience a workflow asks for. This condition only accepts tokens minted for AWS STS. Without it, a token
the same repo obtained for a different service could be replayed here.

### Condition: `sub` must match this repo and an allowed branch
The `sub` claim identifies where the job ran. For a push or manual run, it looks like
`repo:kerinschristopher/Curriculum:ref:refs/heads/<branch>`. This condition does two jobs:

- **Pins the repository.** Every GitHub repository's workflows can get tokens from the same issuer. Without the `repo:` prefix,
  any repo on GitHub could assume this role. It is the most important line in the policy.
- **Pins the branches.** Dev allows `main` and `mod*` (set in `environments/dev/main.tf` via `github_branches`). The operator is
  `StringLike`, so `mod*` matches `mod5`, `mod6` … `mod12` without editing Terraform for each new module branch.
  The module has no default for `github_branches`, and validation rejects any entry starting with `*`. A new environment has to
  name its branches; forgetting the argument fails instead of inheriting something.

A pattern in the trust list is only as narrow as the set of people who can create a branch matching it. That half lives in GitHub,
in the `trusted-branches` ruleset ([`Acme/infra/github/trusted-branches-ruleset.json`](../Acme/infra/github/trusted-branches-ruleset.json)).
It covers `main`, `mod*` and `mod*/**/*`, and only repository admins can create, push to, delete or force-push those branches.
The two systems read `*` differently. IAM's `*` matches `/`, so trust `mod*` accepts `mod/anything`. GitHub matches ruleset patterns
with `fnmatch` in pathname mode, where `*` stops at `/`, and so does `**` unless it's written as `**/`. A ruleset on `mod*` alone, or on `mod**`
(tried, and confirmed with the `rules/branches` API), leaves `mod/anything` open to collaborators while AWS still trusts it.
`mod*/**/*` closes that gap.

Why not allow every branch (`*`)? The workflow file lives in the same repo, so whoever can push a trusted branch can edit the
workflow and run anything with the role. Trusting `*` hands the role to anyone who can push any branch. Narrowing to ruleset-protected
patterns means a push to `feature/x` gets a valid GitHub token that AWS rejects.

### Triggering the workflow
The workflow runs on `workflow_dispatch` only, with no `push` trigger. A push trigger would run on whatever branch the pusher
chose, with whatever workflow they had written. Dispatch from a branch with:

```bash
gh workflow run terraform-plan.yml --ref mod5
```

The run's ref sets the `sub` claim, so every branch you dispatch from has to be in `github_branches`. Don't add an input that
checks out a different branch while the run sits on `main`. That would run untrusted code under `main`'s trust. GitHub only offers
dispatch for workflows whose file exists on the default branch, so the workflow file has to be on `main` before any branch can run it.

The trigger, the trust list and the ruleset move together. Changing one means checking the other two.

### What the trust policy deliberately doesn't accept (yet)
- **`pull_request` events.** A PR-triggered job has `sub = repo:<repo>:pull_request`, which matches no pattern here, so PR plans are
  rejected. This is intentional until Module 6 adds PR plans. At that point, add that `sub` value deliberately.
- **GitHub Environments.** A job bound to an environment has `sub = repo:<repo>:environment:<name>`. Prod will move to this
  model, with required reviewers, in Module 6.
- **Workflow pinning.** The trust policy doesn't check *which workflow file* requested the token (the `job_workflow_ref` claim). Any workflow
  on an allowed branch can assume the role. That's acceptable for a plan-only role. It's worth adding for the apply role.

### Session length: 1 hour
A plan run takes under a minute. Even a slow run with a full EKS refresh takes a few minutes. One hour leaves headroom while limiting how long
leaked credentials stay useful.

## Permissions: what the role can do

The role has two inline policies and no AWS-managed policies. It used to have `ReadOnlyAccess` attached. That was removed because it grants read
on almost every service, including the *contents* of S3 objects, SSM parameters and Lambda source, across the whole account.

The current policies follow these rules:
- **Describe/Get/List only.** Nothing here returns stored data, decrypts or writes.
- **Scope by ARN wherever AWS allows it.** ARNs are built from `var.name` and the current account and region, so a stage or prod role
  scopes itself to its own environment automatically.
- **Region-locked.** Regional statements require `aws:RequestedRegion = us-east-1`.
- **Use `*` only where AWS offers no resource-level scoping** for that action. Putting an ARN there would make the statement never match.

### `terraform-plan-read`
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

### `terraform-state-access`

| Sid | Actions | Scope | Why it's there |
|---|---|---|---|
| `StateBucketList` | `s3:ListBucket` | the state bucket, **with** `s3:prefix` = `N/` or `N/*` | Lets the backend check for this environment's state objects. Without `ListBucket`, S3 reports a missing object as AccessDenied instead of NotFound. The prefix condition hides other environments' keys |
| `StateRead` | `s3:GetObject` | `ckerins-tfstate-12345/N/*` | Read this environment's state. State stores values in plaintext, so `account/` and other environments' state stay unreadable |
| `StateDigestRead` | `dynamodb:GetItem` | the lock table, **with** `dynamodb:LeadingKeys` = `<bucket>/N/terraform.tfstate-md5` | Read this environment's state checksum, which plan verifies the downloaded state against. This is the digest item only, not the lock item |

There is no `s3:PutObject`, so plan never writes state. There is also no `dynamodb:PutItem` or `DeleteItem`, so the role can't take, hold or release any lock.

#### Why CI plans don't lock
The workflow runs `terraform plan -lock=false`. Terraform releases a DynamoDB lock by deleting the lock item, so a plan that
locks would need `DeleteItem`. That permission was removed on purpose, because it would let a CI job release a lock held by someone's
`apply` and let a second writer in. IAM can't limit `DeleteItem` to "locks this role created": `LeadingKeys` scopes the item key, and every
dev run uses the same key. So with DynamoDB locking, the choice is either "CI can delete dev's lock" or "CI plans don't lock".

Skipping the lock is safe here because this role never writes state. The worst case is a plan computed against the state from just before
a concurrent apply. S3 writes state as a whole object, so the plan sees the old state or the new one, never a partial one, and a rerun fixes it.
Applies still lock as normal. The workflow's `concurrency` group keeps CI plans from overlapping each other.

To lock CI plans again, don't re-add `DeleteItem` on the table. Instead, move the backend to S3 native locking (`use_lockfile = true`, Terraform 1.10+).
Releasing the lock then needs `s3:DeleteObject` on a single `N/terraform.tfstate.tflock` object, which is a much narrower grant.

This policy assumes each environment's backend key is `"<env>/terraform.tfstate"`, set in `environments/<env>/terraform.tf`.
If a backend key changes, this policy has to change with it.

### What the role deliberately can't do
- Read any other environment's state.
- Take, hold or release any state lock, its own environment's included.
- Read data: S3 objects outside its state prefix, your SSM parameters, Secrets Manager values, DynamoDB rows, Lambda code.
- Decrypt with KMS.
- Read EC2 instance user data.
- Write, create or delete anything in AWS.

### Accepted residual exposure
AWS gives these actions no resource-level scoping: `ec2:Describe*`, `elasticloadbalancing:Describe*`, `autoscaling:Describe*`,
`eks:DescribeAddonVersions`, `iam:ListOpenIDConnectProviders`, `kms:ListAliases` and `logs:DescribeLogGroups`.
This project uses a single AWS account, so these statements can't be narrowed further. Each environment's plan role can see
the others' network, load balancer and Auto Scaling *configuration*, but never data. That's a deliberate trade-off. Only separate per-environment
accounts would remove it, and that's out of scope here.

## How it's verified

| Check | What it proves | Status |
|---|---|---|
| [`simulate-plan-role.sh`](../Acme/infra/scripts/tests/simulate-plan-role.sh) (IAM policy simulator, 34 cases) | The policy logic: allowed actions are allowed, out-of-scope ones denied (including every lock write), user data explicitly denied. Condition values are supplied by hand | Passing |
| `terraform-plan` workflow on `mod5` | The real trust match, plus real condition values for the region lock, the SSM AMI lookup, the state read and the checksum read, with `-lock=false` | Passing (push-triggered); re-check with `gh workflow run --ref mod5` pending |
| Negative trust test: push from throwaway branch `trust-check` (run `37536403620`) | A branch outside `main`/`mod*` gets `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Passed, branch deleted. Historical: ran under the old push trigger |
| Negative trust test, dispatch: `gh workflow run --ref trust-check` | Same rejection with the manual trigger | Pending |
| No push trigger: push `mod5` | A push starts no workflow run | Pending |
| Ruleset coverage: `gh api repos/kerinschristopher/Curriculum/rules/branches/<name>` (encode `/` as `%2F`) | `main`, `mod5`, `modern`, `mod/evil` and `mod/a/b` get all four rules, and `trust-check` and `feature/mod5` get none | Passed |
| Ruleset enforcement: push `HEAD:mod/evil` with admin bypass set to "pull requests only" | A push that doesn't bypass is rejected | Pending |
| Full refresh: deploy dev (VPC + EKS), run the CI plan, expect `No changes` | The EKS, KMS (alias condition), logs and cluster-IAM statements against real resources. `terraform plan` only calls APIs for resources already in state | Passed: 58 resources refreshed with no AccessDenied, and `No changes` at `eb34972`, after naming the cluster admin explicitly (see `modules/eks/main.tf`) |

Run the simulator from WSL with AWS credentials that can call `iam:SimulatePrincipalPolicy`:

```bash
Acme/infra/scripts/tests/simulate-plan-role.sh        # dev
Acme/infra/scripts/tests/simulate-plan-role.sh stage  # once a stage role exists
```

## Changing the role

- **Allow another branch.** Add it to `github_branches` in `environments/<env>/main.tf` **and** to the `trusted-branches` ruleset's
  include list, then re-apply the ruleset (`gh api -X PUT repos/kerinschristopher/Curriculum/rulesets/<id> --input Acme/infra/github/trusted-branches-ruleset.json`).
  A trusted branch that the ruleset doesn't cover is open to every collaborator. New `modN` branches already match both and need no change.
- **Add stage or prod.** Call the same module with `name = "stage"` or `"prod"`. Tighten trust as you go from dev to stage to prod:
  stage gets `["main"]`, and prod gets `["main"]` before moving to a GitHub Environment with required reviewers.
  Before you do this, give the node group role an environment-scoped name. The full checklist is in the future-environments note at the top of
  [`modules/iam-roles/main.tf`](../Acme/infra/terraform/modules/iam-roles/main.tf).
- **Lock CI plans.** Switch the backend to S3 native locking (see [Why CI plans don't lock](#why-ci-plans-dont-lock)). Don't re-add DynamoDB `DeleteItem`.
- **Run plan on pull requests.** Add the `repo:<repo>:pull_request` `sub` value to the trust policy on purpose. It isn't a branch pattern.
- **Plan fails with AccessDenied.** The error names the action and the resource. Add the action to the statement for that
  service, with the narrowest scope the action supports. Check the service's IAM reference before falling back to `*`. Then rerun the simulator and add a case for it.
