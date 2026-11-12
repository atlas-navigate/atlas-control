"""
Pairing: the one-time trust between a phone or browser and this Atlas.

There are no accounts, usernames, passwords or PINs. A client becomes
trusted once, by proving it is physically with the owner's Atlas.

The first device to pair becomes the primary user. While there is none
("unclaimed"), a device pairs by:

  * button colour — the owner presses the Atlas button, the light shows one
    of six colours, and the device must pick that colour (one try per press);
  * setup code — the backup: the code on the device label, typed in or
    scanned from the label's QR code.

Once Atlas has a primary user, every other device asks, and only the primary
user can tap "Allow" (both screens show the same 4-digit code). Being on
Atlas's own Wi-Fi proves nothing: its password is the same on every unit.

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
# Protocol the apps speak. 2: button colour + primary user; no hotspot proof.
VERSION = 2

# Reachable without pairing: the page and its assets (it shows the pairing
# screen), public identity, health, and the pairing endpoints themselves.
EXEMPT_EXACT = {"/", "/mobile", "/tracker", "/favicon.ico", "/api/health", "/api/device/info",
                "/api/pair/status", "/api/pair/claim", "/api/pair/hotspot", "/api/pair/request",
                "/api/pair/color/start", "/api/device"}
EXEMPT_PREFIX = ("/static/", "/fonts/", "/tiles/", "/api/pair/request/", "/api/pair/color/")
# Pairing endpoints that act on behalf of an already-paired client.
PAIRED_ONLY = re.compile(r"^/api/pair/(requests|clients)(/|$)|^/api/pair/request/[^/]+/(approve|deny)$")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

OWNER = "owner"
MEMBER = "member"

_lock = threading.Lock()
_cache = {}                     # token_hash -> (client dict | None, fetched_at)
_CACHE_TTL = 30
# Failed setup codes are counted in app_settings (wall-clock seconds), so a
# restart does not hand a guesser a fresh allowance.
FAILURES_SETTING = "pairing_failures"
FAILURE_WINDOW = 600


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
    """A new paired client. The first one while Atlas has no primary user
    becomes it; everyone after that is a member."""
    token = secrets.token_urlsafe(32)
    client_id = secrets.token_hex(8)
    now = int(time.time())
    conn = db.get_db()
    with _lock:
        role = MEMBER if owner_id() else OWNER
        conn.execute("INSERT INTO paired_clients (id, name, kind, token_hash, proof, created, last_seen, role) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (client_id, _clean_name(name, kind), kind if kind in KINDS else "browser",
                      _hash(token), proof, now, now, role))
        conn.commit()
    audit_log.record("device_paired", _clean_name(name, kind), kind=kind if kind in KINDS else "browser",
                     method=proof.split(":", 1)[0], role=role)
    return client_id, token


def owner_id():
    """The primary user's client id, or None while Atlas is unclaimed.

    A database where clients exist but none is primary (made by a release
    without roles, or after the primary was removed by one) promotes the
    oldest remaining client, as the migration did."""
    conn = db.get_db()
    row = conn.execute("SELECT id FROM paired_clients WHERE revoked=0 AND role=? ORDER BY created LIMIT 1",
                       (OWNER,)).fetchone()
    if row:
        return row["id"]
    oldest = conn.execute("SELECT id FROM paired_clients WHERE revoked=0 ORDER BY created, rowid LIMIT 1").fetchone()
    if not oldest:
        return None
    conn.execute("UPDATE paired_clients SET role=? WHERE id=?", (OWNER, oldest["id"]))
    conn.commit()
    _cache.clear()
    return oldest["id"]


def claimed():
    """True once Atlas has a primary user."""
    return owner_id() is not None


def is_owner(client):
    return bool(client) and client.get("role") == OWNER


def make_primary(client_id):
    """Hand the primary-user role to another paired device (audited by the
    route that calls it)."""
    conn = db.get_db()
    with _lock:
        row = conn.execute("SELECT id FROM paired_clients WHERE id=? AND revoked=0", (client_id,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE paired_clients SET role=? WHERE revoked=0 AND id<>?", (MEMBER, client_id))
        conn.execute("UPDATE paired_clients SET role=? WHERE id=?", (OWNER, client_id))
        conn.commit()
        _cache.clear()
    return True


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
        "SELECT id, name, kind, role, created, last_seen FROM paired_clients WHERE token_hash=? AND revoked=0",
        (th,)).fetchone()
    client = dict(row, token_hash=th) if row else None
    seen = (client["last_seen"] or 0) if client else 0
    if client and (seen < time.time() - 300 or seen > time.time() + 300):
        conn = db.get_db()
        conn.execute("UPDATE paired_clients SET last_seen=? WHERE id=?", (int(time.time()), client["id"]))
        conn.commit()
    with _lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[th] = (client, now)
    return client


def list_clients():
    owner_id()                       # repair a missing primary before showing roles
    rows = db.get_db().execute(
        "SELECT id, name, kind, proof, role, created, last_seen FROM paired_clients WHERE revoked=0 "
        "ORDER BY created").fetchall()
    return [dict(r, role=r["role"] or MEMBER) for r in rows]


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
        _color["requests"].clear()
        _color["press_until"] = 0.0


IDENTITY_SETTING = "pairing_identity"


def bind_identity():
    """Pairings belong to the identity that issued them. Software updates,
    reboots and factory resets keep the identity, so paired phones and
    browsers keep working. Only a new identity (storage cloned into another
    unit) ends them. Returns True when this start found a new identity."""
    if not device_identity.present():
        return False
    current = device_identity.device().get("device_id")
    if not current:
        return False
    row = db.get_db().execute("SELECT value FROM app_settings WHERE key=?", (IDENTITY_SETTING,)).fetchone()
    stored = row["value"] if row else None
    if stored == current:
        return False
    if stored:
        revoke_all()
        audit_log.record("identity_changed", reason=str(device_identity.device().get("created_reason") or "")[:40])
    db.set_app_setting(IDENTITY_SETTING, current)
    return bool(stored)


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
class PairingError(ValueError):
    """A refusal the client can act on: `code` tells the apps which screen
    comes next (e.g. "claimed" → ask the primary user instead)."""

    def __init__(self, message, code="refused"):
        super().__init__(message)
        self.code = code


CLAIMED_MESSAGE = ("This Atlas is already set up. Ask its primary user to allow this device "
                   "— they'll see a prompt on their phone.")


def _load_failures():
    import json
    raw = db.get_app_settings().get(FAILURES_SETTING)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    now = time.time()

    def keep(ts):
        return [t for t in ts if isinstance(t, (int, float)) and now - FAILURE_WINDOW < t <= now + 60]
    return {"ip": {ip: keep(ts) for ip, ts in (data.get("ip") or {}).items() if keep(ts)},
            "all": keep(data.get("all") or [])}


def _rate_limited(ip):
    failures = _load_failures()
    return len(failures["ip"].get(ip, [])) >= 5 or len(failures["all"]) >= 20


def _record_failure(ip):
    import json
    with _lock:
        failures = _load_failures()
        failures["ip"].setdefault(ip, []).append(time.time())
        failures["all"].append(time.time())
        if len(failures["ip"]) > 200:                 # many addresses: keep the busiest
            failures["ip"] = dict(sorted(failures["ip"].items(), key=lambda kv: -len(kv[1]))[:200])
        db.set_app_setting(FAILURES_SETTING, json.dumps(failures))


def claim_with_setup_code(code, name, kind, ip):
    """The label's backup proof, accepted only while Atlas is unclaimed.
    Returns (client_id, token) or raises PairingError."""
    import atlas_blocking
    if claimed():
        # Someone has the label but Atlas is set up: worth a line in the log.
        audit_log.record("pairing_code_rejected", None, reason="claimed")
        raise PairingError(CLAIMED_MESSAGE, "claimed")
    if _rate_limited(ip):
        raise PairingError("Too many attempts. Wait a few minutes and try again.", "locked")
    if not atlas_blocking.call(device_identity.check_setup_code, code):
        _record_failure(ip)
        audit_log.record("pairing_code_rejected", None)
        raise PairingError("That setup code doesn't match this Atlas. Use the code labelled "
                           "“Setup code” — not the Wi-Fi password.", "wrong_code")
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


# ── button colour ────────────────────────────────────────────────────────
# A pairing attempt waits for a press (press_button); the press shows a
# colour on the light (choose_color); the device picks once (paired or
# wrong). Two devices waiting at once is a conflict: nobody can tell whose
# press it was, so both start over. Sessions live in memory: a restart simply
# asks the phone to begin again.
COLOR_TTL = 120
PRESS_WINDOW = 60
MAX_COLOR_REQUESTS = 5
COLOR_IDS = ("green", "blue", "purple", "yellow", "cyan", "white")
_color = {"requests": {}, "press_until": 0.0}


def _color_live(req, now):
    return req["state"] in ("press_button", "choose_color") and now < req["expires"]


def _color_prune(now):
    for rid, req in list(_color["requests"].items()):
        if req["state"] in ("press_button", "choose_color") and now >= req["expires"]:
            req["state"] = "expired"
            req["color"] = None
        if now - req["created"] > 3 * COLOR_TTL:
            _color["requests"].pop(rid, None)


def _color_bind(req, now):
    import random
    req["color"] = secrets.choice(COLOR_IDS)
    choices = list(COLOR_IDS)
    random.SystemRandom().shuffle(choices)
    req["choices"] = choices
    req["state"] = "choose_color"
    req["expires"] = now + COLOR_TTL


def _color_conflict(reqs):
    for req in reqs:
        req["state"] = "conflict"
        req["color"] = None
    _color["press_until"] = 0.0


def _color_view(req, now):
    view = {"request_id": req["id"], "state": req["state"],
            "expires_in": max(0, int(req["expires"] - now)) if _color_live(req, now) else 0}
    if req["state"] == "choose_color":
        view["choices"] = list(req["choices"])
    return view


def color_start(name, kind):
    """Begin pairing by button colour. Raises PairingError when Atlas already
    has a primary user (ask them instead) or too many devices are waiting."""
    if claimed():
        raise PairingError(CLAIMED_MESSAGE, "claimed")
    now = time.monotonic()
    conflict = False
    with _lock:
        _color_prune(now)
        live = [r for r in _color["requests"].values() if _color_live(r, now)]
        if len(live) >= MAX_COLOR_REQUESTS:
            raise PairingError("Too many devices are trying to connect. Try again in a few minutes.", "busy")
        secret = secrets.token_urlsafe(24)
        req = {"id": secrets.token_hex(8), "secret_hash": _hash(secret), "name": _clean_name(name, kind),
               "kind": kind if kind in KINDS else "browser", "state": "press_button", "color": None,
               "choices": None, "created": now, "expires": now + COLOR_TTL}
        _color["requests"][req["id"]] = req
        if now < _color["press_until"]:
            # The button was pressed just before this device asked.
            choosing = [r for r in live if r["state"] == "choose_color"]
            if choosing:
                _color_conflict(choosing + [req])
                conflict = True
            else:
                _color_bind(req, now)
        view = dict(_color_view(req, now), secret=secret)
    if conflict:
        audit_log.record("pairing_color_conflict", None)
    return view


def button_pressed():
    """A short press on the Atlas button. Returns what it did: "bound" (the
    waiting device now picks a colour), "window" (nobody waiting yet: the next
    device to ask is bound), "conflict", "busy" (a device is already picking)
    or "claimed" (Atlas is set up: nothing to do)."""
    if claimed():
        return "claimed"
    now = time.monotonic()
    with _lock:
        _color_prune(now)
        live = [r for r in _color["requests"].values() if _color_live(r, now)]
        if any(r["state"] == "choose_color" for r in live):
            return "busy"
        if len(live) == 1:
            _color_bind(live[0], now)
            _color["press_until"] = 0.0
            return "bound"
        if len(live) > 1:
            _color_conflict(live)
            result = "conflict"
        else:
            _color["press_until"] = now + PRESS_WINDOW
            return "window"
    audit_log.record("pairing_color_conflict", None)
    return result


def _color_get(request_id, secret):
    req = _color["requests"].get(str(request_id or ""))
    if not req or not hmac.compare_digest(req["secret_hash"], _hash(str(secret or ""))):
        return None
    return req


def color_poll(request_id, secret):
    now = time.monotonic()
    with _lock:
        _color_prune(now)
        req = _color_get(request_id, secret)
        if not req:
            return {"state": "unknown"}
        return _color_view(req, now)


def color_answer(request_id, secret, color):
    """The device's pick. Returns (client_id, token, kind) for the right
    colour; raises PairingError otherwise. One pick per press."""
    now = time.monotonic()
    with _lock:
        _color_prune(now)
        req = _color_get(request_id, secret)
        if not req:
            raise PairingError("This connection attempt has ended. Start again.", "unknown")
        if req["state"] != "choose_color":
            raise PairingError("Press the button on your Atlas first.", req["state"])
        right = hmac.compare_digest(str(color or ""), req["color"] or "")
        req["state"] = "paired" if right else "wrong"
        req["color"] = None
        name, kind = req["name"], req["kind"]
    if not right:
        audit_log.record("pairing_color_rejected", None)
        raise PairingError("That's not the colour the light showed. Press the button on your Atlas "
                           "to try again.", "wrong")
    if claimed():
        raise PairingError(CLAIMED_MESSAGE, "claimed")
    client_id, token = _issue(name, kind, "button_color")
    return client_id, token, kind


def color_state():
    """What the light shows for pairing: {"pair_color", "press_window"}."""
    now = time.monotonic()
    with _lock:
        color = next((r["color"] for r in _color["requests"].values()
                      if r["state"] == "choose_color" and now < r["expires"]), None)
        return {"pair_color": color, "press_window": color is None and now < _color["press_until"]}


def create_request(name, kind, client_nonce):
    request_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    server_nonce = secrets.token_hex(8)
    code = device_identity.match_code(server_nonce, str(client_nonce or "")[:64])
    conn = db.get_db()
    now = int(time.time())
    conn.execute("DELETE FROM pair_requests WHERE created < ? OR created > ?", (now - 3600, now + 3600))
    pending = conn.execute("SELECT COUNT(*) FROM pair_requests WHERE status='pending' AND " + _LIVE,
                           _live_args()).fetchone()[0]
    if pending >= 5:
        raise ValueError("Too many devices are waiting to be allowed. Try again in a few minutes.")
    conn.execute("INSERT INTO pair_requests (id, secret_hash, name, kind, server_nonce, match_code, created) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (request_id, _hash(secret), _clean_name(name, kind), kind if kind in KINDS else "browser",
                  server_nonce, code, int(time.time())))
    conn.commit()
    return {"request_id": request_id, "secret": secret, "server_nonce": server_nonce,
            "match_code": code, "expires_in": REQUEST_TTL}


# A request is live for REQUEST_TTL seconds. One "created" in the future was
# made before the clock was corrected backward (after power loss): treat it as
# expired rather than approvable until the clock catches up.
_LIVE = "created > ? AND created <= ?"


def _live_args():
    now = int(time.time())
    return (now - REQUEST_TTL, now + 60)


def pending_requests():
    rows = db.get_db().execute(
        "SELECT id, name, kind, match_code, created FROM pair_requests WHERE status='pending' AND " + _LIVE,
        _live_args()).fetchall()
    return [dict(r) for r in rows]


def decide(request_id, approve, by_client_id):
    conn = db.get_db()
    cur = conn.execute("UPDATE pair_requests SET status=?, decided_by=? WHERE id=? AND status='pending' "
                       "AND " + _LIVE, ("approved" if approve else "denied", by_client_id, request_id)
                       + _live_args())
    conn.commit()
    return cur.rowcount > 0


def poll_request(request_id, secret):
    """The waiting client's view. Issues the token exactly once on approval."""
    row = db.get_db().execute("SELECT * FROM pair_requests WHERE id=?", (request_id,)).fetchone()
    if not row or not hmac.compare_digest(row["secret_hash"], _hash(str(secret or ""))):
        return {"status": "unknown"}
    low, high = _live_args()
    if row["status"] == "pending" and not (low < row["created"] <= high):
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
