#!/usr/bin/env python3
"""Sign a built release manifest: writes atlas-release.json.sig next to it.

    tools/release/sign_release.py dist/1.2.0/atlas-release.json --key ~/atlas-keys/primary.key

A signature is added next to any already in the .sig file (another key's is
kept; this key's own is replaced), so one release can carry the developer
and the production signature: each unit installs it if any signature is
from a key in its keyring. --fresh discards the others.
"""
import argparse
import getpass
import json
import os
import sys

import _common  # noqa: F401
from cryptography.hazmat.primitives import serialization
from atlas_engine import verify


MAX_SIGNATURES = 8      # verify.check_signature reads the first 8


def existing_signatures(sig_path):
    try:
        with open(sig_path, encoding="utf-8") as f:
            entries = json.load(f)["signatures"]
    except FileNotFoundError:
        return []
    except (ValueError, KeyError, TypeError):
        sys.exit(f"{sig_path} is not a signature file; use --fresh to replace it")
    if not isinstance(entries, list):
        sys.exit(f"{sig_path} is not a signature file; use --fresh to replace it")
    return [e for e in entries if isinstance(e, dict)]


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
    ap.add_argument("--fresh", action="store_true", help="drop any signatures already in the .sig file")
    args = ap.parse_args()
    with open(args.manifest, "rb") as f:
        mbytes = f.read()
    m = json.loads(mbytes)
    verify.validate_manifest(m)
    print(f"Signing Atlas {m['version']} (seq {m['seq']}, channel {m.get('channel', '?')})")
    print(f"  bundle {m['bundle']['name']}  {m['bundle']['size']} bytes  sha256 {m['bundle']['sha256']}")
    if not args.yes and input("Sign this release? [y/N] ").strip().lower() != "y":
        sys.exit("not signed")
    sig_path = args.manifest + ".sig"
    others = [] if args.fresh else existing_signatures(sig_path)
    mine = json.loads(verify.sign_manifest(mbytes, load_key(args.key, args.passphrase_env)))["signatures"][0]
    entries = [e for e in others if e.get("key_id") != mine["key_id"]] + [mine]
    if len(entries) > MAX_SIGNATURES:
        sys.exit(f"{sig_path} would hold {len(entries)} signatures; devices read {MAX_SIGNATURES} (use --fresh)")
    with open(sig_path, "w") as f:
        json.dump({"signatures": entries}, f, indent=2)
        f.write("\n")
    kept = len(entries) - 1
    print(f"wrote {sig_path} (key {mine['key_id']}" + (f", kept {kept} other signature{'s' * (kept != 1)})" if kept else ")"))


if __name__ == "__main__":
    main()
