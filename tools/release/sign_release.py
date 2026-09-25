#!/usr/bin/env python3
"""Sign a built release manifest: writes atlas-release.json.sig next to it.

    tools/release/sign_release.py dist/1.2.0/atlas-release.json --key ~/atlas-keys/primary.key
"""
import argparse
import getpass
import json
import os
import sys

import _common  # noqa: F401
from cryptography.hazmat.primitives import serialization
from atlas_engine import verify


def load_key(path, passphrase_env=None):
    with open(os.path.expanduser(path), "rb") as f:
        data = f.read()
    try:
        return serialization.load_pem_private_key(data, password=None)
    except TypeError:
        pw = os.environ.get(passphrase_env) if passphrase_env else None
        pw = pw or getpass.getpass(f"Passphrase for {path}: ")
        return serialization.load_pem_private_key(data, password=pw.encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--key", required=True)
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--passphrase-env", help="read the passphrase from this environment variable")
    args = ap.parse_args()
    with open(args.manifest, "rb") as f:
        mbytes = f.read()
    m = json.loads(mbytes)
    verify.validate_manifest(m)
    print(f"Signing Atlas {m['version']} (seq {m['seq']}, channel {m.get('channel', '?')})")
    print(f"  bundle {m['bundle']['name']}  {m['bundle']['size']} bytes  sha256 {m['bundle']['sha256']}")
    if not args.yes and input("Sign this release? [y/N] ").strip().lower() != "y":
        sys.exit("not signed")
    sig = verify.sign_manifest(mbytes, load_key(args.key, args.passphrase_env))
    with open(args.manifest + ".sig", "wb") as f:
        f.write(sig)
    print(f"wrote {args.manifest}.sig")


if __name__ == "__main__":
    main()
