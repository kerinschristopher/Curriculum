"""Structural tests for the Flux layout (Module 8): the image pins and the entry points Flux applies.

Every image Flux deploys is pinned by digest (design doc D4): with `name:tag@sha256:...` the
runtime pulls by digest, so a moved tag can't change what runs. Kubernetes, kustomize and the
schema check all accept a tag-only image, so nothing else notices a missing digest.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
SAMPLE_API_OVERLAYS = REPO / "Acme" / "platform" / "apps" / "sample-api" / "overlays"
SAMPLE_API_IMAGE = "ghcr.io/kerinschristopher/sample-api"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
APPS_ENTRY_POINTS = REPO / "Acme" / "platform" / "apps" / "overlays"


def test_every_env_overlay_pins_sample_api_by_digest():
    overlays = sorted(SAMPLE_API_OVERLAYS.glob("*/kustomization.yaml"))
    # A moved directory would make the loop below check nothing and pass.
    assert overlays, SAMPLE_API_OVERLAYS
    unpinned = []
    for path in overlays:
        images = yaml.safe_load(path.read_text()).get("images", [])
        image = next((i for i in images if i["name"] == SAMPLE_API_IMAGE), {})
        if "newTag" not in image or not DIGEST.fullmatch(image.get("digest", "")):
            unpinned.append(path.parent.name)
    assert unpinned == []


def test_every_env_has_an_apps_entry_point_for_its_own_overlay():
    envs = sorted(path.parent.name for path in SAMPLE_API_OVERLAYS.glob("*/kustomization.yaml"))
    assert envs, SAMPLE_API_OVERLAYS
    wrong = []
    for env in envs:
        path = APPS_ENTRY_POINTS / env / "kustomization.yaml"
        resources = yaml.safe_load(path.read_text()).get("resources", []) if path.exists() else []
        # The same env's overlay: apps-stage applying dev's overlay would put dev's image in stage.
        if f"../../sample-api/overlays/{env}" not in resources:
            wrong.append(env)
    assert wrong == []
