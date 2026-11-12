#!/usr/bin/python3 -I
"""Download a signed Atlas release from GitHub Releases (install.sh --from-github).

    fetch_release.py OUT_DIR [--tag TAG] [--releases-url URL] [--dev-key PUB]

The newest release (or TAG) is resolved once, so its manifest, signature and
bundle all come from the same release. The signature is checked against this
kit's keyring (plus the developer key) before anything large is downloaded;
the bundle is then downloaded, resuming a partial one, and checked against
the signed size and SHA-256. install.sh installs OUT_DIR exactly like a
release brought on a payload drive (--bundle), verifying it again first.

The same URLs as the updater in every Atlas (update_manager.py), so a unit
installed this way updates from where it was installed. ATLAS_RELEASES_URL
overrides the address (a mirror, or a local test server). Standard library
plus the engine's verifier.
"""
import argparse
import base64
import os
import re
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "system"))
from atlas_engine import engine, verify  # noqa: E402

# update_manager.DEFAULT_RELEASES_URL (tests/test_installer.py keeps them equal).
DEFAULT_RELEASES_URL = "https://github.com/atlas-navigate/atlas-control/releases"
USER_AGENT = "Atlas-Installer/1"
CHUNK = 256 * 1024
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,80}$")


def say(msg):
    print(f"    {msg}", flush=True)


def _allowed(url, developer):
    # Plain http only for a developer unit's local test server.
    return url.startswith("https://") or (developer and url.startswith("http://"))


def _get(url, limit, developer):
    if not _allowed(url, developer):
        raise SystemExit(f"refusing an insecure release address: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise SystemExit(f"{url} is larger than a release file can be")
    return data


def release_dir_url(base, tag):
    """The directory holding one release's files: TAG's, or the newest one's."""
    if "github.com/" not in base:
        return f"{base}/{tag}" if tag else base
    if tag:
        return f"{base}/download/{tag}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    req = urllib.request.Request(f"{base}/latest", method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        urllib.request.build_opener(NoRedirect).open(req, timeout=20)
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
        if exc.code in (301, 302, 303, 307, 308) and "/tag/" in location:
            return f"{base}/download/{location.rsplit('/tag/', 1)[1].split('?')[0].strip('/')}"
    raise SystemExit(f"no release has been published at {base}")


def trusted_keys(dev_key):
    keys = verify.load_keyring(os.path.join(REPO, "system", "keys", "keyring.json"))
    if dev_key:
        raw = base64.b64decode(open(dev_key).read().strip())
        keys[verify.key_id(raw)] = raw
    return keys


def download(url, path, size, developer):
    """Into path, resuming what is already there."""
    if not _allowed(url, developer):
        raise SystemExit(f"refusing an insecure release address: {url}")
    have = os.path.getsize(path) if os.path.exists(path) else 0
    if have > size:
        os.unlink(path)
        have = 0
    if have == size:
        return
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        if have and r.status != 206:
            have = 0                          # the server sent it all again
        shown = time.monotonic()
        with open(path, "ab" if have else "wb") as f:
            while True:
                block = r.read(CHUNK)
                if not block:
                    break
                f.write(block)
                have += len(block)
                if have > size:
                    raise SystemExit("the bundle is larger than its signed manifest says")
                if time.monotonic() - shown > 10:
                    say(f"{have * 100 // size}% of {size // (1024 * 1024)} MB")
                    shown = time.monotonic()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--tag", default="")
    ap.add_argument("--releases-url", default=os.environ.get("ATLAS_RELEASES_URL") or DEFAULT_RELEASES_URL)
    ap.add_argument("--dev-key", default="")
    args = ap.parse_args(argv)
    if args.tag and not _TAG.match(args.tag):
        raise SystemExit(f"not a release tag: {args.tag}")
    developer = bool(args.dev_key)
    base = args.releases_url.rstrip("/")
    try:
        rel = release_dir_url(base, args.tag)
        say(f"Release: {rel}")
        mbytes = _get(f"{rel}/{engine.MANIFEST_NAME}", verify.MAX_MANIFEST_BYTES, developer)
        sig = _get(f"{rel}/{engine.SIG_NAME}", 64 * 1024, developer)
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"could not reach the release ({getattr(exc, 'reason', exc)}) — "
                         "check the Internet connection and the clock") from None
    try:
        manifest, kid = verify.verify_manifest(mbytes, sig, trusted_keys(args.dev_key), arch="aarch64")
    except verify.VerifyError as exc:
        raise SystemExit(f"the release is not signed by a key this kit trusts: {exc}") from None
    say(f"Atlas {manifest['version']} — signed by key {kid}")
    bundle = manifest["bundle"]
    os.makedirs(args.out, mode=0o755, exist_ok=True)
    path = os.path.join(args.out, bundle["name"])
    url = bundle.get("url") or f"{rel}/{bundle['name']}"
    try:
        download(url, path, bundle["size"], developer)
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"the download stopped ({getattr(exc, 'reason', exc)}) — run the same command "
                         "again to resume") from None
    if verify.verify_bundle_file(path, bundle) is not None:
        os.unlink(path)
        raise SystemExit("the downloaded bundle does not match its signed manifest (removed — try again)")
    for name, data in ((engine.MANIFEST_NAME, mbytes), (engine.SIG_NAME, sig)):
        with open(os.path.join(args.out, name), "wb") as f:
            f.write(data)
    # Other releases' bundles left from earlier runs would confuse --bundle.
    for name in os.listdir(args.out):
        if name.endswith(".tar.xz") and name != bundle["name"]:
            os.unlink(os.path.join(args.out, name))
    say(f"Downloaded and verified: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
