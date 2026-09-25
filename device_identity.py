"""
The app's read-only view of this Atlas's identity (/etc/atlas/identity),
created by the root `atlas-identity` tool on the production line or at first
boot. See system/atlas_engine/identity.py for what each file holds.

The app can read: device.json (public facts), hotspot.json (the label's
hotspot credentials), setup_code.hash (to check a setup code typed or scanned
from the label) and proxy_secret. It never sees the TLS private key or the
setup code itself.

A developer checkout without an identity gets a stable stand-in, and pairing
is not enforced there (see pairing.required()).
"""
import base64
import hashlib
import hmac
import json
import os
import threading

IDENTITY_DIR = os.environ.get("ATLAS_IDENTITY_DIR", "/etc/atlas/identity")

_LOCK = threading.Lock()
_CACHE = {"mtime": None, "device": None}


def _path(*parts):
    return os.path.join(IDENTITY_DIR, *parts)


def present():
    return os.path.exists(_path("device.json"))


def device():
    """Public identity facts (cached; re-read when device.json changes)."""
    try:
        mtime = os.stat(_path("device.json")).st_mtime
    except OSError:
        return {"device_id": "developer", "name": "Atlas (developer checkout)", "short": "DEV",
                "hostname": "atlas", "tls_spki_sha256": None, "provisioned": False}
    with _LOCK:
        if _CACHE["mtime"] != mtime:
            with open(_path("device.json"), encoding="utf-8") as f:
                data = json.load(f)
            data["provisioned"] = True
            _CACHE.update(mtime=mtime, device=data)
        return dict(_CACHE["device"])


def hotspot():
    """{ssid, password} from the label, or None."""
    try:
        with open(_path("hotspot.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def normalize_setup_code(code):
    return "".join(c for c in str(code or "").upper() if c.isalnum())


def check_setup_code(code):
    """Constant-time check of a setup code against the stored PBKDF2 hash."""
    try:
        with open(_path("setup_code.hash"), encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return False
    normalized = normalize_setup_code(code)
    if not 8 <= len(normalized) <= 32:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", normalized.encode(), base64.b64decode(stored["salt"]),
                                 int(stored.get("iterations", 200_000)))
    return hmac.compare_digest(base64.b64encode(digest).decode(), stored["hash"])


def match_code(server_nonce, client_nonce, spki=None):
    """Short code shown on both the new client and the approving client.

    Native apps compute it from the TLS key they actually saw, so an attacker
    relaying the connection with their own certificate produces a different
    code on the two screens.
    """
    spki = spki if spki is not None else (device().get("tls_spki_sha256") or "")
    digest = hashlib.sha256(f"atlas-pair|{spki}|{server_nonce}|{client_nonce}".encode()).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 10000:04d}"
