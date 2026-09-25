"""
Release manifest verification — the one implementation used by the root
engine (authoritatively), the app (to show "Update could not be verified"
early) and the release tools (to check a release before publishing it).

A release is described by atlas-release.json and signed in a sibling
atlas-release.json.sig:

    {"signatures": [{"key_id": "<16 hex>", "sig": "<base64 Ed25519>"}]}

Each signature covers DOMAIN + the exact manifest bytes. The domain prefix
means a signature made for anything else with the same key can never be
replayed as a release signature. Verification happens on the raw bytes
before they are parsed.

Trust anchors live in the keyring installed with the engine
(/usr/local/lib/atlas/keys/keyring.json). A developer unit may additionally
trust keys in /etc/atlas/dev-keys/, but only while /etc/atlas/dev-mode
exists — retail units never have either.
"""
import base64
import binascii
import hashlib
import json
import os
import re

DOMAIN = b"ATLAS-MANIFEST-V1\x00"
MANIFEST_FORMAT = 1
PRODUCT = "atlas-control"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 ** 3
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.+-]{1,40})?$")
_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.tar\.xz$")


class VerifyError(Exception):
    """The release is not trustworthy or not usable on this device."""


def key_id(public_key_raw):
    return hashlib.sha256(public_key_raw).hexdigest()[:16]


def _decode_b64(value, what):
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, TypeError, ValueError):
        raise VerifyError(f"malformed {what}") from None


def load_keyring(path, dev_keys_dir=None, dev_mode=False):
    """Return {key_id: raw_public_key} of keys trusted for release signing."""
    trusted = {}
    revoked = set()
    try:
        with open(path, encoding="utf-8") as f:
            ring = json.load(f)
    except OSError:
        ring = {"keys": []}
    except ValueError:
        raise VerifyError("keyring is corrupt") from None
    for entry in ring.get("revoked", []) or []:
        revoked.add(str(entry))
    for entry in ring.get("keys", []) or []:
        if entry.get("status", "active") not in ("active", "backup"):
            continue
        raw = _decode_b64(entry.get("pub", ""), "public key")
        if len(raw) != 32:
            raise VerifyError("keyring contains a key of the wrong size")
        kid = key_id(raw)
        if kid not in revoked:
            trusted[kid] = raw
    if dev_mode and dev_keys_dir and os.path.isdir(dev_keys_dir):
        for name in sorted(os.listdir(dev_keys_dir)):
            if not name.endswith(".pub"):
                continue
            with open(os.path.join(dev_keys_dir, name), encoding="utf-8") as f:
                raw = _decode_b64(f.read().strip(), "developer key")
            if len(raw) == 32 and key_id(raw) not in revoked:
                trusted[key_id(raw)] = raw
    return trusted


def keyring_version(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(json.load(f).get("keyring_version", 0))
    except (OSError, ValueError, TypeError):
        return 0


def check_signature(manifest_bytes, sig_bytes, trusted_keys):
    """Return the key_id that signed the manifest, or raise VerifyError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not trusted_keys:
        raise VerifyError("no release-signing keys are installed")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise VerifyError("manifest is too large")
    try:
        sigdoc = json.loads(sig_bytes.decode("utf-8"))
        entries = sigdoc["signatures"]
        if not isinstance(entries, list):
            raise TypeError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise VerifyError("signature file is malformed") from None
    message = DOMAIN + manifest_bytes
    for entry in entries[:8]:
        if not isinstance(entry, dict):
            continue
        kid = str(entry.get("key_id", ""))
        raw = trusted_keys.get(kid)
        if raw is None:
            continue
        sig = _decode_b64(entry.get("sig", ""), "signature")
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, message)
            return kid
        except InvalidSignature:
            continue
    raise VerifyError("no valid signature from a trusted key")


def _require(cond, message):
    if not cond:
        raise VerifyError(message)


def validate_manifest(manifest, arch=None, installed_seq=None, engine_version=None,
                      python_version=None):
    """Check a parsed, signature-verified manifest against this device."""
    _require(isinstance(manifest, dict), "manifest is not an object")
    _require(manifest.get("format") == MANIFEST_FORMAT, "unsupported manifest format")
    _require(manifest.get("product") == PRODUCT, "manifest is for a different product")
    version = manifest.get("version")
    _require(isinstance(version, str) and _VERSION.match(version), "manifest version is invalid")
    seq = manifest.get("seq")
    _require(isinstance(seq, int) and not isinstance(seq, bool) and seq > 0, "manifest seq is invalid")
    if arch is not None:
        _require(manifest.get("arch") == arch, f"release is for {manifest.get('arch')}, not {arch}")
    if python_version is not None and manifest.get("python"):
        _require(manifest.get("python") == python_version,
                 f"release needs Python {manifest.get('python')}")
    if installed_seq is not None:
        _require(seq > int(installed_seq), "release is not newer than the installed one")
    min_engine = manifest.get("min_engine", 1)
    _require(isinstance(min_engine, int), "min_engine is invalid")
    if engine_version is not None:
        # A release may require a newer engine than the one running, as long
        # as it ships that engine: the running one installs it first.
        bundled = manifest.get("engine_version", 0)
        bundled = bundled if isinstance(bundled, int) and not isinstance(bundled, bool) else 0
        _require(min_engine <= max(engine_version, bundled), "release needs a newer updater")
    bundle = manifest.get("bundle")
    _require(isinstance(bundle, dict), "manifest has no bundle")
    _require(isinstance(bundle.get("name"), str) and _BUNDLE_NAME.match(bundle["name"]),
             "bundle name is invalid")
    size = bundle.get("size")
    _require(isinstance(size, int) and 0 < size <= MAX_BUNDLE_BYTES, "bundle size is invalid")
    _require(isinstance(bundle.get("sha256"), str) and _HEX64.match(bundle["sha256"]),
             "bundle hash is invalid")
    unpacked = bundle.get("unpacked_size")
    _require(isinstance(unpacked, int) and 0 < unpacked <= 4 * MAX_BUNDLE_BYTES,
             "unpacked size is invalid")
    chunk_size = bundle.get("chunk_size")
    chunks = bundle.get("chunks")
    if chunks is not None:
        _require(isinstance(chunk_size, int) and chunk_size >= 1024 * 1024, "chunk size is invalid")
        _require(isinstance(chunks, list) and len(chunks) == -(-size // chunk_size),
                 "chunk list does not match bundle size")
        _require(all(isinstance(c, str) and _HEX64.match(c) for c in chunks), "chunk hash is invalid")
    url = bundle.get("url", "")
    _require(isinstance(url, str) and (url == "" or url.startswith("https://")),
             "bundle URL must be https")
    db = manifest.get("db", {})
    _require(isinstance(db, dict), "db section is invalid")
    for k in ("schema_target", "compat_floor"):
        _require(isinstance(db.get(k, 0), int), f"db.{k} is invalid")
    notes = manifest.get("notes", [])
    _require(isinstance(notes, list) and all(isinstance(n, str) for n in notes), "notes are invalid")
    return manifest


def verify_manifest(manifest_bytes, sig_bytes, trusted_keys, **device):
    """Signature first, then parse, then validate. Returns (manifest, key_id)."""
    kid = check_signature(manifest_bytes, sig_bytes, trusted_keys)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise VerifyError("manifest is not valid JSON") from None
    return validate_manifest(manifest, **device), kid


def verify_bundle_file(path, bundle):
    """Check a downloaded bundle's size and SHA-256 against the manifest.

    Returns None when it matches, else the index of the first bad chunk (or
    -1 when only the size/overall hash is wrong), so a resumable download
    can restart from the damaged part.
    """
    size = os.path.getsize(path)
    chunk_size = bundle.get("chunk_size")
    chunks = bundle.get("chunks")
    overall = hashlib.sha256()
    bad_chunk = None
    with open(path, "rb") as f:
        index = 0
        while True:
            block = f.read(chunk_size or (1 << 20))
            if not block:
                break
            overall.update(block)
            if chunks is not None and bad_chunk is None:
                if index >= len(chunks) or hashlib.sha256(block).hexdigest() != chunks[index]:
                    bad_chunk = index
            index += 1
    if size != bundle["size"] or overall.hexdigest() != bundle["sha256"]:
        return bad_chunk if bad_chunk is not None else -1
    return None


def sign_manifest(manifest_bytes, private_key):
    """Used by tools/release/sign_release.py. Returns the .sig file bytes."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    raw_pub = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sig = private_key.sign(DOMAIN + manifest_bytes)
    doc = {"signatures": [{"key_id": key_id(raw_pub), "sig": base64.b64encode(sig).decode()}]}
    return (json.dumps(doc, indent=2) + "\n").encode()
