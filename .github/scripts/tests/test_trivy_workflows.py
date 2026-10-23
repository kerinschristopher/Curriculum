"""Structural tests for the Trivy scan steps in sample-api-ci.yml and sample-api-rescan.yml.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"
TRIVY = "aquasecurity/trivy-action"

# trivy-action steps per workflow. Counted, so a step that stops matching (deleted, moved to a
# fork) fails here instead of silently leaving the checks below (L9a-13). M4 makes sample-api-ci's 2.
TRIVY_STEPS = {"sample-api-ci.yml": 1, "sample-api-rescan.yml": 1}


def trivy_steps(workflow):
    jobs = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"]
    return [s for job in jobs.values() for s in job.get("steps", []) if s.get("uses", "").split("@")[0] == TRIVY]


@pytest.mark.parametrize("workflow", sorted(TRIVY_STEPS))
def test_trivy_caches_its_db(workflow):
    steps = trivy_steps(workflow)
    assert len(steps) == TRIVY_STEPS[workflow], [s.get("name") for s in steps]
    # Set explicitly, not left to the action's default, which a pin bump could change unseen.
    # YAML reads `true` as a bool and `"true"` as a string; the action gets "true" either way.
    off = {s["name"]: s.get("with", {}).get("cache") for s in steps if str(s.get("with", {}).get("cache")).lower() != "true"}
    assert off == {}
