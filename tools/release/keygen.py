#!/usr/bin/env python3
"""Generate an Ed25519 release-signing key pair.

    tools/release/keygen.py --out ~/atlas-keys/primary          (production)
    tools/release/keygen.py --out ~/.config/atlas-dev/dev --dev (developer)

Production keys: run on an offline machine; the private key is encrypted with
a passphrase you type twice and must never be copied to a device, a CI
system or the repository. Generate two (primary + escrowed backup) and add
both public entries to system/keys/keyring.json.

Developer keys are unencrypted and are trusted only by units with
/etc/atlas/dev-mode, never by retail units.
"""
import argparse
import base64
import getpass
import json
import os
import sys

import _common  # noqa: F401
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from atlas_engine.verify import key_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="path prefix; writes <out>.key and <out>.pub")
    ap.add_argument("--dev", action="store_true", help="unencrypted developer key")
    args = ap.parse_args()
    out = os.path.expanduser(args.out)
    if os.path.exists(out + ".key"):
        sys.exit(f"{out}.key already exists — refusing to overwrite a signing key")
    key = Ed25519PrivateKey.generate()
    if args.dev:
        enc = serialization.NoEncryption()
    else:
        pw = getpass.getpass("Passphrase for the new release key: ")
        if len(pw) < 12 or pw != getpass.getpass("Repeat passphrase: "):
            sys.exit("passphrases must match and be at least 12 characters")
        enc = serialization.BestAvailableEncryption(pw.encode())
    os.makedirs(os.path.dirname(out) or ".", mode=0o700, exist_ok=True)
    fd = os.open(out + ".key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc))
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub_b64 = base64.b64encode(raw).decode()
    with open(out + ".pub", "w") as f:
        f.write(pub_b64 + "\n")
    print(f"key id: {key_id(raw)}")
    print("keyring entry:")
    print(json.dumps({"pub": pub_b64, "status": "active" if not args.dev else "dev"}, indent=2))


if __name__ == "__main__":
    main()
