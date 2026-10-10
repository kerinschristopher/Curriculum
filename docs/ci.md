# CI/CD

What runs on GitHub Actions, when, with which permissions, and which AWS role (if any) each job assumes. The IAM side
(trust policies, permission statements and why each exists) is in [`docs/iam.md`](iam.md). The design and its evidence are in
[`docs/design/module-6-ci-cd.md`](design/module-6-ci-cd.md).

## At a glance

| Workflow | Runs on | AWS role | What it's for |
|---|---|---|---|
| [`sample-api-ci.yml`](../.github/workflows/sample-api-ci.yml) | `pull_request`; `push` to `main`/`mod*`; `push` of `v*` tags | none | Lint, test, render manifests, build and scan the image; publish it from `main`; add release tags |
| [`sample-api-rescan.yml`](../.github/workflows/sample-api-rescan.yml) | `schedule` (daily 06:17 UTC); `workflow_dispatch` | none | Re-scan the published images against today's vulnerability database |
| [`terraform-plan.yml`](../.github/workflows/terraform-plan.yml) | `pull_request`; `push` to `main`/`mod*`; `workflow_dispatch` | plan role (PRs and manual runs only) | fmt, validate, policy tests; plan dev and comment the plan on the PR |
| [`terraform-apply.yml`](../.github/workflows/terraform-apply.yml) | `push` to `main` touching dev Terraform; `workflow_dispatch` (apply or destroy) | plan role, then apply role after approval | Plan dev, wait for a human to approve that plan, apply exactly it |
| [`terraform-plan-reusable.yml`](../.github/workflows/terraform-plan-reusable.yml) | `workflow_call` only | whichever role the caller passes (always the plan role today) | The one plan job both Terraform workflows share |

### Why five workflows, not the three the curriculum lists

The Module 6 deliverable names three files: `sample-api-ci.yml`, `terraform-plan.yml` and `terraform-apply.yml`. All three
exist under those names. The other two are a deliberate design choice (design doc FR9 and "Other Designs Considered"), not leftovers:

- **`terraform-plan-reusable.yml` (a reusable workflow, `workflow_call`).**
  - Both Terraform workflows call it, so the plan you read on the PR and the plan applied after merge come from one job
    definition: same init, same flags, same summary, same Conftest check. With two copies they would drift apart, and the plan
    you approve could be produced differently from the one that's applied.
  - It is also the module's worked example of a reusable workflow, one of the Module 6 concepts.
- **`sample-api-rescan.yml` (the `schedule` trigger).**
  - A daily scan of images that are already published. Folded into `sample-api-ci.yml` it would need a "not on schedule"
    condition on every CI job.
  - A new vulnerability would show as a red `sample-api-ci` run on `main`, as if a merge had broken the build.
  - The third-party scanner would share a file with the `push` job, which holds `packages: write`. On its own, the scanner
    job runs with no token permissions at all.

**What three files would buy:**
- the deliverable matched literally;
- each pipeline readable top to bottom in one file;
- none of the reusable-workflow quirks this setup had to work around:
  - the caller job's check name changes when it's skipped, hence the `plan-result` aggregator;
  - `with:` can't read `env`, so the role ARNs are literals;
  - permissions must be granted by the caller.

**Why we kept five anyway:** one plan definition matters more for a gated apply than one fewer file. The scanner's isolation is
worth a second file. And the two extras are this repo's only working examples of the reusable-workflow and `schedule` concepts.

Every job starts from `permissions: contents: read` at the workflow level and asks for more only where it needs it. Every third-party
action is pinned to a full commit SHA, with the version in a comment.

## Required checks before merging to `main`

The `main-merge-gate` ruleset ([`Acme/infra/github/main-merge-gate-ruleset.json`](../Acme/infra/github/main-merge-gate-ruleset.json))
only accepts changes to `main` through a pull request whose required checks pass:

| Check | Workflow | Passes when |
|---|---|---|
| `lint` | sample-api-ci | gofmt, `go vet` and golangci-lint are clean (or the app didn't change) |
| `test-result` | sample-api-ci | every `test (<go version>)` leg passed (or the app didn't change) |
| `kustomize` | sample-api-ci | every Kustomize base and overlay renders (or nothing it covers changed) |
| `build-scan` | sample-api-ci | the image builds and Trivy finds no fixable HIGH/CRITICAL vulnerability (or the app didn't change) |
| `fmt` | terraform-plan | `terraform fmt -check -recursive` is clean (or no Terraform changed) |
| `validate-result` | terraform-plan | every root validates and every module's `terraform test` passes (or no Terraform changed) |
| `policy` | terraform-plan | the Conftest policy tests pass and the seeded bad plan is still caught (or no Terraform changed) |
| `plan-result` | terraform-plan | the dev plan ran and passed the policy (or the change didn't affect dev, or the PR is from a fork, where no plan runs) |

Two rules shape this list:
- **No workflow-level path filters on required workflows.** A required check that never reports blocks the PR forever ("Expected — waiting
  for status"). Instead, each workflow's first job, `changes`, diffs the PR and later jobs skip themselves. A skipped job counts as passing.
- **Aggregate checks, not matrix legs or reusable-workflow callers.** `test (1.26)`, `validate (account)` and `plan-dev / plan` change
  name when the matrix changes or the job is skipped (a skipped `plan-dev` reports as `plan-dev`, not `plan-dev / plan`). The `*-result`
  jobs always report under one name, and fail if `changes` itself failed.

Admins can bypass the ruleset only on a pull request (an explicit, logged choice on the PR page), never with a direct push. Re-apply
the ruleset after editing its JSON with
`gh api -X PUT repos/kerinschristopher/Curriculum/rulesets/24832716 --input Acme/infra/github/main-merge-gate-ruleset.json`.

## `sample-api-ci.yml`

| Job | Needs / runs when | Permissions | Does |
|---|---|---|---|
| `changes` | not on tags | `contents: read` | Decides whether `Acme/apps/sample-api/`, `Acme/platform/` or this workflow changed. A new branch or force-push (no usable base) runs everything |
| `lint` | app changed | `contents: read` | gofmt, `go vet ./...`, golangci-lint v2.14.0, on the Go version in `go.mod` |
| `test` (matrix `go: ["1.26", "1.27"]`) | app changed | `contents: read` | `go test -race -count=1 -v ./...` on both supported Go releases |
| `test-result` | always (not on tags) | `contents: read` | Fails unless `changes` succeeded and every `test` leg passed or was skipped |
| `kustomize` | app changed | `contents: read` | `kubectl kustomize` over the sample-api base, its overlays and the infrastructure overlays |
| `build-scan` | needs lint, test-result, kustomize | `contents: read` only: no secrets, no OIDC | Builds the image once (`VERSION=sha-<short>` via `-ldflags`) to a tarball and scans the tarball with Trivy v0.75.0 (`HIGH,CRITICAL`, `ignore-unfixed`, exit code 1). On `push` to `main` only, uploads the tarball (1 day) |
| `push` | `push` to `main`, after build-scan | `contents: read`, `packages: write` | Downloads the scanned tarball, `docker load`, logs in to GHCR with the run's `GITHUB_TOKEN`, pushes `ghcr.io/kerinschristopher/sample-api:sha-<short>`. Never checks out code, so what's pushed is what was scanned |
| `release-tag` | `push` of a `v<major>.<minor>.<patch>` tag | `packages: write` only | Adds the `<major>.<minor>.<patch>` tag to the existing `sha-<short>` image of the tagged commit (`imagetools create`), then checks both tags have the same digest. Nothing is rebuilt |

- **No `latest` tag.** A moving label lets nodes with cached images run different builds under one name.
- **No AWS access at all**, so the `pull_request` trigger is safe here.
- **Concurrency:** on a PR, `sample-api-ci-<ref>`, and a new push to the same PR cancels the old run. Every push run (to `main`, `mod*`
  or a tag) gets a group of its own (`sample-api-ci-<run id>`), so it is never cancelled, not even while pending. One shared group
  wouldn't be enough: GitHub keeps only the newest pending run in a group, and a dropped run on `main` means a merge commit with no
  `sha-*` image to release.
- **Not here yet, on purpose:** Docker layer caching and the Trivy DB cache (`cache: false`) come in Module 7, which measures cold vs warm
  builds. Images are amd64 only; multi-arch is also Module 7.

### Releasing an image
1. Merge to `main` and wait for a green `sample-api-ci` run. GHCR then has `sha-<short>` for the merge commit.
2. Tag that commit and push the tag: `git tag v0.2.0 <commit> && git push origin v0.2.0`.
3. `release-tag` adds `0.2.0` to the same image. If the commit has no `sha-*` image (not on `main`, or its run failed), the job fails and says so.
4. Point the overlays' `images[].newTag` at the release in a PR. Nothing deploys automatically yet (Flux arrives in Module 8).

The first push needs a one-time manual setting: in the `sample-api` package's settings, under "Manage Actions access", give the
`Curriculum` repository the **Write** role. Without it, `push` fails with a 403.

## `sample-api-rescan.yml`

| Job | Needs | Permissions | Does |
|---|---|---|---|
| `resolve` | none | `contents: read` | Lists the package's tags (anonymously: the package is public). Picks the newest `sha-*` image (walking `main`'s history from the tip) and the highest semver tag |
| `rescan` (matrix: one leg per picked image) | resolve | none (`permissions: {}`) | Trivy v0.75.0 against each image with `build-scan`'s thresholds. `fail-fast: false`, so one vulnerable image doesn't hide the other's result |

The image doesn't change between scans, but the vulnerability database does. A red run means a fix is available for something in a
published image: rebuild (merge anything to the app, or bump Go or the base image), release, and move the overlays. Scheduled workflows run only
from the default branch, and GitHub disables them after 60 days without repository activity. Neither job logs in to GHCR, and the scanner job's
`GITHUB_TOKEN` has no permissions, so the scanner has nothing worth stealing. If the package is ever made private, both jobs need `packages: read` and a GHCR login.

## `terraform-plan.yml`

| Job | Needs / runs when | Permissions | AWS | Does |
|---|---|---|---|---|
| `changes` | always | `contents: read` | none | Decides whether any Terraform or policy changed (`terraform`), and whether dev's root, its modules or the policies changed (`dev`). Manual runs and new branches run everything |
| `fmt` | Terraform changed | `contents: read` | none | `terraform fmt -check -recursive -diff` over `Acme/infra/terraform` |
| `validate` (matrix over `account`, `ci-iam`, `environments/dev`, `state-backend`) | Terraform changed | `contents: read` | none | `init -backend=false`, `validate` |
| `module-test` (matrix over modules with a `tests/` directory: `vpc`) | Terraform changed | `contents: read` | none | `init -backend=false`, `terraform test` with the AWS provider mocked: no credentials, nothing created |
| `validate-result` | always | `contents: read` | none | Aggregates `validate` and `module-test` |
| `policy` | Terraform changed | `contents: read` | none | Installs Conftest 0.71.1 (SHA-256 checked), runs `conftest verify`, and requires exactly 3 denies and 1 warning from the seeded bad plan |
| `plan-dev` | dev changed, on a same-repo PR or a manual run; needs fmt, validate-result, policy | `contents: read`, `id-token: write` | **plan role**, `sub = …:pull_request` or `…:ref:refs/heads/<main or mod*>` | Calls `terraform-plan-reusable.yml` for `environments/dev` |
| `plan-result` | always | `contents: read` | none | Aggregates `plan-dev` |
| `comment` | same-repo `pull_request`, after plan-dev, unless plan-dev was cancelled | `pull-requests: write` only | none | Posts the text plan as one PR comment (marker `<!-- terraform-plan:dev -->`), edited in place on later pushes, truncated at 60,000 characters. If the latest plan failed or was skipped, it replaces the old plan with a "no current plan for `<sha>`" note, so an older commit's plan never looks current (a skipped plan with no earlier comment posts nothing) |

- **Pushes to `main`/`mod*`** run fmt, validate and policy only: no plan, no OIDC token.
- **Fork PRs skip `plan-dev`**, because GitHub gives them no OIDC token. The post-merge plan in `terraform-apply.yml` still shows the reviewer the plan before anything is applied.
- **The comment job has no AWS access and the plan job can't write to the PR.** The comment script reads the plan from a file and uses no
  `${{ }}` expressions, so nothing a PR controls (branch name, title, plan text) is interpolated into code.
- **Concurrency:** per PR number (or ref), cancelling older PR runs. Safe because plans never take the state lock.

## `terraform-plan-reusable.yml`

Inputs: `working-directory`, `role-arn`, `artifact-name`, `save-plan`, `destroy`, `gated`. Outputs: `has_changes`, `plan_artifact_id`.
One job, `plan`, which needs `contents: read` and `id-token: write` from its caller:

1. Assume `role-arn` through OIDC and print the identity (`assumed-role/dev-github-actions-plan/...`).
2. `terraform init`, then `terraform plan -lock=false -detailed-exitcode [-destroy] -out=tfplan`. Exit code 0 means no changes, 2 means changes.
3. Write the run summary: the counts, any resource that will be destroyed or replaced listed on its own, then the full plan.
4. Upload the redacted text plan as `<artifact-name>-text` (1 day).
5. Run the Conftest policy on `terraform show -json tfplan`, on every plan, including no-op plans.
6. Only if `save-plan` and there are changes: upload `tfplan` and `.terraform.lock.hcl` as `<artifact-name>` (1 day).

It's a whole job rather than a set of shared steps because credentials and an initialised `.terraform/` don't carry over from one job
to the next. A called workflow gets no more permissions than its caller grants, and its OIDC `sub` describes the caller's trigger, so the
trust policies are the same as if the steps were inline.

## `terraform-apply.yml`

| Job | Needs / runs when | Permissions | AWS | Does |
|---|---|---|---|---|
| `plan` | always | `contents: read`, `id-token: write` | **plan role**, `sub = …:ref:refs/heads/main` (`…:ref:refs/heads/mod…` for a dispatch from a `mod*` branch) | Calls the reusable plan with `save-plan`, `gated`, and `destroy` for a destroy dispatch |
| `apply` | `has_changes`; **`environment: dev-apply`** | `contents: read`, `id-token: write`, `actions: write` | **apply role**, `sub = …:environment:dev-apply` | Waits for approval. Then checks out the same commit, downloads `tfplan` and the lock file, `init -lockfile=readonly`, `terraform apply tfplan` (this one takes the state lock), and deletes the plan artifact |

- **The plan is visible before anyone approves.** The `plan` job has no gate and finishes first. Only then does `apply` ask for approval,
  so the reviewer reads the plan in the run summary before deciding.
- **What's applied is what was approved.** The apply uses the saved plan file, and Terraform refuses it if the state changed since ("Saved plan is stale").
- **No changes, no approval request.** `has_changes = false` skips `apply`.
- **Write credentials exist only after approval**, and only for that job.
- **Concurrency:** `terraform-dev-apply`, never cancelled. A newer run waits for the current one; GitHub keeps only the newest pending run.
- **The saved plan is unredacted** and the repo is public, so it lives one day at most and is deleted after a successful apply. Today it holds
  only resource IDs and ARNs. Revisit before `enable_eks = true`, or before any secret enters dev's state: keep the plan in the private state
  bucket, or re-plan inside the approved job.

### Approve or reject an apply
1. Open the run from the Actions tab (or the "Review deployments" notification).
2. Read the **plan** job's summary: the counts first, then anything destroyed or replaced, then the full plan.
3. Click **Review deployments**, tick `dev-apply`, and choose **Approve and deploy** or **Reject**.
   - **Reject:** nothing is applied, and the apply role is never assumed.
   - **Approve:** the apply runs the saved plan. If the state changed meanwhile, it fails as stale; re-run the workflow to re-plan.
   - **Ignore it:** the request expires after 30 days. Nothing is applied.
   - **Approve within 24 hours.** The saved plan artifact is kept for 1 day only. After that, approving fails at
     "Download the saved plan" and nothing is applied; re-run the workflow to plan again.

### Tear dev down, or re-apply
`gh workflow run terraform-apply.yml --ref main -f action=destroy` (or `-f action=apply`). It goes through the same gate. A dispatch from a `mod*`
branch can plan, but the environment refuses its apply job before any credential exists. From any other branch, the plan role refuses
the plan too.

With `enable_eks = false` (the default), dev is only the VPC: subnets, internet gateway and route tables, with no NAT gateway, so it costs
nothing. With EKS on it costs about $0.20/hour, and the apply role can't create it yet (see [`docs/iam.md`](iam.md#before-eks-is-in-scope)).

## The four-way coupling rule

Every AWS-facing job depends on four settings in four places agreeing with each other:

| | Lives in | Example (plan on a PR) | Example (apply) |
|---|---|---|---|
| 1. The trigger, or `environment:` | the workflow file | `on: pull_request` | `environment: dev-apply` |
| 2. The OIDC `sub` GitHub sends for it | (follows from 1) | `repo:…:pull_request` | `repo:…:environment:dev-apply` |
| 3. The IAM trust policy | `Acme/infra/terraform/ci-iam/main.tf` (human-applied) | `trust_pull_requests = true` | `apply_environment = "dev-apply"` |
| 4. Who can produce that `sub` | GitHub rulesets and environments, `Acme/infra/github/` | any same-repo PR (accepted for a read-only role) | the `dev-apply` reviewer and its `main`-only branch policy |

Change one and check the other three. A mismatch fails closed (the assume-role step gets `Not authorized to perform sts:AssumeRoleWithWebIdentity`)
unless you widen the trust beyond what GitHub protects, which fails open. That's why the trust list and the rulesets are changed together.

## Testing the Terraform

`fmt` and `validate` only check syntax. Four kinds of test check behaviour, from cheapest to most real:

| Test | Where | Runs | Proves |
|---|---|---|---|
| Policy (Conftest) | `Acme/infra/policies/terraform/` | CI, on every plan | The plan breaks no security rule (today: no SSH from the internet). See below |
| Module unit tests (`terraform test`) | `modules/<name>/tests/*.tftest.hcl` | CI (`module-test`), on every Terraform change | The module's logic with a **mocked** AWS provider: NAT only when asked for (cost), one subnet of each kind per zone, the internet route, the caller's tags on every resource (the apply role requires `Environment`). Each test was checked to fail against a deliberately broken copy |
| Integration test (Terratest) | [`Acme/infra/terraform/test/`](../Acme/infra/terraform/test/README.md) | By hand from WSL, as a human | That AWS accepts the module and the routing really works: builds the VPC module in real AWS, checks it through the AWS API, destroys it |
| Plan as a test | `plan-dev`, and `terraform-apply.yml`'s `has_changes` | CI | A plan with no changes skips the approval; an unexpected diff shows up in the PR comment before merge |

Terratest isn't in CI because it needs AWS **write** access, which no PR job has by design. Its README says what running it in CI
would need.

## Policy checks (Conftest)

`Acme/infra/policies/terraform/` holds the rules every plan must pass. Today that's one: no security group may allow SSH from `0.0.0.0/0`
or `::/0`. The rules, their tests, the seeded bad plan and how to run them locally are in
[its README](../Acme/infra/policies/terraform/README.md). A policy failure fails the plan job, and the apply never gets a plan to use.
Fix the Terraform; don't skip the policy.

## Rolling back

| What went wrong | Roll back by |
|---|---|
| A bad app change merged | Revert the PR. CI builds, scans and publishes a new `sha-*` image. Releases are untouched until you tag |
| A bad release is deployed | Move the overlays' `newTag` back to the previous release in a PR. Every release tag stays in GHCR |
| A bad infrastructure change was applied | Revert the PR. The merge produces a new plan to undo it, which you approve like any other. Old state versions are kept in the versioned S3 bucket if you need to inspect them |
| An apply failed halfway | Terraform releases the lock and state records what was done. Fix forward in a PR, or revert |
| A runner died mid-apply and the lock is still held | From WSL: `terraform force-unlock <lock id>` in `Acme/infra/terraform/environments/dev` (the ID is in the error message). Only once you're sure no apply is running |
| A workflow change broke CI | Revert the PR. The required checks run on the revert too |

## Runners: GitHub-hosted vs self-hosted

Every job here runs on GitHub-hosted `ubuntu-24.04` runners. That's a deliberate choice, not a default left alone:

| | GitHub-hosted (used here) | Self-hosted |
|---|---|---|
| **Cost** | Standard runners are free for public repositories. Private repositories use the plan's included minutes, then pay per minute | No per-minute charge, but you pay for the machine, its network and your time running it. Worth it for long builds, special hardware (GPUs, Arm) or private-network access |
| **Queue time** | Usually seconds. A busy period or the account's concurrent-job limit (20 on the Free plan) can make jobs wait | Instant when a runner is idle; jobs queue behind each other when every runner is busy. You size the pool |
| **Security isolation** | A fresh VM for every job, destroyed afterwards. Nothing a job leaves behind reaches the next one | The machine persists between jobs unless you build ephemeral runners. **On a public repository, a fork's pull request can run its own code on your machine.** GitHub recommends against self-hosted runners for public repos for that reason |
| **Who patches it** | GitHub updates the runner images (OS, toolchains) on a regular cycle | You patch the OS and installed tools. The runner application updates itself |
| **Network** | Public internet only; reaches AWS through public endpoints | Can sit inside a VPC and reach private endpoints. That's the usual reason to self-host |

This repository is public and nothing here needs private-network access, so GitHub-hosted runners are cheaper, safer and less work.
Revisit if a job needs to reach a private EKS endpoint, or builds get long enough for runner minutes to cost real money.

## Setting up the GitHub side from scratch

All of these are captured in `Acme/infra/github/` and are applied by hand (CI has no permission to change them):

```bash
R=repos/kerinschristopher/Curriculum
gh api -X POST $R/rulesets --input Acme/infra/github/trusted-branches-ruleset.json
gh api -X POST $R/rulesets --input Acme/infra/github/main-merge-gate-ruleset.json
gh api -X PUT  $R/environments/dev-apply --input Acme/infra/github/dev-apply-environment.json
gh api -X POST $R/environments/dev-apply/deployment-branch-policies --input Acme/infra/github/dev-apply-branch-policy.json
```

To update an existing ruleset, use `-X PUT $R/rulesets/<id>` instead (`gh api $R/rulesets` lists the IDs). The GHCR package's Actions access
(see [Releasing an image](#releasing-an-image)) can only be set on the package's settings page.
