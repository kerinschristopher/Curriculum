# CI/CD

What runs on GitHub Actions, when, with which permissions, and which AWS role (if any) each job assumes. The IAM side
(trust policies, permission statements and why each exists) is in [`docs/iam.md`](iam.md). The design and its evidence are in
[`docs/design/module-6-ci-cd.md`](design/module-6-ci-cd.md).

## At a glance

| Workflow | Runs on | AWS role | What it's for |
|---|---|---|---|
| [`sample-api-ci.yml`](../.github/workflows/sample-api-ci.yml) | `pull_request`; `push` to `main`/`mod*`; `push` of `v*` tags | none | Lint, test, render manifests, build and scan the image; publish it from `main`; add release tags |
| [`sample-api-rescan.yml`](../.github/workflows/sample-api-rescan.yml) | `schedule` (daily 06:17 UTC); `workflow_dispatch` | none | Re-scan the published images against today's vulnerability database |
| [`terraform-plan.yml`](../.github/workflows/terraform-plan.yml) | `pull_request`; `push` to `main`/`mod*`; `workflow_dispatch`; `schedule` (daily 05:41 UTC) | plan role (PRs, manual runs and the nightly run only) | fmt, validate, module and policy tests; plan dev and comment the plan on the PR; nightly drift check |
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
| `scripts-test` | sample-api-ci | shellcheck and the pytest tests for `.github/scripts/` pass, against a throwaway registry (or no file those tests read changed) |
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

**Until Module 7 the required checks never decided a merge.** Direct pushes to `main` were refused, as intended. But the older
`trusted-branches` ruleset also covered `main`, and its `update` rule refuses every update to `main` by anyone without its bypass.
A merge is an update, so every PR, however green, was refused, and each one merged into `main` (#9, #11-#14) went in with the admin
bypass, which skips the required checks too. Module 7 splits it (`test_branch_rulesets.py` guards the files): `trusted-branches`
keeps its four rules on `mod*` only, and a new `main-protect` ruleset keeps `creation`, `deletion` and `non_fast_forward` on `main`,
with no `update` rule. **Pending the live apply:** the JSON files describe the split, but GitHub changes only with `gh api` (create
`main-protect`, then PUT `trusted-branches`), done just before PR #15 merges. From then on the checks above decide merges. What that
allows, and the gaps left open, are in `docs/iam.md` ("GitHub-side controls").

## `sample-api-ci.yml`

| Job | Needs / runs when | Permissions | Does |
|---|---|---|---|
| `changes` | not on tags | `contents: read` | Decides whether `Acme/apps/sample-api/`, `Acme/platform/`, Flux's bootstrap manifests (`Acme/infra/bootstrap/`, Module 8), this workflow or one of the image scripts Module 7 adds (`oci-platform-layout.sh`, `push-oci.sh`) changed (`app`), and whether a file a test in `.github/scripts/tests` reads changed (`scripts`). Both are tested there. A new branch or force-push (no usable base) runs everything |
| `lint` | app changed | `contents: read` | gofmt, `go vet ./...`, golangci-lint v2.14.0, on the Go version in `go.mod`. Restores the Go build cache first (see "Caching") |
| `test` (matrix `go: ["1.26", "1.27"]`) | app changed | `contents: read` | `go test -race -count=1 -v ./...` on both supported Go releases, with a Go build cache per version |
| `test-result` | always (not on tags) | `contents: read` | Fails unless `changes` succeeded and every `test` leg passed or was skipped |
| `kustomize` | app changed | `contents: read` | `kubectl kustomize` over the sample-api base, its overlays, the infrastructure overlays and the apps entry points (`Acme/platform/apps/overlays/*`, Module 8) |
| `scripts-test` | `.github/scripts/`, any workflow file in `.github/workflows/`, a ruleset JSON in `Acme/infra/github/` or a sample-api `Dockerfile` changed: every file a test there reads, so a change to one alone can't skip the test that would catch it | `contents: read` | shellcheck over `.github/scripts/*.sh`, then pytest 9.1.1 (in a venv) over `.github/scripts/tests`, with a throwaway `registry:3` service container (pinned by digest) for the release-tag tests. `REQUIRE_REGISTRY=1` makes a missing registry a failure, not a skip. Runs every branch of `pick-rescan-images.sh` and `release-tag.sh`, including ones the daily rescan and the once-per-release job have never hit. Not a `build-scan` dependency |
| `build-scan` | app changed; needs changes, lint, test-result, kustomize | `contents: read` only: no secrets, no OIDC | Builds the image once (`VERSION=sha-<short>` via `-ldflags`) to a tarball and scans the tarball with Trivy v0.75.0 (`HIGH,CRITICAL`, `ignore-unfixed`, exit code 1). Caches the image layers and the Go build cache inside the build, but not Trivy (see "Caching"). On `push` to `main` only, uploads the tarball (1 day) |
| `push` | `push` to `main`, after build-scan | `contents: read`, `packages: write` | Downloads the scanned tarball, `docker load`, logs in to GHCR with the run's `GITHUB_TOKEN`, pushes `ghcr.io/kerinschristopher/sample-api:sha-<short>`. Never checks out code, so what's pushed is what was scanned |
| `release-tag` | `push` of a `v<major>.<minor>.<patch>` tag | `contents: read`, `packages: write` | Runs `.github/scripts/release-tag.sh`: adds the `<major>.<minor>.<patch>` tag to the existing `sha-<short>` image of the tagged commit with `imagetools create --prefer-index=false`, which copies the manifest as-is (the default wraps it in a new index with a new digest), then checks both tags have the same digest. Nothing is rebuilt. Checks out only `.github/scripts`. Tested by `scripts-test`; the job itself first runs on a real `v*` tag |

- **No `latest` tag.** A moving label lets nodes with cached images run different builds under one name.
- **No AWS access at all**, so the `pull_request` trigger is safe here.
- **Concurrency:** on a PR, `sample-api-ci-<ref>`, and a new push to the same PR cancels the old run. Every push run (to `main`, `mod*`
  or a tag) gets a group of its own (`sample-api-ci-<run id>`), so it is never cancelled, not even while pending. One shared group
  wouldn't be enough: GitHub keeps only the newest pending run in a group, and a dropped run on `main` means a merge commit with no
  `sha-*` image to release.
- **Caching:**
  - **Go build cache (`actions/cache`), in `lint` and each `test` leg.** `~/.cache/go-build` holds compiled packages; the slowest
    part is the race-instrumented standard library. The key is the OS, the Go version (or `go.mod` for `lint`) and a hash of
    `go.mod` and every `.go` file. On a code change the restore key falls back to the newest older cache, so only changed packages
    recompile. `-count=1` still runs every test: only compiled code is reused, never test results.
  - **Not `setup-go`'s cache:** that one is keyed on `go.sum`, and this app has none (standard library only, nothing to
    download).
  - **golangci-lint** keeps its own analysis cache (the action's default).
  - **`build-scan`'s image layers (BuildKit, `type=gha`, `scope=sample-api`, `mode=max`).** Every Dockerfile step whose inputs
    didn't change is reused (`CACHED` in the log). `mode=max` also saves the builder stage, where the compile runs; `mode=min`
    would save only the final `scratch` stage. The compile step takes `VERSION=sha-<short>` as a build argument, so a new commit
    always recompiles: only a re-run of the same commit is fully cached.
  - **`build-scan`'s Go build cache, inside the image build.** The Dockerfile's `go build` mounts a cache
    (`--mount=type=cache,id=go-build-${TARGETARCH}`), but the layer cache doesn't save a mount's contents, so on a fresh runner
    it would start empty. `buildkit-cache-dance` copies it in from a runner directory before the build and back out after it,
    and `actions/cache` saves that directory under its own prefix, `go-build-docker-` (so `lint` and `test` never restore it).
    On an exact key hit (no source change) nothing new is saved and the copy back out is skipped.
  - **Not Trivy (`cache: false`, in `build-scan` and `rescan`).** The action's default, `cache: true`, caches the vulnerability
    DB per day (`cache-trivy-<date>`) and, through its `setup-trivy` step, the `trivy` program itself
    (`trivy-binary-v0.75.0-Linux-X64`). The binary's key never expires, and the DB is carried forward from day to day through
    the `cache-trivy-` restore key, while the daily rescan keeps restoring both; a hit is used without any check: a poisoned DB or a trojaned `trivy` saved there would scan every later build, and "pinned to v0.75.0"
    would only name the cache key (G1b, G1c in the design doc). Module 7 tried it and measured no gain (see "Run times"), so it
    was removed after the PR #15 review (design doc Q9). Now each run downloads the DB and the pinned Trivy release fresh. The
    switch is set explicitly, and a test requires it, because the action's default is `true`.
  - **Any step in a job can write the cache.** The cache service takes the runner's own token, which `permissions:` doesn't
    limit, so `build-scan`'s `contents: read` is no defence here: a compromised step in it could poison the layer cache or the
    Go build cache. The pushed image is still exactly the scanned one; the risk is to the builds that start from that cache
    later (G1 in the design doc).
  - **Scope:** a run can restore the caches its own branch saved and those `main` saved; a PR's caches are visible only to that
    PR. GitHub evicts caches unused for 7 days, and the oldest ones once the repository passes 10 GB.
  - **The first run on `main` after a merge is partly warm, not cold.** Module 6 said it was cold, reasoning that a PR's caches
    never reach `main`. That part is true, but `main` saves caches on every run of its own, and a branch can read what it saved
    for 7 days. So the merge commit's run misses on the exact keys (the merged source is new to `main`), then falls back to
    `main`'s newest earlier entries:
    - the Go build caches in `lint`, `test` and `build-scan` through their `restore-keys` prefixes, so only the changed packages
      recompile;
    - the image layers through the shared `scope=sample-api`, so every Dockerfile step before the first changed input is reused.

    It's fully cold only when `main` has no earlier entry to fall back on: the first run after the merge that adds a cache (for
    the image caches, the first run after Module 7's PR A merges), after 7 days with no run on `main`, or after the entries
    were deleted (`gh cache delete`) or evicted by the 10 GB limit.
- **Images are amd64 only.** Multi-arch is designed (Module 7's PR C) but postponed until after Module 8 and later modules.

### Releasing an image
1. Merge to `main` and wait for a green `sample-api-ci` run. GHCR then has `sha-<short>` for the merge commit.
2. Tag that commit and push the tag: `git tag v0.2.0 <commit> && git push origin v0.2.0`.
3. `release-tag` adds `0.2.0` to the same image. If the commit has no `sha-*` image (not on `main`, or its run failed), the job fails and says so. If it fails on a digest mismatch, the release tag was already pushed: delete that package version (Packages > sample-api > versions), fix the cause and re-run the job.
4. Point the overlays' `images[]` at the release in a PR: set `newTag` to the release, and `digest` to its digest from `docker buildx imagetools inspect ghcr.io/kerinschristopher/sample-api:0.2.0 --format '{{.Manifest.Digest}}'`. Change both lines together: the runtime pulls by digest, so a new `newTag` with the old `digest` still runs the old image. Nothing deploys automatically yet (Flux arrives in Module 8).

The first push needs a one-time manual setting: in the `sample-api` package's settings, under "Manage Actions access", give the
`Curriculum` repository the **Write** role. Without it, `push` fails with a 403.

## `sample-api-rescan.yml`

| Job | Needs | Permissions | Does |
|---|---|---|---|
| `resolve` | none | `contents: read` | Lists the package's tags (anonymously: the package is public) and the last 500 commits on `main`, then runs `.github/scripts/pick-rescan-images.sh`: the newest `sha-*` image (walking `main`'s history from the tip) and the highest semver tag. No tags at all (an empty, `null` or missing `tags` list) ends in `::error::Nothing to scan`; a malformed reply fails as an error of its own. Tested by `scripts-test` in `sample-api-ci.yml` |
| `rescan` (matrix: one leg per picked image) | resolve | none (`permissions: {}`) | Trivy v0.75.0 against each image with `build-scan`'s thresholds. Like `build-scan`'s, nothing is cached: the DB and Trivy are downloaded fresh each run. `fail-fast: false`, so one vulnerable image doesn't hide the other's result |

The image doesn't change between scans, but the vulnerability database does. A red run means a fix is available for something in a
published image: rebuild (merge anything to the app, or bump Go or the base image), release, and move the overlays. Scheduled workflows run only
from the default branch, and GitHub disables them after 60 days without repository activity. Neither job logs in to GHCR, and the scanner job's
`GITHUB_TOKEN` has no permissions, so the scanner has nothing worth stealing. If the package is ever made private, both jobs need `packages: read` and a GHCR login.

## `terraform-plan.yml`

| Job | Needs / runs when | Permissions | AWS | Does |
|---|---|---|---|---|
| `changes` | always | `contents: read` | none | Decides whether any Terraform or policy changed (`terraform`), and whether dev's root, its modules or the policies changed (`dev`). Manual runs, the nightly run and new branches run everything |
| `fmt` | Terraform changed | `contents: read` | none | `terraform fmt -check -recursive -diff` over `Acme/infra/terraform` |
| `validate` (matrix over `account`, `ci-iam`, `environments/dev`, `state-backend`) | Terraform changed | `contents: read` | none | `init -backend=false`, `validate` |
| `module-test` (matrix over folders with a `tests/` directory: `modules/vpc`, `environments/dev`) | Terraform changed | `contents: read` | none | `init -backend=false`, `terraform test` with the AWS provider mocked: no credentials, nothing created |
| `validate-result` | always | `contents: read` | none | Aggregates `validate` and `module-test` |
| `policy` | Terraform changed | `contents: read` | none | Installs Conftest 0.71.1 (SHA-256 checked), runs `conftest verify`, and requires exactly 3 denies and 1 warning from the seeded bad plan |
| `plan-dev` | dev changed, on a same-repo PR, a manual run or the nightly run; needs fmt, validate-result, policy | `contents: read`, `id-token: write` | **plan role**, `sub = …:pull_request` or `…:ref:refs/heads/<main or mod*>` (the nightly run is always `main`) | Calls `terraform-plan-reusable.yml` for `environments/dev` |
| `plan-result` | always | `contents: read` | none | Aggregates `plan-dev` |
| `drift` | nightly run only, after plan-dev | `contents: read` | none | Fails unless the plan is empty (`has_changes` is `false`): dev has drifted from `main`. The plan is in `plan-dev`'s run summary. Not a required check |
| `comment` | same-repo `pull_request`, after plan-dev, unless plan-dev was cancelled | `pull-requests: write` only | none | Posts the text plan as one PR comment (marker `<!-- terraform-plan:dev -->`), edited in place on later pushes, truncated at 60,000 characters. If the latest plan failed or was skipped, it replaces the old plan with a "no current plan for `<sha>`" note, so an older commit's plan never looks current (a skipped plan with no earlier comment posts nothing) |

- **Pushes to `main`/`mod*`** run fmt, validate, module-test and policy (and their aggregators) only: no plan, no OIDC token.
- **Fork PRs skip `plan-dev`**, because GitHub gives them no OIDC token. The post-merge plan in `terraform-apply.yml` still shows the reviewer the plan before anything is applied.
- **The comment job has no AWS access and the plan job can't write to the PR.** The comment script reads the plan from a file and uses no
  `${{ }}` expressions, so nothing a PR controls (branch name, title, plan text) is interpolated into code.
- **Concurrency:** per PR number (or ref), cancelling older PR runs. Safe because plans never take the state lock.
- **The nightly drift check** (`schedule`, 05:41 UTC) plans dev from `main` and fails if anything would change: someone changed dev
  outside Terraform, or a merge's apply was never approved.
  - **Red until the first approval:** it stays red until the first approved `terraform-apply` creates dev.
  - **`main` only:** scheduled workflows run only from the default branch, so it starts once this file is on `main`.
  - **Inactivity:** GitHub disables scheduled workflows after 60 days without repository activity.
  - **Fixing drift:** fix AWS, or the code (merge, then approve the apply). Re-run the workflow to confirm.

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
| `apply` | `has_changes`; **`environment: dev-apply`** | `contents: read`, `id-token: write` | **apply role**, `sub = …:environment:dev-apply` | Waits for approval. Then checks out the same commit, downloads `tfplan` and the lock file, `init -lockfile=readonly`, `terraform apply tfplan` (this one takes the state lock) |
| `cleanup` | `plan` and `apply`; `always()`, when a plan was saved (`plan_artifact_id` set) | `actions: write` | none | Deletes the saved plan artifact whatever `apply`'s result: applied, failed, rejected or refused by the environment. It's a separate job because a step inside `apply` never runs when `apply` never starts |

- **The plan is visible before anyone approves.** The `plan` job has no gate and finishes first. Only then does `apply` ask for approval,
  so the reviewer reads the plan in the run summary before deciding.
- **What's applied is what was approved.** The apply uses the saved plan file, and Terraform refuses it if the state changed since ("Saved plan is stale").
- **No changes, no approval request.** `has_changes = false` skips `apply`.
- **Write credentials exist only after approval**, and only for that job.
- **Admin bypass:** `dev-apply` has `can_admins_bypass: true`, so a repository admin can deploy without the reviewer's approval.
  Today the only admin is also the only reviewer, so this changes nothing; set it to `false` when someone else joins
  (recorded in [`docs/iam.md`](iam.md#github-side-controls)).
- **Concurrency:** `terraform-dev-apply`, never cancelled. A newer run waits for the current one; GitHub keeps only the newest pending run.
- **The saved plan is unredacted** and the repo is public, so it lives one day at most and is deleted by the `cleanup` job after every run that saved one, including rejected and refused runs. Today it holds
  only resource IDs and ARNs. Revisit before `enable_eks = true`, or before any secret enters dev's state: keep the plan in the private state
  bucket, or re-plan inside the approved job.

### Approve or reject an apply
1. Open the run from the Actions tab (or the "Review deployments" notification).
2. Read the **plan** job's summary: the counts first, then anything destroyed or replaced, then the full plan.
3. Click **Review deployments**, tick `dev-apply`, and choose **Approve and deploy** or **Reject**.
   - **Reject:** nothing is applied, and the apply role is never assumed. `apply` shows as failed, and `cleanup` still deletes the saved plan.
   - **Approve:** the apply runs the saved plan. If the state changed meanwhile, it fails as stale; re-run the workflow to re-plan.
   - **Ignore it:** the request expires after 30 days. Nothing is applied.
   - **Approve within 24 hours.** The saved plan artifact is kept for 1 day only. After that, approving fails at
     "Download the saved plan" and nothing is applied; re-run the workflow to plan again.

### Tear dev down, or re-apply
`gh workflow run terraform-apply.yml --ref main -f action=destroy` (or `-f action=apply`). It goes through the same gate. A dispatch from a `mod*`
branch can plan, but the environment refuses its apply job before any credential exists. From any other branch, the plan role refuses
the plan too.

With `local.enable_eks = false` (set in `environments/dev/main.tf`, and not overridable), dev is only the VPC: subnets, internet gateway and route tables, with no NAT gateway, so it costs
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
| Module unit tests (`terraform test`) | `modules/<name>/tests/*.tftest.hcl` | CI (`module-test`), on every Terraform change | The module's logic with a **mocked** AWS provider: NAT only when asked for (cost), one subnet of each kind per zone, the internet route, the caller's tags on every resource (the apply role requires `Environment`), and a single zone is rejected. Each test was checked to fail against a deliberately broken copy |
| Root unit tests (`terraform test`) | `environments/dev/tests/*.tftest.hcl` | CI (`module-test`), on every Terraform change | That EKS is off unless the committed code turns it on, and that `enable_eks = true` from outside (as a tfvars file would set it) is ignored. The mocked provider needs real-looking values for `aws_caller_identity`, `aws_iam_policy_document` and `aws_partition` once EKS is in the plan. Checked red against the old variable, then green |
| Integration test (Terratest) | [`Acme/infra/terraform/test/`](../Acme/infra/terraform/test/README.md) | By hand from WSL, as a human | That AWS accepts the module and the routing really works: builds the VPC module in real AWS, checks it through the AWS API, destroys it |
| Plan as a test (the curriculum's "minimum viable test") | `plan-dev`, `drift`, and `terraform-apply.yml`'s `has_changes` | CI; `drift` nightly on `main` | The plan is empty when nothing should change: the nightly `drift` job fails on any diff between `main` and dev's real state. A plan with no changes also skips the approval, and an unexpected diff shows up in the PR comment before merge |

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
| A bad release is deployed | Move the overlays' `newTag` and `digest` back to the previous release in a PR (both lines, as in "Releasing an image", step 4). Every release tag stays in GHCR |
| A bad infrastructure change was applied | Revert the PR. The merge produces a new plan to undo it, which you approve like any other. Old state versions are kept in the versioned S3 bucket if you need to inspect them |
| An apply failed halfway | Terraform releases the lock and state records what was done. Fix forward in a PR, or revert |
| A runner died mid-apply and the lock is still held | From WSL: `terraform force-unlock <lock id>` in `Acme/infra/terraform/environments/dev` (the ID is in the error message). Only once you're sure no apply is running |
| A workflow change broke CI | Revert the PR. The required checks run on the revert too |

## Run times

Measured on draft PR #9 (2026-10-10), wall clock from the first job's start to the last job's end. The target is PR feedback in under
5 minutes for either pipeline (design doc NFR1).

**Before any Go caching** (13 pushes): `sample-api-ci` 89-117 s, `terraform-plan` 71-90 s (plan of dev with EKS off).

**Go build cache, cold vs warm.** Run `38082108439`: attempt 1 had an empty cache, attempt 2 re-ran the same commit:

| Job / step | Cold | Warm |
|---|---|---|
| `test (1.26)`: job / `go test` step | 35 s / 26 s | 11 s / 5 s |
| `test (1.27)`: job / `go test` step | 42 s / 23 s | 17 s / 2 s |
| `lint`: job / `go vet` step | 34 s / 19 s | 15 s / 4 s |
| `build-scan` (no cache yet) | 44 s | 48 s |
| Whole `sample-api-ci` run | 100 s | 82 s |

- **The compile was most of each Go job**, and the cache removes it (the race-instrumented standard library above all). The whole
  run gains less, because `build-scan` waits for `lint`, `test` and `kustomize` and then takes about 45 s itself: it is now the
  longest step in the chain. Shortening it is Module 7's BuildKit cache (below).
- **When the warm path applies:** re-runs, later pushes to the same PR (through the restore key), and later runs on `main`, which
  restore `main`'s own earlier caches. Module 6 wrote here that the first run on `main` after a merge is cold: it's partly warm
  (see "Caching" under `sample-api-ci.yml`).

**`build-scan` with image caches (Module 7).** Each sample, with its run ID, attempt and the caches that existed before it, is a row
in [`ci-timings.csv`](ci-timings.csv). "Empty" means every cache was deleted first; a "re-run" is another attempt on the same
commit; a "new commit" changes the source or the Dockerfile.

| Condition | Run / attempts | `build-scan` job | `Build image` step |
|---|---|---|---|
| No cache (baseline, n=6) | `39853501547` / 2-7 | 45-51 s | 14-18 s |
| Layer cache, empty (n=3) | `39868715776` / 2, 4, 6 | 55-67 s | 27-34 s |
| Layer cache, re-run (n=3) | `39868715776` / 3, 5, 7 | 24-33 s | 2-3 s |
| + Go cache mount, new commit (n=1) | `39884322656` / 1 | 63 s | 22 s |
| + cache-dance, new commit, nothing to restore (n=1) | `39893262563` / 1 | 50 s | 19 s |
| + cache-dance, one-line `main.go` change (n=1) | `39897448709` / 1 | 58 s | 15 s |
| + cache-dance, source unchanged (exact hit, n=1) | `39902968713` / 1 | 40 s | 8 s |

- **The layer cache pays only on a re-run of the same commit** (`Build image` 2-3 s instead of 14-18 s). A new commit always
  recompiles, because the version is a build argument of the compile step. An empty cache is about 10 s *slower* than no cache:
  the run uploads every layer.
- **The cache mount alone gains nothing in CI,** as expected: the layer cache doesn't save a mount's contents, so each fresh
  runner starts with it empty.
- **cache-dance makes the compile nearly free, but not the job.** On the one-line change the compile took 0.3 s instead of
  11.6 s, but carrying the cache cost about 19 s (restore 6 s, copy in 3 s, copy out 8 s, save 2 s). For a one-package,
  standard-library app that's about what it saves. It pays when the source didn't change (an exact hit skips the copy out and
  the save), as in a workflow-only commit. A larger Go codebase should gain more; that isn't measured here (an experiment on a
  generated codebase was planned, then dropped).
- **The Trivy DB cache gains nothing.** In run `39902968713`, attempt 1 downloaded the DB from `mirror.gcr.io` in about 5 s;
  attempt 2 restored it from the cache in about 4 s (job 40 s, then 41 s). The DB is the same file for every Trivy user, so
  caching it saves no work of ours. It also costs: about 4 s to save the DB on each branch's first run of the day, 95.6 MB per
  branch per day plus the 47.3 MB Trivy binary once per version per branch toward the 10 GB limit, and a write path for both.
  So it was removed (see "Caching").
- **n=1 rows show a direction, not a measurement.** The final numbers, n=3 for every condition, were planned for the end of
  Module 7's multi-arch work, which is postponed.

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
gh api -X POST $R/rulesets --input Acme/infra/github/main-protect-ruleset.json
gh api -X POST $R/rulesets --input Acme/infra/github/main-merge-gate-ruleset.json
gh api -X PUT  $R/environments/dev-apply --input Acme/infra/github/dev-apply-environment.json
gh api -X POST $R/environments/dev-apply/deployment-branch-policies --input Acme/infra/github/dev-apply-branch-policy.json
```

To update an existing ruleset, use `-X PUT $R/rulesets/<id>` instead (`gh api $R/rulesets` lists the IDs). The GHCR package's Actions access
(see [Releasing an image](#releasing-an-image)) can only be set on the package's settings page.
