"""Structural tests for sample-api's Dockerfile: how the compile step is cached.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[3] / "Acme" / "apps" / "sample-api" / "Dockerfile"


def instructions():
    """[(line number, instruction)], with `\\` continuations joined and comments dropped."""
    found, pending, start = [], "", 0
    for number, line in enumerate(DOCKERFILE.read_text().splitlines(), 1):
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start = number
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        found.append((start, pending + stripped))
        pending = ""
    return found


def mount_settings(value):
    """'type=cache,id=x' -> {'type': 'cache', 'id': 'x'}, so the order of the parts doesn't matter."""
    return dict(part.split("=", 1) for part in value.split(","))


def test_compile_uses_per_arch_cache_mount():
    steps = instructions()
    line, compile_step = next((n, i) for n, i in steps if i.startswith("RUN ") and "go build" in i)
    # RUN's flags come before the command: RUN --mount=... CGO_ENABLED=0 ... go build ...
    flags = re.match(r"RUN((?:\s+--\S+)*)", compile_step).group(1).split()
    mounts = [mount_settings(f[len("--mount="):]) for f in flags if f.startswith("--mount=")]
    assert mounts, f"Dockerfile:{line} has no --mount: {compile_step}"
    assert {"type": "cache", "id": "go-build-${TARGETARCH}", "target": "/root/.cache/go-build"} in mounts, mounts
    # TARGETARCH is empty in a stage that doesn't declare it, which would give every arch the same id.
    stage = max(n for n, i in steps if i.startswith("FROM ") and n < line)
    declared = {word.split("=")[0] for n, i in steps if stage < n < line and i.startswith("ARG ")
                for word in i.split()[1:]}
    assert "TARGETARCH" in declared, f"no ARG TARGETARCH between Dockerfile:{stage} and :{line}"
