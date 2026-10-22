"""Structural tests for sample-api-ci.yml: which changed paths turn on which jobs, and build caching.

`changes` greps the PR's changed files with one regex per output. `scripts=true` runs scripts-test,
so every file a test in this directory reads must match the scripts regex. Otherwise a change to
that file alone skips the test that would catch it, and a skipped job counts as passing.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "sample-api-ci.yml"

# Files a structural test reads. Some don't exist yet: listing them now covers their tests from
# the commit that adds them.
GUARDED = [
    "Acme/infra/github/main-merge-gate-ruleset.json",
    ".github/workflows/terraform-plan.yml",
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
    ".github/scripts/check-trivy-db.sh",
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
