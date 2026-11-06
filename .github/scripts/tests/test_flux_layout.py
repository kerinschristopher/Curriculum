"""Structural tests for the Flux layout (Module 8): the image pins and the entry points Flux applies.

Every image Flux deploys is pinned by digest (design doc D4): with `name:tag@sha256:...` the
runtime pulls by digest, so a moved tag can't change what runs. Kubernetes, kustomize and the
schema check all accept a tag-only image, so nothing else notices a missing digest.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import functools
import http.server
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
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
# Where every remote resource we render comes from today: if it doesn't answer, a failed render is the network.
EGRESS_PROBE = "https://raw.githubusercontent.com/"
APPS_ENTRY_POINTS = REPO / "Acme" / "platform" / "apps" / "overlays"
BOOTSTRAP = REPO / "Acme" / "infra" / "bootstrap"
# Any indentation or list dash, because an indented Secret still reaches the cluster. A future
# `valuesFrom: - kind: Secret` would be flagged too, which fails safe.
SECRET_KIND = re.compile(r"""^\s*-?\s*kind:\s*["']?Secret["']?\s*$""", re.M)

KIND_INFRASTRUCTURE = "Acme/platform/infrastructure/overlays/kind"
KUBE_PROMETHEUS_STACK = "oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack"
MONITORING = REPO / "Acme" / "platform" / "infrastructure" / "base" / "monitoring"
# Any registry-hosted name:tag in rendered text, with its digest if it has one. The chart puts images in
# containers, in Prometheus's and Alertmanager's spec.image and in operator arguments (F6); a later version
# may add another place, and a text scan still sees it.
IMAGE_REFERENCE = re.compile(rf"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?/[a-z0-9._/-]+:[\w.-]+(?:@{DIGEST.pattern})?")


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
            # Not ephemeralContainers: a pod can't be created with them (Module 9 design, F18), so no manifest has one.
            if key in ("containers", "initContainers"):
                # `image` is optional (F18). A missing one reads as "", which fails the
                # tag@digest match like any unpinned image.
                yield from (container.get("image", "") for container in value)
            else:
                yield from container_images(value)
    elif isinstance(node, list):
        for item in node:
            yield from container_images(item)


def test_a_container_without_an_image_reads_as_unpinned():
    # `image` is optional on a container (Module 9 design, F18): the digest check must report it, not crash.
    doc = {"spec": {"template": {"spec": {"containers": [{"name": "app"}]}}}}
    images = list(container_images(doc))
    assert [image for image in images if not TAG_AND_DIGEST.search(image)] == [""]


def egress_works():
    """Whether EGRESS_PROBE answers at all. Any HTTP status means the network is up; only no answer means it's down."""
    try:
        with urllib.request.urlopen(urllib.request.Request(EGRESS_PROBE, method="HEAD"), timeout=10):
            return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def fail_naming_the_cause(action, suspect, stderr):
    """Fail, blaming the network if EGRESS_PROBE doesn't answer, otherwise `suspect` (PR #22 finding 1, D6)."""
    if egress_works():
        cause = f"{EGRESS_PROBE} answers, so the cause is {suspect}"
    else:
        cause = f"the network is down ({EGRESS_PROBE} doesn't answer)"
    pytest.fail(f"{action} failed: {cause}.\n{stderr}")


@functools.cache
def rendered(entry):
    """The objects `kustomize build` builds from `entry`, a folder relative to the repo, rendered once per run."""
    render = subprocess.run(["kustomize", "build", str(REPO / entry)], capture_output=True, text=True)
    if render.returncode:
        fail_naming_the_cause(f"Rendering {entry}", "the kustomization or a URL in it", render.stderr)
    return tuple(doc for doc in yaml.safe_load_all(render.stdout) if doc)


def test_renders_use_the_pinned_kustomize():
    # The kustomize on PATH must be the one sample-api-ci.yml pins to Flux's version, in scripts-test
    # and locally: the runner image ships its own (5.8.3), and kubectl bundles yet another.
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "sample-api-ci.yml").read_text())
    pinned = f"v{workflow['env']['KUSTOMIZE_VERSION']}"
    version = subprocess.run(["kustomize", "version"], capture_output=True, text=True, check=True)
    assert version.stdout.strip() == pinned, version.stdout


def test_renders_use_the_pinned_helm():
    # The Helm on PATH must be the one sample-api-ci.yml pins to Flux's version, in scripts-test and
    # locally: the runner image ships Helm 3.22.1, and helm-controller renders with v4.3.1.
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "sample-api-ci.yml").read_text())
    pinned = f"v{workflow['env']['HELM_VERSION']}"
    version = subprocess.run(["helm", "version", "--template", "{{.Version}}"], capture_output=True, text=True, check=True)
    assert version.stdout.strip() == pinned, version.stdout


def test_a_render_that_fails_offline_names_the_network(tmp_path, monkeypatch):
    # PR #22 finding 1 (Module 9 design A1, D6): an outage must not look like a broken kustomization.
    (tmp_path / "kustomization.yaml").write_text("resources:\n  - https://manifests.invalid/deploy.yaml\n")
    # .invalid never resolves (RFC 2606), so both lookups fail offline, as in an outage.
    monkeypatch.setattr(sys.modules[__name__], "EGRESS_PROBE", "https://probe.invalid/")
    with pytest.raises(pytest.fail.Exception) as failure:
        rendered(str(tmp_path))
    assert "manifests.invalid" in str(failure.value), failure.value
    assert "network" in str(failure.value), failure.value


def test_a_render_that_fails_online_blames_the_kustomization(tmp_path, monkeypatch):
    # The other half: with the network up, a broken kustomization must not be blamed on an outage.
    (tmp_path / "kustomization.yaml").write_text("resources:\n  - missing.yaml\n")
    # A local server stands in for a working network, so this runs offline too. It answers every
    # request with 501, and any HTTP answer means the network is up.
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(sys.modules[__name__], "EGRESS_PROBE", f"http://127.0.0.1:{server.server_port}/")
        with pytest.raises(pytest.fail.Exception) as failure:
            rendered(str(tmp_path))
    finally:
        server.shutdown()
    assert "missing.yaml" in str(failure.value), failure.value
    assert "network" not in str(failure.value), failure.value


def test_every_container_flux_deploys_has_a_tag_and_digest():
    # The render, not the files: a renamed image in base/ leaves the overlay's `images` entry
    # matching nothing, so the overlay still looks pinned and the Deployment isn't.
    # Characterisation (C6): every image was already pinned, so this was green on arrival. The
    # mutations are in the design doc's log.
    found = {}
    for entry in ENTRY_POINTS:
        found[entry] = [image for doc in rendered(entry) for image in container_images(doc)]
    # Bootstrap holds no containers: test_operator_image_is_pinned_by_tag_and_digest covers its image.
    empty = [entry for entry, images in found.items() if not images and entry != "Acme/infra/bootstrap"]
    assert empty == []
    unpinned = sorted({i for images in found.values() for i in images if not TAG_AND_DIGEST.search(i)})
    assert unpinned == []


def test_flux_kustomizations_set_only_known_fields():
    # Flux applies spec.images, spec.patches and spec.components after the render, so the test
    # above can't see them, and any one can swap an image (third C3 review, finding 2). A newTag
    # alone drops the digest. Each may set only what they set today: a new field fails here until
    # a test covers it.
    # Characterisation (C6): green on arrival. The mutations are in the design doc's log.
    allowed = {"deletionPolicy", "dependsOn", "interval", "path", "prune", "retryInterval", "sourceRef", "timeout", "wait"}
    extra = {name: sorted(set(spec) - allowed) for name, spec in flux_kustomizations().items() if set(spec) - allowed}
    assert extra == {}


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
    rulesets_on_push_branch = [p.name for p in rulesets if applies_to(json.loads(p.read_text()), f"refs/heads/{push}")]
    assert rulesets_on_push_branch == []


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
def test_secret_check_sees_bare_quoted_and_nested_secrets(carrier):
    # The Operator applies whatever a ResourceSet template generates, so an indented Secret reaches
    # the cluster just like a top-level one.
    # The regex alone covers files Flux never applies and Secrets nested in a ResourceSet template.
    # It misses a trailing comment, flow style, a quoted key and .json; for applied files,
    # test_nothing_flux_applies_is_a_secret catches those.
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


def test_flux_instance_sets_only_known_fields():
    # The Operator builds Flux's controllers in the cluster, so no render shows them.
    # spec.kustomize.patches edits them after the build, and distribution.artifact or variant picks
    # other manifests: any one can swap a controller's image (third C3 review, finding 2).
    # Characterisation (C6): green on arrival. The mutations are in the design doc's log.
    spec = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["spec"]
    assert sorted(spec) == ["components", "distribution", "sync"], sorted(spec)
    assert sorted(spec["distribution"]) == ["registry", "version"], sorted(spec["distribution"])


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


def test_ci_renders_exactly_the_folders_flux_applies():
    # ENTRY_POINTS is kept by hand, and the render loop and digest tests trust it: a folder Flux
    # applies but the list misses deploys unrendered and unchecked (third C3 review, finding 3).
    # Characterisation (C6): the list already matched, so this was green on arrival. The mutations
    # are in the design doc's log.
    sync = yaml.safe_load((BOOTSTRAP / "flux-instance.yaml").read_text())["spec"]["sync"]
    paths = [sync["path"]] + [spec["path"] for spec in flux_kustomizations().values()]
    assert sorted(ENTRY_POINTS) == sorted(path.removeprefix("./") for path in paths)


def test_every_flux_kustomization_is_in_platform_yaml():
    # The checks above read platform.yaml only, but Flux applies every file in the render. One
    # elsewhere would skip them all: no dependsOn, its own spec.images (third C3 review, finding 4).
    # Characterisation (C6): every one was already in platform.yaml, so this was green on arrival.
    # The mutations are in the design doc's log.
    found = sorted(
        (entry, doc["metadata"]["name"])
        for entry in ENTRY_POINTS
        for doc in rendered(entry)
        if doc["kind"] == "Kustomization" and doc["apiVersion"].startswith("kustomize.toolkit.fluxcd.io/")
    )
    assert found == sorted(("Acme/infra/bootstrap", name) for name in flux_kustomizations())


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


def test_operator_helmrelease_reverts_hand_edits():
    # helm-controller compares the live cluster only when driftDetection is set; without it, a kubectl
    # edit of the cluster-admin Operator stays until the next chart or values change (L18).
    _, templates = operator_resourceset()
    spec = templates["HelmRelease"]["spec"]
    assert spec.get("driftDetection") == {"mode": "enabled"}, spec


def test_operator_resourceset_generates_only_the_release_and_its_source():
    # The Operator builds these objects in the cluster, so no render shows them. Inline values or
    # postRenderers on the HelmRelease beat valuesFrom's tag@digest, and resourcesTemplate, steps or
    # a third template can add any object (third C3 review, finding 2). Each may set only what it
    # sets today: a new field fails here until a test covers it.
    # Characterisation (C6): green on arrival. The mutations are in the design doc's log.
    resourceset, templates = operator_resourceset()
    assert sorted(resourceset["spec"]) == ["inputs", "resources"], sorted(resourceset["spec"])
    # templates is keyed by kind, so a second HelmRelease would hide behind the first.
    assert [t["kind"] for t in resourceset["spec"]["resources"]] == ["OCIRepository", "HelmRelease"]
    spec = templates["HelmRelease"]["spec"]
    assert sorted(spec) == ["chartRef", "driftDetection", "interval", "releaseName", "valuesFrom"], sorted(spec)


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


def test_nothing_flux_applies_is_a_secret():
    # The file checks above read text and file names. The render is what Flux applies: a
    # secretGenerator in an extensionless Kustomization file, or a kind written as an escape, a tag
    # or a block scalar, renders a Secret they miss (third C3 review, finding 6).
    # Characterisation (C6): nothing rendered has ever been a Secret, so this was green on arrival.
    # The mutations are in the design doc's log.
    secrets = sorted(
        (entry, doc["metadata"]["name"]) for entry in ENTRY_POINTS for doc in rendered(entry) if doc["kind"] == "Secret"
    )
    assert secrets == []


def monitoring_objects():
    """{kind: object} of what Flux's infrastructure Kustomization applies to the monitoring namespace."""
    return {
        doc["kind"]: doc
        for doc in rendered(KIND_INFRASTRUCTURE)
        if doc["metadata"].get("namespace") == "monitoring"
        or (doc["kind"] == "Namespace" and doc["metadata"]["name"] == "monitoring")
    }


def test_the_release_installs_the_pinned_chart():
    # Module 9 (D1 = C, D3 = C): Flux's infrastructure Kustomization installs kube-prometheus-stack,
    # and which chart it installs can't change without a PR.
    objects = monitoring_objects()
    assert {"HelmRelease", "OCIRepository"} <= set(objects), sorted(objects)
    release, source = objects["HelmRelease"], objects["OCIRepository"]
    # A wrong name leaves helm-controller no chart: the release never goes Ready, infrastructure fails
    # its wait, and every apps-* stops receiving changes (R11). kubeconform doesn't check names.
    assert release["spec"]["chartRef"] == {"kind": "OCIRepository", "name": source["metadata"]["name"]}, release["spec"]
    spec = source["spec"]
    assert spec["url"] == KUBE_PROMETHEUS_STACK, spec
    # The chart's templates run as cluster-admin. With a digest, a re-pushed tag changes nothing: the
    # digest beats the tag (Module 8 F22). The tag stays for people to read; P4b checks they agree.
    ref = spec.get("ref", {})
    assert sorted(ref) == ["digest", "tag"], ref
    assert re.fullmatch(r"\d+\.\d+\.\d+", ref["tag"]), ref
    assert DIGEST.fullmatch(ref["digest"]), ref


def test_the_release_reads_its_values_from_values_yaml():
    # values.yaml is what P4b's render test checks (image digests, selectors), so it must be what
    # helm-controller installs with; otherwise the chart runs on its defaults, tag-only images included.
    objects = monitoring_objects()
    assert "ConfigMap" in objects, sorted(objects)
    configmap = objects["ConfigMap"]
    assert configmap["data"] == {"values.yaml": (MONITORING / "values.yaml").read_text()}, sorted(configmap["data"])
    # The rendered name, not the generator's: kustomize doesn't rewrite valuesFrom, so a hash suffix would
    # leave it naming a ConfigMap that doesn't exist.
    assert objects["HelmRelease"]["spec"].get("valuesFrom") == [
        {"kind": "ConfigMap", "name": configmap["metadata"]["name"], "valuesKey": "values.yaml"}
    ], objects["HelmRelease"]["spec"]
    # helm-controller acts on a values change at once instead of at its 30m interval.
    assert configmap["metadata"].get("labels", {}).get("reconcile.fluxcd.io/watch") == "Enabled", configmap["metadata"]


def test_the_release_sets_only_known_fields():
    # Inline values or postRenderers would beat values.yaml, the file the tests check (Module 8's third
    # review, finding 2). A new field fails here until a test covers it.
    # Characterisation (C6) for this first assert: green on arrival; inline values turn it red (recorded).
    spec = monitoring_objects()["HelmRelease"]["spec"]
    allowed = {"chartRef", "driftDetection", "install", "interval", "timeout", "upgrade", "valuesFrom"}
    assert sorted(set(spec) - allowed) == [], sorted(spec)
    # Helm alone installs a chart's CRDs once and never updates them (F4): CreateReplace moves them with the
    # chart. RetryOnFailure retries a failed install every 5m; the default rolls it back and stalls until Git changes.
    assert spec.get("install") == {"crds": "Create", "strategy": {"name": "RetryOnFailure"}}, spec.get("install")
    assert spec.get("upgrade") == {"crds": "CreateReplace", "strategy": {"name": "RetryOnFailure"}}, spec.get("upgrade")


def test_no_flux_apply_holds_a_release_and_a_monitoring_object():
    # F22: Flux dry-runs a Kustomization's objects in one batch before applying any. A PrometheusRule
    # beside the HelmRelease that brings its CRD fails that dry-run, so the release is never applied and
    # the CRD never comes: infrastructure fails every retry and every apps-* stops. kubeconform catches
    # it only until P3a adds the monitoring schemas.
    # Characterisation (C6): green on arrival; a PrometheusRule in base/monitoring turns it red (recorded).
    mixed = []
    for entry in ENTRY_POINTS:
        docs = rendered(entry)
        if any(doc["kind"] == "HelmRelease" for doc in docs):
            mixed += [
                (entry, doc["kind"], doc["metadata"]["name"])
                for doc in docs
                if doc["apiVersion"].startswith("monitoring.coreos.com/")
            ]
    assert mixed == []


@pytest.fixture(scope="session")
def chart(tmp_path_factory):
    """The chart the HelmRelease installs, pulled by its digest as Flux pulls it (D3 = C)."""
    ref = monitoring_objects()["OCIRepository"]["spec"]["ref"]
    reference = f"{KUBE_PROMETHEUS_STACK}@{ref['digest']}"
    folder = tmp_path_factory.mktemp("chart")
    pull = subprocess.run(["helm", "pull", reference, "--destination", str(folder)], capture_output=True, text=True)
    if pull.returncode:
        fail_naming_the_cause(f"Pulling {reference}", "the chart reference", pull.stderr)
    (tgz,) = folder.glob("*.tgz")
    return tgz


def test_the_chart_at_the_digest_is_the_tagged_version(chart):
    # Flux installs the digest; people read the tag. An upgrade that changes one line of the two makes
    # the PR say one version while the cluster runs another, and nothing else compares them.
    show = subprocess.run(["helm", "show", "chart", str(chart)], capture_output=True, text=True, check=True)
    tag = monitoring_objects()["OCIRepository"]["spec"]["ref"]["tag"]
    assert yaml.safe_load(show.stdout)["version"] == tag


@pytest.fixture(scope="session")
def chart_render(chart):
    """The chart's manifests as helm-controller renders them: values.yaml, the release's name and namespace,
    and the kind node's Kubernetes version. Hooks included, because Helm renders them too."""
    release = monitoring_objects()["HelmRelease"]["metadata"]
    cluster = yaml.safe_load((REPO / "Acme" / "platform" / "kind" / "sample-api-cluster.yaml").read_text())
    kube_version = re.search(r":v(\d+\.\d+\.\d+)@", cluster["nodes"][0]["image"]).group(1)
    render = subprocess.run(
        ["helm", "template", release["name"], str(chart), "--namespace", release["namespace"],
         "--values", str(MONITORING / "values.yaml"), "--kube-version", kube_version],
        capture_output=True, text=True, check=True,
    )
    return render.stdout


def test_every_image_the_chart_runs_is_pinned(chart_render):
    # A re-pushed tag changes what runs, and the operator runs with cluster-wide rights (NFR3).
    # test_every_container_flux_deploys_has_a_tag_and_digest reads kustomize renders, so it never sees these.
    docs = [doc for doc in yaml.safe_load_all(chart_render) if doc]
    images = set(IMAGE_REFERENCE.findall(chart_render)) | {i for doc in docs for i in container_images(doc)}
    # Prometheus's image is in no container field, so only the scan finds it: a broken scan fails here.
    assert any("/prometheus/prometheus:" in image for image in images), sorted(images)
    assert sorted(image for image in images if not TAG_AND_DIGEST.search(image)) == []
