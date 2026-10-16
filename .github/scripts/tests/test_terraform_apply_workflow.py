"""Structural tests for terraform-apply.yml: the saved plan is deleted however the apply ends.

The binary tfplan-dev artifact is unredacted and the repo is public. A step inside the gated
apply job never runs when the apply is rejected or refused by the dev-apply environment,
because that job never starts. So the delete must live in its own job, one that runs whatever
apply's result. A real reject is slow to set up, so these check the workflow's shape instead.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "terraform-apply.yml"


@pytest.fixture(scope="module")
def jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def deletes_artifact(job):
    """True if any step calls the artifacts API (gh api -X DELETE .../actions/artifacts/<id>)."""
    return any("actions/artifacts" in step.get("run", "") for step in job.get("steps", []))


def test_no_gated_job_deletes_the_plan(jobs):
    # A job with an environment may never start (rejected, or refused for its branch).
    gated = [name for name, job in jobs.items() if "environment" in job and deletes_artifact(job)]
    assert gated == []


def test_cleanup_job_runs_whatever_apply_did(jobs):
    cleaners = [name for name, job in jobs.items() if deletes_artifact(job)]
    assert cleaners == ["cleanup"]
    cleanup = jobs["cleanup"]
    assert {"plan", "apply"} <= set(cleanup["needs"])
    assert "always()" in cleanup["if"]
    # No saved plan (no changes, plan error, Conftest failure) → empty ID → skip, don't 404.
    assert "plan_artifact_id != ''" in cleanup["if"]
    # Least privilege: deleting the artifact is all this job does.
    assert cleanup["permissions"] == {"actions": "write"}


def test_apply_cannot_write_actions(jobs):
    assert "actions" not in jobs["apply"]["permissions"]
