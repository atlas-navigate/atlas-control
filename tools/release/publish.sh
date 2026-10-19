#!/usr/bin/env bash
# Publish a signed release on GitHub Releases, the way devices will see it.
#
#   tools/release/publish.sh dist/1.2.0 [--repo atlas-navigate/atlas-control]
#   tools/release/publish.sh dist/1.0.0-dev.X --dev-key /etc/atlas/dev-keys/dev.pub
#
# A release signed with the developer key passes only with --dev-key, and
# only developer units (/etc/atlas/dev-mode) will install it; units without
# that key report "Update could not be verified".
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
DIR="${1:?usage: publish.sh RELEASE_DIR [--repo OWNER/NAME] [--dev-key FILE.pub]}"
shift
REPO="atlas-navigate/atlas-control"
VERIFY_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="${2:?--repo needs OWNER/NAME}"; shift 2 ;;
        --dev-key) VERIFY_ARGS+=(--dev-key "${2:?--dev-key needs a .pub file}"); shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${ATLAS_BUILD_PYTHON:-python3}"

command -v gh >/dev/null || { echo "gh (GitHub CLI) is required" >&2; exit 1; }
VERSION=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$DIR/atlas-release.json")
BUNDLE=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"]["name"])' "$DIR/atlas-release.json")
# Tag exactly the commit the bundle was built from, not whatever main is now.
SHA=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["git_sha"])' "$DIR/atlas-release.json")
TAG="v$VERSION"

echo "== verifying $DIR"
"$PY" "$HERE/verify_release.py" "$DIR" "${VERIFY_ARGS[@]}"

echo "== creating draft $TAG on $REPO"
NOTES=$("$PY" -c 'import json,sys; print("\n".join("- " + n for n in json.load(open(sys.argv[1])).get("notes", [])) or "Atlas " + sys.argv[2])' \
        "$DIR/atlas-release.json" "$VERSION")
gh release create "$TAG" --repo "$REPO" --target "$SHA" --draft --title "Atlas $VERSION" --notes "$NOTES" \
    "$DIR/atlas-release.json" "$DIR/atlas-release.json.sig" "$DIR/$BUNDLE"

echo "== downloading the draft's assets back and verifying them"
CHECK=$(mktemp -d)
trap 'rm -rf "$CHECK"' EXIT
gh release download "$TAG" --repo "$REPO" --dir "$CHECK"
"$PY" "$HERE/verify_release.py" "$CHECK" "${VERIFY_ARGS[@]}"

read -rp "Publish $TAG so devices can install it? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "left as a draft"; exit 0; }
gh release edit "$TAG" --repo "$REPO" --draft=false --latest
echo "== published $TAG"
