"""Tests for pick-rescan-images.sh, the image pick in sample-api-rescan.yml.

The rescan runs once a day from main, so a branch of that step can stay unexecuted for months.
These run every branch on every PR instead: tags JSON on stdin, a commit list (newest first,
as `git log --format=%H` prints it) in a file, the picked images as JSON on stdout.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "pick-rescan-images.sh"

# Newest first. sample-api-ci.yml tags an image sha-<first 7 chars of the commit>.
NEWEST = "a" * 40
MIDDLE = "b" * 40
OLDEST = "c" * 40
COMMITS = [NEWEST, MIDDLE, OLDEST]


def pick(tmp_path, tags_json, commits=COMMITS):
    """Run the script; return the finished process (exit code, stdout, stderr)."""
    commits_file = tmp_path / "commits.txt"
    commits_file.write_text("".join(c + "\n" for c in commits))
    return subprocess.run(
        [str(SCRIPT), str(commits_file)],
        input=tags_json,
        capture_output=True,
        text=True,
        check=False,
    )


def tags(*names):
    """GHCR's tags/list reply for the given tag names."""
    return json.dumps({"name": "kerinschristopher/sample-api", "tags": list(names)})


def test_picks_newest_sha_and_highest_release(tmp_path):
    # NEWEST has no image, so MIDDLE's is the newest. sort -V puts 1.10.0 above 1.9.0.
    result = pick(tmp_path, tags("sha-ccccccc", "sha-bbbbbbb", "1.9.0", "1.10.0", "0.1.1"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '["sha-bbbbbbb","1.10.0"]'


def test_only_sha_warns_no_release(tmp_path):
    result = pick(tmp_path, tags("sha-aaaaaaa"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '["sha-aaaaaaa"]'
    assert "::warning::No semver release tag" in result.stderr


def test_only_release_warns_no_sha(tmp_path):
    # Today's real package: only 0.1.1, no sha-* yet.
    result = pick(tmp_path, tags("0.1.1"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '["0.1.1"]'
    assert "::warning::No sha-* image" in result.stderr


def test_ignores_tags_that_are_not_releases(tmp_path):
    # v-prefixed, pre-release and latest aren't releases; sha-ddddddd isn't on main.
    result = pick(tmp_path, tags("v1.2.0", "1.2.0-rc.1", "latest", "sha-ddddddd", "0.1.1"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '["0.1.1"]'


@pytest.mark.parametrize(
    "tags_json",
    ['{"tags":[]}', '{"tags":null}', "{}"],
    ids=["empty", "null", "missing"],
)
def test_nothing_to_scan(tmp_path, tags_json):
    # The branch the teacher said could never run: a registry can send null, not [], for no tags.
    result = pick(tmp_path, tags_json)
    assert result.returncode == 1
    assert "::error::Nothing to scan" in result.stderr
    assert result.stdout == ""


def test_malformed_reply_fails_loudly(tmp_path):
    # Not a list and not null: a broken reply, which must not look like "nothing to scan".
    result = pick(tmp_path, '{"tags":"x"}')
    assert result.returncode != 0
    assert "Nothing to scan" not in result.stderr
