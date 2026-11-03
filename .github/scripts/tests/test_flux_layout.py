"""Structural tests for the Flux layout (Module 8): the image pins and the entry points Flux applies.

Every image Flux deploys is pinned by digest (design doc D4): with `name:tag@sha256:...` the
runtime pulls by digest, so a moved tag can't change what runs. Kubernetes, kustomize and the
schema check all accept a tag-only image, so nothing else notices a missing digest.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from test_branch_rulesets import applies_to
from test_sample_api_ci_workflow import ENTRY_POINTS

REPO = Path(__file__).resolve().parents[3]
SAMPLE_API_OVERLAYS = REPO / "Acme" / "platform" / "apps" / "sample-api" / "overlays"
SAMPLE_API_IMAGE = "ghcr.io/kerinschristopher/sample-api"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# name:tag@digest. The tag has no `/`, so a registry port like host:5000/name@sha256:... does not count.
TAG_AND_DIGEST = re.compile(rf":[^/:@]+@{DIGEST.pattern}$")
APPS_ENTRY_POINTS = REPO / "Acme" / "platform" / "apps" / "overlays"
BOOTSTRAP = REPO / "Acme" / "infra" / "bootstrap"
# Any indentation or list dash, because an indented Secret still reaches the cluster. A future
# `valuesFrom: - kind: Secret` would be flagged too, which fails safe.
SECRET_KIND = re.compile(r"""^\s*-?\s*kind:\s*["']?Secret["']?\s*$""", re.M)




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


def container_images(node):
    """Every container's image under a rendered object, for any workload kind."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("containers", "initContainers"):
                yield from (container["image"] for container in value)
            else:
                yield from container_images(value)
    elif isinstance(node, list):
        for item in node:
            yield from container_images(item)


def test_every_container_flux_deploys_has_a_tag_and_digest():
    # The render, not the files: a renamed image in base/ leaves the overlay's `images` entry
    # matching nothing, so the overlay still looks pinned and the Deployment isn't.
    # Characterisation (C6): every image was already pinned, so this was green on arrival. The
    # mutations are in the design doc's log.
    found = {}
    for entry in ENTRY_POINTS:
        render = subprocess.run(
            ["kubectl", "kustomize", str(REPO / entry)], stdout=subprocess.PIPE, text=True, check=True
        )
        found[entry] = [image for doc in yaml.safe_load_all(render.stdout) for image in container_images(doc)]
    # Bootstrap holds no containers: test_operator_image_is_pinned_by_tag_and_digest covers its image.
    empty = [entry for entry, images in found.items() if not images and entry != "Acme/infra/bootstrap"]
    assert empty == []
    unpinned = sorted({i for images in found.values() for i in images if not TAG_AND_DIGEST.search(i)})
    assert unpinned == []


def test_no_flux_kustomization_rewrites_images():
    # Flux applies spec.images after the render, so the test above can't see it. A newTag alone
    # drops the digest, and a newName or a digest alone deploys something else.
    # Characterisation (C6): none has ever been committed, so this was green on arrival. Adding
    # one temporarily turns it red; the result is in the design doc's log.
    assert [name for name, spec in flux_kustomizations().items() if "images" in spec] == []


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


def image_automation():
    """{kind: object} of dev's image-automation.yaml, which dev's entry point must list."""
    entry = yaml.safe_load((APPS_ENTRY_POINTS / "dev" / "kustomization.yaml").read_text())
    assert "image-automation.yaml" in entry["resources"], entry["resources"]
    path = APPS_ENTRY_POINTS / "dev" / "image-automation.yaml"
    return {doc["kind"]: doc for doc in yaml.safe_load_all(path.read_text())}


def test_dev_image_markers_name_a_policy_that_exists():
    # A marker naming no real policy is only a comment: dev would silently never update.
    policy = image_automation()["ImagePolicy"]["metadata"]
    reference = f'{policy["namespace"]}:{policy["name"]}'
    overlay = (SAMPLE_API_OVERLAYS / "dev" / "kustomization.yaml").read_text()
    for field, setter in (("newTag", "tag"), ("digest", "digest")):
        marker = f'# {{"$imagepolicy": "{reference}:{setter}"}}'
        assert re.search(rf"^\s*{field}: \S+ {re.escape(marker)}$", overlay, re.M), (field, marker)


def test_image_automation_pushes_beside_main_never_to_it():
    git = image_automation()["ImageUpdateAutomation"]["spec"]["git"]
    checkout, push = git["checkout"]["ref"]["branch"], git["push"]["branch"]
    # main only changes through the merge gate: Flux proposes, a reviewed PR decides.
    assert checkout == "main", checkout
    assert push != checkout, push
    # The automation's branch is never a trusted branch: if it matched mod*, Flux would hold a branch
    # the AWS plan role trusts. A ruleset can't be relied on to stop this key: P9's push bypassed one.
    # Characterisation (C6): no ruleset has ever covered it, so this was green on arrival. Adding the
    # branch to a ruleset file temporarily turns it red; the result is in the design doc's log.
    rulesets = sorted((REPO / "Acme" / "infra" / "github").glob("*-ruleset.json"))
    assert rulesets
    refusing = [p.name for p in rulesets if applies_to(json.loads(p.read_text()), f"refs/heads/{push}")]
    assert refusing == []


def test_image_automation_only_rewrites_dev():
    update = image_automation()["ImageUpdateAutomation"]["spec"]["update"]
    assert update["strategy"] == "Setters", update
    assert (REPO / update["path"]).resolve() == (SAMPLE_API_OVERLAYS / "dev").resolve(), update


def test_image_automation_writes_with_a_key_that_stays_out_of_git():
    objects = image_automation()
    source = objects["GitRepository"]
    assert objects["ImageUpdateAutomation"]["spec"]["sourceRef"]["name"] == source["metadata"]["name"]
    assert source["spec"]["url"].startswith("ssh://"), source["spec"]
    assert source["spec"]["secretRef"]["name"] == "flux-image-updates", source["spec"]
    # Characterisation (C6): no Secret has ever been committed, so this half was green on arrival.
    # A temporary `kind: Secret` file turns it red; the result is in the design doc's log.
    committed = [str(p) for p in (REPO / "Acme").rglob("*.y*ml") if SECRET_KIND.search(p.read_text())]
    assert committed == []


@pytest.mark.parametrize(
    "carrier",
    [
        "kind: Secret\n",
        '---\nkind: "Secret"\n',
        "items:\n  - kind: Secret\n",
        "spec:\n  resources:\n    - apiVersion: v1\n      kind: Secret\n",
    ],
    ids=["bare-document", "quoted", "list-item", "resourceset-template"],
)
def test_secret_check_sees_every_way_a_secret_can_be_written(carrier):
    # The Operator applies whatever a ResourceSet template generates, so an indented Secret reaches
    # the cluster just like a top-level one.
    assert SECRET_KIND.search(carrier)


def test_secret_check_ignores_other_kinds():
    # Characterisation (C6): this was green on arrival. Dropping the regex's end-of-line anchor
    # temporarily turns it red; the result is in the design doc's log.
    assert not SECRET_KIND.search("kind: ConfigMap\nkind: SecretStore\n")


def test_image_automation_watches_the_image_the_overlays_deploy():
    # Characterisation (C6): all of these were green on arrival. Changing any one temporarily turns
    # it red; the results are in the design doc's log.
    objects = image_automation()
    assert objects["ImageRepository"]["spec"]["image"] == SAMPLE_API_IMAGE
    policy = objects["ImagePolicy"]["spec"]
    assert policy["imageRepositoryRef"]["name"] == objects["ImageRepository"]["metadata"]["name"]
    # Only semver ignores the sha-* tags: anything else would pick one, and dev's bumps would never stop.
    assert "semver" in policy["policy"], policy
    # Without it the :digest marker writes an empty digest.
    assert policy["digestReflectionPolicy"] in ("IfNotPresent", "Always"), policy
    spec = objects["ImageUpdateAutomation"]["spec"]
    # Either would stop dev updating, silently.
    assert "policySelector" not in spec and "suspend" not in spec, spec
    # A refspec pushes beside push.branch, and this key can write mod*.
    assert "refspec" not in spec["git"]["push"], spec["git"]["push"]
    entry = yaml.safe_load((APPS_ENTRY_POINTS / "dev" / "kustomization.yaml").read_text())
    # A namespace here would move the image objects out of flux-system, and the markers would match nothing.
    assert "namespace" not in entry, entry


def test_image_automation_commits_are_conventional_and_credit_no_account():
    commit = image_automation()["ImageUpdateAutomation"]["spec"]["git"]["commit"]
    # Flux's default message isn't a Conventional Commit, and merging the PR brings it into main.
    assert commit.get("messageTemplate", "").startswith("chore(sample-api): deploy "), commit
    # .invalid is a reserved domain: no GitHub account can be credited with these commits.
    assert commit["author"]["email"].endswith(".invalid"), commit


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


def test_flux_instance_runs_the_image_controllers():
    # Image automation needs both; the FluxInstance only runs what it lists.
    components = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["spec"]["components"]
    missing = {"image-reflector-controller", "image-automation-controller"} - set(components)
    assert not missing, missing


def test_flux_syncs_its_own_folder_from_main_anonymously():
    sync = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["spec"].get("sync", {})
    assert sync.get("kind") == "GitRepository", sync
    # Anonymous HTTPS (D3): the repo is public, so no credential sits in the cluster.
    assert sync.get("url", "").startswith("https://"), sync
    # Characterisation (C6): the sync has never had a pullSecret, so this was green on arrival.
    # Adding one temporarily turns it red; the result is in the design doc's log.
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
        # A suspended Kustomization stops applying and stops reverting drift, silently.
        assert "suspend" not in spec, name
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
    # Characterisation (C6): there has only ever been one, so this was green on arrival. A second
    # input set temporarily turns it red; the result is in the design doc's log.
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
    # Characterisation (C6): neither key has ever been set, so this was green on arrival. Adding
    # either one temporarily turns it red; the result is in the design doc's log.
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


def test_bootstrap_holds_no_generated_flux_manifests():
    # The Operator carries Flux's ~8,600 generated lines (CRDs included) in its image, so the repo
    # doesn't. `flux install --export` or `flux bootstrap` would write them here, in a subfolder too.
    generated = []
    for path in sorted(BOOTSTRAP.rglob("*.yaml")):
        text = path.read_text()
        kinds = {doc.get("kind") for doc in yaml.safe_load_all(text) if doc}
        if "CustomResourceDefinition" in kinds or len(text.splitlines()) > 200:
            generated.append(path.relative_to(BOOTSTRAP).as_posix())
    assert generated == []


def test_no_kustomization_generates_a_secret():
    # A secretGenerator renders a real Secret with no `kind: Secret` in any file.
    # Characterisation (C6): none has ever been committed, so this was green on arrival. Adding
    # one temporarily turns it red; the result is in the design doc's log.
    paths = sorted((REPO / "Acme").rglob("kustomization.y*ml"))
    assert paths
    generating = [str(p) for p in paths if "secretGenerator" in yaml.safe_load(p.read_text())]
    assert generating == []
