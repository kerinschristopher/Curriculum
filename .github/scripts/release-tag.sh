#!/usr/bin/env bash
# Gives an image main already built, scanned and pushed a release tag: IMAGE:SRC_TAG also
# becomes IMAGE:<major>.<minor>.<patch>. Nothing is rebuilt. Run by release-tag in
# sample-api-ci.yml after it logs in to the registry; tests/ run it against a local registry.
#
#   release-tag.sh IMAGE SRC_TAG GIT_TAG
#   e.g. release-tag.sh ghcr.io/kerinschristopher/sample-api sha-abc1234 v1.2.3
#
# Exits 1 if GIT_TAG isn't v<major>.<minor>.<patch>, if IMAGE:SRC_TAG doesn't exist, or if the
# release tag doesn't end up on the same digest as SRC_TAG.
set -euo pipefail

usage="usage: release-tag.sh IMAGE SRC_TAG GIT_TAG"
image="${1:?$usage}"
src_tag="${2:?$usage}"
git_tag="${3:?$usage}"

semver="${git_tag#v}"
if ! [[ "$semver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "::error::Tag $git_tag is not v<major>.<minor>.<patch>"
  exit 1
fi
src="${image}:${src_tag}"
if ! docker buildx imagetools inspect "$src" > /dev/null 2>&1; then
  echo "::error::$src doesn't exist. Tag a commit that's on main with a green sample-api-ci run."
  exit 1
fi
# --prefer-index=false copies a single source as-is. The default wraps a plain manifest (what
# CI's docker push makes) in a new index with a new digest, so the check below would fail.
docker buildx imagetools create --prefer-index=false --tag "${image}:${semver}" "$src"

# Prove the release label points at the same image, not a copy. This can only be checked after
# the push (--dry-run prints re-formatted JSON, not the bytes it would push), so on a mismatch
# the wrong tag is already there: say how to remove it.
a="$(docker buildx imagetools inspect "$src" --format '{{json .Manifest.Digest}}')"
b="$(docker buildx imagetools inspect "${image}:${semver}" --format '{{json .Manifest.Digest}}')"
echo "sha tag: $a  release tag: $b"
if [ "$a" != "$b" ]; then
  echo "::error::${image}:${semver} is $b, not $src's $a. Delete that package version (Packages > sample-api > versions on GitHub), fix the cause, then re-run this job."
  exit 1
fi
