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


# Scripts on the image's build, scan and push path. A change to one alone must run build-scan and
# push (the app filter), not just scripts-test. Listed before they exist, like GUARDED (L9a-7).
IMAGE_SCRIPTS = [
    ".github/scripts/oci-platform-layout.sh",
    ".github/scripts/push-oci.sh",
]


def test_app_filter_covers_image_scripts(filters):
    missed = [path for path in IMAGE_SCRIPTS if not re.search(filters["app"], path)]
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
    # Keys are write-once: on an exact hit nothing will be saved, so skip the extract.
    assert dance["with"]["skip-extraction"] == f"${{{{ steps.{cache['id']}.outputs.cache-hit }}}}"
