"""Structural tests for the Flux layout (Module 8): the image pins and the entry points Flux applies.

Every image Flux deploys is pinned by digest (design doc D4): with `name:tag@sha256:...` the
runtime pulls by digest, so a moved tag can't change what runs. Kubernetes, kustomize and the
schema check all accept a tag-only image, so nothing else notices a missing digest.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import functools
import http.server
import json
import os
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
# Where the remote resources kustomize renders come from: if it doesn't answer, a failed render is the network.
EGRESS_PROBE = "https://raw.githubusercontent.com/"
# Where the chart comes from: if ghcr.io doesn't answer, a failed chart pull is the network.
CHART_PROBE = "https://ghcr.io/v2/"
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
# The control-plane parts kind hides on loopback (Module 9 design, F8): a monitor for them only ever sees "down".
CONTROL_PLANE = re.compile(r"kube-controller-manager|kube-scheduler|etcd|kube-proxy")
# What helm-controller reads, by (kind, name): kind's overlay adds a second values ConfigMap, and PR 2 a
# dashboards one, so the kind alone no longer names one object.
RELEASE = ("HelmRelease", "kube-prometheus-stack")
CHART_SOURCE = ("OCIRepository", "kube-prometheus-stack")
BASE_VALUES = ("ConfigMap", "kube-prometheus-stack-values")
KIND_VALUES = ("ConfigMap", "kube-prometheus-stack-values-kind")
SAMPLE_API_ENVS = ("dev", "stage", "prod")


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


def sample_api_objects(env):
    """What Flux applies for `env`'s apps entry point, keyed by (kind, name)."""
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in rendered(f"Acme/platform/apps/overlays/{env}")}


def test_the_service_exposes_the_metrics_port_the_app_listens_on():
    # P1e (FR2): the app serves /metrics on :8081 (main.go, P1d), and Prometheus reaches it through the
    # Service's `metrics` port (P4c's ServiceMonitor names it). A named targetPort is looked up in the container.
    for env in SAMPLE_API_ENVS:
        objects = sample_api_objects(env)
        [container] = objects[("Deployment", "sample-api")]["spec"]["template"]["spec"]["containers"]
        ports = {port["name"]: port["containerPort"] for port in container["ports"]}
        assert ports.get("metrics") == 8081, (env, ports)
        targets = {port["name"]: port["targetPort"] for port in objects[("Service", "sample-api")]["spec"]["ports"]}
        assert ports.get(targets.get("metrics"), targets.get("metrics")) == 8081, (env, targets)


def test_each_env_scrapes_its_own_sample_api_metrics_port():
    # P4c (FR2): a ServiceMonitor in sample-api's base, so each env's namespace gets one. It must pick the
    # Service by its labels and name its `metrics` port (8081, test above), and stay in its own namespace:
    # with three envs, a namespaceSelector would scrape every env's pods three times over.
    for env in SAMPLE_API_ENVS:
        objects = sample_api_objects(env)
        monitor = objects.get(("ServiceMonitor", "sample-api"))
        assert monitor, (env, sorted(objects))
        service = objects[("Service", "sample-api")]
        selector = monitor["spec"]["selector"]["matchLabels"]
        assert selector.items() <= service["metadata"]["labels"].items(), (env, selector)
        endpoints = [(e.get("port"), e.get("path", "/metrics")) for e in monitor["spec"]["endpoints"]]
        assert endpoints == [("metrics", "/metrics")], (env, endpoints)
        assert "namespaceSelector" not in monitor["spec"], (env, monitor["spec"])
        assert monitor["metadata"]["namespace"] == service["metadata"]["namespace"], env


def env_value(objects, container, name):
    """`name`'s value in `container`, following a configMapKeyRef into the rendered ConfigMap; None if unset."""
    for var in container.get("env", []):
        if var["name"] == name:
            if "value" in var:
                return var["value"]
            ref = var["valueFrom"]["configMapKeyRef"]
            return objects[("ConfigMap", ref["name"])].get("data", {}).get(ref["key"])
    return None


def test_only_dev_turns_fault_injection_on():
    # P1e (R7): /fault answers 500 only where FAULT_INJECTION=enabled (main.go, P1c), so only dev may set it.
    # The stage and prod half is characterisation (C6): green on arrival. The mutation is in the design log.
    found = {}
    for env in SAMPLE_API_ENVS:
        objects = sample_api_objects(env)
        [container] = objects[("Deployment", "sample-api")]["spec"]["template"]["spec"]["containers"]
        found[env] = env_value(objects, container, "FAULT_INJECTION")
    assert found == {"dev": "enabled", "stage": None, "prod": None}


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


def egress_works(probe):
    """Whether `probe` answers at all. Any HTTP status means the network is up; only no answer means it's down."""
    try:
        with urllib.request.urlopen(urllib.request.Request(probe, method="HEAD"), timeout=10):
            return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def fail_naming_the_cause(action, suspect, stderr, probe=None):
    """Fail, blaming the network if `probe` (default EGRESS_PROBE) doesn't answer, else `suspect` (PR #22 finding 1, D6)."""
    probe = probe or EGRESS_PROBE
    if egress_works(probe):
        cause = f"{probe} answers, so the cause is {suspect}"
    else:
        cause = f"the network is down ({probe} doesn't answer)"
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
    # Charts that a HelmRelease installs aren't in these renders: test_every_image_the_chart_runs_is_pinned covers them.
    # Characterisation (C6): every image was already pinned, so this was green on arrival. The
    # mutations are in the design doc's log.
    found = {}
    for entry in ENTRY_POINTS:
        found[entry] = [image for doc in rendered(entry) for image in container_images(doc)]
    # Bootstrap holds no containers: test_operator_image_is_pinned_by_tag_and_digest covers its image.
    # monitoring-config holds only the alert rule and dashboard ConfigMaps (Module 9 D1 = C).
    no_containers = {"Acme/infra/bootstrap", "Acme/platform/infrastructure/monitoring-config"}
    empty = [entry for entry, images in found.items() if not images and entry not in no_containers]
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
    assert sorted(kustomizations) == sorted(["infrastructure", "monitoring-config"] + [f"apps-{env}" for env in envs])
    assert kustomizations["infrastructure"]["path"] == "./Acme/platform/infrastructure/overlays/kind"
    # The ingress webhook must be running before any Ingress is applied.
    assert kustomizations["infrastructure"].get("wait") is True
    # Module 9 D1 = C: the alert rule and our dashboard need the chart's CRDs, so they apply only after
    # infrastructure is Ready (F22). wait: Ready means the rule is in the cluster, not just sent.
    config = kustomizations["monitoring-config"]
    assert config["path"] == "./Acme/platform/infrastructure/monitoring-config"
    assert {"name": "infrastructure"} in config.get("dependsOn", [])
    assert config.get("wait") is True
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


def seconds(duration):
    """A Go duration as Flux writes it ("10m", "1h30m", "5m0s"), in seconds; anything else fails."""
    parts = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert duration and parts, duration
    return sum(int(value or 0) * unit for value, unit in zip(parts.groups(), (3600, 60, 1)))


def longest_helm_attempt(spec):
    """The longest single Helm install or upgrade a HelmRelease allows, in seconds. install.timeout and
    upgrade.timeout default to spec.timeout, which defaults to helm-controller's 5m; either, when set, overrides it."""
    overall = spec.get("timeout", "5m")
    return max(seconds(spec.get(action, {}).get("timeout", overall)) for action in ("install", "upgrade"))


def test_infrastructure_outlasts_every_helm_attempt_it_waits_on():
    # Carry-overs D6: infrastructure (wait: true) waits for each HelmRelease it applies to be Ready, and its
    # clock starts before Helm's attempt does. If it isn't longer, a slow but healthy install marks
    # infrastructure failed and holds back every apps-* (dependsOn) until its next retry.
    outer = seconds(flux_kustomizations()["infrastructure"]["timeout"])
    releases = [doc for doc in rendered(KIND_INFRASTRUCTURE) if doc["kind"] == "HelmRelease"]
    assert releases, KIND_INFRASTRUCTURE
    inner = {doc["metadata"]["name"]: longest_helm_attempt(doc["spec"]) for doc in releases}
    assert {name: limit for name, limit in inner.items() if limit >= outer} == {}, (outer, inner)


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
    # The chart's templates run as cluster-admin. With a digest, a re-pushed 0.62.1 changes nothing:
    # the digest beats semver (Module 9 P0e, D3 = C). semver stays, tied to the input, for people to read.
    assert source["ref"] == {"semver": "<< inputs.version | quote >>", "digest": "<< inputs.digest | quote >>"}, source["ref"]
    assert DIGEST.fullmatch(inputs[0].get("digest", "")), inputs


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


def test_the_operator_chart_at_the_digest_is_the_input_version(tmp_path):
    # Flux pulls the digest; people and the image-tag check read the version. An upgrade that bumps the
    # version but not the digest would keep installing the old chart beside the new image (Module 9 P0e).
    resourceset, templates = operator_resourceset()
    inputs = resourceset["spec"]["inputs"][0]
    reference = f"{templates['OCIRepository']['spec']['url']}@{inputs['digest']}"
    pull = subprocess.run(["helm", "pull", reference, "--destination", str(tmp_path)], capture_output=True, text=True)
    if pull.returncode:
        fail_naming_the_cause(f"Pulling {reference}", "the chart digest", pull.stderr, probe=CHART_PROBE)
    (tgz,) = tmp_path.glob("*.tgz")
    show = subprocess.run(["helm", "show", "chart", str(tgz)], capture_output=True, text=True, check=True)
    assert yaml.safe_load(show.stdout)["version"] == inputs["version"]


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
    """{(kind, name): object} of what Flux's infrastructure Kustomization applies to the monitoring namespace."""
    return {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in rendered(KIND_INFRASTRUCTURE)
        if doc["metadata"].get("namespace") == "monitoring"
        or (doc["kind"] == "Namespace" and doc["metadata"]["name"] == "monitoring")
    }


def test_the_release_installs_the_pinned_chart():
    # Module 9 (D1 = C, D3 = C): Flux's infrastructure Kustomization installs kube-prometheus-stack,
    # and which chart it installs can't change without a PR.
    objects = monitoring_objects()
    assert {RELEASE, CHART_SOURCE} <= set(objects), sorted(objects)
    release, source = objects[RELEASE], objects[CHART_SOURCE]
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
    # values.yaml is what P4b's render test checks (image digests), so it must be what helm-controller
    # installs with; otherwise the chart runs on its defaults, tag-only images included. kind adds its own
    # file after it: helm-controller merges valuesFrom in order, and the later file wins.
    objects = monitoring_objects()
    files = {BASE_VALUES: MONITORING / "values.yaml", KIND_VALUES: REPO / KIND_INFRASTRUCTURE / "monitoring-values.yaml"}
    assert set(files) <= set(objects), sorted(objects)
    # The rendered names, not the generator's: kustomize doesn't rewrite valuesFrom, so a hash suffix would
    # leave it naming a ConfigMap that doesn't exist.
    assert objects[RELEASE]["spec"].get("valuesFrom") == [
        {"kind": "ConfigMap", "name": name, "valuesKey": "values.yaml"} for _, name in files
    ], objects[RELEASE]["spec"]
    for key, path in files.items():
        assert objects[key]["data"] == {"values.yaml": path.read_text()}, key
        # helm-controller acts on a values change at once instead of at its 30m interval.
        assert objects[key]["metadata"].get("labels", {}).get("reconcile.fluxcd.io/watch") == "Enabled", key


def test_the_release_sets_only_known_fields():
    # Inline values or postRenderers would beat values.yaml, the file the tests check (Module 8's third
    # review, finding 2). A new field fails here until a test covers it.
    # Characterisation (C6) for this first assert: green on arrival; inline values turn it red (recorded).
    spec = monitoring_objects()[RELEASE]["spec"]
    allowed = {"chartRef", "driftDetection", "install", "interval", "timeout", "upgrade", "valuesFrom"}
    assert sorted(set(spec) - allowed) == [], sorted(spec)
    # Helm alone installs a chart's CRDs once and never updates them (F4): CreateReplace moves them with the
    # chart. RetryOnFailure retries a failed install every 5m; the default rolls it back and stalls until Git changes.
    assert spec.get("install") == {"crds": "Create", "strategy": {"name": "RetryOnFailure"}}, spec.get("install")
    assert spec.get("upgrade") == {"crds": "CreateReplace", "strategy": {"name": "RetryOnFailure"}}, spec.get("upgrade")


def test_the_release_reverts_hand_edits():
    # Carry-overs D5, reopening L6's cycle 4: without `mode: enabled`, a kubectl edit of a chart object stays
    # until the chart or values change, and nothing fails (`warn` only logs it). P9 proved the behaviour live
    # (Grafana scaled to 0, put back); this guards the setting that gives it. The ignore stops every reconcile
    # stripping the webhook's annotation from each PrometheusRule, and the webhook re-adding it (design F10).
    # Characterisation (C6): green on arrival; the mutations are in the design log.
    drift = monitoring_objects()[RELEASE]["spec"].get("driftDetection", {})
    assert drift.get("mode") == "enabled", drift
    ignore = {"paths": ["/metadata/annotations/prometheus-operator-validated"], "target": {"kind": "PrometheusRule"}}
    assert ignore in drift.get("ignore", []), drift


def test_kind_prometheus_keeps_two_days():
    # Carry-overs D5: kind's Prometheus stores its samples in emptyDir, which a restart wipes, so longer
    # history only costs the laptop memory and disk. Losing this line brings back the chart's 10d with no
    # failure, on a VM sized for 2d (P6 measured 216 MiB). The values test proves the release reads this file.
    # Characterisation (C6): green on arrival; the mutation is in the design log.
    values = yaml.safe_load(monitoring_objects()[KIND_VALUES]["data"]["values.yaml"])
    assert values["prometheus"]["prometheusSpec"].get("retention") == "2d", values["prometheus"]["prometheusSpec"]


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
    ref = monitoring_objects()[CHART_SOURCE]["spec"]["ref"]
    reference = f"{KUBE_PROMETHEUS_STACK}@{ref['digest']}"
    folder = tmp_path_factory.mktemp("chart")
    pull = subprocess.run(["helm", "pull", reference, "--destination", str(folder)], capture_output=True, text=True)
    if pull.returncode:
        fail_naming_the_cause(f"Pulling {reference}", "the chart reference", pull.stderr, probe=CHART_PROBE)
    (tgz,) = folder.glob("*.tgz")
    return tgz


def test_the_chart_at_the_digest_is_the_tagged_version(chart):
    # Flux installs the digest; people read the tag. An upgrade that changes one line of the two makes
    # the PR say one version while the cluster runs another, and nothing else compares them.
    show = subprocess.run(["helm", "show", "chart", str(chart)], capture_output=True, text=True, check=True)
    tag = monitoring_objects()[CHART_SOURCE]["spec"]["ref"]["tag"]
    assert yaml.safe_load(show.stdout)["version"] == tag


def helm_template(chart, *values):
    """The chart's manifests as helm-controller renders them: `values` files merged in order (the later
    wins), the release's name and namespace, and the kind node's Kubernetes version. Hooks included,
    because Helm renders them too."""
    release = monitoring_objects()[RELEASE]["metadata"]
    cluster = yaml.safe_load((REPO / "Acme" / "platform" / "kind" / "sample-api-cluster.yaml").read_text())
    kube_version = re.search(r":v(\d+\.\d+\.\d+)@", cluster["nodes"][0]["image"]).group(1)
    render = subprocess.run(
        ["helm", "template", release["name"], str(chart), "--namespace", release["namespace"],
         *[arg for path in values for arg in ("--values", str(path))], "--kube-version", kube_version],
        capture_output=True, text=True, check=True,
    )
    return render.stdout


@pytest.fixture(scope="session")
def chart_render(chart):
    """The chart rendered with base values.yaml alone."""
    return helm_template(chart, MONITORING / "values.yaml")


def test_every_image_the_chart_runs_is_pinned(chart_render):
    # A re-pushed tag changes what runs, and the operator runs with cluster-wide rights (NFR3).
    # test_every_container_flux_deploys_has_a_tag_and_digest reads kustomize renders, so it never sees these.
    docs = [doc for doc in yaml.safe_load_all(chart_render) if doc]
    images = set(IMAGE_REFERENCE.findall(chart_render)) | {i for doc in docs for i in container_images(doc)}
    # Prometheus's image is in no container field, so only the scan finds it: a broken scan fails here.
    assert any("/prometheus/prometheus:" in image for image in images), sorted(images)
    assert sorted(image for image in images if not TAG_AND_DIGEST.search(image)) == []


def selects(selector, labels):
    """Whether a label selector picks `labels`. matchExpressions aren't read, so they fail loudly here."""
    assert set(selector) <= {"matchLabels"}, selector
    return selector.get("matchLabels", {}).items() <= labels.items()


def test_prometheus_selects_each_env_sample_api_monitor(chart_render):
    # P4c+ (F7): the chart's Prometheus picks only monitors labelled release: <release> unless told
    # otherwise, so sample-api's unlabelled ServiceMonitor would never be scraped, silently. Checked
    # against each env's real monitor and namespace, so a selector narrowed again, or a label dropped
    # from either side, shows here.
    [prometheus] = [doc for doc in yaml.safe_load_all(chart_render) if doc and doc["kind"] == "Prometheus"]
    spec = prometheus["spec"]
    for env in SAMPLE_API_ENVS:
        objects = sample_api_objects(env)
        monitor = objects[("ServiceMonitor", "sample-api")]
        namespace = objects[("Namespace", monitor["metadata"]["namespace"])]
        assert selects(spec["serviceMonitorSelector"], monitor["metadata"].get("labels", {})), (
            env, spec["serviceMonitorSelector"])
        assert selects(spec["serviceMonitorNamespaceSelector"], namespace["metadata"].get("labels", {})), (
            env, spec["serviceMonitorNamespaceSelector"])


MONITORING_CONFIG = "Acme/platform/infrastructure/monitoring-config"
ALERT_TESTS = REPO / MONITORING_CONFIG / "sample-api-alerts.test.yaml"
SAMPLE_API_DASHBOARD = REPO / MONITORING_CONFIG / "dashboards" / "sample-api.json"
# D4 = C: the chart's dashboard the on-call doc names for cluster health, by uid and title.
CLUSTER_HEALTH_DASHBOARD = ("7d57716318ee0dddbac5a7f451fb7753", "Node Exporter / Nodes")


def promtool(chart_render, folder, *args):
    """promtool from the Prometheus image the chart runs (P3b = B: values.yaml holds the one pin), run in `folder`."""
    [prometheus] = [doc for doc in yaml.safe_load_all(chart_render) if doc and doc["kind"] == "Prometheus"]
    image = prometheus["spec"]["image"]
    assert TAG_AND_DIGEST.search(image), image
    docker = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if docker.returncode:
        pytest.fail(f"promtool runs in Docker, which isn't answering (start Docker Desktop): {docker.stderr.strip()}")
    # As the host's user: pytest's tmp_path is mode 0700, and the image's own user couldn't read it.
    return subprocess.run(
        ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "--volume", f"{folder}:/work",
         "--workdir", "/work", "--entrypoint", "/bin/promtool", image, *args],
        capture_output=True, text=True,
    )


def test_sample_api_alert_behaves_as_its_promtool_tests_say(chart_render, tmp_path):
    # FR3: the rule's maths, proved before merge. The rule is read from what Flux applies (a file the
    # kustomization doesn't list is never applied). promtool reads plain rule files, not Kubernetes
    # objects, so the PrometheusRule's spec is copied out: the object in Git stays the only copy.
    [rule] = [doc for doc in rendered(MONITORING_CONFIG) if doc["kind"] == "PrometheusRule"]
    (tmp_path / "sample-api-alerts.rules.yaml").write_text(yaml.safe_dump(rule["spec"]))
    (tmp_path / ALERT_TESTS.name).write_text(ALERT_TESTS.read_text())
    run = promtool(chart_render, tmp_path, "test", "rules", ALERT_TESTS.name)
    assert run.returncode == 0, run.stdout + run.stderr


def test_prometheus_loads_the_sample_api_rule(chart_render):
    # P5 (F7 again): the chart's Prometheus loads only rules labelled release: <release> unless told
    # otherwise, so the unlabelled rule would pass promtool and still never be evaluated, silently.
    # Checked against the rule Flux applies and the namespace it lands in.
    [prometheus] = [doc for doc in yaml.safe_load_all(chart_render) if doc and doc["kind"] == "Prometheus"]
    spec = prometheus["spec"]
    [rule] = [doc for doc in rendered(MONITORING_CONFIG) if doc["kind"] == "PrometheusRule"]
    namespace = monitoring_objects()[("Namespace", rule["metadata"]["namespace"])]
    assert selects(spec["ruleSelector"], rule["metadata"].get("labels", {})), spec["ruleSelector"]
    assert selects(spec["ruleNamespaceSelector"], namespace["metadata"].get("labels", {})), (
        spec["ruleNamespaceSelector"])


def test_nothing_watches_the_control_plane_kind_hides(chart_render):
    # kind binds these to 127.0.0.1 (F8), so their targets are down for good and their alerts never stop firing.
    docs = [doc for doc in yaml.safe_load_all(chart_render) if doc]
    monitors = [d["metadata"]["name"] for d in docs if d["kind"] == "ServiceMonitor"]
    exprs = [r["expr"] for d in docs if d["kind"] == "PrometheusRule"
             for g in d["spec"]["groups"] for r in g["rules"]]
    assert [m for m in monitors if CONTROL_PLANE.search(m)] == []
    assert [e for e in exprs if CONTROL_PLANE.search(str(e))] == []


def sidecar_dashboards(chart_docs, docs):
    """{file name: JSON text} for each dashboard in `docs` that Grafana's sidecar loads: ConfigMaps carrying
    the label the chart gives the sidecar. The label is read from the chart, so a renamed one shows."""
    [grafana] = [d for d in chart_docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-grafana")]
    [sidecar] = [c for c in grafana["spec"]["template"]["spec"]["containers"] if c["name"] == "grafana-sc-dashboard"]
    env = {e["name"]: e.get("value") for e in sidecar["env"]}
    label = (env["LABEL"], env["LABEL_VALUE"])
    return {key: text for d in docs if d["kind"] == "ConfigMap" and label in d["metadata"].get("labels", {}).items()
            for key, text in d.get("data", {}).items()}


def test_grafana_loads_the_sample_api_dashboard(chart_render):
    # FR4 (D4 = C): Grafana sees a dashboard only through its sidecar, which loads ConfigMaps carrying the
    # label the chart gives it. The label is read from the chart, so a renamed one shows here, not as a
    # dashboard missing from Grafana. The file in Git must be what Flux applies, and its uid its own: two
    # dashboards with one uid means one silently replaces the other.
    chart_docs = [doc for doc in yaml.safe_load_all(chart_render) if doc]
    ours = sidecar_dashboards(chart_docs, rendered(MONITORING_CONFIG))
    assert sorted(ours) == [SAMPLE_API_DASHBOARD.name]
    assert ours[SAMPLE_API_DASHBOARD.name] == SAMPLE_API_DASHBOARD.read_text()
    defaults = {json.loads(text)["uid"] for text in sidecar_dashboards(chart_docs, chart_docs).values()}
    assert json.loads(ours[SAMPLE_API_DASHBOARD.name])["uid"] not in defaults, sorted(defaults)


def test_every_sample_api_dashboard_query_is_valid_promql(chart_render, tmp_path):
    # FR4: Grafana sends a panel's query to Prometheus only when someone opens the dashboard, so a typo shows
    # as an empty panel during an incident, not as a failure. Grafana fills in its variables first, so the test
    # does too, then has promtool (the pinned Prometheus image's) parse each query as a recording rule. The
    # variable's own query, label_values(...), is Grafana's syntax, not PromQL, so it isn't checked.
    # Characterisation (C6): green on arrival; a broken query turns it red (recorded).
    dashboard = json.loads(SAMPLE_API_DASHBOARD.read_text())
    filled = {"$__rate_interval": "5m", "$namespace": "sample-api-dev"}
    rules = []
    for panel in [p for top in dashboard["panels"] for p in [top, *top.get("panels", [])]]:
        for target in panel.get("targets", []):
            expr = target["expr"]
            for variable, value in filled.items():
                expr = expr.replace(variable, value)
            assert "$" not in expr, (panel["title"], expr)
            rules.append({"record": f"dashboard:panel_{panel['id']}_{target['refId']}", "expr": expr})
    assert rules, "no queries found in the dashboard"
    (tmp_path / "dashboard.rules.yaml").write_text(
        yaml.safe_dump({"groups": [{"name": "sample-api-dashboard", "rules": rules}]}))
    run = promtool(chart_render, tmp_path, "check", "rules", "dashboard.rules.yaml")
    assert run.returncode == 0, run.stdout + run.stderr


def test_every_sample_api_dashboard_query_uses_the_chart_datasource(chart_render):
    # FR4: a panel names its datasource by uid. A uid Grafana doesn't have (an export's ${DS_PROMETHEUS},
    # or one a UI edit made up) shows only as "datasource not found" when someone opens the panel. The uid
    # is read from what the chart provisions, so a renamed one shows here. Grafana's own annotation
    # source (-- Grafana --) isn't a query. Characterisation (C6): green on arrival; a wrong uid turns it red.
    [provisioned] = [d for d in yaml.safe_load_all(chart_render) if d and d["kind"] == "ConfigMap"
                     and d["metadata"].get("labels", {}).get("grafana_datasource") == "1"]
    [prometheus] = [s for s in yaml.safe_load(provisioned["data"]["datasource.yaml"])["datasources"]
                    if s["type"] == "prometheus"]

    def datasources(node):
        if isinstance(node, dict):
            if "datasource" in node:
                yield node["datasource"]
            for value in node.values():
                yield from datasources(value)
        elif isinstance(node, list):
            for item in node:
                yield from datasources(item)

    used = [s for s in datasources(json.loads(SAMPLE_API_DASHBOARD.read_text())) if s != {
        "type": "grafana", "uid": "-- Grafana --"}]
    assert used, "no datasources found in the dashboard"
    assert sorted({s["uid"] for s in used}) == [prometheus["uid"]], used


def test_the_named_cluster_health_dashboard_is_installed(chart_render):
    # D4 = C: the on-call doc (P8) sends people to this chart dashboard for cluster health. It arrives only
    # while grafana.defaultDashboardsEnabled is on and the chart still ships it, so the switch turned off or
    # a chart bump that drops or renames it would leave the doc pointing at nothing.
    # Characterisation (C6): green on arrival; the switch off turns it red.
    chart_docs = [doc for doc in yaml.safe_load_all(chart_render) if doc]
    dashboards = [json.loads(text) for text in sidecar_dashboards(chart_docs, chart_docs).values()]
    uid, title = CLUSTER_HEALTH_DASHBOARD
    assert {d["uid"]: d["title"] for d in dashboards}.get(uid) == title, sorted(d["title"] for d in dashboards)


@pytest.fixture(scope="session")
def kind_chart_render(chart, tmp_path_factory):
    """The chart as the kind cluster gets it: each ConfigMap the release's valuesFrom names, in that order
    (the later wins), taken from what Flux applies, so a dropped valuesFrom entry shows too."""
    objects = monitoring_objects()
    folder = tmp_path_factory.mktemp("kind-values")
    files = []
    for i, source in enumerate(objects[RELEASE]["spec"]["valuesFrom"]):
        path = folder / f"{i}-{source['name']}.yaml"
        path.write_text(objects[(source["kind"], source["name"])]["data"][source["valuesKey"]])
        files.append(path)
    return helm_template(chart, *files)


def test_kind_drops_the_alert_its_clock_sets_off(kind_chart_render):
    # WSL2 steps the kind node's clock, which moves every process's process_start_time_seconds at each
    # scrape (mon-test, 2026-11-10: 20 changes in 10 min, 0 restarts), so AlertmanagerClusterCrashlooping,
    # the chart's only rule on that metric, fires for good on kind, the live cluster included. A real
    # crashloop still shows in KubePodCrashLooping, which counts restarts, so that one must stay.
    docs = [doc for doc in yaml.safe_load_all(kind_chart_render) if doc]
    alerts = [r.get("alert") for d in docs if d["kind"] == "PrometheusRule"
              for g in d["spec"]["groups"] for r in g["rules"]]
    assert "KubePodCrashLooping" in alerts, "the chart's restart alert is missing, or no rules were found"
    assert "AlertmanagerClusterCrashlooping" not in alerts
