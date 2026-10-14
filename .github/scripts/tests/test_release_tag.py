"""Tests for release-tag.sh, the retag in sample-api-ci.yml's release-tag job.

release-tag only runs when a v* git tag is pushed, so before these it had never run. The tests
push tiny images straight to a throwaway registry over its HTTP API, not with docker push: WSL's
Docker and the runner's store images differently, and this way the media type is exactly the
one each test asks for. Then they run the script against them.

    docker run -d --rm -p 5000:5000 --name reg registry:3
    ~/.venvs/iam-tests/bin/pytest -v .github/scripts/tests

With no registry running the tests skip; with REQUIRE_REGISTRY=1 (CI) they fail instead.
TEST_REGISTRY overrides localhost:5000.
"""

import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "release-tag.sh"
REGISTRY = os.environ.get("TEST_REGISTRY", "localhost:5000")
SHA_TAG = "sha-abc1234"

DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
ACCEPT = ", ".join([DOCKER_MANIFEST, DOCKER_LIST, OCI_MANIFEST, OCI_INDEX])


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def call(method, path, data=None, headers=None):
    """One registry API request: (status, headers, body). HTTP errors are returned, not raised."""
    url = path if path.startswith("http") else f"http://{REGISTRY}{path}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def push_blob(repo, data):
    status, headers, _ = call("POST", f"/v2/{repo}/blobs/uploads/")
    assert status == 202, status
    location = urllib.parse.urljoin(f"http://{REGISTRY}", headers["Location"])
    sep = "&" if "?" in location else "?"
    status, _, body = call(
        "PUT", f"{location}{sep}digest={digest(data)}", data, {"Content-Type": "application/octet-stream"}
    )
    assert status == 201, (status, body)


def push_manifest(repo, ref, body, media_type):
    """Upload a manifest or index under a tag (or digest); return its digest."""
    status, _, err = call("PUT", f"/v2/{repo}/manifests/{ref}", body, {"Content-Type": media_type})
    assert status == 201, (status, err)
    return digest(body)


def build_manifest(repo):
    """Upload a one-file layer and its config; return a plain Docker manifest pointing at them."""
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as t:
        info = tarfile.TarInfo("hello.txt")
        info.size = 6
        t.addfile(info, io.BytesIO(b"hello\n"))
    layer = gzip.compress(tar.getvalue(), mtime=0)
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {},
            "rootfs": {"type": "layers", "diff_ids": [digest(tar.getvalue())]},
        }
    ).encode()
    push_blob(repo, layer)
    push_blob(repo, config)
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": DOCKER_MANIFEST,
            "config": {
                "mediaType": "application/vnd.docker.container.image.v1+json",
                "size": len(config),
                "digest": digest(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
                    "size": len(layer),
                    "digest": digest(layer),
                }
            ],
        }
    ).encode()


def head(repo, ref):
    """(digest, media type) of a tag, or None if there's no such tag."""
    status, headers, _ = call("HEAD", f"/v2/{repo}/manifests/{ref}", headers={"Accept": ACCEPT})
    if status == 404:
        return None
    assert status == 200, status
    return headers["Docker-Content-Digest"], headers["Content-Type"]


def tags(repo):
    status, _, body = call("GET", f"/v2/{repo}/tags/list")
    if status == 404:
        return []
    return sorted(json.loads(body)["tags"] or [])


@pytest.fixture(scope="session")
def registry():
    try:
        status, _, _ = call("GET", "/v2/")
    except OSError as err:
        status = err
    if status != 200:
        msg = f"no registry at {REGISTRY} ({status}); start one: docker run -d --rm -p 5000:5000 --name reg registry:3"
        if os.environ.get("REQUIRE_REGISTRY") == "1":
            pytest.fail(msg)
        pytest.skip(msg)


@pytest.fixture
def repo(registry):
    """A fresh repository per test, so tests can't see each other's tags."""
    return f"release-tag-test/{uuid.uuid4().hex[:12]}"


@pytest.fixture
def plain_source(repo):
    """sha-* as a single plain manifest: what CI's docker push makes."""
    return push_manifest(repo, SHA_TAG, build_manifest(repo), DOCKER_MANIFEST)


@pytest.fixture
def index_source(repo):
    """sha-* as an OCI index around one manifest: how GHCR's 0.1.1 is stored."""
    manifest = build_manifest(repo)
    push_manifest(repo, digest(manifest), manifest, DOCKER_MANIFEST)
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX,
            "manifests": [
                {
                    "mediaType": DOCKER_MANIFEST,
                    "size": len(manifest),
                    "digest": digest(manifest),
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    ).encode()
    return push_manifest(repo, SHA_TAG, index, OCI_INDEX)


def release_tag(repo, git_tag):
    return subprocess.run(
        [str(SCRIPT), f"{REGISTRY}/{repo}", SHA_TAG, git_tag],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("git_tag", ["v1.2", "v1.2.3-rc.1", "vnext"], ids=["two-part", "pre-release", "word"])
def test_rejects_tags_that_are_not_releases(repo, plain_source, git_tag):
    result = release_tag(repo, git_tag)
    assert result.returncode == 1
    assert "is not v<major>.<minor>.<patch>" in result.stdout + result.stderr
    assert tags(repo) == [SHA_TAG]  # nothing pushed


def test_fails_when_the_sha_image_is_missing(repo):
    # The commit has no image: not on main, or its sample-api-ci run failed or hasn't finished.
    result = release_tag(repo, "v1.2.3")
    assert result.returncode == 1
    assert "doesn't exist" in result.stdout + result.stderr
    assert tags(repo) == []


@pytest.mark.parametrize("source", ["plain_source", "index_source"], ids=["plain-manifest", "index"])
def test_release_is_the_same_image(repo, source, request):
    # The teacher's case is plain-manifest: by default imagetools create wraps it in a new index.
    src_digest = request.getfixturevalue(source)
    result = release_tag(repo, "v1.2.3")
    assert result.returncode == 0, result.stdout + result.stderr
    assert head(repo, SHA_TAG)[0] == src_digest
    assert head(repo, "1.2.3") == head(repo, SHA_TAG)  # same digest and same media type
