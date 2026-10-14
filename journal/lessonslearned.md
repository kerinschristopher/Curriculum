# Lessons learned: where Claude's assumptions were wrong

Every time something Claude assumed, wrote or recommended turned out to be wrong: it caused an error or a block, or it
had to be amended. Mistakes I made myself, and claims by the reviewer, are left out unless Claude repeated or acted on
them.

**Sources:**
- **The 2026-10-13/14 session** (PR #9 review fixes, merge and first apply), written from the full session.
- **Earlier sessions** (2026-10-06 to 2026-10-13), from the "Deviations", "Review follow-ups" and "Final code
  review" sections of `docs/design/module-6-ci-cd.md`, plus a keyword search of the saved session transcripts. That
  search looked for phrases like "that was wrong", "correction" and "not reproduced". It will have missed corrections
  that were made quietly, so treat the earlier part as incomplete.

## Patterns
1. **The simulator, mocks and local runs prove logic, not what AWS or GitHub actually does.** The two most expensive
   misses (the apply role's parent-VPC check and the empty `requirements.txt`) both passed every local check. In each
   case, the first *real* run of the path found the problem.
2. **"It passed here" can depend on the environment.** A venv that already had pytest, a WSL Docker with a different
   image store from the runner, and Terraform installed only in WSL all behaved differently from what Claude assumed.
3. **Read and verify before asserting.** Several corrections were confident statements about state (that dev was
   deployed, that GHCR returns 404, that the first rescan finds no `sha-*`) that a 10-second read-only check would
   have settled.
4. **Run the tool rather than imitate it.** Hand-written `terraform fmt` alignment and hand-written validation
   conditions failed the real tool.
5. **A check whose job never started proves nothing.** A skipped `apply` job never asks for its environment, and a
   rejected one runs none of its steps, cleanup included. Before calling a check passed, confirm that the path it's
   meant to exercise actually ran.

---

## 2026-10-13/14: PR #9 review fixes, merge and first apply

| # | What Claude assumed or wrote | What actually happened | How it surfaced | Fix |
|---|---|---|---|---|
| 1 | **The apply role could create subnets and route tables**, because `Ec2CreateTagged` allowed creates carrying `aws:RequestTag/Environment = dev`. The 96/96 simulator run was taken as proof | AWS also checks `CreateSubnet` and `CreateRouteTable` against the **parent VPC** (and `CreateNatGateway` against its subnet and Elastic IP), and `aws:RequestTag` doesn't exist for a parent. The first CI apply created the VPC and IGW, then got 403 on all 4 subnets and both route tables | **The first real `terraform-apply` after merging PR #9** (run `38492865526`) | New `Ec2CreateInOwned` statement (parent ARNs, `aws:ResourceTag`), 6 new simulator cases with the parent ARN, `ci-iam` applied. Lesson: the simulator answers only the questions the cases ask, and every create case used resource `*` |
| 2 | **Risk R12** (earlier session): "the role can create a dev subnet inside a VPC it doesn't own" | The premise was backwards: the role couldn't create inside **any** VPC (#1). The mitigation R12 wrote down ("split the statement by ARN, confirm with the simulator which ARNs each action is checked against") was exactly the fix, but it was deferred instead of tested | Same failed apply | R12 closed by the same fix. Lesson: when a risk says "confirm which ARNs are checked", confirm it before merging, not after |
| 3 | **The committed `.github/scripts/tests/requirements.txt` contained `pytest==9.1.1`.** Local pytest passing was taken as proof | The file was committed **empty** (unsaved in the editor). Locally, the IAM tests' venv already had pytest, so the file was never used. In CI, `scripts-test` built a fresh venv and failed with `pytest: No such file or directory` (exit 127) | First CI run after the push | Follow-up commit with the contents. Lesson: check `git diff --cached` (or `git show`) for every file before committing, and test the install path, not just the tests |
| 4 | **The "No sha-* image" warning path would be the first nightly rescan after merge** (said in chat and written into the design doc) | The merge's own `push` job publishes `sha-<merge commit>`, so a rescan after that picks **two** images. The one-image path only happens before that push lands, or if it fails. The handoff note also expected `0.1.1` to be **red** in Trivy, where Claude had said "green unless Trivy finds something" | Re-reading `module-6-handoff.md` while updating it | The handoff and the design doc were corrected in the #1 commit |
| 5 | The `.github/scripts` tests' comment said **"GHCR sends `null`"** for no tags | Never observed. GHCR wasn't checked with a no-tags package; `null` is just what a registry *can* send | Claude's own review before commit | Reworded to "a registry can send null" |
| 6 | **The handoff note** (written by Claude in an earlier session) said a missing package "might be a 404" | GHCR answers **403 DENIED** at the *token* request, before the tag list is ever fetched | Read-only `curl` against GHCR | Recorded in the design doc and in `docs/ci.md` |
| 7 | **The handoff's reproduction plan for #1**: `docker load` plus `docker push` locally, the way CI does | WSL runs Docker 29 with the **containerd** image store, and the runner runs Docker 28 with the **classic** store, so a local push can produce a different kind of image (an index rather than a plain manifest) | Comparing `docker info` locally with the runner's build log | The reproduction pushed with `buildx … oci-mediatypes=false`, and the tests push hand-built manifests over the registry API |
| 8 | **`imagetools create --dry-run` could check the digest before pushing**, so a bad release tag could never be pushed | `--dry-run` prints re-formatted JSON, not the exact bytes, so its hash never matches | Step 4 of the hand reproduction | The check stays after the push, and the error explains how to delete the bad tag |
| 9 | **The planned release-tag cases included rejecting `1.2.3` (no `v`)** | The script strips an optional `v`, so it accepts `1.2.3`; only the workflow's `v*` trigger keeps it out | Noticed while writing the tests, before running them | That case was dropped and replaced by `vnext` |
| 10 | **The `Ec2CreateInOwned` snippet was hand-aligned** (`sid     = …`) | `terraform fmt` only aligns consecutive single-line arguments. Committed, it would have failed the required `fmt` check | `terraform fmt -check` before committing | Run `terraform fmt` rather than hand-aligning |
| 11 | **The PR description used relative links** (`../blob/mod6/…`) | Unverified whether they resolve from a PR page | Claude's own check while applying | Changed to absolute URLs |
| 12 | **The PR stays a draft until the user says it's ready**, so the post-merge checks were treated as later work | PR #9 was merged that evening, so "post-merge" became the very next run, and #1 surfaced straight away | The failed apply run on `main` | Lesson: post-merge risks are pre-merge risks for whoever clicks merge. Rehearse the first real run where possible (here, apply-role cases with the parent ARNs) |
| 13 | **`gh workflow run terraform-apply.yml --ref mod6` would test that the environment refuses `mod6`** | Dev already matched the code, so the plan had no changes and `apply` (`if: has_changes`) was **skipped**. It never asked for `dev-apply`, and the run went green without testing anything (run `38502992425`) | The user asked why it "worked without issue"; the job list showed `apply: skipped` | Re-run with `-f action=destroy`, so the plan has changes and `apply` must try to start: refused with "Branch "mod6" is not allowed to deploy to dev-apply" (run `38503255363`), and AWS unchanged |
| 14 | **The saved plan "is deleted after every apply attempt, failed ones included"** (`docs/ci.md`, and risk R4 relies on it). The delete step's `if: always()` was taken to cover every outcome | The delete is a step *inside* the `apply` job. A rejected run (`38504172153`) or one refused by the environment (`38503255363`) never starts that job, so no step runs and the unredacted `tfplan-dev` stays downloadable for its 1-day retention | Checking the artifacts after the reject test | Fixed on `mod7`: the delete is its own `cleanup` job (`needs: [plan, apply]`, `if: always()` and a saved plan, `actions: write` only), guarded by `test_terraform_apply_workflow.py`, and the docs are corrected. The two leftover artifacts were left to expire. Low impact today (no secrets in dev's plan) |

### Also learned this session (not errors, but surprises)
- An index source keeps its digest under `imagetools create` with the default settings; only a plain manifest gets
  wrapped. So testing against GHCR's `0.1.1` (an index) would never have shown the release-tag bug.
- `scripts-test` was advisory until made a required check. A test that doesn't gate the merge only offers advice.

---

## Earlier sessions (2026-10-06 to 2026-10-13)

| Date | What Claude assumed or wrote | What actually happened | How it surfaced | Fix |
|---|---|---|---|---|
| 10-06 | **A dev `terraform plan` would show no changes** after a fix | Dev wasn't deployed, so every plan showed the full build (55 adds) | The plan output | Claude corrected itself: the plan can't confirm the fix while dev is torn down; look for the specific resource instead |
| 10-06 | **Terraform could be run from the Windows side** | Terraform, aws and gh are only installed in WSL, so `fmt` and `validate` couldn't run and the policy rewrite went in untested | Command not found | Recorded in memory (`wsl-toolchain`). Everything now runs from WSL |
| 10-07 | **`actions/setup-go` with its default cache** in the checks workflow | "Restore cache failed: Dependencies file is not found … go.sum". The app is stdlib only and has no `go.sum` | CI warning, reported by the user | `cache: false` on setup-go; the Go *build* cache cached with `actions/cache` (`f7969b7`) |
| 10-10 | **The reviewer didn't know about the `LOG_LEVEL` and EKS endpoint fixes** | Both were acknowledged in the PR #5 approval | Re-reading the PR #5 reviews | Design doc corrected (F22, T16, T19); memory now says to read all prior PR reviews before designing |
| 10-10 | **Phase 2 apply-role policy**: denying the deletion of dev's own state implicitly was enough | Only an *implicit* deny, which a later broad Allow could override | `SIM_PLAN_JSON` tests before the apply | Explicit `DenyStateDeletes` |
| 10-10 | **"$0 per merge while EKS is off"** (Quality attributes, Cost) | The NAT gateway and its Elastic IP were created regardless and are billed hourly | Phase 3 review of the plan | The NAT gateway and EIP now follow `enable_eks` |
| 10-10 | **`plan-dev` could be a required check** | A job calling a reusable workflow reports as `plan-dev / plan` when it runs, but as `plan-dev` when it's skipped, so either name leaves some PRs waiting forever | Seen on draft PR #9 | `plan-result` aggregator, which is the required check |
| 10-10 | **One concurrency group for push runs** of `sample-api-ci` | GitHub cancels a *pending* run when a newer one queues in the same group, which could drop a merge commit's `sha-*` image | Code review | Each push run gets its own group (`3986ee9`) |
| 10-10 | **The Conftest SSH policy saw every ingress rule** | Unknown-at-plan ingress values are left out of `after` and were silently skipped; Rego also sorts `null` before numbers | Code review | Warns on unknown rules, numeric ports required (`15756a1`) |
| 10-10 | **The SSH policy matched the protocol `tcp`** | `"TCP"` (upper case) slipped through | Final code review | Case-insensitive match (`2e48d51`) |
| 10-10 | **The apply role's tag rules stopped it handing resources to another environment** | It could change or remove the `Environment` tag itself | Code review | `Ec2RetagOwned` statement (`38cb51c`) |
| 10-10 | **The rescan's `pick` step failed cleanly when curl failed** | Without `pipefail`, a failed curl fed jq empty input and ended in a misleading "Nothing to scan" | Code review, then a local run | `set -euo pipefail` (`7292655`) |
| 10-10 | **Cross-variable validation would work on any Terraform** | It needs Terraform 1.9+ | Code review | `required_version` raised (`ed21913`) |
| 10-10 | **The docs matched the code after each phase** | A doc audit found wording that later commits had overtaken (for example, the plan artifact is deleted after *every* apply attempt, not only successful ones) | Three doc audits | `508629e`, `00f3a7d` |
| 10-13 | **`enable_eks` as a local could be checked in a variable validation that only read locals** | Terraform requires a variable's validation to refer to that variable: "Invalid variable validation condition … must refer to var.eks_public_access_cidrs" | `terraform validate` | The condition reads `var.eks_public_access_cidrs` |
| 10-13 | **(Reviewer's claim, checked by Claude)**: import blocks "break every later plan" | Not reproduced: a plan with the blocks still present gave "No changes". Listed here because the lesson is the same: reproduce before you fix | `terraform plan` in two roots | The blocks were removed anyway (`2fc07e6`) |
