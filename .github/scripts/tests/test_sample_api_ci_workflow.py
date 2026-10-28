"""Structural tests for sample-api-ci.yml: which changed paths turn on which jobs, and build caching.

`changes` greps the PR's changed files with one regex per output. `scripts=true` runs scripts-test,
so every file a test in this directory reads must match the scripts regex. Otherwise a change to
that file alone skips the test that would catch it, and a skipped job counts as passing.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import json
import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "sample-api-ci.yml"
REPO = WORKFLOW.parents[2]

# Files a structural test reads. Some don't exist yet: listing them now covers their tests from
# the commit that adds them. test_merge_gate_ruleset.py reads every workflow, so a new one must
# be covered too: new-workflow.yml stands for any workflow added later.
GUARDED = [
    "Acme/infra/github/main-merge-gate-ruleset.json",
    ".github/workflows/sample-api-ci.yml",
    ".github/workflows/sample-api-rescan.yml",
    ".github/workflows/terraform-apply.yml",
    ".github/workflows/terraform-plan.yml",
    ".github/workflows/terraform-plan-reusable.yml",
    ".github/workflows/new-workflow.yml",
    "Acme/apps/sample-api/Dockerfile",
    "Acme/apps/sample-api/Dockerfile.test",
    "Acme/infra/github/trusted-branches-ruleset.json",
    "Acme/infra/github/main-protect-ruleset.json",
    "Acme/platform/apps/sample-api/overlays/dev/kustomization.yaml",
    "Acme/platform/apps/sample-api/overlays/stage/kustomization.yaml",
    "Acme/platform/apps/sample-api/overlays/prod/kustomization.yaml",
    "Acme/platform/apps/overlays/dev/kustomization.yaml",
    "Acme/platform/apps/overlays/stage/kustomization.yaml",
    "Acme/platform/apps/overlays/prod/kustomization.yaml",
    "Acme/infra/bootstrap/flux-operator-values.yaml",
    "Acme/infra/bootstrap/flux-instance.yaml",
]

# One path per prefix in the app filter: the app's code, its manifests, Flux's bootstrap
# manifests (Module 8), this workflow and the
# scripts on the image's build, scan and push path. A change to any one alone must run lint,
# test, build-scan and push, not just scripts-test. Scripts are listed before they exist (L9a-7).
APP_PATHS = [
    "Acme/apps/sample-api/main.go",
    "Acme/platform/apps/sample-api/base/deployment.yaml",
    ".github/workflows/sample-api-ci.yml",
    ".github/scripts/oci-platform-layout.sh",
    ".github/scripts/push-oci.sh",
    "Acme/infra/bootstrap/kustomization.yaml",
]

# The directories Flux applies (Module 8). The required kustomize check must render each one, or a
# broken entry point merges and only fails in the cluster.
ENTRY_POINTS = [
    "Acme/platform/apps/overlays/dev",
    "Acme/platform/apps/overlays/stage",
    "Acme/platform/apps/overlays/prod",
    "Acme/infra/bootstrap",
    "Acme/platform/infrastructure/overlays/kind",
]



@pytest.fixture(scope="module")
def filters():
    """{output: regex} from the diff step's `grep -qE '<regex>' ...; then echo "<output>=true"` lines."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["changes"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "diff")
    found = re.findall(r"grep -qE '([^']+)' <<<\"\$changed\"; then\s+echo \"(\w+)=true\"", script)
    filters = {output: regex for regex, output in found}
    # If the step is reworded and nothing is extracted, fail here rather than with a KeyError.
    assert set(filters) == {"app", "scripts"}, found
    return filters


def test_scripts_filter_covers_guarded_files(filters):
    # grep -E and Python's re agree on what these regexes use: ^ $ ( | ) and \.
    missed = [path for path in GUARDED if not re.search(filters["scripts"], path)]
    assert missed == []


def test_app_filter_covers_app_paths(filters):
    missed = [path for path in APP_PATHS if not re.search(filters["app"], path)]
    assert missed == []


def test_kustomize_job_renders_every_entry_point():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["kustomize"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Render every base and overlay")
    loop = re.search(r"for dir in (.*?); do", script, re.DOTALL)
    assert loop, script
    # Expand each entry as bash would: a glob counts only for directories that exist.
    entries = [entry for entry in loop.group(1).split() if entry != "\\"]
    rendered = {path.relative_to(REPO).as_posix() for entry in entries for path in REPO.glob(entry)}
    missed = [path for path in ENTRY_POINTS if path not in rendered]
    assert missed == []


def build_options():
    """The `with:` of build-scan's docker/build-push-action step."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["build-scan"]["steps"]
    step = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action@"))
    return step["with"]


def cache_settings(value):
    """'type=gha,scope=x' -> {'type': 'gha', 'scope': 'x'}, so the order of the parts doesn't matter."""
    return dict(part.split("=", 1) for part in value.split(","))


def test_build_scan_uses_the_gha_layer_cache():
    options = build_options()
    assert "cache-from" in options and "cache-to" in options, sorted(options)
    assert cache_settings(options["cache-from"]) == {"type": "gha", "scope": "sample-api"}
    # mode=max also saves the builder stage, where the compile runs; mode=min saves only scratch.
    assert cache_settings(options["cache-to"]) == {"type": "gha", "scope": "sample-api", "mode": "max"}


def build_scan_steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["build-scan"]["steps"]


def step_index(steps, action):
    """Index of the first step that uses `action@...`."""
    uses = [s.get("uses", "").split("@")[0] for s in steps]
    assert action in uses, uses
    return uses.index(action)


def test_build_scan_persists_the_go_build_cache_mount():
    # G2: the gha layer cache doesn't save mount contents, so actions/cache keeps a host
    # directory and buildkit-cache-dance copies it into the mount and back out.
    steps = build_scan_steps()
    dance_at = step_index(steps, "reproducible-containers/buildkit-cache-dance")
    dance = steps[dance_at]
    # A commit SHA, not a tag: a tag can be moved to other code.
    assert re.fullmatch(r"reproducible-containers/buildkit-cache-dance@[0-9a-f]{40}", dance["uses"]), dance["uses"]
    # The helper image defaults to busybox:latest, so pin it by digest (NFR2, L9a-6).
    image = dance["with"].get("utility-image", "")
    assert re.fullmatch(r"ghcr\.io/containerd/busybox(:[\w.-]+)?@sha256:[0-9a-f]{64}", image), image
    # The builder must exist first, and the mount must be filled before the build reads it.
    assert step_index(steps, "docker/setup-buildx-action") < dance_at < step_index(steps, "docker/build-push-action")
    # The id is what the Dockerfile's go-build-${TARGETARCH} becomes on amd64.
    ((host_dir, mount),) = json.loads(dance["with"]["cache-map"]).items()
    assert mount == {"id": "go-build-amd64", "target": "/root/.cache/go-build"}
    # actions/cache restores the host directory first. Post steps run in reverse, so its save
    # runs after the dance's extract.
    cache_at = step_index(steps, "actions/cache")
    assert cache_at < dance_at
    cache = steps[cache_at]
    assert cache["with"]["path"] == host_dir
    # Q7: a prefix of its own, so lint/test and build-scan never restore each other's caches.
    assert cache["with"]["key"].startswith("go-build-docker-"), cache["with"]["key"]
    assert cache["with"]["restore-keys"].startswith("go-build-docker-"), cache["with"]["restore-keys"]
    # The Dockerfile pins Go: without it, a Go bump with unchanged source is an exact hit, so the
    # new compile is never saved (skip-extraction) and every later build compiles from scratch.
    assert "'Acme/apps/sample-api/Dockerfile'" in cache["with"]["key"], cache["with"]["key"]
    # The source is the rest of the compile's input: without it, a code change is an exact hit,
    # so the new compile is never saved and every later build recompiles the changed packages.
    for source in ("'Acme/apps/sample-api/go.mod'", "'Acme/apps/sample-api/**/*.go'"):
        assert source in cache["with"]["key"], cache["with"]["key"]
    # Keys are write-once: on an exact hit nothing will be saved, so skip the extract.
    assert dance["with"]["skip-extraction"] == f"${{{{ steps.{cache['id']}.outputs.cache-hit }}}}"
