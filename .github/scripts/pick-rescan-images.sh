#!/usr/bin/env bash
# Picks the images sample-api-rescan.yml scans: the newest sha-* image on main and the highest
# semver release. No network and no git, so tests/ can run every branch of it.
#
#   pick-rescan-images.sh COMMITS_FILE < tags.json
#
#   stdin         GHCR's tags/list reply: {"name": ..., "tags": [...]}
#   COMMITS_FILE  commit SHAs on main, newest first (git log --format=%H)
#   stdout        the picked tags as a compact JSON array, for a fromJSON matrix
#   stderr        the tag list, and ::warning:: / ::error:: annotations
#
# Exits 1 when there's nothing to scan.
set -euo pipefail

commits_file="${1:?usage: pick-rescan-images.sh COMMITS_FILE < tags.json}"

# A registry may send "tags": null (or no key) for no tags; // [] makes that an empty list.
# Not .tags[]?, which would also hide a malformed reply as "nothing to scan".
tags="$(jq -r '.tags // [] | .[]')"

{ echo "Tags in GHCR:"; echo "$tags"; } >&2

images=()
# sha-<7 chars of the commit>, as sample-api-ci.yml tags it
while read -r c; do
  if grep -qx "sha-${c:0:7}" <<<"$tags"; then images+=("sha-${c:0:7}"); break; fi
done <"$commits_file"
[ ${#images[@]} -gt 0 ] || echo "::warning::No sha-* image for any of the given commits on main" >&2

release="$(grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' <<<"$tags" | sort -V | tail -1 || true)"
if [ -n "$release" ]; then images+=("$release"); else echo "::warning::No semver release tag" >&2; fi

[ ${#images[@]} -gt 0 ] || { echo "::error::Nothing to scan" >&2; exit 1; }
printf '%s\n' "${images[@]}" | jq -R . | jq -cs .
