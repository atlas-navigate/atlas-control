#!/usr/bin/env python3
"""Check a release exactly as a device will before publishing it.

    tools/release/verify_release.py dist/1.2.0 [--keyring system/keys/keyring.json] [--dev-key X.pub]
"""
import argparse
import base64
import json
import os
import sys

import _common
from atlas_engine import verify


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("release_dir")
    ap.add_argument("--keyring", default=os.path.join(_common.REPO, "system", "keys", "keyring.json"))
    ap.add_argument("--dev-key", action="append", default=[], help="also trust this developer .pub")
    args = ap.parse_args()
    keys = verify.load_keyring(args.keyring)
    for p in args.dev_key:
        raw = base64.b64decode(open(os.path.expanduser(p)).read().strip())
        keys[verify.key_id(raw)] = raw
    d = args.release_dir
    mbytes = open(os.path.join(d, "atlas-release.json"), "rb").read()
    sig = open(os.path.join(d, "atlas-release.json.sig"), "rb").read()
    try:
        manifest, kid = verify.verify_manifest(mbytes, sig, keys, arch="aarch64")
        bad = verify.verify_bundle_file(os.path.join(d, manifest["bundle"]["name"]), manifest["bundle"])
        if bad is not None:
            raise verify.VerifyError(f"bundle does not match manifest (chunk {bad})")
    except verify.VerifyError as exc:
        sys.exit(f"NOT VALID: {exc}")
    print(f"OK: Atlas {manifest['version']} seq {manifest['seq']} signed by {kid}")
    print(json.dumps(manifest.get("notes", []), indent=2))


if __name__ == "__main__":
    main()
