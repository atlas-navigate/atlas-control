"""
Pairing: the one-time trust between a phone or browser and this Atlas.

There are no accounts, usernames, passwords or PINs. A client becomes
trusted once, by proving it is physically with the owner's Atlas, in any of
three ways:

  * setup code — the code on the device label, typed in or scanned from the
    label's QR code (the phone app does this in one tap);
  * hotspot — the client is connected to Atlas's own Wi-Fi network, whose
    unique password is also on the label;
  * approval — the client asks, and someone on an already-paired phone or
    browser taps "Allow" (both screens show the same 4-digit code).

The client then holds a random 256-bit token — an HttpOnly, Secure,
SameSite=Strict cookie for browsers and web views, or a bearer token for the
native apps — and is never asked again. Owners can see and remove paired
devices in Settings. Only a hash of each token is stored.

Cookie-carrying state changes must also send X-Atlas-CSRF, a value derived
from the token that the page reads from /api/pair/status; other sites can
neither read it nor send custom headers without a CORS preflight Atlas never
grants. Bearer requests are not sent automatically by browsers, so they
need no CSRF value.
"""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time

import audit_log
import database as db
import device_identity

COOKIE = "atlas_client"
COOKIE_MAX_AGE = 10 * 365 * 24 * 3600          # Max-Age, not Expires: the clock may be wrong
REQUEST_TTL = 180
KINDS = {"browser", "android", "ios", "tracker"}

# Reachable without pairing: the page and its assets (it shows the pairing
# screen), public identity, health, and the pairing endpoints themselves.
EXEMPT_EXACT = {"/", "/mobile", "/tracker", "/favicon.ico", "/api/health", "/api/device/info",
                "/api/pair/status", "/api/pair/claim", "/api/pair/hotspot", "/api/pair/request",
                "/api/device"}
EXEMPT_PREFIX = ("/static/", "/fonts/", "/tiles/", "/api/pair/request/")
# Pairing endpoints that act on behalf of an already-paired client.
PAIRED_ONLY = re.compile(r"^/api/pair/(requests|clients)(/|$)|^/api/pair/request/[^/]+/(approve|deny)$")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

_lock = threading.Lock()
_cache = {}                     # token_hash -> (client dict | None, fetched_at)
_CACHE_TTL = 30
_failures = {}                  # ip -> [timestamps] of failed setup-code attempts
_global_failures = []


def required():
    """Pairing is enforced on every provisioned Atlas (and in tests on request)."""
    if os.environ.get("ATLAS_PAIRING") == "off":
        return False
    return device_identity.present() or os.environ.get("ATLAS_PAIRING") == "on"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_for(token_hash):
    """Per-client CSRF value (stable, derived; nothing extra to store)."""
    key = _csrf_key()
    return hmac.new(key, f"csrf|{token_hash}".encode(), hashlib.sha256).hexdigest()[:32]


def _csrf_key():
    try:
        with open(os.path.join(device_identity.IDENTITY_DIR, "proxy_secret"), "rb") as f:
            return b"atlas-csrf|" + f.read().strip()
    except OSError:
        import atlas_paths
        try:
            with open(os.path.join(atlas_paths.DATA_DIR, ".secret_key"), "rb") as f:
                return b"atlas-csrf|" + f.read().strip()
        except OSError:
            return b"atlas-csrf|development"


# ── storage ──────────────────────────────────────────────────────────────
def _issue(name, kind, proof):
    token = secrets.token_urlsafe(32)
    client_id = secrets.token_hex(8)
    now = int(time.time())
    conn = db.get_db()
    conn.execute("INSERT INTO paired_clients (id, name, kind, token_hash, proof, created, last_seen) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (client_id, _clean_name(name, kind), kind if kind in KINDS else "browser",
                  _hash(token), proof, now, now))
    conn.commit()
    audit_log.record("device_paired", _clean_name(name, kind), kind=kind if kind in KINDS else "browser",
                     method=proof.split(":", 1)[0])
    return client_id, token


def _clean_name(name, kind):
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()[:60]
    return name or {"android": "Android phone", "ios": "iPhone", "tracker": "Shared phone"}.get(kind, "Web browser")


def lookup(token):
    """The paired client for a token, or None (cached briefly)."""
    if not token or len(token) > 200:
        return None
    th = _hash(token)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(th)
        if hit and now - hit[1] < _CACHE_TTL:
            return hit[0]
    row = db.get_db().execute(
        "SELECT id, name, kind, created, last_seen FROM paired_clients WHERE token_hash=? AND revoked=0",
        (th,)).fetchone()
    client = dict(row, token_hash=th) if row else None
    if client and (client["last_seen"] or 0) < time.time() - 300:
        conn = db.get_db()
        conn.execute("UPDATE paired_clients SET last_seen=? WHERE id=?", (int(time.time()), client["id"]))
        conn.commit()
    with _lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[th] = (client, now)
    return client


def list_clients():
    rows = db.get_db().execute(
        "SELECT id, name, kind, proof, created, last_seen FROM paired_clients WHERE revoked=0 "
        "ORDER BY created").fetchall()
    return [dict(r) for r in rows]


def revoke(client_id):
    conn = db.get_db()
    cur = conn.execute("UPDATE paired_clients SET revoked=1 WHERE id=? AND revoked=0", (client_id,))
    conn.commit()
    with _lock:
        _cache.clear()
    return cur.rowcount > 0


def revoke_all():
    conn = db.get_db()
    conn.execute("DELETE FROM paired_clients")
    conn.execute("DELETE FROM pair_requests")
    conn.commit()
    with _lock:
        _cache.clear()


# ── request identity ─────────────────────────────────────────────────────
def token_from_environ(environ):
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip(), "bearer"
    for part in environ.get("HTTP_COOKIE", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and v:
            return v, "cookie"
    return None, None


def authenticate(environ):
    token, how = token_from_environ(environ)
    client = lookup(token) if token else None
    environ["atlas.client"] = client
    environ["atlas.auth"] = how if client else None
    return client


def client_check(environ):
    """request_guard hook: None to continue, or (status, message, code)."""
    client = authenticate(environ)
    if not required():
        return None
    path = environ.get("PATH_INFO", "")
    if path.startswith("/socket.io"):
        return None                     # decided in the Socket.IO connect handler
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if PAIRED_ONLY.match(path) and client is None:
        return ("401 Unauthorized", "This device is not connected to Atlas yet.", "pairing_required")
    exempt = path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIX)
    if client is None and not exempt:
        return ("401 Unauthorized", "This device is not connected to Atlas yet.", "pairing_required")
    if client is not None and method in MUTATING and environ.get("atlas.auth") == "cookie":
        sent = environ.get("HTTP_X_ATLAS_CSRF", "")
        if not hmac.compare_digest(sent.encode(), csrf_for(client["token_hash"]).encode()):
            return ("403 Forbidden", "Please reload the page and try again.", "csrf")
    return None


# ── proofs ───────────────────────────────────────────────────────────────
def _rate_limited(ip):
    now = time.monotonic()
    with _lock:
        recent = [t for t in _failures.get(ip, []) if now - t < 600]
        _failures[ip] = recent
        _global_failures[:] = [t for t in _global_failures if now - t < 600]
        return len(recent) >= 5 or len(_global_failures) >= 20


def _record_failure(ip):
    now = time.monotonic()
    with _lock:
        _failures.setdefault(ip, []).append(now)
        _global_failures.append(now)


def claim_with_setup_code(code, name, kind, ip):
    """Returns (client_id, token) or raises ValueError with a friendly message."""
    import atlas_blocking
    if _rate_limited(ip):
        raise ValueError("Too many attempts. Wait a few minutes and try again.")
    if not atlas_blocking.call(device_identity.check_setup_code, code):
        _record_failure(ip)
        audit_log.record("pairing_code_rejected", None)
        raise ValueError("That setup code doesn't match this Atlas. Check the label and try again.")
    return _issue(name, kind, "setup_code")


def hotspot_network(hotspot_active, iface_addr_fn):
    """The hotspot's network, if Atlas's own hotspot is running."""
    if not hotspot_active:
        return None
    cidr = iface_addr_fn()
    try:
        return ipaddress.ip_interface(cidr) if cidr else None
    except ValueError:
        return None


def claim_via_hotspot(environ, name, kind, hotspot_iface):
    """Trust a client that reached Atlas through Atlas's own hotspot.

    nginx reports which local address the connection arrived on; it must be
    the hotspot's address and the client must be inside the hotspot network.
    """
    if hotspot_iface is None:
        raise ValueError("Atlas's hotspot is not on.")
    server_addr = environ.get("atlas.server_addr")
    client_ip = environ.get("REMOTE_ADDR", "")
    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        raise ValueError("Connect to Atlas's Wi-Fi network first.")
    if server_addr != str(hotspot_iface.ip) or client not in hotspot_iface.network or client == hotspot_iface.ip:
        raise ValueError("Connect to Atlas's Wi-Fi network first.")
    return _issue(name, kind, "hotspot")


def create_request(name, kind, client_nonce):
    request_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    server_nonce = secrets.token_hex(8)
    code = device_identity.match_code(server_nonce, str(client_nonce or "")[:64])
    conn = db.get_db()
    conn.execute("DELETE FROM pair_requests WHERE created < ?", (int(time.time()) - 3600,))
    pending = conn.execute("SELECT COUNT(*) FROM pair_requests WHERE status='pending' AND created > ?",
                           (int(time.time()) - REQUEST_TTL,)).fetchone()[0]
    if pending >= 5:
        raise ValueError("Too many devices are waiting to be allowed. Try again in a few minutes.")
    conn.execute("INSERT INTO pair_requests (id, secret_hash, name, kind, server_nonce, match_code, created) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (request_id, _hash(secret), _clean_name(name, kind), kind if kind in KINDS else "browser",
                  server_nonce, code, int(time.time())))
    conn.commit()
    return {"request_id": request_id, "secret": secret, "server_nonce": server_nonce,
            "match_code": code, "expires_in": REQUEST_TTL}


def pending_requests():
    rows = db.get_db().execute(
        "SELECT id, name, kind, match_code, created FROM pair_requests WHERE status='pending' AND created > ?",
        (int(time.time()) - REQUEST_TTL,)).fetchall()
    return [dict(r) for r in rows]


def decide(request_id, approve, by_client_id):
    conn = db.get_db()
    cur = conn.execute("UPDATE pair_requests SET status=?, decided_by=? WHERE id=? AND status='pending' "
                       "AND created > ?", ("approved" if approve else "denied", by_client_id,
                                           request_id, int(time.time()) - REQUEST_TTL))
    conn.commit()
    return cur.rowcount > 0


def poll_request(request_id, secret):
    """The waiting client's view. Issues the token exactly once on approval."""
    row = db.get_db().execute("SELECT * FROM pair_requests WHERE id=?", (request_id,)).fetchone()
    if not row or not hmac.compare_digest(row["secret_hash"], _hash(str(secret or ""))):
        return {"status": "unknown"}
    if row["status"] == "pending" and row["created"] <= int(time.time()) - REQUEST_TTL:
        return {"status": "expired"}
    if row["status"] != "approved":
        return {"status": row["status"]}
    conn = db.get_db()
    cur = conn.execute("UPDATE pair_requests SET status='issued' WHERE id=? AND status='approved'", (request_id,))
    conn.commit()
    if cur.rowcount != 1:
        return {"status": "issued"}
    client_id, token = _issue(row["name"], row["kind"], f"approved_by:{row['decided_by']}")
    return {"status": "approved", "client_id": client_id, "token": token}
