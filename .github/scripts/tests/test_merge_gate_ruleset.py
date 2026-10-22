"""Structural tests for main-merge-gate-ruleset.json: the checks a PR into main must pass.

A required check is matched by name. If a context names a check that no PR reports (a typo, a
renamed job, a matrix leg, a reusable-workflow caller), every PR waits for it forever ("Expected —
waiting for status"). So every context must be a job that pull_request runs report under one name.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
RULESET = ROOT / "Acme" / "infra" / "github" / "main-merge-gate-ruleset.json"
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def contexts():
    rules = json.loads(RULESET.read_text())["rules"]
    checks = next(rule for rule in rules if rule["type"] == "required_status_checks")
    contexts = [check["context"] for check in checks["parameters"]["required_status_checks"]]
    # An empty list would pass every test below without checking anything.
    assert contexts
    return contexts


@pytest.fixture(scope="module")
def pr_checks():
    """Check names that pull_request runs report under one fixed name."""
    names = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        # PyYAML reads the key `on` as the boolean True.
        if "pull_request" not in workflow[True]:
            continue
        for job_id, job in workflow["jobs"].items():
            # A matrix leg reports as `test (1.26)`, a reusable-workflow caller as `plan-dev / plan`.
            if "strategy" in job or "uses" in job:
                continue
            names.add(job.get("name", job_id))
    return names


def test_every_context_is_a_pr_check(contexts, pr_checks):
    # Characterisation (C6): written after the ruleset, so green on arrival. A misspelled context
    # turns it red (recorded in the design doc's log, not committed).
    unknown = [context for context in contexts if context not in pr_checks]
    assert unknown == []
