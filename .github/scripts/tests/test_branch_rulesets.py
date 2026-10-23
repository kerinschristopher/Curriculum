"""Structural tests for the branch rulesets in Acme/infra/github/*-ruleset.json.

A merge into main is an update to main. So an `update` rule on main refuses every PR merge,
however green its checks, and the only way in is the admin bypass, which skips the required
checks too: the merge gate is never enforced. main keeps its protection against deletion and
force-push in a ruleset of its own (main-protect); trusted-branches keeps all four rules on mod*.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import json
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

GITHUB = Path(__file__).resolve().parents[3] / "Acme" / "infra" / "github"
MAIN = "refs/heads/main"


@pytest.fixture(scope="module")
def rulesets():
    found = {path.name: json.loads(path.read_text()) for path in sorted(GITHUB.glob("*-ruleset.json"))}
    # An empty glob would pass every test below without checking anything.
    assert found
    return found


def applies_to(ruleset, ref):
    """Whether an active ruleset's ref_name condition includes ref, e.g. refs/heads/main."""
    def hits(patterns):
        return any(p == "~ALL" or (p == "~DEFAULT_BRANCH" and ref == MAIN) or fnmatchcase(ref, p) for p in patterns)
    names = ruleset["conditions"]["ref_name"]
    return ruleset["enforcement"] == "active" and hits(names["include"]) and not hits(names["exclude"])


def rule_types(ruleset):
    return {rule["type"] for rule in ruleset["rules"]}


def test_no_ruleset_blocks_merges_into_main(rulesets):
    blocking = [name for name, r in rulesets.items() if applies_to(r, MAIN) and "update" in rule_types(r)]
    assert blocking == []


def test_main_cannot_be_deleted_or_force_pushed(rulesets):
    # Guards the split: taking main out of trusted-branches must not drop these.
    kept = set().union(*(rule_types(r) for r in rulesets.values() if applies_to(r, MAIN)))
    assert {"deletion", "non_fast_forward"} <= kept, kept


def test_mod_branches_keep_all_four_rules(rulesets):
    trusted = rulesets["trusted-branches-ruleset.json"]
    assert applies_to(trusted, "refs/heads/mod7-caching")
    assert rule_types(trusted) == {"creation", "update", "deletion", "non_fast_forward"}
