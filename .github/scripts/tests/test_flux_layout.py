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
BOOTSTRAP = REPO / "Acme" / "infra" / "bootstrap"


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


def test_operator_image_is_pinned_by_tag_and_digest():
    # The Operator runs as cluster-admin: a moved tag must not change what runs.
    values = yaml.safe_load((BOOTSTRAP / "flux-operator-values.yaml").read_text())
    tag = values["image"]["tag"]
    assert re.fullmatch(r"v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", tag), tag


def test_flux_instance_pins_an_exact_flux_version():
    # A range such as 2.x lets the Operator upgrade Flux without a PR.
    distribution = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["spec"]["distribution"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", distribution["version"]), distribution["version"]
    assert distribution["registry"] == "ghcr.io/fluxcd", distribution["registry"]


def test_flux_syncs_its_own_folder_from_main_anonymously():
    sync = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["spec"].get("sync", {})
    assert sync.get("kind") == "GitRepository", sync
    # Anonymous HTTPS (D3): the repo is public, so no credential sits in the cluster.
    assert sync.get("url", "").startswith("https://"), sync
    assert "pullSecret" not in sync, sync
    # Only main is behind the merge gate; any other branch reaches a cluster-admin controller unreviewed.
    assert sync.get("ref") == "refs/heads/main", sync
    # Flux applies its own folder, so a change to the FluxInstance is a PR.
    folder = REPO / sync.get("path", "")
    resources = yaml.safe_load((folder / "kustomization.yaml").read_text()).get("resources", [])
    assert "flux-instance.yaml" in resources, folder


def flux_kustomizations():
    """{name: spec} of the Flux Kustomizations in platform.yaml."""
    docs = yaml.safe_load_all((BOOTSTRAP / "platform.yaml").read_text())
    return {d["metadata"]["name"]: d["spec"] for d in docs if d and d["kind"] == "Kustomization"}


def test_platform_applies_infrastructure_then_each_env():
    resources = yaml.safe_load((BOOTSTRAP / "kustomization.yaml").read_text())["resources"]
    # Not listed means Flux never applies it.
    assert "platform.yaml" in resources, resources
    kustomizations = flux_kustomizations()
    envs = sorted(path.parent.name for path in APPS_ENTRY_POINTS.glob("*/kustomization.yaml"))
    assert envs, APPS_ENTRY_POINTS
    assert sorted(kustomizations) == sorted(["infrastructure"] + [f"apps-{env}" for env in envs])
    assert kustomizations["infrastructure"]["path"] == "./Acme/platform/infrastructure/overlays/kind"
    # The ingress webhook must be running before any Ingress is applied.
    assert kustomizations["infrastructure"].get("wait") is True
    for env in envs:
        spec = kustomizations[f"apps-{env}"]
        assert spec["path"] == f"./Acme/platform/apps/overlays/{env}", env
        assert {"name": "infrastructure"} in spec.get("dependsOn", []), env
    for name, spec in kustomizations.items():
        assert (REPO / spec["path"] / "kustomization.yaml").exists(), name
        assert spec["sourceRef"] == {"kind": "GitRepository", "name": "flux-system"}, name
        assert spec.get("prune") is True, name
        # Deleting Flux's objects (a recovery drill) must not delete the workloads.
        assert spec.get("deletionPolicy") == "Orphan", name


def test_flux_instance_is_never_pruned():
    # If flux-instance.yaml left Git, prune would make Flux uninstall itself.
    annotations = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["metadata"].get("annotations", {})
    assert annotations.get("kustomize.toolkit.fluxcd.io/prune") == "disabled", annotations


def test_operator_values_reach_flux_as_one_configmap():
    # helm install and Flux's HelmRelease read the same file, so the tag@digest lives in one place (D2b).
    kustomization = yaml.safe_load((BOOTSTRAP / "kustomization.yaml").read_text())
    generators = {g["name"]: g for g in kustomization.get("configMapGenerator", [])}
    assert "flux-operator-values" in generators, kustomization
    generator = generators["flux-operator-values"]
    # valuesFrom reads a ConfigMap in the HelmRelease's own namespace.
    assert generator.get("namespace") == "flux-system", generator
    assert generator.get("files") == ["values.yaml=flux-operator-values.yaml"], generator
    options = generator.get("options", {})
    # The HelmRelease names the ConfigMap inside a ResourceSet template, which kustomize doesn't rewrite.
    assert options.get("disableNameSuffixHash") is True, generator
    # helm-controller acts on a values change at once instead of at its 30m interval.
    assert options.get("labels", {}).get("reconcile.fluxcd.io/watch") == "Enabled", generator



def operator_resourceset():
    """The ResourceSet in flux-operator.yaml, and its templates by kind, placeholders filled in as the Operator does."""
    resourceset = yaml.safe_load((BOOTSTRAP / "flux-operator.yaml").read_text())
    meta = resourceset["metadata"]
    text = yaml.safe_dump(resourceset["spec"]["resources"])
    text = text.replace("<< inputs.provider.name >>", meta["name"])
    text = text.replace("<< inputs.provider.namespace >>", meta["namespace"])
    return resourceset, {t["kind"]: t for t in yaml.safe_load(text)}


def test_operator_resourceset_pins_the_version_helm_install_used():
    resourceset, templates = operator_resourceset()
    assert resourceset["kind"] == "ResourceSet", resourceset["kind"]
    # More than one input set would generate one HelmRelease each, all fighting over the same release.
    inputs = resourceset["spec"]["inputs"]
    assert len(inputs) == 1, inputs
    version = inputs[0]["version"]
    # The docs' "*" would let the Operator upgrade itself without a PR.
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    # The chart and its image move together, in the same PR.
    tag = yaml.safe_load((BOOTSTRAP / "flux-operator-values.yaml").read_text())["image"]["tag"]
    assert tag.split("@")[0] == f"v{version}", (version, tag)
    source = templates["OCIRepository"]["spec"]
    assert source["url"] == "oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator", source["url"]
    # A tag or digest beside semver wins over it, and the input would then change nothing.
    assert source["ref"] == {"semver": "<< inputs.version | quote >>"}, source["ref"]


def test_operator_helmrelease_takes_over_the_helm_install_release():
    _, templates = operator_resourceset()
    release = templates["HelmRelease"]
    spec = release["spec"]
    # helm-controller adopts the release helm install made only if its name and namespace match (U9).
    assert spec["releaseName"] == "flux-operator", spec["releaseName"]
    assert release["metadata"]["namespace"] == "flux-system", release["metadata"]
    for key in ("targetNamespace", "storageNamespace"):
        assert key not in spec, key
    # Runs as helm-controller: a helm uninstall deletes the chart's own service account, which breaks the fallback (U9).
    assert "serviceAccountName" not in spec, spec
    assert spec["chartRef"] == {"kind": "OCIRepository", "name": templates["OCIRepository"]["metadata"]["name"]}, spec
    # The ConfigMap kustomization.yaml generates from flux-operator-values.yaml: the same tag@digest as helm install.
    assert spec.get("valuesFrom") == [
        {"kind": "ConfigMap", "name": "flux-operator-values", "valuesKey": "values.yaml"}
    ], spec.get("valuesFrom")


def test_operator_resourceset_is_applied_and_never_pruned():
    resources = yaml.safe_load((BOOTSTRAP / "kustomization.yaml").read_text())["resources"]
    assert "flux-operator.yaml" in resources, resources
    resourceset, _ = operator_resourceset()
    # Pruned, it would delete the HelmRelease, and deleting a HelmRelease uninstalls the Operator (R10).
    annotations = resourceset["metadata"].get("annotations", {})
    assert annotations.get("kustomize.toolkit.fluxcd.io/prune") == "disabled", annotations
