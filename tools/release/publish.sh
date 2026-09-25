#!/usr/bin/env bash
# Publish a signed release on GitHub Releases, the way devices will see it.
#
#   tools/release/publish.sh dist/1.2.0 [--repo atlas-navigate/atlas-control]
#
#   1. verify the release exactly as a device will (verify_release.py)
#   2. create a DRAFT release v<version> and upload the manifest, signature
#      and bundle
#   3. download the uploaded assets back and verify them again (catches a
#      truncated or wrong upload before any device can see it)
#   4. publish: devices find it through .../releases/latest
#
# Needs the GitHub CLI (gh) logged in with permission to create releases.
# The signing key is not needed here — sign first, offline.
set -euo pipefail
DIR="${1:?usage: publish.sh RELEASE_DIR [--repo OWNER/NAME]}"
REPO="atlas-navigate/atlas-control"
[[ "${2:-}" == "--repo" ]] && REPO="${3:?--repo needs OWNER/NAME}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${ATLAS_BUILD_PYTHON:-python3}"

command -v gh >/dev/null || { echo "gh (GitHub CLI) is required" >&2; exit 1; }
VERSION=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$DIR/atlas-release.json")
BUNDLE=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"]["name"])' "$DIR/atlas-release.json")
TAG="v$VERSION"

echo "== verifying $DIR"
"$PY" "$HERE/verify_release.py" "$DIR"

echo "== creating draft $TAG on $REPO"
NOTES=$("$PY" -c 'import json,sys; print("\n".join("- " + n for n in json.load(open(sys.argv[1])).get("notes", [])) or "Atlas " + sys.argv[2])' \
        "$DIR/atlas-release.json" "$VERSION")
gh release create "$TAG" --repo "$REPO" --draft --title "Atlas $VERSION" --notes "$NOTES" \
    "$DIR/atlas-release.json" "$DIR/atlas-release.json.sig" "$DIR/$BUNDLE"

echo "== downloading the draft's assets back and verifying them"
CHECK=$(mktemp -d)
trap 'rm -rf "$CHECK"' EXIT
gh release download "$TAG" --repo "$REPO" --dir "$CHECK"
"$PY" "$HERE/verify_release.py" "$CHECK"

read -rp "Publish $TAG so devices can install it? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "left as a draft"; exit 0; }
gh release edit "$TAG" --repo "$REPO" --draft=false --latest
echo "== published $TAG"
