"""Structural tests for sample-api-ci.yml: which changed paths turn on which jobs, and build caching.

`changes` greps the PR's changed files with one regex per output. `scripts=true` runs scripts-test,
so every file a test in this directory reads must match the scripts regex. Otherwise a change to
that file alone skips the test that would catch it, and a skipped job counts as passing.

    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "sample-api-ci.yml"
REPO = WORKFLOW.parents[2]

# Files a structural test reads. Some don't exist yet: listing them now covers their tests from
# the commit that adds them. test_merge_gate_ruleset.py reads every workflow, so a new one must
# be covered too: new-workflow.yml stands for any workflow added later.
GUARDED = [
    "Acme/infra/github/main-merge-gate-ruleset.json",
    ".github/workflows/sample-api-ci.yml",
    ".github/workflows/sample-api-rescan.yml",
    ".github/workflows/terraform-apply.yml",
    ".github/workflows/terraform-plan.yml",
    ".github/workflows/terraform-plan-reusable.yml",
    ".github/workflows/new-workflow.yml",
    "Acme/apps/sample-api/Dockerfile",
    "Acme/apps/sample-api/Dockerfile.test",
    "Acme/infra/github/trusted-branches-ruleset.json",
    "Acme/infra/github/main-protect-ruleset.json",
    "Acme/platform/apps/sample-api/overlays/dev/kustomization.yaml",
    "Acme/platform/apps/sample-api/overlays/stage/kustomization.yaml",
    "Acme/platform/apps/sample-api/overlays/prod/kustomization.yaml",
    "Acme/platform/apps/overlays/dev/kustomization.yaml",
    "Acme/platform/apps/overlays/stage/kustomization.yaml",
    "Acme/platform/apps/overlays/prod/kustomization.yaml",
    "Acme/platform/apps/overlays/dev/image-automation.yaml",
    "Acme/infra/bootstrap/flux-operator-values.yaml",
    "Acme/infra/bootstrap/flux-operator.yaml",
    "Acme/infra/bootstrap/flux-instance.yaml",
    "Acme/infra/bootstrap/kustomization.yaml",
    "Acme/infra/bootstrap/platform.yaml",
    # test_flux_layout.py's guard reads subfolders too: this is where `flux bootstrap` writes.
    "Acme/infra/bootstrap/flux-system/gotk-components.yaml",
    # test_kustomize_job_validates_against_the_cluster_kubernetes_version reads the node image.
    "Acme/platform/kind/sample-api-cluster.yaml",
    # test_flux_layout.py's no-Secret checks read every YAML file under Acme/: these stand for the rest.
    "Acme/new-file.yaml",
    "Acme/new-file.yml",
    "Acme/platform/apps/sample-api/base/new-file.yaml",
    "Acme/platform/infrastructure/overlays/kind/new-file.yaml",
    # test_flux_layout.py renders every entry point, and kustomize reads any file a kustomization
    # lists, whatever its extension, and a kustomization file named plain Kustomization.
    "Acme/platform/apps/sample-api/overlays/dev/new-patch.json",
    "Acme/platform/apps/overlays/dev/extra/Kustomization",
    "Acme/infra/bootstrap/new-file.json",
]

# One path per prefix in the app filter: the app's code, its manifests, Flux's bootstrap
# manifests (Module 8), this workflow and the
# scripts on the image's build, scan and push path. A change to any one alone must run lint,
# test, build-scan and push, not just scripts-test. Scripts are listed before they exist (L9a-7).
APP_PATHS = [
    "Acme/apps/sample-api/main.go",
    "Acme/platform/apps/sample-api/base/deployment.yaml",
    ".github/workflows/sample-api-ci.yml",
    ".github/scripts/oci-platform-layout.sh",
    ".github/scripts/push-oci.sh",
    "Acme/infra/bootstrap/kustomization.yaml",
]

# The directories Flux applies (Module 8). The required kustomize check must render each one, or a
# broken entry point merges and only fails in the cluster.
ENTRY_POINTS = [
    "Acme/platform/apps/overlays/dev",
    "Acme/platform/apps/overlays/stage",
    "Acme/platform/apps/overlays/prod",
    "Acme/infra/bootstrap",
    "Acme/platform/infrastructure/overlays/kind",
]

# The kustomize each Flux release renders with: its kustomize-controller's go.mod replaces
# sigs.k8s.io/kustomize/api with this release's api. Flux 2.10.0 = kustomize-controller v1.10.1,
# api v0.21.1 = kustomize v5.8.1. A Flux bump fails until its row is looked up and added (D7).
KUSTOMIZE_FOR_FLUX = {"2.10.0": "5.8.1"}
# The Helm each Flux release renders charts with: its helm-controller embeds helm.sh/helm/v4 at this
# version. Flux 2.10.0 = helm-controller v1.7.1 = Helm v4.3.1. A Flux bump fails until its row is added.
HELM_FOR_FLUX = {"2.10.0": "4.3.1"}


@pytest.fixture(scope="module")
def filters():
    """{output: regex} from the diff step's `grep -qE '<regex>' ...; then echo "<output>=true"` lines."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["changes"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "diff")
    found = re.findall(r"grep -qE '([^']+)' <<<\"\$changed\"; then\s+echo \"(\w+)=true\"", script)
    filters = {output: regex for regex, output in found}
    # If the step is reworded and nothing is extracted, fail here rather than with a KeyError.
    assert set(filters) == {"app", "scripts"}, found
    return filters


def test_scripts_filter_covers_guarded_files(filters):
    # grep -E and Python's re agree on what these regexes use: ^ $ ( | ) and \.
    missed = [path for path in GUARDED if not re.search(filters["scripts"], path)]
    assert missed == []


def test_app_filter_covers_app_paths(filters):
    missed = [path for path in APP_PATHS if not re.search(filters["app"], path)]
    assert missed == []


def test_changes_step_sees_a_non_ascii_path(tmp_path):
    # git quotes a path with non-ASCII bytes ("Acme/platform/caf\303\251.yaml"), so no ^Acme/ filter
    # matches it: every job skips, and a skipped job passes the merge gate. This runs the step itself
    # on a throwaway repo, with git's defaults (no user or system config).
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["changes"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "diff")
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }

    def git(*args):
        run = subprocess.run(["git", *args], cwd=tmp_path, env=env, stdout=subprocess.PIPE, text=True, check=True)
        return run.stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base")
    base = git("rev-parse", "HEAD")
    path = tmp_path / "Acme" / "platform" / "café.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("kind: ConfigMap\n")
    git("add", "-A")
    git("commit", "-q", "-m", "head")
    output = tmp_path / "github_output"
    step_env = {**env, "EVENT": "pull_request", "PR_BASE": base, "PR_HEAD": git("rev-parse", "HEAD")}
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=tmp_path,
        env={**step_env, "GITHUB_OUTPUT": str(output)},
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    assert output.read_text().split() == ["app=true", "scripts=true"]


def test_kustomize_job_renders_every_entry_point():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["kustomize"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Render every base and overlay")
    loop = re.search(r"for dir in (.*?); do", script, re.DOTALL)
    assert loop, script
    # Expand each entry as bash would: a glob counts only for directories that exist.
    entries = [entry for entry in loop.group(1).split() if entry != "\\"]
    rendered = {path.relative_to(REPO).as_posix() for entry in entries for path in REPO.glob(entry)}
    missed = [path for path in ENTRY_POINTS if path not in rendered]
    assert missed == []


def kustomize_step(name):
    """The `kustomize` job's step called `name`."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["kustomize"]["steps"]
    names = [s.get("name") for s in steps]
    assert name in names, names
    return steps[names.index(name)]


def commands(step):
    """The commands bash runs: comment lines dropped, backslash continuations joined."""
    script = re.sub(r"\\\n\s*", "", step["run"])
    return [line.strip() for line in script.splitlines() if line.strip() and not line.strip().startswith("#")]


def kubeconform_command(step):
    """The step's one `kubeconform -strict` command."""
    found = [line for line in commands(step) if line.startswith("kubeconform -strict")]
    assert len(found) == 1, step["run"]
    return found[0]


def test_kustomize_job_validates_schemas():
    # D7: kubeconform checks what the render step wrote against Kubernetes', Flux's and the
    # Operator's schemas. Each download is pinned by version and checksum, as Conftest is.
    step = kustomize_step("Validate manifests against their schemas")
    env = step.get("env", {})
    for tool in ("KUBECONFORM", "FLUX", "OPERATOR"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(env.get(f"{tool}_VERSION", ""))), env
        assert re.fullmatch(r"[0-9a-f]{64}", str(env.get(f"{tool}_SHA256", ""))), env
    assert step["run"].count("sha256sum -c") == 4, step["run"]
    # -strict rejects unknown fields in Kubernetes' own kinds, and repeated keys.
    assert "-strict" in step["run"], step["run"]


def test_kustomize_job_validates_against_the_versions_the_cluster_runs():
    # A Flux or Operator upgrade in Acme/infra/bootstrap must move these pins too, or CI keeps
    # validating against the old schemas. Both pairs in one assert, so one mismatch can't hide the other.
    env = kustomize_step("Validate manifests against their schemas").get("env", {})
    bootstrap = REPO / "Acme" / "infra" / "bootstrap"
    flux = yaml.safe_load((bootstrap / "flux-instance.yaml").read_text())["spec"]["distribution"]["version"]
    tag = yaml.safe_load((bootstrap / "flux-operator-values.yaml").read_text())["image"]["tag"]
    operator = tag.removeprefix("v").split("@")[0]
    assert (str(env.get("FLUX_VERSION")), str(env.get("OPERATOR_VERSION"))) == (flux, operator)


def test_helm_pin_matches_the_flux_version():
    # scripts-test installs HELM_VERSION, so P4b renders kube-prometheus-stack as helm-controller does;
    # the runner image ships Helm 3.22.1. FLUX_VERSION is tied to flux-instance.yaml by the test above.
    env = yaml.safe_load(WORKFLOW.read_text()).get("env", {})
    flux = str(kustomize_step("Validate manifests against their schemas").get("env", {}).get("FLUX_VERSION"))
    assert flux in HELM_FOR_FLUX, f"look up the Helm that Flux {flux}'s helm-controller embeds"
    assert str(env.get("HELM_VERSION")) == HELM_FOR_FLUX[flux], env
    assert re.fullmatch(r"[0-9a-f]{64}", str(env.get("HELM_SHA256", ""))), env


def test_kustomize_pin_matches_the_flux_version():
    # Both render jobs install KUSTOMIZE_VERSION, so CI renders as Flux's controller does, whatever
    # kubectl the runner image ships. FLUX_VERSION is tied to flux-instance.yaml by the test above.
    env = yaml.safe_load(WORKFLOW.read_text()).get("env", {})
    flux = str(kustomize_step("Validate manifests against their schemas").get("env", {}).get("FLUX_VERSION"))
    assert flux in KUSTOMIZE_FOR_FLUX, f"look up the kustomize that Flux {flux}'s kustomize-controller uses"
    assert str(env.get("KUSTOMIZE_VERSION")) == KUSTOMIZE_FOR_FLUX[flux], env
    assert re.fullmatch(r"[0-9a-f]{64}", str(env.get("KUSTOMIZE_SHA256", ""))), env


def test_kustomize_job_installs_the_pinned_kustomize_first():
    # The runner image ships its own kustomize (5.8.3 on 20261101.360.1), so without this install,
    # or with it after the render, `kustomize build` still succeeds with the wrong version.
    # scripts-test isn't checked here: test_flux_layout.py asserts the version it actually runs.
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["kustomize"]["steps"]
    names = [s.get("name") for s in steps]
    assert "Install kustomize" in names, names
    assert names.index("Install kustomize") < names.index("Render every base and overlay"), names
    lines = commands(steps[names.index("Install kustomize")])
    assert any(line.startswith("curl") and "${KUSTOMIZE_VERSION}" in line for line in lines), lines
    assert any("${KUSTOMIZE_SHA256}" in line and "sha256sum -c" in line for line in lines), lines
    assert "kustomize version" in lines, lines
    render = commands(kustomize_step("Render every base and overlay"))
    assert any(line.startswith('kustomize build "$dir"') for line in render), render


def test_kustomize_job_validates_against_the_cluster_kubernetes_version():
    # Without -kubernetes-version, kubeconform uses the schemas for Kubernetes master, not what
    # the cluster runs. A node image upgrade must move this too, as a Flux upgrade does above.
    step = kustomize_step("Validate manifests against their schemas")
    env = step.get("env", {})
    cluster = yaml.safe_load((REPO / "Acme" / "platform" / "kind" / "sample-api-cluster.yaml").read_text())
    image = cluster["nodes"][0]["image"]
    node = re.fullmatch(r"kindest/node:v(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}", image)
    assert node, image
    assert str(env.get("KUBERNETES_VERSION")) == node.group(1), env
    assert '-kubernetes-version "$KUBERNETES_VERSION"' in kubeconform_command(step), step["run"]
    # "default" reads the schema repo's moving master, so a required check could turn red with no commit here.
    assert re.fullmatch(r"[0-9a-f]{40}", str(env.get("KUBERNETES_SCHEMAS_COMMIT", ""))), env
    assert "/${KUBERNETES_SCHEMAS_COMMIT}/" in step["run"], step["run"]
    assert "-schema-location default" not in step["run"], step["run"]


def test_kustomize_job_expands_every_resourceset():
    # kubectl kustomize leaves a ResourceSet's templates unexpanded, so kubeconform never sees the
    # objects it generates. Output outside rendered/ would be skipped without an error.
    step = kustomize_step("Validate manifests against their schemas")
    assert re.fullmatch(r"[0-9a-f]{64}", str(step.get("env", {}).get("OPERATOR_CLI_SHA256", ""))), step.get("env")
    bootstrap = REPO / "Acme" / "infra" / "bootstrap"
    resourcesets = [
        path.relative_to(REPO).as_posix()
        for path in sorted(bootstrap.rglob("*.yaml"))
        if any(doc and doc.get("kind") == "ResourceSet" for doc in yaml.safe_load_all(path.read_text()))
    ]
    assert resourcesets, bootstrap
    lines = commands(step)
    build = r'flux-operator build resourceset -f "\$GITHUB_WORKSPACE/(\S+)" > rendered/([^/\s]+)\.yaml'
    built = [match.groups() for match in (re.fullmatch(build, line) for line in lines) if match]
    assert sorted(path for path, _ in built) == resourcesets
    # Characterisation (C6): green on arrival, with one ResourceSet. Two writing one file, or one
    # taking a kustomize render's name (Acme_...), would overwrite objects kubeconform should see.
    outputs = [name for _, name in built]
    assert len(set(outputs)) == len(outputs), lines
    assert not any(name.startswith("Acme_") for name in outputs), lines
    # Each of these would leave the generated objects unchecked and the step green.
    validate = kubeconform_command(step)
    last_build = max(index for index, line in enumerate(lines) if line.startswith("flux-operator build"))
    assert last_build < lines.index(validate), lines
    assert validate.endswith(" rendered/*.yaml"), validate
    assert "-ignore-missing-schemas" not in validate, validate
    # Not asserted: the CLI's checksum target and download URL. If either is wrong, sha256sum -c
    # fails loudly in CI unless the hash is changed too, which is enough here.


def build_options():
    """The `with:` of build-scan's docker/build-push-action step."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["build-scan"]["steps"]
    step = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action@"))
    return step["with"]


def cache_settings(value):
    """'type=gha,scope=x' -> {'type': 'gha', 'scope': 'x'}, so the order of the parts doesn't matter."""
    return dict(part.split("=", 1) for part in value.split(","))


def test_build_scan_uses_the_gha_layer_cache():
    options = build_options()
    assert "cache-from" in options and "cache-to" in options, sorted(options)
    assert cache_settings(options["cache-from"]) == {"type": "gha", "scope": "sample-api"}
    # mode=max also saves the builder stage, where the compile runs; mode=min saves only scratch.
    assert cache_settings(options["cache-to"]) == {"type": "gha", "scope": "sample-api", "mode": "max"}


def build_scan_steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["build-scan"]["steps"]


def step_index(steps, action):
    """Index of the first step that uses `action@...`."""
    uses = [s.get("uses", "").split("@")[0] for s in steps]
    assert action in uses, uses
    return uses.index(action)


def test_build_scan_persists_the_go_build_cache_mount():
    # G2: the gha layer cache doesn't save mount contents, so actions/cache keeps a host
    # directory and buildkit-cache-dance copies it into the mount and back out.
    steps = build_scan_steps()
    dance_at = step_index(steps, "reproducible-containers/buildkit-cache-dance")
    dance = steps[dance_at]
    # A commit SHA, not a tag: a tag can be moved to other code.
    assert re.fullmatch(r"reproducible-containers/buildkit-cache-dance@[0-9a-f]{40}", dance["uses"]), dance["uses"]
    # The helper image defaults to busybox:latest, so pin it by digest (NFR2, L9a-6).
    image = dance["with"].get("utility-image", "")
    assert re.fullmatch(r"ghcr\.io/containerd/busybox(:[\w.-]+)?@sha256:[0-9a-f]{64}", image), image
    # The builder must exist first, and the mount must be filled before the build reads it.
    assert step_index(steps, "docker/setup-buildx-action") < dance_at < step_index(steps, "docker/build-push-action")
    # The id is what the Dockerfile's go-build-${TARGETARCH} becomes on amd64.
    ((host_dir, mount),) = json.loads(dance["with"]["cache-map"]).items()
    assert mount == {"id": "go-build-amd64", "target": "/root/.cache/go-build"}
    # actions/cache restores the host directory first. Post steps run in reverse, so its save
    # runs after the dance's extract.
    cache_at = step_index(steps, "actions/cache")
    assert cache_at < dance_at
    cache = steps[cache_at]
    assert cache["with"]["path"] == host_dir
    # Q7: a prefix of its own, so lint/test and build-scan never restore each other's caches.
    assert cache["with"]["key"].startswith("go-build-docker-"), cache["with"]["key"]
    assert cache["with"]["restore-keys"].startswith("go-build-docker-"), cache["with"]["restore-keys"]
    # The Dockerfile pins Go: without it, a Go bump with unchanged source is an exact hit, so the
    # new compile is never saved (skip-extraction) and every later build compiles from scratch.
    assert "'Acme/apps/sample-api/Dockerfile'" in cache["with"]["key"], cache["with"]["key"]
    # The source is the rest of the compile's input: without it, a code change is an exact hit,
    # so the new compile is never saved and every later build recompiles the changed packages.
    for source in ("'Acme/apps/sample-api/go.mod'", "'Acme/apps/sample-api/**/*.go'"):
        assert source in cache["with"]["key"], cache["with"]["key"]
    # Keys are write-once: on an exact hit nothing will be saved, so skip the extract.
    assert dance["with"]["skip-extraction"] == f"${{{{ steps.{cache['id']}.outputs.cache-hit }}}}"
