# CI/CD

This repo has three GitHub Actions workflows. None of them uses a long-lived
credential: AWS access is via OIDC (OpenID Connect) short-lived role sessions,
and GHCR (GitHub Container Registry) access is via the per-run `GITHUB_TOKEN`.

| Workflow | Runs on | What "passing" means | Credential |
|---|---|---|---|
| [`sample-api-ci.yml`](../.github/workflows/sample-api-ci.yml) | PRs and pushes to `main` touching `Acme/apps/sample-api/**` | gofmt clean, `go vet` + golangci-lint clean, tests pass under the race detector, image builds, Trivy finds no fixable HIGH/CRITICAL vulnerabilities. On `main`, the scanned image is pushed to GHCR. | `GITHUB_TOKEN` (push job only, `packages: write`) |
| [`terraform-plan.yml`](../.github/workflows/terraform-plan.yml) | PRs touching `environments/dev/**` or `modules/**` | `terraform fmt` + `validate` pass, `plan` succeeds; plan is posted as a PR comment | IAM role `dev-github-actions-plan` via OIDC |
| [`terraform-apply.yml`](../.github/workflows/terraform-apply.yml) | Pushes to `main` touching the same paths; manual "Run workflow" (apply or destroy) | A required reviewer approved the `dev-apply` environment, then the saved plan applied cleanly | IAM role `dev-github-actions-apply` via OIDC |

---

## IAM roles assumed by the workflows

Both roles are defined in [`Acme/infra/terraform/modules/iam-roles`](../Acme/infra/terraform/modules/iam-roles)
and instantiated by [`Acme/infra/terraform/ci-iam`](../Acme/infra/terraform/ci-iam).

### How OIDC works here

1. A job with `permissions: id-token: write` asks GitHub for a signed OIDC token.
2. `aws-actions/configure-aws-credentials` sends it to AWS STS (`AssumeRoleWithWebIdentity`).
3. AWS checks the token was issued by `token.actions.githubusercontent.com` (the OIDC
   identity provider registered in the account), that `aud` is `sts.amazonaws.com`,
   and that the `sub` claim matches the role's trust policy exactly.
4. STS returns credentials valid for at most one hour. Nothing is stored in GitHub.

The `sub` claim is what separates the roles. GitHub sets it from what triggered the job:

| Situation | `sub` claim |
|---|---|
| Push to a branch | `repo:kerinschristopher/Curriculum:ref:refs/heads/<branch>` |
| Pull request | `repo:kerinschristopher/Curriculum:pull_request` |
| Job that declares `environment: <name>` | `repo:kerinschristopher/Curriculum:environment:<name>` |

### `dev-github-actions-plan`: used by `terraform-plan.yml`

| | |
|---|---|
| ARN | `arn:aws:iam::401352756330:role/dev-github-actions-plan` |
| Trusted `sub` | `repo:kerinschristopher/Curriculum:pull_request` |
| Permissions | AWS managed `ReadOnlyAccess`; inline `terraform-state-access`: `s3:ListBucket` on the state bucket, `s3:GetObject` on its objects, `dynamodb:GetItem/PutItem/DeleteItem` on the lock table |
| Cannot | Create, change, or delete anything in AWS, or write Terraform state |
| Max session | 1 hour |

**Why:** `plan` must refresh every resource in state, so it needs broad *read*. It needs
no write access at all. Pull requests from forks are never issued OIDC tokens, so only
branches pushed by people with write access to this repo can assume it.

### `dev-github-actions-apply`: used by `terraform-apply.yml`

| | |
|---|---|
| ARN | `arn:aws:iam::401352756330:role/dev-github-actions-apply` |
| Trusted `sub` | `repo:kerinschristopher/Curriculum:environment:dev-apply` |
| Permissions | AWS managed `ReadOnlyAccess` + `AmazonVPCFullAccess`; inline `terraform-state-access`: the plan role's state permissions plus `s3:PutObject` on **only** `dev/terraform.tfstate` |
| Cannot | Create or modify any IAM role or policy (including its own); write any state file other than dev's (e.g. `ci-iam/terraform.tfstate`) |
| Max session | 1 hour |

**Why:** trusting only the `dev-apply` environment means the role can be assumed only by a
job that passed that environment's required-reviewer gate. A push to `main` alone is not
enough. Its write permissions are limited to what `environments/dev` currently creates
(VPC networking).

**Follow-up:** turning on `enable_eks` will make apply fail with `AccessDenied`, because
the EKS module creates IAM roles. That's intentional. Widening this role (scoped IAM
permissions, ideally with a permissions boundary) should be a deliberate, reviewed change.

### Why CI can't change its own permissions

The OIDC provider and both roles live in their own Terraform root config, `ci-iam/`,
which **a human applies locally** with admin credentials. CI never applies it:

```
Acme/infra/terraform/
├── state-backend/      human-applied: S3 state bucket + DynamoDB lock table (local state)
├── ci-iam/             human-applied: OIDC provider + plan and apply roles (state: ci-iam/terraform.tfstate)
└── environments/dev/   CI plans on PRs and applies on merge (state: dev/terraform.tfstate)
```

If the roles lived in `environments/dev`, the apply role would need permission to edit
IAM roles, including its own, and could grant itself anything. That would make the PR
review and the approval gate meaningless. The same reasoning is behind scoping its
`s3:PutObject` to dev's state key.

The roles originally lived in `environments/dev` (Module 3). They were moved without
being recreated: `import` blocks in `ci-iam` adopted them, and a `removed { destroy = false }`
block in `environments/dev` drops them from dev's state on its next apply.

---

## Protected deploys: the `dev-apply` environment

| Setting | Value | Why |
|---|---|---|
| Required reviewers | Repo owner | Nothing is created until someone approves after the merge. Keeps a reviewer's merge from starting billed resources unattended. |
| Deployment branches | `main` only | A job on any other branch can't enter the environment, so it can't get the apply role. |

Two reviews protect each infrastructure change: the PR review (merge to `main`) and the
environment approval (apply). A job waiting for approval gives up after 30 days and
creates nothing.

Important: GitHub auto-creates an environment with **no** protection rules if a workflow
references one that doesn't exist. The environment must be created (with its reviewer)
before `terraform-apply.yml` first runs.

---

## Security hardening

Following GitHub's [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use):

- **Actions pinned to full commit SHAs**, with the version in a comment. Tags can be moved.
  In March 2026, 76 of 77 `aquasecurity/trivy-action` tags were force-pushed to
  credential-stealing code, and SHA-pinned workflows were unaffected.
- **Least-privilege `GITHUB_TOKEN`**: every workflow defaults to `contents: read`. Jobs
  opt in to more (`id-token: write`, `pull-requests: write`, `packages: write`) only where needed.
- **`persist-credentials: false`** on every checkout, so the token isn't left on disk for later steps.
- **Third-party scanner isolated**: Trivy runs in `build-scan`, which has no secrets and no
  write permissions. Only the separate `push` job can write packages, and it never checks
  out code. It pushes the exact image tarball that was scanned (passed as an artifact),
  not a rebuild.
- **No script injection**: untrusted values are passed through environment variables or
  files, never interpolated into `run:` scripts. The PR-comment script reads the plan from a file.
- **Runner image pinned** (`ubuntu-24.04`, not `ubuntu-latest`, which moves to Ubuntu 26 on
  2026-10-19).
- **Repo-level**: secret scanning and push protection enabled.

---

## Speed: caching and parallelism

| Cache | Key | Effect |
|---|---|---|
| Go build cache (`setup-go`) | Go version + `go.mod` hash | Compiled packages reused by lint and test |
| golangci-lint analysis | `go.mod` hash + interval | Lint results reused for unchanged code |
| Docker layers (`type=gha`) | Layer content | Unchanged layers skip rebuild; `go.mod` is copied before source so it stays cached when only code changes |
| Trivy binary | Trivy version | No re-download |
| Trivy vulnerability DB | Date (`cache-trivy-YYYY-MM-DD`) | Downloaded at most once a day, so vulnerability data is never more than a day old |

Measured on run `36505105965` (same commit, PR event):

| | Attempt 1 (cold caches) | Attempt 2 (warm caches) |
|---|---|---|
| Whole run | 1m44s | — |
| lint | 39s | 31s |
| test | 39s | 16s |
| build-scan | 1m0s | 35s |
| Cache results | all misses, then saved | all hits; all 4 Docker build steps `CACHED` |

A re-run of the same commit is the best case. On a real code change, the Docker layers
from `COPY *.go` onward rebuild.

Other speed measures:
- `lint` and `test` run in parallel.
- `paths:` filters: app changes don't run Terraform, and Terraform changes don't run the app pipeline.
- A newer push to a PR cancels that PR's in-progress run (never on `main`).
- Tests use `-count=1` so the restored Go cache can't replay stale **test results**.

---

## Operating it

**Normal change:** open a PR, read the plan comment, get it reviewed and merged, then in
**Actions → terraform-apply** click **Review deployments → Approve**.

**Destroy dev** (same-day cleanup after testing): Actions → terraform-apply →
**Run workflow** → `action: destroy` → approve. The run summary shows the destroy plan.

**Change CI's own permissions:** edit `modules/iam-roles`, then locally:
```bash
cd Acme/infra/terraform/ci-iam
terraform plan -out=ci-iam.tfplan
terraform apply ci-iam.tfplan
```

**Read logs:** `gh run view <run-id> --log-failed`, or on a run's page: gear icon → Download log archive.

---

## Cost controls

- `environments/dev` variable **`enable_eks` defaults to `false`**. The NAT gateway follows
  it (`enable_nat_gateway = var.enable_eks`), so the default apply creates only free VPC
  resources (VPC, subnets, route tables, internet gateway).
- Enabling EKS costs roughly $0.10/hr (control plane) + $0.045/hr (NAT) + node instances.
  Destroy the same day.
- Public repo: GitHub-hosted runner minutes and GHCR storage are free.

---

## Known limitations / follow-ups

- **Plan-to-apply gap:** the reviewer approves based on the PR's plan comment. The apply
  job re-plans against current state after approval and applies that saved plan (shown
  in the run summary). If state changed in between, they can differ.
- **`dynamodb_table` is deprecated** in the S3 backend. Migrate to `use_lockfile = true`.
  The roles would then need `s3:PutObject`/`s3:DeleteObject` on the `.tflock` object.
- **`ci-iam` and `state-backend` are applied by hand.** A PR changing them isn't planned by CI.
- The apply role needs scoped IAM permissions before EKS can be enabled (see above).
