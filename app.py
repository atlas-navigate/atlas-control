#!/usr/bin/env python3
"""
Atlas Control — Local Offline GUI
For Jetson Orin Nano + Heltec V4 on the front USB-C port (udev symlink
/dev/meshtastic; the 40-pin header UART /dev/ttyTHS1 remains supported as a
fallback — see README "Heltec V4 mesh radio")
"""
# Save the real stdlib Queue BEFORE monkey patching.
# meshtastic's DeferredExecution runs in a real OS thread (not a greenlet),
# so it must use the real Queue — the gevent-patched Queue throws LoopExit
# when .get() is called from a non-greenlet thread.
import queue as _stdlib_queue
_RealQueue = _stdlib_queue.Queue

# Save the real stdlib select.select BEFORE monkey patching so that pyserial's
# blocking read() — used from native worker threads (GPS reader, meshtastic
# reader) — can issue a real kernel-blocking select. gevent's patched
# select.select burns 100% CPU when invoked from a non-greenlet thread because
# it routes through libev's per-thread hub instead of just sleeping.
import select as _stdlib_select
_RealSelect = _stdlib_select.select

# gevent monkey patching must happen before any other imports so that
# urllib/socket I/O inside gevent greenlets is non-blocking.
# thread=False and os=False keep Python threading and os.fork intact.
# queue=False is required for meshtastic: its background worker uses the stdlib
# queue from a real OS thread, and gevent's queue raises LoopExit there.
# so meshtastic, pyserial, and other thread-based libraries work correctly.
from gevent import monkey; monkey.patch_all(thread=False, os=False, signal=False, queue=False, subprocess=False, select=False, time=False)

# Force pyserial's POSIX backend to use the unpatched select.select. The patched
# version pegs a CPU core when called from the GPS reader / Meshtastic reader
# native threads (they aren't greenlets, so gevent's cooperative path degrades
# into a libev busy-loop). Shadow only pyserial's reference so the rest of the
# process keeps the gevent-aware select.
import sys as _sys
try:
    import types as _types
    import serial.serialposix as _serialposix
    _native_select_ns = _types.SimpleNamespace(
        select=_RealSelect,
        error=_serialposix.select.error,
    )
    _serialposix.select = _native_select_ns
    print(
        f"[atlas] pyserial select shadow installed; "
        f"serialposix.select.select={_serialposix.select.select!r}",
        file=_sys.stderr,
    )
except Exception as _exc:
    print(f"[atlas] pyserial select shadow FAILED: {_exc}", file=_sys.stderr)

# Meshtastic creates a module-level DeferredExecution worker at import time.
# Patch its Queue dependency before importing meshtastic anywhere else so that
# worker thread uses the real stdlib Queue instead of gevent's queue.
import meshtastic.util as _meshtastic_util
_meshtastic_util.Queue = _RealQueue

import os
import sys
import re
import shutil
import json
import math
import time
import logging
import argparse
import threading
import subprocess
import unicodedata
import ipaddress as _ipaddress
import faulthandler as _faulthandler
import signal as _signal

# Allow `kill -USR2 <pid>` to dump every Python thread's stack to stderr.
# Used to diagnose runaway CPU loops without needing ptrace privileges.
try:
    _faulthandler.enable()
    _faulthandler.register(_signal.SIGUSR2, all_threads=True, chain=False)
except Exception:
    pass

from flask import Flask, render_template, request, jsonify, send_from_directory, send_file, Response, stream_with_context
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import atlas_paths
import atlas_blocking
import safe_math
import atlas_helper
import database as db
import meshtastic
import meshtastic.util
from mesh_manager import MeshManager
from gps_node import GpsNode as GpsManager      # ground-up rewrite with UBX + Kalman + self-DR
import ai_manager as ai_manager_mod
from ai_manager import (AIManager, reverse_geocode, geocoder_status,
                        _CAT_STOPWORDS, _detect_category,
                        available_ram_mb as _available_ram_mb, _ROUTING_MIN_FREE_MB)
from routing_node import RoutingNode, STATE_BBOX as _STATE_BBOX
from navigation_node import NavigationNode
from mobile_bridge import AtlasMobileBridge
from lan_beacon import LanBeacon
import system_stats

# Meshtastic may already have built its global publishingThread if it was
# imported before the queue patch landed in an earlier process. Replace it with
# a fresh DeferredExecution so the live worker thread blocks on a real Queue.
meshtastic.util.Queue = _RealQueue
meshtastic.publishingThread = meshtastic.util.DeferredExecution("publishing")

# ------------------------------------------------------------------ setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("atlas-control")
# Coordinates, secrets and channel keys never reach the journal at any level
# this filter sees; see sensitive_log.py.
import sensitive_log
sensitive_log.install()

_HIKING_CACHE_LOCK = threading.Lock()
_HIKING_PARKS_CACHE = {"db_mtime": None, "data": None}
_HIKING_TRAILS_CACHE = {}
_AUTO_HOTSPOT_LOCK = threading.Lock()
_AUTO_HOTSPOT_GRACE_UNTIL = 0.0
# Serialise all mutating Wi-Fi/hotspot operations so the failover loop and
# concurrent API requests don't stomp on each other.
_WIFI_OPS_LOCK = threading.RLock()
_WIFI_SWITCH_LOCK = threading.Lock()
_WIFI_SWITCH_STATE = {
    "pending": False,
    "ssid": "",
    "startedAt": 0,
    "finishedAt": 0,
    "ok": None,
    "message": "",
    "result": None,
}
_RFC6598_SHARED_NET = _ipaddress.ip_network("100.64.0.0/10")
_LOCAL_NETWORKS_LOCK = threading.Lock()
_LOCAL_NETWORKS_CACHE = {"fetched_at": 0.0, "nets": [], "addrs": []}

# LAN hint IPs — remember the last IP Atlas had per SSID so Android can skip
# mDNS and dial the IP directly after a LAN switch.
_LAN_HINTS_PATH = os.path.join(atlas_paths.DATA_DIR, "wifi_hints.json")
_LAN_HINTS_LOCK = threading.Lock()

def _load_lan_hints() -> dict:
    try:
        with open(_LAN_HINTS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_lan_hint(ssid: str, ip: str):
    if not ssid or not ip:
        return
    with _LAN_HINTS_LOCK:
        hints = _load_lan_hints()
        hints[ssid] = ip
        try:
            with open(_LAN_HINTS_PATH, "w") as f:
                json.dump(hints, f)
        except Exception:
            pass

def _get_lan_hint(ssid: str) -> str | None:
    if not ssid:
        return None
    with _LAN_HINTS_LOCK:
        return _load_lan_hints().get(ssid)

# Keep request/access logs from dominating disk I/O on the Jetson. The UI
# already polls and uses websockets heavily, so per-request INFO logging adds
# steady journal churn without helping normal operation.
for noisy_logger in (
    "geventwebsocket.handler",
    "engineio.server",
    "socketio.server",
):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

app = Flask(__name__, static_folder="static", template_folder="templates")

# Load (or generate) a persistent random secret key so session tokens can't
# be forged by knowing the source code. The key file is chmod 600 so only
# the atlas service user can read it.
def _load_secret_key():
    import secrets as _sec
    key_path = os.path.join(atlas_paths.DATA_DIR, ".secret_key")
    try:
        with open(key_path) as _f:
            k = _f.read().strip()
            if len(k) >= 32:
                return k
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    k = _sec.token_urlsafe(48)
    with open(key_path, "w") as _f:
        _f.write(k)
    os.chmod(key_path, 0o600)
    logger.info("Generated new secret key at %s", key_path)
    return k


def _nps_trails_db_path():
    return os.path.join(atlas_paths.STATIC_DATA_DIR, "nps_trails.db")


def _hiking_db_mtime(db_path):
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return None

app.config["SECRET_KEY"] = _load_secret_key()


def _discover_local_networks(cache_ttl=30):
    now = time.time()
    with _LOCAL_NETWORKS_LOCK:
        cached = _LOCAL_NETWORKS_CACHE
        if now - cached["fetched_at"] < cache_ttl:
            return list(cached["nets"]), list(cached["addrs"])

    nets = []
    addrs = []
    seen_nets = set()
    seen_addrs = set()
    try:
        out = atlas_blocking.check_output(["ip", "-o", "addr", "show", "up"], text=True, timeout=3)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4 or parts[2] not in ("inet", "inet6"):
                continue
            cidr = parts[3].strip()
            try:
                iface_net = _ipaddress.ip_interface(cidr)
            except ValueError:
                continue
            addr = iface_net.ip
            if addr.is_loopback or addr.is_link_local:
                continue
            addrs_key = str(addr)
            if addrs_key not in seen_addrs:
                addrs.append(addr)
                seen_addrs.add(addrs_key)
            net = iface_net.network
            net_key = str(net)
            if net_key not in seen_nets:
                nets.append(net)
                seen_nets.add(net_key)
    except Exception:
        pass

    if not addrs:
        try:
            out = atlas_blocking.check_output(["hostname", "-I"], text=True, timeout=2)
            for raw in out.split():
                try:
                    addr = _ipaddress.ip_address(raw.strip())
                except ValueError:
                    continue
                if addr.is_loopback or addr.is_link_local:
                    continue
                addrs_key = str(addr)
                if addrs_key not in seen_addrs:
                    addrs.append(addr)
                    seen_addrs.add(addrs_key)
                # Fallback assumptions for address-only discovery.
                if addr.version == 4:
                    if addr.is_private or addr in _RFC6598_SHARED_NET:
                        net = _ipaddress.ip_network(f"{addr}/24", strict=False)
                    else:
                        continue
                else:
                    net = _ipaddress.ip_network(f"{addr}/64", strict=False)
                net_key = str(net)
                if net_key not in seen_nets:
                    nets.append(net)
                    seen_nets.add(net_key)
        except Exception:
            pass

    with _LOCAL_NETWORKS_LOCK:
        _LOCAL_NETWORKS_CACHE["fetched_at"] = now
        _LOCAL_NETWORKS_CACHE["nets"] = list(nets)
        _LOCAL_NETWORKS_CACHE["addrs"] = list(addrs)
    return list(nets), list(addrs)


socketio = SocketIO(
    app,
    # Origin is enforced by request_guard.EdgeMiddleware (below), which sees
    # every /socket.io/ request before engine.io does.
    cors_allowed_origins="*",
    async_mode="gevent",
    http_compression=True,       # gzip-compress engine.io polling payloads
    compression_threshold=512,   # only compress payloads > 512 bytes
    ping_timeout=60,
    ping_interval=25,
)

def _extra_hostnames():
    """Owner-configured extra names for this Atlas (e.g. a VPN MagicDNS name)."""
    try:
        raw = db.get_app_settings().get("extra_hostnames", "")
    except Exception:
        return ()
    return [n for n in (raw or "").replace(";", ",").split(",") if n.strip()]


# Outermost WSGI layer: must wrap Flask-SocketIO's middleware, not sit inside
# it, or /socket.io/ traffic would bypass every check. See request_guard.py.
import request_guard
import pairing
import device_identity
import audit_log
import perf_metrics
import retention
import vector_store
app.wsgi_app = request_guard.EdgeMiddleware(
    app.wsgi_app,
    host_policy=request_guard.HostPolicy(extra_names=_extra_hostnames),
    client_check=pairing.client_check,
    logger=logger,
)

# ── Local security log ──────────────────────────────────────────────────
# Which paired device changed what — never the values themselves (no
# passwords, SSIDs, coordinates, messages or setting contents); see
# audit_log.py. Pairing, updates, factory reset and privacy deletions are
# recorded where they happen.
_AUDITED_ROUTES = [(m, re.compile(rf"^{pat}$"), action, keys) for m, pat, action, keys in (
    ("PUT", r"/api/settings", "settings_changed", True),
    ("PUT", r"/api/ai/settings", "ray_settings_changed", True),
    ("POST", r"/api/ai/model", "ray_model_changed", True),
    ("PUT", r"/api/ai/models/[\w.:-]+/overrides", "ray_settings_changed", True),
    ("DELETE", r"/api/ai/models/[\w.:-]+/overrides", "ray_settings_changed", False),
    ("PUT", r"/api/gps/share", "location_sharing_changed", True),
    ("PUT", r"/api/device/config/[a-z_]+", "radio_settings_changed", True),
    ("PUT", r"/api/device/owner", "radio_name_changed", False),
    ("POST", r"/api/wifi/connect", "wifi_connected", False),
    ("POST", r"/api/wifi/disconnect", "wifi_disconnected", False),
    ("POST", r"/api/hotspot/config", "hotspot_changed", False),
    ("POST", r"/api/hotspot/start", "hotspot_started", False),
    ("POST", r"/api/hotspot/stop", "hotspot_stopped", False),
    ("POST", r"/api/power/ai", "ray_power_changed", False),
    ("POST", r"/api/power/comms", "radio_power_changed", False),
    ("POST", r"/api/pair/request/[\w-]+/approve", "device_approved", False),
    ("POST", r"/api/pair/request/[\w-]+/deny", "device_denied", False),
    ("DELETE", r"/api/pair/clients/[\w-]+", "device_removed", False),
    ("POST", r"/api/update/(?:start|install|apply)", "update_requested", False),
    ("POST", r"/api/update/cancel", "update_cancelled", False),
    ("PUT", r"/api/privacy/retention", "retention_changed", True),
)]


@app.after_request
def _audit_sensitive_request(response):
    if response.status_code < 400 and request.method in ("POST", "PUT", "DELETE"):
        for method, pattern, action, with_keys in _AUDITED_ROUTES:
            if method == request.method and pattern.match(request.path):
                detail = {}
                body = request.get_json(silent=True) if with_keys else None
                if isinstance(body, dict):
                    detail["keys"] = sorted(str(k)[:60] for k in body)[:30]
                audit_log.record(action, audit_log.actor_from(request.environ), **detail)
                break
    return response


def _confirmed(expected=True):
    """Destructive requests must say so explicitly: {"confirm": true} or,
    for factory reset, {"confirm": "RESET"} — so a stray or scripted call
    can never erase anything by accident."""
    body = request.get_json(silent=True)
    return isinstance(body, dict) and body.get("confirm") == expected and \
        type(body.get("confirm")) is type(expected)


def _needs_confirmation(what):
    return jsonify({"ok": False, "error": f"Confirm to {what}.", "confirm_required": True}), 400


mesh = None
gps_manager = None
ai_manager = None
routing_node: RoutingNode = None
nav_node: NavigationNode  = None
mobile_bridge = None
lan_beacon: LanBeacon = None  # initialised in main(); see lan_beacon.py
# ── Rate limiter — memory-backed, keyed by client IP ─────────────────────────
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["300 per minute"],
    storage_uri="memory://",
)

@app.errorhandler(429)
def _too_many_requests(_exc):
    return jsonify({"error": "Too many tries — wait a minute and try again.", "code": "rate_limited"}), 429


# ── Access control: allow local-only client addresses ────────────────────────
# Covers normal private LANs plus shared-address/CGNAT networks some tethering
# and travel-router setups expose locally:
#   10.x.x.x      — VPN (10.13.13.x), hotspot (10.42.0.x), etc.
#   192.168.x.x   — home/office Wi-Fi
#   172.16-31.x.x — Docker, enterprise LANs
#   100.64/10     — RFC6598 shared address space seen on some local uplinks
#   127.x / ::1   — loopback

def _is_local(ip):
    try:
        addr = _ipaddress.ip_address(ip)
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        local_nets, _ = _discover_local_networks()
        return (
            addr.is_loopback
            or addr.is_private
            or (addr.version == 4 and addr in _RFC6598_SHARED_NET)
            or any(addr in net for net in local_nets)
        )
    except ValueError:
        return False

@app.before_request
def restrict_to_local():
    ip = request.remote_addr or ""
    if _is_local(ip):
        return   # allowed
    return (
        "<html><body style='font-family:sans-serif;padding:40px'>"
        "<h2>Access Denied</h2>"
        "<p>Atlas Control is only accessible from local networks.</p>"
        "<p>On your hotspot or LAN: <code>https://atlas.local</code></p>"
        "<p style='font-size:12px;color:#888'>Accept the certificate warning once — "
        "it is self-signed but the connection is encrypted and fully local.</p>"
        "</body></html>",
        403,
    )

@app.after_request
def add_security_headers(response):
    """Add security headers to every response."""
    # Prevent the browser from rendering this page in a frame (clickjacking)
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Stop MIME-type sniffing
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Block reflected XSS in legacy browsers
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    # No referrer to external sites
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Content-Security-Policy: local sources only. The prebuilt app page sets
    # the strict variant itself (index()); this looser one remains for pages
    # with inline script (tracker) and the in-browser-Babel fallback.
    response.headers.setdefault("Content-Security-Policy", _CSP_LOOSE)
    return response


_CSP_COMMON = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
               "font-src 'self' data:; connect-src 'self' ws: wss:; worker-src blob:; "
               "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
_CSP_LOOSE = "script-src 'self' 'unsafe-inline' 'unsafe-eval'; " + _CSP_COMMON
# The prebuilt app has no inline script, no inline handlers and no eval, so
# injected markup could not run script even if it got onto the page.
_CSP_STRICT = "script-src 'self'; " + _CSP_COMMON


# ── Input helpers ─────────────────────────────────────────────────────────────

def _validate_coord(lat, lon):
    """Validate lat/lon are finite and in range. Raises ValueError on bad input."""
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("Coordinates must be finite numbers")
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("Latitude must be between -90 and 90")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError("Longitude must be between -180 and 180")

def _sanitize_text(s, max_len=None):
    """Strip null bytes and C0/C1 control characters from user text input."""
    s = "".join(c for c in s if unicodedata.category(c)[0] != "C")
    if max_len:
        s = s[:max_len]
    return s.strip()


# ------------------------------------------------------------------ tile cache
_TILE_DIR = atlas_paths.TILES_DIR

# Pre-decoded 1×1 transparent PNG — returned for any missing topo tile so
# Leaflet shows the base map cleanly instead of broken-tile icons.
# Decoding at import time avoids a base64 decode on every missing-tile request.
import base64 as _b64
_TRANSPARENT_PNG = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
    "AABjkB6QAAAABJRU5ErkJggg=="
)

_FONTS_DIR = atlas_paths.FONTS_DIR

@app.route("/fonts/<path:fontstack>/<range>.pbf")
def serve_glyph(fontstack, range):
    """Serve offline map glyph (font) PBF files for MapLibre vector rendering."""
    # Resolve the full path and reject anything that escapes the fonts directory
    # (guards against path traversal like ../../etc/passwd)
    fonts_real = os.path.realpath(_FONTS_DIR)
    pbf_path   = os.path.realpath(os.path.join(_FONTS_DIR, fontstack, f"{range}.pbf"))
    if not pbf_path.startswith(fonts_real + os.sep):
        return Response("", status=403)
    if not os.path.exists(pbf_path):
        return Response("", status=204)
    return send_file(pbf_path, mimetype="application/x-protobuf")

def _serve_pmtiles_file(pmtiles_path, not_found_msg="File not found"):
    """Serve a PMTiles file with validated HTTP range request support."""
    if not os.path.exists(pmtiles_path):
        return Response(not_found_msg, status=404)
    file_size = os.path.getsize(pmtiles_path)
    range_header = request.headers.get("Range", "")
    if range_header:
        try:
            raw = range_header.strip()
            if not raw.startswith("bytes="):
                return Response("Invalid range", status=416)
            parts = raw[6:].split("-", 1)
            start = int(parts[0]) if parts[0] else 0
            end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            # Clamp and validate
            start = max(0, start)
            end   = min(end, file_size - 1)
            if start > end or start >= file_size:
                return Response("Range Not Satisfiable", status=416,
                                headers={"Content-Range": f"bytes */{file_size}"})
            length = end - start + 1
            # Cap a single range read at 32 MiB to prevent memory exhaustion
            _MAX_CHUNK = 32 * 1024 * 1024
            if length > _MAX_CHUNK:
                end = start + _MAX_CHUNK - 1
                length = _MAX_CHUNK
        except (ValueError, IndexError):
            return Response("Invalid range", status=416)
        with open(pmtiles_path, "rb") as f:
            f.seek(start)
            data = f.read(length)
        resp = Response(data, status=206, mimetype="application/octet-stream")
        resp.headers["Content-Range"]  = f"bytes {start}-{end}/{file_size}"
        resp.headers["Accept-Ranges"]  = "bytes"
        resp.headers["Content-Length"] = str(length)
        return resp
    return send_file(pmtiles_path, mimetype="application/octet-stream", conditional=True)

@app.route("/tiles/map.pmtiles")
def serve_pmtiles():
    """Serve the Protomaps PMTiles basemap with HTTP range request support."""
    return _serve_pmtiles_file(os.path.join(_TILE_DIR, "map.pmtiles"), "map.pmtiles not found")

@app.route("/tiles/topo.pmtiles")
def serve_topo_pmtiles():
    """Serve the topo PMTiles raster archive with HTTP range request support."""
    return _serve_pmtiles_file(
        os.path.join(_TILE_DIR, "topo.pmtiles"),
        "topo.pmtiles not found — run setup_topo_pmtiles.sh"
    )

@app.route("/tiles/<int:z>/<int:x>/<int:y>.png")
def serve_tile(z, x, y):
    """Serve offline raster map tiles from the local z/x/y.png directory."""
    tile_dir = os.path.join(_TILE_DIR, str(z), str(x))
    return send_from_directory(tile_dir, f"{y}.png", conditional=True)

@app.route("/tiles/topo/<int:z>/<int:x>/<int:y>.png")
def serve_topo_tile(z, x, y):
    """Serve offline topo overlay tiles; return transparent PNG for missing tiles
    so Leaflet shows the base map cleanly instead of broken-tile icons."""
    tile_dir = os.path.join(_TILE_DIR, "topo", str(z), str(x))
    for ext in ("png", "jpg", "jpeg"):
        tile_name = f"{y}.{ext}"
        tile_path = os.path.join(tile_dir, tile_name)
        if os.path.exists(tile_path):
            return send_from_directory(tile_dir, tile_name, conditional=True)
    # 1×1 transparent PNG — lets the base layer show through
    return Response(_TRANSPARENT_PNG, mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------------ JSX pre-compilation
# The Android WebView can't spare the ~300 MB that in-browser Babel uses to
# compile the Atlas JSX bundle at runtime.  We pre-compile once at startup
# (using the vendored babel.min.js via Node) and serve the result as a plain
# <script> tag via the /mobile route.  If Node/compilation fails the route
# falls back to the normal index.html with Babel intact.

_BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
_INDEX_HTML       = os.path.join(_BASE_DIR, "templates", "index.html")
_COMPILED_JS      = os.path.join(_BASE_DIR, "static", "app.compiled.js")
_MOBILE_HTML      = None   # cached modified HTML string (in-memory)
_MOBILE_HTML_LOCK = threading.Lock()


def _build_mobile_html():
    """
    Return index.html with Babel stripped out and the pre-compiled script
    injected.  Returns None if the compiled JS file doesn't exist yet.
    """
    if not os.path.exists(_COMPILED_JS):
        return None
    try:
        with open(_INDEX_HTML, "r", encoding="utf-8") as fh:
            html = fh.read()
        # A release ships a content-hashed copy that browsers may cache forever.
        app_js = "/static/app.compiled.js"
        try:
            with open(os.path.join(_BASE_DIR, "static", "build", "manifest.json"), encoding="utf-8") as fh:
                app_js = json.load(fh).get("app_js") or app_js
        except (OSError, ValueError):
            pass
        # Drop the Babel loader
        html = html.replace('<script src="/static/vendor/babel.min.js"></script>\n', "")
        # Replace the inline JSX block with the pre-compiled external script.
        # The block spans from <script type="text/babel"> … </script>.
        import re as _re
        html = _re.sub(
            r'<script type="text/babel">.*?</script>',
            lambda _m: f'<script src="{app_js}"></script>',
            html,
            flags=_re.DOTALL,
        )
        # Nothing blocks the first paint; deferred scripts still run in order.
        html = html.replace("<script src=", "<script defer src=")
        return html
    except Exception as exc:
        logger.warning("mobile HTML build failed: %s", exc)
        return None


def _precompile_jsx():
    """
    Extract the JSX block from index.html, compile it with Node + Babel
    standalone, and cache the result in static/app.compiled.js.
    Runs in a background thread so it never blocks startup.
    """
    global _MOBILE_HTML
    try:
        # Only recompile if index.html is newer than the compiled output. A
        # release directory is read-only and always ships its bundle prebuilt
        # (extraction leaves sub-second mtime differences, so don't compare).
        if os.path.exists(_COMPILED_JS):
            if not atlas_paths.code_is_writable() or \
                    os.path.getmtime(_INDEX_HTML) <= os.path.getmtime(_COMPILED_JS):
                logger.info("JSX: using the prebuilt bundle")
                with _MOBILE_HTML_LOCK:
                    _MOBILE_HTML = _build_mobile_html()
                return

        # A release directory is read-only and ships its bundle prebuilt;
        # only a developer checkout compiles at runtime.
        if not atlas_paths.code_is_writable():
            logger.warning("JSX pre-compile: bundle missing or stale and the code "
                           "directory is read-only; serving in-browser Babel")
            return

        # Extract JSX from index.html
        with open(_INDEX_HTML, "r", encoding="utf-8") as fh:
            html = fh.read()

        import re as _re
        m = _re.search(r'<script type="text/babel">(.*?)</script>', html, _re.DOTALL)
        if not m:
            logger.warning("JSX pre-compile: no <script type=\"text/babel\"> block found")
            return

        jsx_content = m.group(1)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jsx", mode="w",
                                        encoding="utf-8", delete=False) as tmp:
            tmp.write(jsx_content)
            tmp_path = tmp.name

        compile_script = os.path.join(_BASE_DIR, "compile_jsx.js")
        result = subprocess.run(
            ["node", compile_script, tmp_path, _COMPILED_JS],
            capture_output=True, text=True, timeout=120,
        )
        os.unlink(tmp_path)

        if result.returncode == 0:
            logger.info("JSX pre-compile: %s", result.stdout.strip())
            with _MOBILE_HTML_LOCK:
                _MOBILE_HTML = _build_mobile_html()
        else:
            logger.warning("JSX pre-compile failed (rc=%d): %s",
                           result.returncode, result.stderr.strip()[:400])
    except Exception as exc:
        logger.warning("JSX pre-compile error: %s", exc)


# Kick off compilation in the background so it never delays startup
threading.Thread(target=_precompile_jsx, daemon=True, name="jsx-precompile").start()


# ------------------------------------------------------------------ pages
@app.route("/")
def index():
    # Prefer the pre-compiled JSX bundle when it's ready: shipping the raw
    # 9.7 k-line template with babel-standalone forces every desktop browser
    # to compile JSX on every cold load (multi-second hit on the Jetson).
    # Fall back to the raw template only while the background precompile is
    # still in flight or has failed.
    global _MOBILE_HTML
    with _MOBILE_HTML_LOCK:
        html = _MOBILE_HTML
    if html is None:
        built = _build_mobile_html()
        if built:
            with _MOBILE_HTML_LOCK:
                _MOBILE_HTML = built
                html = built
    if html:
        resp = Response(html, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = _CSP_STRICT
    else:
        resp = send_file(os.path.join(_BASE_DIR, "templates", "index.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/mobile")
def mobile():
    """
    Serves index.html with Babel stripped and the pre-compiled JS injected.
    Used by the Android / iOS apps to avoid the 300 MB in-browser Babel cost.
    Never serve the raw Babel page to embedded mobile clients because Android
    WebView will often flash the shell and then blank out while Babel tries to
    compile the full app in-process.
    """
    global _MOBILE_HTML
    with _MOBILE_HTML_LOCK:
        html = _MOBILE_HTML
    if html is None:
        built = _build_mobile_html()
        if built:
            with _MOBILE_HTML_LOCK:
                _MOBILE_HTML = built
                html = built
    if html:
        resp = Response(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = _CSP_STRICT
        return resp
    embedded = request.args.get("embedded") == "1" or "AtlasMobile" in request.headers.get("User-Agent", "")
    if embedded:
        return Response(
            """
            <!doctype html>
            <html lang="en">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>Atlas Mobile</title>
              <style>
                body {
                  margin: 0;
                  min-height: 100vh;
                  display: grid;
                  place-items: center;
                  background: #09111c;
                  color: #f3f7fb;
                  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                }
                .card {
                  max-width: 32rem;
                  margin: 1.5rem;
                  padding: 1.25rem 1.4rem;
                  border-radius: 16px;
                  background: rgba(17, 27, 43, 0.92);
                  border: 1px solid rgba(126, 156, 191, 0.18);
                  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.28);
                }
                h1 { margin: 0 0 0.75rem; font-size: 1.1rem; }
                p { margin: 0.5rem 0; color: #c9d7e6; }
                code { color: #5ef2c2; }
              </style>
            </head>
            <body>
              <div class="card">
                <h1>Atlas mobile UI is still building</h1>
                <p>The precompiled mobile bundle is not ready yet, so Atlas is avoiding the heavy fallback page that can blank Android WebView.</p>
                <p>Wait a few seconds and reload. If this persists, rebuild the mobile bundle on Atlas so <code>/static/app.compiled.js</code> is present.</p>
              </div>
            </body>
            </html>
            """,
            mimetype="text/html",
            headers={"Cache-Control": "no-store"},
        )
    # Desktop browser fallback remains available for local debugging.
    resp = send_file(_INDEX_HTML)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/tracker")
def tracker_page():
    return send_file(os.path.join(_BASE_DIR, "templates", "tracker.html"))


# ------------------------------------------------------------------ API: device
@app.route("/api/device/info")
def api_device_info():
    """Public identity: what discovery shows before a device is paired."""
    import atlas_version
    dev = device_identity.device()
    return jsonify({
        "app": "atlas-control",
        "api": 2,
        # Pairing protocol the apps speak (claim / hotspot / approval, bearer
        # tokens, SPKI pin). An app that sees a higher number asks to be updated.
        "pairing": 1,
        "device_id": dev.get("device_id"),
        "name": dev.get("name"),
        "hostname": dev.get("hostname"),
        "tls_spki_sha256": dev.get("tls_spki_sha256"),
        "version": atlas_version.version(),
        "pairing_required": pairing.required(),
    })


# ------------------------------------------------------------------ API: pairing
def _pair_kind(data):
    kind = str(data.get("kind") or "browser")
    return kind if kind in pairing.KINDS else "browser"


def _pair_response(client_id, token, kind, extra=None):
    """Browsers get an HttpOnly cookie; native apps get the token itself."""
    body = {"ok": True, "client_id": client_id, "csrf": pairing.csrf_for(pairing._hash(token))}
    if kind in ("android", "ios"):
        body["token"] = token
    body.update(extra or {})
    resp = jsonify(body)
    resp.set_cookie(pairing.COOKIE, token, max_age=pairing.COOKIE_MAX_AGE, httponly=True,
                    secure=request.is_secure, samesite="Strict", path="/")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _hotspot_interface():
    """ip_interface of Atlas's own hotspot, or None when it is not running."""
    iface = _wifi_iface()
    if not iface:
        return None
    out, rc = _nmcli("-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", iface)
    if rc != 0:
        return None
    conn, cidr = "", None
    for line in out.splitlines():
        if line.startswith("GENERAL.CONNECTION:"):
            conn = line.split(":", 1)[1].strip()
        elif line.startswith("IP4.ADDRESS") and cidr is None:
            cidr = line.split(":", 1)[1].strip()
    return pairing.hotspot_network(conn == "Hotspot", lambda: cidr)


@app.route("/api/pair/status")
def api_pair_status():
    client = request.environ.get("atlas.client")
    dev = device_identity.device()
    hs = None
    if client is None and request.environ.get("atlas.server_addr"):
        try:
            hs = _hotspot_interface()
        except Exception:
            hs = None
    body = {
        "required": pairing.required(),
        "paired": client is not None,
        "device": {"name": dev.get("name"), "id": dev.get("device_id"), "hostname": dev.get("hostname")},
        "via_hotspot": bool(hs and request.environ.get("atlas.server_addr") == str(hs.ip)),
    }
    if client is not None:
        body["client"] = {"id": client["id"], "name": client["name"], "kind": client["kind"]}
        if request.environ.get("atlas.auth") == "cookie":
            body["csrf"] = pairing.csrf_for(client["token_hash"])
    resp = jsonify(body)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/pair/claim", methods=["POST"])
@limiter.limit("10 per minute")
def api_pair_claim():
    data = request.get_json(silent=True) or {}
    kind = _pair_kind(data)
    try:
        client_id, token = pairing.claim_with_setup_code(data.get("setup_code"), data.get("name"), kind,
                                                         request.remote_addr or "")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    logger.info("Paired a new %s using the setup code", kind)
    return _pair_response(client_id, token, kind)


@app.route("/api/pair/hotspot", methods=["POST"])
@limiter.limit("10 per minute")
def api_pair_hotspot():
    data = request.get_json(silent=True) or {}
    kind = _pair_kind(data)
    try:
        client_id, token = pairing.claim_via_hotspot(request.environ, data.get("name"), kind,
                                                     _hotspot_interface())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    logger.info("Paired a new %s over Atlas's hotspot", kind)
    return _pair_response(client_id, token, kind)


@app.route("/api/pair/request", methods=["POST"])
@limiter.limit("6 per minute")
def api_pair_request():
    data = request.get_json(silent=True) or {}
    try:
        req = pairing.create_request(data.get("name"), _pair_kind(data), data.get("client_nonce"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429
    socketio.emit("pair_request", {"request_id": req["request_id"], "name": pairing._clean_name(
        data.get("name"), _pair_kind(data)), "kind": _pair_kind(data), "match_code": req["match_code"]},
        to="paired", namespace="/")
    return jsonify(dict(req, ok=True))


@app.route("/api/pair/request/<request_id>")
def api_pair_request_poll(request_id):
    result = pairing.poll_request(request_id, request.args.get("secret"))
    if result.get("status") == "approved":
        kind = request.args.get("kind") or "browser"
        return _pair_response(result["client_id"], result["token"], kind if kind in pairing.KINDS else "browser",
                              {"status": "approved"})
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/pair/requests")
def api_pair_requests():
    return jsonify(pairing.pending_requests())


@app.route("/api/pair/request/<request_id>/approve", methods=["POST"])
def api_pair_approve(request_id):
    ok = pairing.decide(request_id, True, request.environ["atlas.client"]["id"])
    socketio.emit("pair_request_decided", {"request_id": request_id, "approved": True}, to="paired", namespace="/")
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/api/pair/request/<request_id>/deny", methods=["POST"])
def api_pair_deny(request_id):
    ok = pairing.decide(request_id, False, request.environ["atlas.client"]["id"])
    socketio.emit("pair_request_decided", {"request_id": request_id, "approved": False}, to="paired", namespace="/")
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/api/pair/clients")
def api_pair_clients():
    me = request.environ["atlas.client"]["id"]
    return jsonify([dict(c, this=(c["id"] == me)) for c in pairing.list_clients()])


@app.route("/api/pair/clients/<client_id>", methods=["DELETE"])
def api_pair_revoke(client_id):
    ok = pairing.revoke(client_id)
    if ok:
        _disconnect_sockets(client_id)
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/api/device")
def api_device():
    if request.environ.get("atlas.client") is None and pairing.required():
        # Unpaired callers (discovery probes) learn only that this is an Atlas.
        return jsonify({"app": "atlas-control", "pairing_required": True})
    info = mesh.get_device_info() if mesh else {"connected": False}
    # Stable identifier the mobile apps probe for during LAN discovery.
    # Without it, a port-5000 fingerprint sweep can't distinguish Atlas
    # from any other HTTP server that happens to 200 on /api/device.
    info["app"] = "atlas-control"
    return jsonify(info)

@app.route("/api/health")
def api_health():
    """Liveness for the updater, nginx's status page and reconnecting clients.

    Everyone gets {ok, version}. The updater's health check connects directly
    on loopback (not through nginx) and additionally gets the facts it needs
    to decide whether a new release is healthy.
    """
    import atlas_version
    info = atlas_version.info()
    body = {"ok": True, "app": "atlas-control", "version": info["version"]}
    if request.environ.get("atlas.direct_loopback"):
        try:
            conn = db.get_db()
            conn.execute("SELECT 1 FROM app_settings LIMIT 1").fetchall()
            db_ok = True
        except Exception as exc:
            logger.warning("health: database check failed: %s", exc)
            db_ok = False
        body.update({
            "seq": info.get("seq", 0),
            "release_path": atlas_paths.CODE_DIR,
            "pid": os.getpid(),
            "db_ok": db_ok,
            "schema_version": db.schema_version(),
            "compat_floor": int(db.get_meta("compat_floor", "0") or 0),
        })
        body["ok"] = db_ok
    return jsonify(body), (200 if body["ok"] else 503)


# ------------------------------------------------------------------ API: stats
@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())

# ------------------------------------------------------------------ API: nodes
@app.route("/api/nodes")
def api_nodes():
    return jsonify(db.get_all_nodes())

@app.route("/api/nodes/<node_id>")
def api_node(node_id):
    node = db.get_node(node_id)
    return jsonify(node) if node else ("", 404)

@app.route("/api/nodes/<node_id>/dm-diagnostics")
def api_node_dm_diagnostics(node_id):
    if not mesh:
        return jsonify({"error": "Mesh manager unavailable"}), 503
    diagnostics = mesh.get_dm_diagnostics(node_id)
    return jsonify(diagnostics) if diagnostics else ("", 404)

@app.route("/api/nodes/<node_id>/request-nodeinfo", methods=["POST"])
def api_node_request_nodeinfo(node_id):
    """Trigger a NodeInfo exchange with a specific node (or broadcast if node_id='all').

    This resolves PKI DM failures where the Atlas firmware doesn't have the
    sender's public key, causing MAX_RETRANSMIT on the remote node.
    """
    if not mesh:
        return jsonify({"error": "Mesh manager unavailable"}), 503
    target = None if node_id.lower() in ("all", "broadcast") else node_id
    result = mesh.request_nodeinfo(target)
    return jsonify(result)

@app.route("/api/mesh/scan", methods=["POST"])
def api_mesh_scan():
    """Broadcast NodeInfo + Position requests to the full mesh.

    Every node up to the hop limit responds with its info.  Multi-hop nodes
    have their responses relayed back by intermediate nodes.  Results arrive
    incrementally via node_update / position_update socket events.
    """
    if not mesh:
        return jsonify({"error": "Mesh manager unavailable"}), 503
    result = mesh.scan_mesh()
    return jsonify(result)

@app.route("/api/debug/interface-nodes")
def api_debug_interface_nodes():
    """Return the live node DB from the Meshtastic interface (not SQLite).

    Shows public-key presence and is_unmessagable so we can diagnose
    PKI key-exchange failures and MAX_RETRANSMIT issues.
    """
    if not mesh:
        return jsonify({"error": "Mesh manager unavailable"}), 503
    return jsonify(mesh.get_interface_nodes())

@app.route("/api/nodes/<node_id>/alias", methods=["PUT"])
def api_node_alias(node_id):
    data = request.json or {}
    alias = data.get("alias", "").strip() or None
    if alias and len(alias) > 100:
        return jsonify({"error": "alias too long (max 100 characters)"}), 400
    db.update_node_alias(node_id, alias)
    # Broadcast so NodesPage and MessagingPanel both pick up the new alias without
    # needing a full page refresh.  Sending alias=None clears the alias everywhere.
    socketio.emit("node_update", {"node_id": node_id, "alias": alias}, namespace="/")
    return jsonify({"ok": True})

# ------------------------------------------------------------------ API: channels
@app.route("/api/channels")
def api_channels():
    return jsonify(mesh.get_channels() if mesh else db.get_channels())

@app.route("/api/channels", methods=["POST"])
def api_channel_create():
    data = request.json or {}
    num = data.get("channel_num")
    name = (data.get("name") or "").strip()
    psk = data.get("psk")
    if num is None or not (0 <= int(num) <= 7):
        return jsonify({"error": "channel_num (0-7) required"}), 400
    try:
        if not mesh:
            raise RuntimeError("Meshtastic device is not available")
        mesh.set_channel(int(num), name, psk_text=psk)
        return jsonify({"ok": True, "channels": mesh.get_channels()})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"channel create failed: {e}")
        return jsonify({"error": str(e)}), 502

@app.route("/api/channels/<int:channel_num>", methods=["PUT"])
def api_channel_update(channel_num):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    psk = data.get("psk")
    try:
        if not mesh:
            raise RuntimeError("Meshtastic device is not available")
        mesh.set_channel(channel_num, name, psk_text=psk)
        return jsonify({"ok": True, "channels": mesh.get_channels()})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"channel update failed: {e}")
        return jsonify({"error": str(e)}), 502

@app.route("/api/channels/<int:channel_num>", methods=["DELETE"])
def api_channel_delete(channel_num):
    try:
        if not mesh:
            raise RuntimeError("Meshtastic device is not available")
        ok = mesh.delete_channel(channel_num)
        return jsonify({"ok": ok, "channels": mesh.get_channels()})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"channel delete failed: {e}")
        return jsonify({"error": str(e)}), 502

@app.route("/api/channels/<int:channel_num>/share")
def api_channel_share(channel_num):
    try:
        if not mesh:
            raise RuntimeError("Meshtastic device is not available")
        share_url = mesh.get_channel_share_url(channel_num, add_only=True)
        qr_base64 = None
        try:
            import io
            import base64 as _b64
            import qrcode

            img = qrcode.make(share_url)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qr_base64 = _b64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as qr_err:
            logger.warning(f"channel share QR generation failed: {qr_err}")

        return jsonify({
            "channel_num": channel_num,
            "share_url": share_url,
            "qr_base64": qr_base64,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"channel share failed: {e}")
        return jsonify({"error": str(e)}), 502

# ------------------------------------------------------------------ API: messages
@app.route("/api/messages")
def api_messages():
    limit = min(max(1, request.args.get("limit", 200, type=int)), 2000)
    channel = request.args.get("channel", None, type=int)
    return jsonify(db.get_messages(limit, channel))

@app.route("/api/messages/send", methods=["POST"])
def api_send_message():
    data = request.json
    text = data.get("text", "")
    dest = data.get("destination")
    ch = data.get("channel")
    if not text:
        return jsonify({"error": "No text"}), 400
    result = mesh.send_message(text, dest, ch) if mesh else {"ok": False, "error": "Mesh manager unavailable"}
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Message send failed")}), 502
    return jsonify({"sent": True, "transport": result.get("transport"), "channel": result.get("channel")})

# ------------------------------------------------------------------ API: telemetry
@app.route("/api/telemetry")
def api_telemetry():
    node_id = request.args.get("node_id")
    hours = min(max(1, request.args.get("hours", 24, type=int)), 8760)  # cap at 1 year
    return jsonify(db.get_telemetry(node_id, hours))

# ------------------------------------------------------------------ API: positions
@app.route("/api/positions")
def api_positions():
    node_id = request.args.get("node_id")
    return jsonify(db.get_positions(node_id))

# ------------------------------------------------------------------ API: topology
@app.route("/api/topology")
def api_topology():
    return jsonify({
        "nodes": db.get_all_nodes(),
        "links": db.get_topology()
    })

# ------------------------------------------------------------------ API: alerts
@app.route("/api/alerts")
def api_alerts():
    unacked = request.args.get("unacked", "false").lower() == "true"
    return jsonify(db.get_alerts(unacked_only=unacked))

@app.route("/api/alerts/<int:alert_id>/ack", methods=["POST"])
def api_ack_alert(alert_id):
    db.ack_alert(alert_id)
    return jsonify({"ok": True})

@app.route("/api/alerts/ack-all", methods=["POST"])
def api_ack_all_alerts():
    db.ack_all_alerts()
    return jsonify({"ok": True})

# ------------------------------------------------------------------ API: AI
OLLAMA_BASE = "http://localhost:11434"

@app.route("/api/ai/status")
def api_ai_status():
    import urllib.request as urlreq
    running = False
    models = []
    loaded_models = []
    manager_status = ai_manager.startup_status() if ai_manager else {
        "phase": "not_initialized",
        "ready": False,
        "warming_up": False,
        "last_error": "AI manager not initialized",
        "started_at": None,
        "finished_at": None,
    }
    try:
        resp = urlreq.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3)
        data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        running = True
    except Exception:
        pass
    if running:
        try:
            resp = urlreq.urlopen(f"{OLLAMA_BASE}/api/ps", timeout=3)
            ps_data = json.loads(resp.read())
            loaded_models = [m["name"] for m in ps_data.get("models", [])]
        except Exception:
            pass
    doc_count = db.ai_get_document_count()
    chat_count = len(db.ai_get_chats())
    settings = db.ai_get_settings()
    ai_ready = bool(running and manager_status.get("ready"))
    # Geocoder health is surfaced here because its absence is otherwise silent:
    # Ray keeps answering, just without ever knowing where the user is.
    try:
        geo = geocoder_status()
    except Exception as e:
        geo = {"ok": False, "error": str(e)}
    import model_profiles
    active = model_profiles.resolve(settings)
    return jsonify({
        "running": running,
        "ai_ready": ai_ready,
        "active_model": {"id": active["id"], "label": active["label"], "tag": active["tag"],
                         "quant": active["quant"], "num_ctx": active["options"]["num_ctx"]},
        "switch": ai_manager.switch_state() if ai_manager else None,
        "notice": ai_manager.notice() if ai_manager else "",
        "models": models,
        "loaded_models": loaded_models,
        "doc_count": doc_count,
        "chat_count": chat_count,
        "settings": settings,
        "geocoder": geo,
        "startup_phase": manager_status.get("phase"),
        "warming_up": bool(manager_status.get("warming_up")),
        "last_error": manager_status.get("last_error") or "",
        "started_at": manager_status.get("started_at"),
        "finished_at": manager_status.get("finished_at"),
    })


def _require_ai_ready():
    if not ai_manager:
        return jsonify({"error": "AI manager not initialized", "reason": "not_initialized", "retryable": True}), 503
    if ai_manager.is_ready():
        return None
    status = ai_manager.startup_status()
    reason = "warming_up" if status.get("warming_up") else "not_ready"
    message = "Ray is still starting up. Please wait a moment and try again."
    switch = ai_manager.switch_state()
    if status.get("phase") == "switching" and switch:
        return jsonify({"error": f"Ray is switching to {switch['label']} — it will answer again in a minute.",
                        "reason": "switching", "retryable": True, "startup_phase": "switching"}), 503
    if status.get("last_error"):
        reason = "startup_failed"
        message = f"Ray startup failed: {status['last_error']}"
    return jsonify({
        "error": message,
        "reason": reason,
        "retryable": reason != "startup_failed",
        "startup_phase": status.get("phase"),
    }), 503

@app.route("/api/ai/chats", methods=["GET"])
def api_ai_chats_list():
    return jsonify(db.ai_get_chats())

@app.route("/api/ai/chats", methods=["POST"])
def api_ai_chats_create():
    data = request.json or {}
    model = data.get("model")
    chat = db.ai_create_chat(model=model)
    return jsonify(chat)

@app.route("/api/ai/chats/<int:chat_id>", methods=["GET"])
def api_ai_chat_get(chat_id):
    chat = db.ai_get_chat(chat_id)
    if not chat:
        return jsonify({"error": "not found"}), 404
    messages = db.ai_get_messages(chat_id)
    # Drop the per-message /explain trace from the list payload — it can be
    # several KB each and the client fetches explanations on demand via the
    # /explain command, never from this list.
    for m in messages:
        m.pop("explain", None)
    chat["messages"] = messages
    return jsonify(chat)

@app.route("/api/ai/chats/<int:chat_id>", methods=["DELETE"])
def api_ai_chat_delete(chat_id):
    db.ai_delete_chat(chat_id)
    return jsonify({"ok": True})

@app.route("/api/ai/chats/<int:chat_id>/message", methods=["POST"])
def api_ai_chat_message(chat_id):
    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    not_ready = _require_ai_ready()
    if not_ready:
        return not_ready
    try:
        result = ai_manager.chat(chat_id, text)
        return jsonify(result)
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ai/chats/<int:chat_id>/stream", methods=["POST"])
def api_ai_chat_stream(chat_id):
    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    # Wait for startup warmup before checking readiness — prevents 503 on first
    # message after boot while the model is still loading into GPU memory.
    if ai_manager and not ai_manager.is_ready():
        ai_manager._wait_for_startup_warmup(timeout=45)
    not_ready = _require_ai_ready()
    if not_ready:
        return not_ready
    def generate():
        try:
            for chunk in ai_manager.chat_stream(chat_id, text):
                if isinstance(chunk, dict):
                    yield f"data: {json.dumps(chunk)}\n\n"
                elif isinstance(chunk, str) and chunk.startswith("\x00REPLACE\x00"):
                    # Full replacement after calc-tag processing
                    replaced = chunk[len("\x00REPLACE\x00"):]
                    yield f"data: {json.dumps({'replace': replaced})}\n\n"
                else:
                    yield f"data: {json.dumps({'token': chunk})}\n\n"
        except Exception as e:
            logger.error(f"AI stream error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.route("/api/ai/documents", methods=["GET"])
def api_ai_documents_list():
    return jsonify(db.ai_get_documents())

@app.route("/api/ai/documents", methods=["POST"])
def api_ai_documents_create():
    data = request.json or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    tags = (data.get("tags") or "").strip()
    if not title or not content:
        return jsonify({"error": "title and content required"}), 400
    doc_id = db.ai_add_document(title, content, tags=tags, is_seed=False)
    # Embed asynchronously
    def _embed():
        try:
            emb = ai_manager.get_embed(content)
            import json as _json
            db.ai_update_document_embedding(doc_id, _json.dumps(emb))
        except Exception as e:
            logger.warning(f"Async embed failed for doc {doc_id}: {e}")
    if ai_manager:
        threading.Thread(target=_embed, daemon=True).start()
    return jsonify({"ok": True, "id": doc_id})

@app.route("/api/ai/documents/<int:doc_id>", methods=["GET"])
def api_ai_documents_get(doc_id):
    doc = db.ai_get_document(doc_id)
    if not doc:
        return jsonify({"error": "not found"}), 404
    return jsonify(doc)

@app.route("/api/ai/documents/<int:doc_id>", methods=["DELETE"])
def api_ai_documents_delete(doc_id):
    db.ai_delete_document(doc_id)
    return jsonify({"ok": True})

@app.route("/api/ai/settings", methods=["GET"])
def api_ai_settings_get():
    return jsonify(db.ai_get_settings())

# What PUT /api/ai/settings may change. The model, its expert overrides and
# the embedding model change through the switch job (POST /api/ai/model),
# which loads and checks them before saving.
_AI_BEHAVIOUR_SETTINGS = {
    "system_prompt": lambda v: str(v)[:20000],
    "warmup_on_start": lambda v: "true" if str(v).lower() == "true" else "false",
    "keep_alive_hours": lambda v: str(min(24, max(1, int(float(v))))),
    "rag_enabled": lambda v: "true" if str(v).lower() == "true" else "false",
    "rag_top_k": lambda v: str(min(10, max(1, int(float(v))))),
    "inject_mesh_context": lambda v: "true" if str(v).lower() == "true" else "false",
}


@app.route("/api/ai/settings", methods=["PUT"])
def api_ai_settings_put():
    data = request.get_json(silent=True) or {}
    clean, ignored = {}, []
    for key, value in data.items():
        fix = _AI_BEHAVIOUR_SETTINGS.get(key)
        if fix is None:
            ignored.append(key)
            continue
        try:
            clean[key] = fix(value)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"Invalid value for {key}."}), 400
    if clean:
        db.ai_set_settings(clean)
    job = None
    wanted = data.get("model")
    if wanted and ai_manager and wanted != db.ai_get_settings().get("model"):
        try:                        # older pages send the model with the rest
            job = ai_manager.switch_model(str(wanted))
        except ai_manager_mod.ModelSwitchError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409 if exc.busy else 400
    ignored = [k for k in ignored if k != "model"]
    return jsonify({"ok": True, "settings": db.ai_get_settings(), "warming_up": bool(job),
                    "switch": job, "ignored": ignored})


def _model_entries():
    """The models an owner can choose, with what each needs and how it runs here."""
    import model_profiles
    settings = db.ai_get_settings()
    active = model_profiles.resolve(settings)
    hw = model_profiles.hardware()
    installed = set()
    try:
        import urllib.request as urlreq
        with urlreq.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as r:
            installed = {m["name"] for m in json.loads(r.read()).get("models", [])}
    except Exception:
        pass
    ollama = ai_manager.ollama_version() if ai_manager else None
    entries = []
    for model in model_profiles.models():
        prof = model_profiles.resolve(settings, model["id"], hw)
        try:
            measured = json.loads(settings.get("measured:" + model["id"]) or "{}")
        except ValueError:
            measured = {}
        if ollama and model_profiles.version_tuple(ollama) < model_profiles.version_tuple(model.get("min_ollama", "0")):
            status = "needs_engine"
        elif prof["tag"] in installed or any(t.startswith(prof["tag"] + ":") for t in installed):
            status = "installed"
        else:
            status = "download"
        entries.append({
            "id": model["id"], "label": model["label"], "blurb": model.get("blurb", ""),
            "tag": prof["tag"], "quant": prof["quant"], "size_mb": prof["size_mb"],
            "status": status, "active": model["id"] == active["id"],
            "options": prof["options"], "optimized": prof["optimized"], "overrides": prof["overrides"],
            "clamped": {k: list(v) for k, v in prof["clamped"].items()},
            "memory_mb": prof["memory_mb"], "min_ollama": model.get("min_ollama"),
            "speed_tok_s": measured.get("tok_s"),
        })
    return {"models": entries, "active": active["id"], "embed_model": settings.get("embed_model"),
            "hardware": hw, "ollama_version": ollama,
            "override_keys": sorted(model_profiles.OVERRIDE_RANGES),
            "switch": ai_manager.switch_state() if ai_manager else None,
            "notice": ai_manager.notice() if ai_manager else ""}


@app.route("/api/ai/models")
def api_ai_models():
    return jsonify(_model_entries())


@app.route("/api/ai/model", methods=["POST"])
def api_ai_model_switch():
    """Switch Ray to another model (or change its embedding model). 202 + the job."""
    if not ai_manager:
        return jsonify({"ok": False, "error": "Ray is turned off."}), 503
    data = request.get_json(silent=True) or {}
    embed = data.get("embed_model")
    try:
        job = ai_manager.switch_model(str(data.get("model") or db.ai_get_settings().get("model") or ""),
                                      embed_model=str(embed).strip() if embed else None)
    except ai_manager_mod.ModelSwitchError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409 if exc.busy else 400
    return jsonify({"ok": True, "switch": job}), 202


@app.route("/api/ai/model/cancel", methods=["POST"])
def api_ai_model_cancel():
    return jsonify({"ok": bool(ai_manager and ai_manager.cancel_switch())})


@app.route("/api/ai/models/<path:model_id>/overrides", methods=["PUT", "DELETE"])
def api_ai_model_overrides(model_id):
    """Expert overrides for one model. Saving them re-applies the model when it
    is the one in use (the switch job loads and checks it, then saves)."""
    import model_profiles
    model = model_profiles.find(model_id)
    if model is None:
        return jsonify({"ok": False, "error": "Unknown model."}), 404
    raw = {} if request.method == "DELETE" else ((request.get_json(silent=True) or {}).get("overrides") or {})
    if not isinstance(raw, dict):
        return jsonify({"ok": False, "error": "overrides must be an object"}), 400
    kept, clamped = model_profiles.clean_overrides(model, raw)
    settings = db.ai_get_settings()
    job = None
    if ai_manager and model_profiles.resolve(settings)["id"] == model["id"]:
        try:
            job = ai_manager.switch_model(model["id"], overrides=kept)
        except ai_manager_mod.ModelSwitchError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409 if exc.busy else 400
    else:
        db.ai_set_setting(model_profiles.OVERRIDE_PREFIX + model["id"], json.dumps(kept))
    return jsonify({"ok": True, "overrides": kept, "clamped": {k: list(v) for k, v in clamped.items()},
                    "switch": job}), (202 if job else 200)

@app.route("/api/ai/warmup", methods=["POST"])
def api_ai_warmup():
    if not ai_manager:
        return jsonify({"error": "AI manager not initialized"}), 503
    threading.Thread(target=ai_manager._warmup, daemon=True).start()
    return jsonify({"ok": True, "message": "Warmup triggered in background"})

# Category metadata for the knowledge-map endpoint and UI visualisation.
_KM_CATEGORIES = {
    # Specialized clusters are checked first so a substring like "radio" inside
    # "radioactive" or "food" inside a hunting doc cannot mis-bucket them.
    "emp":         {"label": "EMP / Solar Storm",   "color": "#fbbf24",
                    "tags": ["faraday", "electromagnetic pulse", "coronal mass",
                             "solar storm", "geomagnetic", "carrington"]},
    "nuclear":     {"label": "Nuclear / CBRN",      "color": "#fde047",
                    "tags": ["nuclear", "radiation", "fallout", "cbrn", "geiger",
                             "roentgen", "decontamination", "radiological"]},
    # air_quality must precede weather (its docs carry a "wildfire smoke" tag);
    # herbal must precede bushcraft AND medical (tar-medicine doc carries
    # "birch tar", and "medicinal" contains the medical tag "medic");
    # bushcraft must precede knots/water_fire (its docs carry "cordage"/"waterproofing").
    "air_quality": {"label": "Air Quality & Smoke", "color": "#14b8a6",
                    "tags": ["air quality", "wildfire smoke", "aqi", "respirator",
                             "pm2.5"]},
    "herbal":      {"label": "Herbal & Natural Medicine", "color": "#4ade80",
                    "tags": ["herbal", "medicinal", "tincture", "salve",
                             "natural remedies", "wild medicine"]},
    "bushcraft":   {"label": "Bushcraft & Primitive Tech", "color": "#b45309",
                    "tags": ["bushcraft", "primitive", "knapping", "pitch glue",
                             "natural fiber", "selfbow", "hide tanning"]},
    "weather":     {"label": "Weather & Disasters", "color": "#0ea5e9",
                    "tags": ["weather", "tornado", "hurricane", "earthquake",
                             "wildfire", "blizzard", "barometric", "natural disaster"]},
    "cold_heat":   {"label": "Cold & Heat Injury",  "color": "#38bdf8",
                    "tags": ["hypothermia", "frostbite", "heat stroke", "heat exhaustion",
                             "trench foot", "rewarming"]},
    "signaling":   {"label": "Signaling & Rescue",  "color": "#fb7185",
                    "tags": ["signal mirror", "ground-to-air", "heliograph", "morse",
                             "distress", "rescue"]},
    "knots":       {"label": "Knots & Cordage",     "color": "#d97706",
                    "tags": ["knot", "cordage", "lashing", "bowline", "prusik"]},
    "hunting":     {"label": "Hunting & Trapping",  "color": "#84cc16",
                    "tags": ["trapping", "snare", "deadfall", "trotline", "gill net"]},
    "psychology":  {"label": "Survival Psychology", "color": "#c084fc",
                    "tags": ["psychology", "survival mindset", "morale", "resilience",
                             "will to survive"]},
    "water_fire":  {"label": "Water & Fire",       "color": "#06b6d4",
                    "tags": ["water", "purif", "fire", "tinder"]},
    "shelter":     {"label": "Shelter",             "color": "#a16207",
                    "tags": ["shelter", "bivouac"]},
    "food":        {"label": "Food & Foraging",     "color": "#22c55e",
                    "tags": ["food", "forag", "edible", "calor", "garden", "livestock"]},
    "medical":     {"label": "Medical / Trauma",    "color": "#ef4444",
                    "tags": ["medic", "trauma", "hemorrh", "wound", "first aid"]},
    "navigation":  {"label": "Navigation",          "color": "#3b82f6",
                    "tags": ["navigat", "compass", "land nav"]},
    "comms":       {"label": "Communications",      "color": "#a855f7",
                    "tags": ["mesh", "meshtastic", "radio", "amateur"]},
    "power":       {"label": "Power",               "color": "#eab308",
                    "tags": ["power", "batter", "solar"]},
    "security":    {"label": "Security / OPSEC",    "color": "#f97316",
                    "tags": ["security", "opsec"]},
    "ballistics":  {"label": "Ballistics",          "color": "#dc2626",
                    "tags": ["ballistic", "moa", "mil", "drop", "wind"]},
    "firearms":    {"label": "Firearms",            "color": "#9f1239",
                    "tags": ["firearm", "rifle", "pistol", "caliber"]},
    "grid_down":   {"label": "Grid-Down / Collapse","color": "#6b7280",
                    "tags": ["grid", "collapse", "barter", "sustain"]},
    "vehicles":    {"label": "Vehicles & Fuel",     "color": "#475569",
                    "tags": ["vehicle", "fuel", "maintenance"]},
    "wildlife":    {"label": "Wildlife",            "color": "#16a34a",
                    "tags": ["wildlife", "snake", "bear", "spider", "venomous",
                             "rabies", "alligator"]},
    "trees":       {"label": "Native Trees",        "color": "#15803d",
                    "tags": ["tree", "native", "species"]},
    "atlas_app":   {"label": "Atlas App",           "color": "#6366f1",
                    "tags": ["atlas", "app", "ray", "dashboard"]},
}

def _km_category_for(tags_str: str, title: str = ""):
    title_lower = (title or "").lower()
    # Atlas Control app docs are always atlas_app regardless of shared tag keywords
    # like "navigation", "mesh", "battery", "ballistics" that overlap other categories.
    if ("atlas control" in title_lower or "atlas control" in (tags_str or "").lower()
            or title_lower.startswith("ray — ") or title_lower.startswith("ai/ml:")):
        meta = _KM_CATEGORIES["atlas_app"]
        return "atlas_app", meta["color"], meta["label"]
    tags_lower = (tags_str or "").lower()
    for slug, meta in _KM_CATEGORIES.items():
        if any(kw in tags_lower for kw in meta["tags"]):
            return slug, meta["color"], meta["label"]
    return "other", "#94a3b8", "Other"

# Curated cross-references: many knowledge docs end with a "RELATED: <Title>,
# <Title>; …" line naming the other docs they relate to. These are
# human-authored and far more accurate than embedding cosine alone, but the map
# historically ignored them. The helpers below parse those references and
# resolve each to a document id so they can be drawn as authoritative edges.
_KM_RELATED_RE = re.compile(r'RELATED:\s*(.+?)(?:\n\n|$)', re.S)

def _km_norm(s: str) -> str:
    """Normalise a title or reference for comparison: drop parentheticals,
    'see the'/'the'/'doc(s)' filler, and punctuation; collapse whitespace."""
    s = re.sub(r'\(.*?\)', ' ', s or '')
    s = s.replace("see the ", "").replace("see ", "").replace("the ", "")
    s = re.sub(r'\b(doc|docs)\b', '', s.lower())
    s = re.sub(r'[^a-z0-9& ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def _km_curated_edges(docs):
    """Return a set of frozenset({id1,id2}) curated relation pairs parsed from
    each doc's 'RELATED:' cross-references.

    A reference is matched against each doc's MAIN title (the part before the
    ':' subtitle). A reference that ties across several DIFFERENT titles is
    genuinely ambiguous (e.g. a bare 'navigation') and is skipped rather than
    guessed; a tie among docs that share the SAME main title (a two-part series
    like the 'Field Trauma:' docs) links to all of them.
    """
    titles = [(d["id"], _km_norm((d.get("title") or "").split(":")[0])) for d in docs]

    def resolve(ref):
        r = _km_norm(ref)
        if len(r) < 5:
            return []
        cands = []
        for did, mp in titles:
            if mp and (r == mp or r in mp or mp in r):
                rw = {w for w in r.split() if len(w) > 2}
                mw = {w for w in mp.split() if len(w) > 2}
                cands.append((len(rw & mw), did, mp))
        if not cands:                       # fall back to significant-word overlap
            rw = {w for w in r.split() if len(w) > 3}
            if len(rw) < 2:
                return []
            for did, mp in titles:
                n = len(rw & {w for w in mp.split() if len(w) > 3})
                if n >= 2 and n >= 0.6 * len(rw):
                    cands.append((n, did, mp))
            if not cands:
                return []
        top = max(c[0] for c in cands)
        winners = [c for c in cands if c[0] == top]
        if len({c[2] for c in winners}) == 1:   # same titled series → link all
            return [c[1] for c in winners]
        return []                                # cross-topic ambiguity → skip

    edges = set()
    for d in docs:
        src = d["id"]
        for mm in _KM_RELATED_RE.finditer(d.get("content") or ""):
            for ref in re.split(r'[;,]|\band\b', mm.group(1).strip()):
                for tgt in resolve(ref.strip().rstrip(".").strip()):
                    if tgt != src:
                        edges.add(frozenset((src, tgt)))
    return edges


def _knowledge_map_payload(index):
    """Nodes and high-cosine edges for the knowledge-map visualisation."""
    import numpy as np
    docs = db.ai_get_documents()
    nodes = []
    for doc in docs:
        slug, color, label = _km_category_for(doc.get("tags") or "", doc.get("title") or "")
        nodes.append({
            "id":       doc["id"],
            "title":    doc["title"],
            "category": slug,
            "cat_label": label,
            "color":    color,
        })

    # Cosine similarity between two docs = their best-matching passage pair (max
    # cosine), mirroring RAG's "a doc is scored by its single best chunk" rule —
    # one matrix product over every stored passage (vector_store).
    ids, doc_sims = index.doc_similarity()
    peers = {id_: [] for id_ in ids}
    sim = {}
    for a, b in zip(*(x.tolist() for x in np.nonzero(np.triu(doc_sims >= 0.55, k=1)))):
        best, id1, id2 = float(doc_sims[a, b]), ids[a], ids[b]
        sim[frozenset((id1, id2))] = best
        peers[id1].append((best, id2))
        peers[id2].append((best, id1))

    # Symmetric k-NN: keep an edge if it is in the top-6 of EITHER endpoint, so
    # every node shows its strongest links. (The old code only emitted a pair if
    # the lower-id node ranked it, so a doc whose single best relation pointed at
    # a lower id could lose it — an arbitrary, id-order-dependent omission.)
    kept = set()
    for id1 in ids:
        peers[id1].sort(reverse=True)
        for s, id2 in peers[id1][:6]:
            kept.add(frozenset((id1, id2)))

    # Authoritative curated relations from "RELATED:" cross-references. Always
    # drawn (independent of the cosine cap) and flagged so the UI can render them
    # distinctly; weight is floored so they read as strong on the slate→amber ramp.
    curated = _km_curated_edges(docs)

    edges = []
    for pair in kept | curated:
        id1, id2 = sorted(pair)
        is_curated = pair in curated
        cos = sim.get(pair, 0.0)
        weight = round(max(cos, 0.80), 3) if is_curated else round(cos, 3)
        edges.append({"from": id1, "to": id2, "weight": weight,
                      "curated": is_curated})

    return {"nodes": nodes, "edges": edges,
            "categories": {k: {"label": v["label"], "color": v["color"]}
                           for k, v in _KM_CATEGORIES.items()}}


_knowledge_map_cache = {"index": None, "payload": None}


@app.route("/api/ai/knowledge-map")
def api_ai_knowledge_map():
    """Recomputed only when the knowledge base changes, and off the event loop."""
    index = vector_store.current()
    cache = _knowledge_map_cache
    if cache["index"] is not index:
        with perf_metrics.timer("knowledge_map.build"):
            cache["payload"] = atlas_blocking.call(_knowledge_map_payload, index)
        cache["index"] = index
    return jsonify(cache["payload"])

# ------------------------------------------------------------------ API: System stats
@app.route("/api/system/stats")
def api_system_stats():
    with perf_metrics.timer("stats.collect"):
        return jsonify(atlas_blocking.call(system_stats.get_stats))


@app.route("/api/diagnostics/performance")
def api_diagnostics_performance():
    """Local timings (Ray stages, stats, knowledge map) and memory. Paired
    devices only, like every /api route; nothing leaves the device."""
    return jsonify(perf_metrics.snapshot())

# ------------------------------------------------------------------ API: GPS
@app.route("/api/gps")
def api_gps_status():
    return jsonify(gps_manager.get_status() if gps_manager else {
        "connected": False, "port": None, "baud": None, "fix": None
    })

# ------------------------------------------------------------------ API: GPS sharing
@app.route("/api/gps/share", methods=["GET", "PUT"])
def api_gps_share():
    if request.method == "GET":
        s = db.get_app_settings()
        nodes_raw = s.get("gps_share_nodes", "")
        channels_raw = s.get("gps_share_channels", "")
        return jsonify({
            "mode":     "selected" if s.get("gps_share_mode", "off") == "nodes" else s.get("gps_share_mode", "off"),
            "nodes":    [n for n in nodes_raw.split(",") if n],
            "channels": [int(ch) for ch in channels_raw.split(",") if ch.strip().isdigit()],
            "interval": int(s.get("gps_share_interval", "30")),
        })

    data = request.json or {}
    mode = data.get("mode", "off")
    if mode not in ("off", "all", "selected", "nodes"):
        return jsonify({"error": "mode must be off, all, or selected"}), 400
    nodes_list = [n for n in data.get("nodes", []) if n]
    channels_list = []
    for raw in data.get("channels", []) or []:
        try:
            ch = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= ch <= 7 and ch not in channels_list:
            channels_list.append(ch)
    nodes_str  = ",".join(nodes_list)
    channels_str = ",".join(str(ch) for ch in channels_list)
    interval   = max(10, int(data.get("interval", 30)))
    if mode == "selected" and not nodes_list and not channels_list:
        return jsonify({"error": "Select at least one channel or node"}), 400

    db.set_app_settings({
        "gps_share_mode":     mode,
        "gps_share_nodes":    nodes_str,
        "gps_share_channels": channels_str,
        "gps_share_interval": str(interval),
    })
    if gps_manager:
        gps_manager.set_share_config(mode, nodes_str, interval, channels_str=channels_str)
    privacy_enforced = False
    privacy_warning = None
    if mode == "off" and mesh and getattr(mesh, "connected", False):
        privacy_enforced = mesh.sync_gps_privacy()
        if not privacy_enforced:
            privacy_warning = "Settings saved, but could not disable the radio's automatic position broadcasts. You may need to disable them manually in device config."
    return jsonify({
        "ok": True,
        "mode": mode,
        "nodes": nodes_list,
        "channels": channels_list,
        "interval": interval,
        "privacy_enforced": privacy_enforced,
        "warning": privacy_warning,
    })

# ── Offline address + POI index (places.db, built by build_places_index.py) ──
# Searches street addresses and points of interest (gas, food, shops, ATMs,
# hospitals, hotels, …) extracted from the local OSM .pbf state files. 100%
# offline. Absent until build_places_index.py has run.
_PLACES_DB = os.path.join(atlas_paths.STATIC_DATA_DIR, "places.db")

# "<thing> near me" → coarse category column in places.db.
# _CATEGORY_SYNONYMS / _CAT_STOPWORDS / _detect_category live in ai_manager (see
# the import at the top of this file) so this endpoint and Ray share one
# definition of what "food" or "water" means and can never drift apart.


def _fts_expr(tokens):
    """Safe FTS5 MATCH expression: phrase-quoted tokens, prefix on the last."""
    toks = [t for t in tokens if t]
    if not toks:
        return None
    return " ".join([f'"{t}"' for t in toks[:-1]] + [f'"{toks[-1]}"*'])


def _search_places(q, has_pos, near_lat, near_lon, limit=14):
    """Search the offline address/POI index. Returns geocode-result dicts."""
    if not os.path.exists(_PLACES_DB):
        return []
    import re as _re
    tokens = [t for t in _re.findall(r"[a-z0-9]+", q.lower()) if len(t) >= 2]
    if not tokens:
        return []
    name_toks = [t for t in tokens if t not in _CAT_STOPWORDS]
    out = []
    try:
        import sqlite3 as _sq
        con = _sq.connect(f"file:{_PLACES_DB}?mode=ro", uri=True)
        category = _detect_category(q.lower())
        rows = []
        if category and has_pos:
            # "<thing> near me": bounding-box prefilter, widen until we get hits.
            for win in (0.4, 1.2, 3.5):
                rows = list(con.execute(
                    "SELECT name,kind,addr,city,state,lat,lon FROM places "
                    "WHERE category=? AND lat BETWEEN ? AND ? "
                    "AND lon BETWEEN ? AND ?",
                    (category, near_lat - win, near_lat + win,
                     near_lon - win, near_lon + win)))
                if name_toks:
                    rows = [r for r in rows
                            if any(t in (r[0] or "").lower() for t in name_toks)]
                if rows:
                    break
        else:
            expr = _fts_expr(name_toks or tokens)
            if expr:
                try:
                    rows = list(con.execute(
                        "SELECT p.name,p.kind,p.addr,p.city,p.state,p.lat,p.lon "
                        "FROM places_fts f JOIN places p ON p.id=f.rowid "
                        "WHERE places_fts MATCH ? LIMIT 200", (expr,)))
                except Exception:
                    rows = []
        con.close()
    except Exception as _e:
        logger.debug(f"Places geocode: {_e}")
        return []

    seen = []
    for name, kind, addr, city, state, lat, lon in rows:
        if lat is None or lon is None:
            continue
        if any(abs(lat - s[0]) < 3e-4 and abs(lon - s[1]) < 3e-4 for s in seen):
            continue
        seen.append((lat, lon))
        pretty_kind = (kind or "").replace("_", " ")
        disp = name or addr or pretty_kind.title()
        if not disp:
            continue
        sub = []
        if name and addr:
            sub.append(addr)
        if pretty_kind and pretty_kind not in disp.lower():
            sub.append(pretty_kind)
        sub.append(city or state)
        out.append({
            "lat": str(lat), "lon": str(lon),
            "display_name": disp,
            "subtitle": " · ".join(x for x in sub if x),
            "type": "place",
        })
    if has_pos and out:
        out.sort(key=lambda r: (float(r["lat"]) - near_lat) ** 2
                 + (float(r["lon"]) - near_lon) ** 2)
    return out[:limit]


# ------------------------------------------------------------------ API: Navigation
@app.route("/api/nav/geocode")
def api_nav_geocode():
    """Offline geocoder: addresses + POIs (places.db), cities, parks/trails, mesh nodes."""
    import re

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])

    # Direct coordinate input: "lat, lon" or "lat lon"
    import re as _re
    _coord = _re.match(
        r'^([+-]?\d+\.?\d*)\s*[,\s]\s*([+-]?\d+\.?\d*)$', q)
    if _coord:
        lat_v, lon_v = float(_coord.group(1)), float(_coord.group(2))
        if -90 <= lat_v <= 90 and -180 <= lon_v <= 180:
            return jsonify([{
                "lat": str(lat_v), "lon": str(lon_v),
                "display_name": f"{lat_v:.6f}, {lon_v:.6f}",
                "type": "coordinates",
            }])

    try:
        near_lat = float(request.args["near_lat"])
        near_lon = float(request.args["near_lon"])
        _validate_coord(near_lat, near_lon)
        has_pos  = True
    except (KeyError, ValueError):
        near_lat = near_lon = 0.0
        has_pos  = False

    def _dist_m(lat1, lon1, lat2, lon2):
        R = 6_371_000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return R * 2 * math.asin(math.sqrt(max(0.0, a)))

    q_lower = q.lower()
    q_all_tokens = [t for t in re.findall(r"[a-z0-9]+", q_lower) if len(t) >= 3]
    _generic_tokens = {
        "old", "new", "trail", "trails", "trailhead", "mountain", "mount",
        "park", "national", "state", "road", "loop", "lake", "point",
        "camp", "campground", "creek", "ridge", "peak", "falls",
    }
    q_tokens = [t for t in q_all_tokens if t not in _generic_tokens]
    if not q_tokens:
        q_tokens = q_all_tokens or [q_lower]

    def _token_clause(columns):
        clauses = []
        params = []
        for token in q_tokens:
            like = f"%{token}%"
            col_parts = [f"lower({col}) LIKE ?" for col in columns]
            clauses.append("(" + " OR ".join(col_parts) + ")")
            params.extend([like] * len(columns))
        return " OR ".join(clauses), params

    def _match_score(*texts):
        haystacks = " ".join((t or "") for t in texts).lower()
        score = 0
        if q_lower == haystacks.strip():
            score += 100
        if haystacks.startswith(q_lower):
            score += 60
        elif q_lower in haystacks:
            score += 40
        for token in q_tokens:
            if re.search(rf"\b{re.escape(token)}\b", haystacks):
                score += 20
            elif token in haystacks:
                score += 8
        for token in q_all_tokens:
            if token not in q_tokens and re.search(rf"\b{re.escape(token)}\b", haystacks):
                score += 2
        return score

    node_out     = []
    city_out     = []
    park_out     = []

    # ── Source 0: local mesh nodes (always available offline) ─────────────────
    try:
        for n in db.get_all_nodes():
            if not n.get('latitude') or not n.get('longitude'):
                continue
            display = n.get('alias') or n.get('long_name') or n.get('short_name') or n.get('node_id', '')
            if q.lower() in display.lower():
                node_out.append({
                    "lat": str(n['latitude']), "lon": str(n['longitude']),
                    "display_name": f"{display} (mesh node)",
                    "type": "mesh_node",
                })
    except Exception as _e:
        logger.debug(f"Node geocode: {_e}")

    # ── Source 0.5: offline US cities DB (GeoNames, always available) ────────
    _CITIES_DB = os.path.join(atlas_paths.STATIC_DATA_DIR, "us_cities.db")
    if os.path.exists(_CITIES_DB):
        try:
            import sqlite3 as _sq
            _con = _sq.connect(f"file:{_CITIES_DB}?mode=ro", uri=True)
            _sql = ("SELECT name, state_name, lat, lon, population FROM cities "
                    "WHERE ascii_name LIKE ? OR name LIKE ? "
                    "ORDER BY population DESC LIMIT 8")
            _pattern = f"%{q}%"
            for _row in _con.execute(_sql, (_pattern, _pattern)):
                _cname, _cstate, _clat, _clon, _cpop = _row
                city_out.append({
                    "lat": str(_clat), "lon": str(_clon),
                    "display_name": f"{_cname}, {_cstate}",
                    "type": "city",
                })
            # Also handle "City State" / "City, State" / "City, ST" queries
            # e.g. "Seattle Washington" or "Seattle, WA"
            _split_q = re.split(r'\s*,\s*', q, maxsplit=1)
            if len(_split_q) == 1 and ' ' in q:
                _words = q.split()
                _split_q = [' '.join(_words[:-1]), _words[-1]]
            if len(_split_q) == 2 and _split_q[0] and _split_q[1]:
                _csql = ("SELECT name, state_name, lat, lon, population FROM cities "
                         "WHERE (ascii_name LIKE ? OR name LIKE ?) "
                         "AND (state_name LIKE ? OR state_code LIKE ?) "
                         "ORDER BY population DESC LIMIT 8")
                _cp = f"%{_split_q[0]}%"
                _sp = f"%{_split_q[1]}%"
                _seen = {(float(e["lat"]), float(e["lon"])) for e in city_out}
                for _row in _con.execute(_csql, (_cp, _cp, _sp, _sp)):
                    _cname, _cstate, _clat, _clon, _cpop = _row
                    if (_clat, _clon) not in _seen:
                        city_out.append({
                            "lat": str(_clat), "lon": str(_clon),
                            "display_name": f"{_cname}, {_cstate}",
                            "type": "city",
                        })
                        _seen.add((_clat, _clon))
            _con.close()
        except Exception as _e:
            logger.debug(f"City DB geocode: {_e}")

    # ── Source 0.6: offline trail / trailhead DB (nps_trails.db preferred) ──────
    import sqlite3 as _sq3
    _NPS_DB = os.path.join(atlas_paths.STATIC_DATA_DIR, "nps_trails.db")
    _LEGACY_DB = os.path.join(atlas_paths.STATIC_DATA_DIR, "trailheads.db")
    _trail_results = []
    if os.path.exists(_NPS_DB):
        try:
            _con = _sq3.connect(f"file:{_NPS_DB}?mode=ro", uri=True)
            _like = f"%{q}%"
            _trail_exact = q_lower
            for _row in _con.execute(
                """SELECT full_name, designation, states, lat, lon
                   FROM nps_units
                   WHERE full_name LIKE ? OR park_code LIKE ?
                   ORDER BY CASE
                       WHEN lower(full_name) = ? THEN 0
                       WHEN lower(full_name) LIKE ? THEN 1
                       ELSE 2
                   END, full_name
                   LIMIT 8""",
                (_like, _like, _trail_exact, f"{_trail_exact}%",)
            ):
                _pname, _designation, _states, _plat, _plon = _row
                if _plat is None or _plon is None:
                    continue
                park_out.append({
                    "lat": str(_plat), "lon": str(_plon),
                    "display_name": _pname,
                    "subtitle": ", ".join(x for x in [_designation, _states] if x),
                    "type": "national_park",
                })
            if len(park_out) < 8 and q_tokens:
                _where, _params = _token_clause(["full_name", "designation", "states", "park_code"])
                for _row in _con.execute(
                    f"""SELECT full_name, designation, states, lat, lon
                        FROM nps_units
                        WHERE {_where}
                        LIMIT 40""",
                    _params
                ):
                    _pname, _designation, _states, _plat, _plon = _row
                    if _plat is None or _plon is None:
                        continue
                    park_out.append({
                        "lat": str(_plat), "lon": str(_plon),
                        "display_name": _pname,
                        "subtitle": ", ".join(x for x in [_designation, _states] if x),
                        "type": "national_park",
                        "_score": _match_score(_pname, _designation, _states),
                    })
            # Named trail lines
            for _row in _con.execute(
                """SELECT name, park_name,
                          (bbox_south + bbox_north) / 2.0 AS center_lat,
                          (bbox_west + bbox_east) / 2.0 AS center_lon
                   FROM trails
                   WHERE name LIKE ? OR park_name LIKE ?
                   ORDER BY CASE
                       WHEN lower(name) = ? THEN 0
                       WHEN lower(name) LIKE ? THEN 1
                       WHEN lower(park_name) = ? THEN 2
                       ELSE 3
                   END, name
                   LIMIT 8""",
                (_like, _like, _trail_exact, f"{_trail_exact}%", _trail_exact)
            ):
                _tname, _park, _tlat, _tlon = _row
                if _tlat is None or _tlon is None:
                    continue
                _trail_results.append({
                    "lat": str(_tlat), "lon": str(_tlon),
                    "display_name": _tname or (_park or "Trail"),
                    "subtitle": _park,
                    "type": "trail",
                })
            if len(_trail_results) < 12 and q_tokens:
                _where, _params = _token_clause(["name", "park_name"])
                for _row in _con.execute(
                    f"""SELECT name, park_name,
                               (bbox_south + bbox_north) / 2.0 AS center_lat,
                               (bbox_west + bbox_east) / 2.0 AS center_lon
                        FROM trails
                        WHERE {_where}
                        LIMIT 60""",
                    _params
                ):
                    _tname, _park, _tlat, _tlon = _row
                    if _tlat is None or _tlon is None:
                        continue
                    _trail_results.append({
                        "lat": str(_tlat), "lon": str(_tlon),
                        "display_name": _tname or (_park or "Trail"),
                        "subtitle": _park,
                        "type": "trail",
                        "_score": _match_score(_tname, _park),
                    })
            # Trailheads / peaks / viewpoints
            for _row in _con.execute(
                """SELECT th.name, th.feat_type, th.lat, th.lon, u.full_name
                   FROM trailheads th
                   LEFT JOIN nps_units u ON u.park_code = th.park_code
                   WHERE th.name LIKE ?
                   ORDER BY CASE
                       WHEN lower(th.name) = ? THEN 0
                       WHEN lower(th.name) LIKE ? THEN 1
                       ELSE 2
                   END, th.name
                   LIMIT 8""",
                (_like, _trail_exact, f"{_trail_exact}%")
            ):
                _tname, _ttype, _tlat, _tlon, _park = _row
                _trail_results.append({
                    "lat": str(_tlat), "lon": str(_tlon),
                    "display_name": _tname,
                    "subtitle": _park,
                    "type": _ttype or "trailhead",
                })
            if q_tokens:
                _where, _params = _token_clause(["th.name", "th.feat_type", "u.full_name"])
                for _row in _con.execute(
                    f"""SELECT th.name, th.feat_type, th.lat, th.lon, u.full_name
                        FROM trailheads th
                        LEFT JOIN nps_units u ON u.park_code = th.park_code
                        WHERE {_where}
                        LIMIT 60""",
                    _params
                ):
                    _tname, _ttype, _tlat, _tlon, _park = _row
                    _trail_results.append({
                        "lat": str(_tlat), "lon": str(_tlon),
                        "display_name": _tname,
                        "subtitle": _park,
                        "type": _ttype or "trailhead",
                        "_score": _match_score(_tname, _ttype, _park),
                    })
            _con.close()
        except Exception as _e:
            logger.debug(f"NPS DB geocode: {_e}")
    elif os.path.exists(_LEGACY_DB):
        try:
            _con = _sq3.connect(f"file:{_LEGACY_DB}?mode=ro", uri=True)
            for _row in _con.execute(
                "SELECT name, feat_type, lat, lon FROM trailheads "
                "WHERE name LIKE ? ORDER BY rowid LIMIT 8", (f"%{q}%",)
            ):
                _tname, _ttype, _tlat, _tlon = _row
                _trail_results.append({
                    "lat": str(_tlat), "lon": str(_tlon),
                    "display_name": _tname,
                    "type": _ttype or "trailhead",
                })
            _con.close()
        except Exception as _e:
            logger.debug(f"Trail DB geocode: {_e}")
    park_out.sort(key=lambda r: (-int(r.get("_score", 0)), r.get("display_name", "")))
    _trail_results.sort(key=lambda r: (-int(r.get("_score", 0)), r.get("display_name", "")))

    # ── Source 1: offline address + POI index (street addresses + places) ──────
    place_out = _search_places(q, has_pos, near_lat, near_lon)

    merged = []
    for r in park_out[:8] + _trail_results[:12] + place_out[:14] + city_out + node_out:
        r.pop("_score", None)
        rlat, rlon = float(r["lat"]), float(r["lon"])
        if not any(_dist_m(rlat, rlon, float(s["lat"]), float(s["lon"])) < 100
                   for s in merged):
            merged.append(r)

    if has_pos:
        merged.sort(key=lambda r: _dist_m(near_lat, near_lon,
                                          float(r["lat"]), float(r["lon"])))

    return jsonify(merged[:12])


def _point_in_ring(lon, lat, ring):
    inside = False
    if not ring:
        return False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_in_geojson_polygon(lon, lat, geometry):
    if not geometry:
        return False
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "Polygon":
        polygons = [coords]
    elif gtype == "MultiPolygon":
        polygons = coords
    else:
        return False

    for poly in polygons:
        if not poly:
            continue
        outer = poly[0]
        if not _point_in_ring(lon, lat, outer):
            continue
        holes = poly[1:] if len(poly) > 1 else []
        if any(_point_in_ring(lon, lat, hole) for hole in holes):
            continue
        return True
    return False


def _trail_geometry_midpoint(geometry):
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "LineString":
        lines = [coords]
    elif gtype == "MultiLineString":
        lines = coords
    else:
        return None

    best = None
    best_len = -1
    def _seg_len(lat1, lon1, lat2, lon2):
        R = 6_371_000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return R * 2 * math.asin(math.sqrt(max(0.0, a)))
    for line in lines:
        if not line or len(line) < 2:
            continue
        seg_lengths = []
        total = 0.0
        for i in range(len(line) - 1):
            lon1, lat1 = line[i][0], line[i][1]
            lon2, lat2 = line[i + 1][0], line[i + 1][1]
            seg_len = _seg_len(lat1, lon1, lat2, lon2)
            seg_lengths.append(seg_len)
            total += seg_len
        if total <= 0:
            continue
        target = total / 2.0
        walked = 0.0
        point = line[0]
        for i, seg_len in enumerate(seg_lengths):
            if walked + seg_len >= target:
                start = line[i]
                end = line[i + 1]
                ratio = 0 if seg_len == 0 else (target - walked) / seg_len
                point = [
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                ]
                break
            walked += seg_len
        if total > best_len:
            best_len = total
            best = (point[1], point[0], total)
    return best


@app.route("/api/hiking/parks")
def api_hiking_parks():
    import sqlite3 as _sq

    db_path = _nps_trails_db_path()
    if not os.path.exists(db_path):
        return jsonify([])

    try:
        db_mtime = _hiking_db_mtime(db_path)
        with _HIKING_CACHE_LOCK:
            if _HIKING_PARKS_CACHE["db_mtime"] == db_mtime and _HIKING_PARKS_CACHE["data"] is not None:
                return jsonify(_HIKING_PARKS_CACHE["data"])

        con = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(
            """SELECT u.park_code, u.full_name, u.designation, u.states, u.lat, u.lon
               FROM nps_units u
               WHERE u.designation = 'National Parks'
               ORDER BY u.full_name"""
        ).fetchall()
        con.close()
        parks = [
            {
                "park_code": code,
                "name": name,
                "designation": designation,
                "states": states,
                "lat": lat,
                "lon": lon,
            }
            for code, name, designation, states, lat, lon in rows
        ]
        with _HIKING_CACHE_LOCK:
            _HIKING_PARKS_CACHE["db_mtime"] = db_mtime
            _HIKING_PARKS_CACHE["data"] = parks
        return jsonify(parks)
    except Exception as e:
        logger.error(f"hiking parks query failed: {e}")
        return jsonify({"error": str(e)}), 502


@app.route("/api/hiking/parks/<park_code>/trails")
def api_hiking_park_trails(park_code):
    import sqlite3 as _sq

    db_path = _nps_trails_db_path()
    if not os.path.exists(db_path):
        return jsonify([])

    park_code = (park_code or "").strip().upper()
    if not park_code:
        return jsonify({"error": "park_code required"}), 400

    try:
        db_mtime = _hiking_db_mtime(db_path)
        cache_key = (db_mtime, park_code)
        with _HIKING_CACHE_LOCK:
            cached = _HIKING_TRAILS_CACHE.get(cache_key)
            if cached is not None:
                return jsonify(cached)

        con = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
        park = con.execute(
            """SELECT pb.park_code, u.full_name, pb.geometry_json,
                      pb.bbox_west, pb.bbox_south, pb.bbox_east, pb.bbox_north
               FROM park_boundaries pb
               JOIN nps_units u ON u.park_code = pb.park_code
               WHERE pb.park_code = ? AND u.designation = 'National Parks'
               LIMIT 1""",
            (park_code,)
        ).fetchone()
        if not park:
            con.close()
            return jsonify([])

        _, park_name, geometry_json, west, south, east, north = park
        park_geom = json.loads(geometry_json) if geometry_json else None
        # Prefer explicit park_code matches. Falling back to bbox + polygon checks
        # only when needed avoids scanning and decoding large geometry payloads
        # on slower offline devices.
        rows = con.execute(
            """SELECT id, name, park_code, park_name,
                      bbox_west, bbox_south, bbox_east, bbox_north, length_m
               FROM trails
               WHERE park_code = ?
                 AND name IS NOT NULL AND trim(name) <> ''
               ORDER BY name
               LIMIT 12000""",
            (park_code,)
        ).fetchall()

        if not rows:
            rows = con.execute(
                """SELECT id, name, park_code, park_name,
                          bbox_west, bbox_south, bbox_east, bbox_north, length_m
               FROM trails
               WHERE name IS NOT NULL AND trim(name) <> ''
                 AND bbox_east >= ? AND bbox_west <= ?
                 AND bbox_north >= ? AND bbox_south <= ?
               ORDER BY name
               LIMIT 12000""",
                (west, east, south, north)
            ).fetchall()
        con.close()

        trails_by_name = {}
        for tid, name, trail_park_code, trail_park_name, tw, ts, te, tn, length_m in rows:
            center_lon = (tw + te) / 2.0
            center_lat = (ts + tn) / 2.0
            in_park = (
                trail_park_code == park_code
                or (park_geom is not None and _point_in_geojson_polygon(center_lon, center_lat, park_geom))
            )
            if not in_park:
                continue
            clean_name = name.strip()
            key = clean_name.lower()
            rep_len = float(length_m or 0.0)
            existing = trails_by_name.get(key)
            if existing is None:
                trails_by_name[key] = {
                    "id": tid,
                    "name": clean_name,
                    "park_code": park_code,
                    "park_name": park_name,
                    "lat": center_lat,
                    "lon": center_lon,
                    "segments": 1,
                    "_best_len": rep_len,
                }
            else:
                existing["segments"] += 1
                if rep_len > existing.get("_best_len", 0.0):
                    existing["id"] = tid
                    existing["lat"] = center_lat
                    existing["lon"] = center_lon
                    existing["_best_len"] = rep_len

        trails = []
        for trail in trails_by_name.values():
            segments = trail.pop("segments")
            trail.pop("_best_len", None)
            if segments > 1:
                trail["segments"] = segments
            trails.append(trail)

        trails.sort(key=lambda t: t["name"].lower())
        with _HIKING_CACHE_LOCK:
            _HIKING_TRAILS_CACHE.clear()
            _HIKING_TRAILS_CACHE[cache_key] = trails
        return jsonify(trails)
    except Exception as e:
        logger.error(f"hiking trail query failed for {park_code}: {e}")
        return jsonify({"error": str(e)}), 502

@app.route("/api/nav/favorites", methods=["GET", "POST"])
def api_nav_favorites():
    if request.method == "GET":
        return jsonify(db.get_nav_favorites())
    data = request.json or {}
    name = (data.get("name") or "").strip()
    lat, lon = data.get("lat"), data.get("lon")
    if not name or lat is None or lon is None:
        return jsonify({"error": "name, lat, lon required"}), 400
    fav_id = db.add_nav_favorite(name, lat, lon)
    return jsonify({"id": fav_id, "name": name, "lat": float(lat), "lon": float(lon)})

@app.route("/api/nav/favorites/<int:fav_id>", methods=["PATCH", "DELETE"])
def api_nav_favorite_item(fav_id):
    if request.method == "DELETE":
        db.delete_nav_favorite(fav_id)
        return jsonify({"ok": True})
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    db.rename_nav_favorite(fav_id, name)
    return jsonify({"ok": True, "id": fav_id, "name": name})

# ── Routing — delegates to RoutingNode (routing_node.py) ─────────────────────
# _STATE_BBOX, state detection, and all OSRM process management are in
# RoutingNode (imported at the top of this file). The global 'routing_node'
# is initialized in main() and used by all /api/nav/route and /api/nav/navigate
# endpoints below.  start_routing.sh still works and populates osrm_active.json;
# RoutingNode reads that file for pre-loaded containers.

_ACTIVE_PATH = atlas_paths.data_file("osrm_active.json")
_STATES_DIR  = atlas_paths.OSRM_STATES_DIR


# ── Legacy helpers (kept so start_routing.sh / osrm_active.json still work) ──

def _state_from_bbox_legacy(lat: float, lon: float):
    """Return state name for (lat, lon) via bbox lookup (delegates to routing_node)."""
    return routing_node.get_state_for(lat, lon) if routing_node else None



# ── Route calculation ─────────────────────────────────────────────────────────

@app.route("/api/nav/route")
def api_nav_route():
    """Calculate an offline route using RoutingNode (OSRM, 100% offline)."""
    try:
        from_lat = float(request.args["from_lat"])
        from_lon = float(request.args["from_lon"])
        to_lat   = float(request.args["to_lat"])
        to_lon   = float(request.args["to_lon"])
        _validate_coord(from_lat, from_lon)
        _validate_coord(to_lat, to_lon)
    except KeyError:
        return jsonify({"error": "from_lat, from_lon, to_lat, to_lon required"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    mode = request.args.get("mode", "road")
    profile_map = {"road": "car", "driving": "car", "car": "car",
                   "hiking": "hiking"}
    profile = profile_map.get(mode, "car")

    if not routing_node:
        return jsonify({"error": "Routing engine not initialized"}), 503

    result = routing_node.route(from_lat, from_lon, to_lat, to_lon, profile=profile)
    return jsonify(result)


# ── Turn-by-turn navigation endpoints ────────────────────────────────────────

@app.route("/api/nav/navigate", methods=["GET"])
def api_nav_get():
    """Return current navigation state (active route, step, ETA, off-route)."""
    if not nav_node:
        return jsonify({"active": False})
    return jsonify(nav_node.get_state())


@app.route("/api/nav/navigate", methods=["POST"])
def api_nav_start():
    """
    Start turn-by-turn navigation.
    Body: {from_lat, from_lon, to_lat, to_lon, mode?, destination_name?}
    Returns: {ok, route: {total_distance, duration_str, steps: [...]}}
    """
    data = request.json or {}
    try:
        from_lat = float(data["from_lat"])
        from_lon = float(data["from_lon"])
        to_lat   = float(data["to_lat"])
        to_lon   = float(data["to_lon"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "from_lat, from_lon, to_lat, to_lon required"}), 400

    mode = data.get("mode", "road")
    profile_map = {"road": "car", "driving": "car", "car": "car",
                   "hiking": "hiking"}
    profile    = profile_map.get(mode, "car")
    dest_name  = (data.get("destination_name") or "").strip()
    waypoints  = data.get("waypoints") or []

    if not nav_node:
        return jsonify({"error": "Navigation engine not initialized"}), 503

    # Ray and routing are meant to coexist. osrm-routed now runs with --mmap, so
    # a routing instance costs ~42 MB RSS instead of ~1.1 GB, and the embedder
    # loads at num_ctx=1024 instead of 4096 (~790 MB back) — on this box that is
    # enough headroom for both. So this is a pressure valve, not the default
    # path: only evict Ray when the box genuinely cannot fit the cold start,
    # because a route that degrades to a straight line is worse than a briefly
    # unavailable assistant. Best-effort throughout — a Ray/Ollama hiccup must
    # never block navigation from starting.
    unloaded_ray = False
    if ai_manager:
        try:
            # Module-level helpers: the instance has neither, and calling them
            # on it raised AttributeError, which kept this valve shut forever.
            free_mb = _available_ram_mb()
            if free_mb is not None and free_mb < _ROUTING_MIN_FREE_MB:
                logger.warning(
                    f"Only {free_mb:.0f} MB RAM available (< "
                    f"{_ROUTING_MIN_FREE_MB} MB) — unloading Ray so the "
                    f"OSRM cold start can succeed; it reloads when navigation ends.")
                ai_manager.unload_all()
                unloaded_ray = True
            else:
                logger.info(
                    f"Navigation starting with Ray resident "
                    f"({free_mb:.0f} MB RAM available)" if free_mb is not None
                    else "Navigation starting with Ray resident")
        except Exception as e:
            logger.warning(f"Ray memory check before navigation start failed: {e}")

    result = nav_node.start_navigation(
        from_lat, from_lon, to_lat, to_lon,
        profile=profile, destination_name=dest_name, waypoints=waypoints)

    # Prewarm routing for destination state
    if routing_node:
        threading.Thread(
            target=routing_node.prewarm_for_position,
            args=(to_lat, to_lon), daemon=True).start()

    if unloaded_ray and not result.get("ok") and ai_manager:
        # Navigation never actually started, so cancel_navigation() (the only
        # other place that reloads Ray) will never fire for this session —
        # reload now instead of leaving Ray unloaded indefinitely.
        threading.Thread(target=ai_manager._warmup, daemon=True).start()

    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/api/nav/navigate", methods=["DELETE"])
def api_nav_cancel():
    """Cancel active navigation."""
    if nav_node:
        nav_node.cancel_navigation()
    # Reload Ray now that turn-by-turn navigation has ended.
    if ai_manager:
        threading.Thread(target=ai_manager._warmup, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/nav/routing/status")
def api_routing_status():
    """Return currently loaded OSRM instances and routing engine mode."""
    if not routing_node:
        return jsonify({"initialized": False})
    return jsonify({
        "initialized": True,
        "native": routing_node.use_native,
        "engine": "native osrm-routed" if routing_node.use_native else "docker",
        "active": routing_node.get_active_map(),
    })


@app.route("/api/nav/routing/prewarm", methods=["POST"])
def api_routing_prewarm():
    """Prewarm OSRM for a specific state and profile."""
    data    = request.json or {}
    state   = (data.get("state") or "").strip().lower()
    profile = (data.get("profile") or "car").strip().lower()
    if not state:
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is not None and lon is not None and routing_node:
            try:
                lat = float(lat)
                lon = float(lon)
                _validate_coord(lat, lon)
                routing_node.prewarm_for_position(lat, lon)
                return jsonify({"ok": True, "message": "Prewarming route engines for coordinates"})
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        # Prewarm current GPS position
        fix = gps_manager.current_fix if gps_manager else None
        if fix:
            lat, lon = fix.get("latitude"), fix.get("longitude")
            if lat and lon and routing_node:
                routing_node.prewarm_for_position(lat, lon)
                return jsonify({"ok": True, "message": "Prewarming current GPS state"})
        return jsonify({"error": "state required (or have GPS fix)"}), 400
    if routing_node:
        routing_node.prewarm(state, profile)
    return jsonify({"ok": True, "state": state, "profile": profile})


# ── NPS / OSM trails GeoJSON overlay ─────────────────────────────────────────

@app.route("/api/trails")
def api_trails():
    """
    Return trails as GeoJSON FeatureCollection for the map overlay.
    Query params:
      bbox=lon_min,lat_min,lon_max,lat_max  (required)
      limit=N                               (default 500, max 2000)
    Sources: nps_trails.db (preferred) → trailheads.db (legacy fallback)
    """
    import sqlite3 as _sq

    try:
        bbox_str = request.args["bbox"]
        lon_min, lat_min, lon_max, lat_max = map(float, bbox_str.split(","))
    except (KeyError, ValueError):
        return jsonify({"error": "bbox=lon_min,lat_min,lon_max,lat_max required"}), 400

    limit = min(int(request.args.get("limit", 500)), 2000)

    features = []

    # ── Primary: nps_trails.db (line geometries) ──────────────────────────────
    nps_db = os.path.join(atlas_paths.STATIC_DATA_DIR, "nps_trails.db")
    if os.path.exists(nps_db):
        try:
            con = _sq.connect(f"file:{nps_db}?mode=ro", uri=True)
            # Use the spatial index columns for fast bbox filtering
            rows = con.execute(
                """SELECT id, name, surface, trail_type, difficulty,
                          length_m, park_name, geometry_json
                   FROM trails
                   WHERE bbox_east >= ? AND bbox_west <= ?
                     AND bbox_north >= ? AND bbox_south <= ?
                   LIMIT ?""",
                (lon_min, lon_max, lat_min, lat_max, limit)
            ).fetchall()
            con.close()
            for tid, name, surface, ttype, difficulty, length_m, park, coords_json in rows:
                try:
                    geom = json.loads(coords_json)
                except Exception:
                    continue
                if not geom:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "id": tid, "name": name or "Trail",
                        "surface": surface, "trail_type": ttype,
                        "difficulty": difficulty,
                        "length_m": length_m,
                        "park": park,
                        "source": "nps",
                    },
                })
        except Exception as _e:
            logger.debug(f"NPS trails query: {_e}")

    # ── Trailheads (points) from nps_trails.db ────────────────────────────────
    if os.path.exists(nps_db) and len(features) < limit:
        try:
            con = _sq.connect(f"file:{nps_db}?mode=ro", uri=True)
            rows = con.execute(
                """SELECT th.id, th.name, th.feat_type, th.lat, th.lon, u.full_name
                   FROM trailheads
                   AS th
                   LEFT JOIN nps_units AS u ON u.park_code = th.park_code
                   WHERE th.lon >= ? AND th.lon <= ? AND th.lat >= ? AND th.lat <= ?
                   LIMIT ?""",
                (lon_min, lon_max, lat_min, lat_max, limit - len(features))
            ).fetchall()
            con.close()
            for tid, name, ftype, lat, lon, park in rows:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "id": tid, "name": name or "Trailhead",
                        "feat_type": ftype, "park": park,
                        "source": "nps",
                    },
                })
        except Exception as _e:
            logger.debug(f"NPS trailheads query: {_e}")

    # ── Legacy fallback: trailheads.db (points only) ──────────────────────────
    legacy_db = os.path.join(atlas_paths.STATIC_DATA_DIR, "trailheads.db")
    if not features and os.path.exists(legacy_db):
        try:
            con = _sq.connect(f"file:{legacy_db}?mode=ro", uri=True)
            rows = con.execute(
                """SELECT name, feat_type, lat, lon FROM trailheads
                   WHERE lon >= ? AND lon <= ? AND lat >= ? AND lat <= ?
                   LIMIT ?""",
                (lon_min, lon_max, lat_min, lat_max, limit)
            ).fetchall()
            con.close()
            for name, ftype, lat, lon in rows:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"name": name, "feat_type": ftype, "source": "legacy"},
                })
        except Exception as _e:
            logger.debug(f"Legacy trailheads query: {_e}")

    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/nav/reverse")
def api_nav_reverse():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
        _validate_coord(lat, lon)
    except KeyError:
        return jsonify({"error": "lat and lon required"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # ── Offline: shared reverse geocoder (ai_manager), then trailheads ────────
    # Uses the same gravity-weighted lookup Ray does, so the map and the AI can
    # never disagree about where you are. The old local copy here sorted by raw
    # squared degrees with no cos(lat) correction and no population weighting,
    # which named the nearest tiny centroid (Floris) over the town you are
    # actually in (Herndon).
    def _nearest_local(lat, lon):
        """Return display_name for the nearest city/trailhead in the local DB."""
        name = reverse_geocode(lat, lon)
        if name:
            return name
        import sqlite3 as _sq
        deg = 0.5   # ~55 km bounding box each side
        db_path = os.path.join(atlas_paths.STATIC_DATA_DIR, "trailheads.db")
        if not os.path.exists(db_path):
            return None
        try:
            con = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
            row = con.execute(
                "SELECT name, state, lat, lon FROM trailheads "
                "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                "ORDER BY ((lat-?)*(lat-?) + (lon-?)*(lon-?)) LIMIT 1",
                (lat-deg, lat+deg, lon-deg, lon+deg, lat, lat, lon, lon)).fetchone()
            con.close()
            if row:
                return f"{row[0]}, {row[1]}"
        except Exception:
            pass
        return None

    local_name = _nearest_local(lat, lon)
    display = local_name or f"{lat:.6f}, {lon:.6f}"
    return jsonify({"display_name": display, "lat": lat, "lon": lon})

# ------------------------------------------------------------------ API: App settings
@app.route("/api/settings")
def api_settings_get():
    settings = dict(db.get_app_settings())
    # The captured factory snapshot holds the radio's Wi-Fi key and BT PIN;
    # it is only needed server-side to restore the radio.
    for key in list(settings):
        if key.startswith("factory_defaults_device"):
            settings.pop(key)
    return jsonify(settings)

@app.route("/api/settings", methods=["PUT"])
def api_settings_put():
    data = request.json or {}
    db.set_app_settings(data)
    # Apply a serial-port change to the running mesh manager. connect()
    # resolves candidates from mesh.port, which is captured once at startup —
    # without this hand-off a new value only takes effect after a service
    # restart (which is how a stale pin kept the mesh off the radio when it
    # moved between the 40-pin UART and USB-C).
    if mesh and "serial_port" in data:
        new_port = (data.get("serial_port") or "AUTO").strip() or "AUTO"
        if new_port != mesh.port:
            mesh.port = new_port
            if db.get_app_settings().get("comms_enabled", "true") == "true":
                threading.Thread(target=mesh.reconnect, daemon=True).start()
    return jsonify({"ok": True, "settings": db.get_app_settings()})

# ------------------------------------------------------------------ API: Phone Tracker
@app.route("/api/tracker/devices")
def api_tracker_devices():
    return jsonify(db.get_tracker_devices())

@app.route("/api/tracker/checkin", methods=["POST"])
def api_tracker_checkin():
    data = request.json or {}
    device_id = (data.get("device_id") or "").strip()
    name = (data.get("name") or "").strip()
    if not device_id or not name:
        return jsonify({"error": "device_id and name required"}), 400
    if len(device_id) > 64:
        return jsonify({"error": "device_id too long (max 64 characters)"}), 400
    if len(name) > 100:
        return jsonify({"error": "name too long (max 100 characters)"}), 400
    # Tracker data is rendered on every client's map; accept only well-formed
    # values (the colour lands in a style attribute).
    color = str(data.get("color") or "#3b82f6")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return jsonify({"error": "color must look like #1a2b3c"}), 400

    def _num(key, lo, hi):
        v = data.get(key)
        if v is None or v == "":
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(key)
        if not (math.isfinite(v) and lo <= v <= hi):
            raise ValueError(key)
        return v

    try:
        lat = _num("latitude", -90, 90)
        lon = _num("longitude", -180, 180)
        fields = {
            "accuracy": _num("accuracy", 0, 1e6),
            "altitude": _num("altitude", -1e4, 1e5),
            "speed":    _num("speed", 0, 1e4),
            "heading":  _num("heading", 0, 360),
            "battery":  _num("battery", 0, 100),
        }
    except ValueError as exc:
        return jsonify({"error": f"invalid {exc.args[0]}"}), 400
    if (lat is None) != (lon is None):
        return jsonify({"error": "latitude and longitude go together"}), 400
    record = {
        "id":        device_id,
        "name":      _sanitize_text(name, 100),
        "color":     color,
        "latitude":  lat,
        "longitude": lon,
        **fields,
        "last_seen": int(time.time()),
    }
    db.upsert_tracker_device(record)
    dev = db.get_tracker_device(device_id)
    socketio.emit("tracker_update", dev, namespace="/")
    return jsonify({"ok": True})

@app.route("/api/tracker/devices/<device_id>", methods=["DELETE"])
def api_tracker_device_delete(device_id):
    db.delete_tracker_device(device_id)
    socketio.emit("tracker_removed", {"id": device_id}, namespace="/")
    return jsonify({"ok": True})

# ------------------------------------------------------------------ API: Power management
@app.route("/api/power/status")
def api_power_status():
    s = db.get_app_settings()
    return jsonify({
        "ai_enabled": s.get("ai_enabled", "true") == "true",
        "comms_enabled": s.get("comms_enabled", "true") == "true",
    })

@app.route("/api/power/ai", methods=["POST"])
def api_power_ai():
    data = request.json or {}
    enabled = bool(data.get("enabled", True))
    db.set_app_settings({"ai_enabled": "true" if enabled else "false"})
    if enabled:
        if ai_manager:
            threading.Thread(target=ai_manager._warmup, daemon=True).start()
    else:
        if ai_manager:
            threading.Thread(target=ai_manager.unload_all, daemon=True).start()
    return jsonify({"ok": True, "ai_enabled": enabled})

@app.route("/api/power/comms", methods=["POST"])
def api_power_comms():
    data = request.json or {}
    enabled = bool(data.get("enabled", True))
    db.set_app_settings({"comms_enabled": "true" if enabled else "false"})
    if enabled:
        if mesh:
            threading.Thread(target=mesh.reconnect, daemon=True).start()
    else:
        if mesh:
            mesh.disconnect()
    return jsonify({"ok": True, "comms_enabled": enabled})

# ------------------------------------------------------------------ API: Device config
_RADIO_SECRET_FIELDS = (("network", "wifi_psk"), ("bluetooth", "fixed_pin"))


def _redact_radio_config(cfg):
    """Copy of a radio config with its Wi-Fi key and Bluetooth PIN hidden.

    The fields come back as null plus a `<field>_set` flag; a PUT that leaves
    them null or empty keeps the radio's current value.
    """
    import copy
    out = copy.deepcopy(cfg) if isinstance(cfg, dict) else cfg
    if not isinstance(out, dict):
        return out
    for section, field in _RADIO_SECRET_FIELDS:
        sec = out.get(section)
        if isinstance(sec, dict) and field in sec:
            sec[f"{field}_set"] = bool(sec[field])
            sec[field] = None
    return out


@app.route("/api/device/config")
def api_device_config_get():
    if not mesh:
        return jsonify({"connected": False})
    return jsonify(_redact_radio_config(mesh.get_device_config()))

@app.route("/api/device/config/<section>", methods=["PUT"])
def api_device_config_put(section):
    ALLOWED = {"device", "lora", "position", "power", "bluetooth", "network"}
    if section not in ALLOWED:
        return jsonify({"error": f"Unknown section '{section}'"}), 400
    data = dict(request.json or {})
    for sec, field in _RADIO_SECRET_FIELDS:
        if section == sec and data.get(field) in (None, ""):
            data.pop(field, None)   # left blank in the UI: keep the radio's value
        data.pop(f"{field}_set", None)
    if not mesh:
        return jsonify({"ok": False, "error": "mesh manager unavailable"}), 503
    ok = mesh.set_device_config(section, data)
    if not ok:
        return jsonify({"ok": False, "error": f"failed to update {section} settings on device"}), 502
    return jsonify({"ok": True})

@app.route("/api/device/owner")
def api_device_owner_get():
    if not mesh:
        return jsonify({})
    return jsonify(mesh.get_owner_info())

@app.route("/api/device/owner", methods=["PUT"])
def api_device_owner_put():
    data = request.json or {}
    long_name  = _sanitize_text(data.get("long_name")  or "", max_len=39)
    short_name = _sanitize_text(data.get("short_name") or "", max_len=4)
    if not long_name or not short_name:
        return jsonify({"error": "long_name and short_name required"}), 400
    if len(short_name) > 4:
        return jsonify({"error": "short_name max 4 characters"}), 400
    if not mesh:
        return jsonify({"ok": False, "error": "mesh manager unavailable"}), 503
    ok = mesh.set_owner(long_name, short_name)
    # Always sync the new name into the DB so the UI reflects it immediately.
    if mesh:
        node_id = mesh._node_id_str()
        logger.info(f"set_owner ok={ok} node_id={node_id!r} long_name={long_name!r} short_name={short_name!r}")
        if node_id and node_id != "local":
            try:
                conn = db.get_db()
                cur = conn.execute(
                    "UPDATE nodes SET long_name=?, short_name=?, alias=NULL WHERE node_id=?",
                    (long_name, short_name, node_id))
                conn.commit()
                logger.info(f"DB name update: {cur.rowcount} row(s) affected for node_id={node_id!r}")
                if cur.rowcount == 0:
                    # No existing row — list all node_ids so we can spot the mismatch
                    rows = conn.execute("SELECT node_id, long_name FROM nodes").fetchall()
                    logger.warning(f"No row updated. DB contains: {[(r[0], r[1]) for r in rows]}")
                else:
                    # Push the new name to all connected UI clients immediately so
                    # they don't have to wait for the next NODEINFO broadcast.
                    # alias=None clears any stale override so long_name shows everywhere.
                    mesh._emit("node_update", {
                        "node_id": node_id,
                        "long_name": long_name,
                        "short_name": short_name,
                        "alias": None,
                    })
            except Exception as e:
                logger.warning(f"Failed to update node name in DB: {e}")
    else:
        logger.warning("set_owner: mesh is None, skipping DB update")
    if not ok:
        return jsonify({"ok": False, "error": "failed to update node identity on device"}), 502
    return jsonify({"ok": True})

# ------------------------------------------------------------------ API: Software update
# Checking, downloading and handing verified releases to the root update
# engine: see update_manager.py. The engine itself (install, health check,
# rollback, power-loss recovery) lives in system/atlas_engine.
import update_manager as _update_manager

updates = _update_manager.UpdateManager(
    emit=lambda event, data: socketio.emit(event, data, namespace="/"),
    helper_run=atlas_helper.run,
)
_UPDATE_AUTO_CHECK_S = _update_manager.AUTO_CHECK_INTERVAL_S


def _update_background_loop():
    """Greenlet: finish/relay installs, resume interrupted downloads and check
    for updates periodically while online. Never installs by itself."""
    import gevent
    updates.reconcile_after_restart()
    if updates.state.get("state") == "paused" and updates.state.get("manifest"):
        updates.start(install=bool(updates.state.get("install_when_ready")))
    next_check = time.time() + 90
    while True:
        try:
            if updates.poll_engine():
                gevent.sleep(1)
                continue
            if time.time() >= next_check:
                if _network_connectivity_state().get("online"):
                    updates.check()
                    next_check = time.time() + _UPDATE_AUTO_CHECK_S
                else:
                    next_check = time.time() + 300
        except Exception as exc:
            logger.warning("software update loop: %s", exc)
        gevent.sleep(5)


@app.route("/api/update/state")
def api_update_state():
    resp = jsonify(updates.snapshot())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/update/check", methods=["GET", "POST"])
def api_update_check():
    if request.method == "GET":
        return jsonify(updates.snapshot())
    return jsonify(updates.check(user_initiated=True))


@app.route("/api/update/start", methods=["POST"])
def api_update_start():
    data = request.get_json(silent=True) or {}
    return jsonify(updates.start(install=data.get("install", True) is not False))


@app.route("/api/update/install", methods=["POST"])
def api_update_install():
    return jsonify(updates.install())


@app.route("/api/update/cancel", methods=["POST"])
def api_update_cancel():
    return jsonify(updates.cancel())


# Older clients.
@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    return jsonify(updates.start(install=True))


@app.route("/api/update/status")
def api_update_status():
    return jsonify(updates.snapshot())


# ------------------------------------------------------------------ API: Factory reset
def _device_link_state():
    """Probe the live device link more accurately than mesh.connected alone.

    `mesh.connected` is toggled by pub-sub callbacks and can lag the real
    interface state. The truthful signal is whether get_device_config()
    actually returns a config with `connected: True` and no error.
    """
    if not mesh:
        return {"connected": False, "reason": "mesh manager unavailable"}
    cfg = mesh.get_device_config() or {}
    if cfg.get("error"):
        return {"connected": False, "reason": f"config read error: {cfg['error']}"}
    if not cfg.get("connected"):
        return {"connected": False, "reason": "radio interface not present"}
    return {"connected": True, "cfg": cfg}

def _capture_factory_defaults():
    """Snapshot current Heltec config + long_name and persist as defaults.

    Returns (snapshot_dict, None) on success, or (None, error_str) on failure.
    The snapshot includes a `captured_at` epoch timestamp so the UI can
    show the user when the defaults were last refreshed.
    """
    state = _device_link_state()
    if not state["connected"]:
        return None, state.get("reason") or "device unavailable"
    cfg = state["cfg"]
    snapshot = {k: v for k, v in cfg.items() if k != "connected"}
    try:
        owner = mesh.get_owner_info() or {}
    except Exception as e:
        owner = {}
        logger.warning("factory_defaults: get_owner_info failed: %s", e)
    snapshot["owner"] = {
        "long_name": owner.get("long_name", ""),
        "node_id": owner.get("node_id", ""),
        "hw_model": owner.get("hw_model", ""),
    }
    snapshot["captured_at"] = int(time.time())
    try:
        db.set_factory_defaults_device(snapshot)
    except Exception as e:
        return None, f"failed to persist snapshot: {e}"
    return snapshot, None

@app.route("/api/factory-defaults")
def api_factory_defaults_get():
    """Return the stored Heltec factory snapshot. Captures lazily on first call."""
    snap = db.get_factory_defaults_device()
    captured_now = False
    capture_error = None
    state = _device_link_state()
    if not snap:
        snap, capture_error = _capture_factory_defaults()
        captured_now = snap is not None
    return jsonify({
        "snapshot": _redact_radio_config(snap),
        "captured_now": captured_now,
        "device_connected": state["connected"],
        "device_status": state.get("reason") or "connected",
        "capture_error": capture_error,
    })

@app.route("/api/factory-defaults/capture", methods=["POST"])
def api_factory_defaults_capture():
    """Re-capture the Heltec config snapshot from the live radio."""
    snap, err = _capture_factory_defaults()
    if snap is None:
        return jsonify({"ok": False, "error": err or "device not connected or config unavailable"}), 503
    return jsonify({"ok": True, "snapshot": _redact_radio_config(snap)})

def _factory_reset_local_state():
    """The owner's state outside the database: saved networks, network
    hints, hotspot changes, downloaded updates and pre-migration database
    copies (which hold the old data). Best effort; returns what failed."""
    problems = []
    for path in (_LAN_HINTS_PATH, _HOTSPOT_CFG_PATH):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            problems.append(f"{os.path.basename(path)}: {exc.strerror}")
    shutil.rmtree(atlas_paths.BACKUP_DIR, ignore_errors=True)
    updates.forget()
    out, rc = atlas_helper.run("net.forget_all", timeout=60)
    if rc != 0:
        problems.append("saved Wi-Fi networks: " + (out or "helper unavailable")[:120])
    return problems


def _restart_hotspot_with_label_credentials():
    """If the hotspot is on, bring it back with the label's name/password."""
    atlas_blocking.sleep(3)          # let the reset response reach the browser first
    try:
        if _current_hotspot_state().get("active"):
            with _WIFI_OPS_LOCK:
                _start_hotspot_with_saved_config()
    except Exception as exc:
        logger.warning("factory reset: hotspot restart failed: %s", exc)


@app.route("/api/factory-reset", methods=["POST"])
def api_factory_reset():
    """Return Atlas to how it left the factory, ready for a new owner.

    Requires {"confirm": "RESET"}. Steps:
      1. Ensure a Heltec snapshot exists (capture lazily if missing).
      2. Erase the owner's data (see database.factory_reset_data) and state
         outside the database: saved Wi-Fi networks, hotspot changes,
         downloads, database copies. Every paired device must pair again.
      3. Re-write the snapshot to the radio.
      4. Reset short_name to the firmware default (last 4 hex of node ID).
    Kept: the installed software, offline maps/routing and built-in
    reference, and Atlas's identity (name, key, label setup code and Wi-Fi
    password — the hotspot returns to the label's credentials).
    """
    if not _confirmed("RESET"):
        return _needs_confirmation("reset Atlas (type RESET)")
    if updates.state.get("state") == "installing":
        return jsonify({"ok": False, "error": "Atlas is installing an update. Try again when it has finished."}), 409
    snap = db.get_factory_defaults_device()
    if not snap:
        snap, _ = _capture_factory_defaults()

    # Wipe the radio's NodeDB BEFORE the SQL wipe so any in-flight nodeinfo
    # broadcasts that arrive during the wipe land in already-empty state and
    # the radio doesn't re-populate the local DB from its cached nodedb on
    # the next sync pass.
    nodedb_result = {"radio_reset": False, "in_memory_cleared": 0}
    if mesh:
        try:
            nodedb_result = mesh.wipe_nodedb()
        except Exception as e:
            logger.error("factory_reset: nodedb wipe failed: %s", e)
            nodedb_result = {"error": str(e)}

    try:
        db.factory_reset_data()
    except Exception as e:
        logger.error("factory_reset: DB wipe failed: %s", e)
        return jsonify({"ok": False, "error": f"data wipe failed: {e}"}), 500
    pairing.revoke_all()
    local_problems = _factory_reset_local_state()
    for problem in local_problems:
        logger.warning("factory_reset: %s", problem)
    audit_log.record("factory_reset", None)

    device_result = {"applied": False, "reason": "no snapshot or device unavailable", "nodedb": nodedb_result}
    if mesh and mesh.connected and snap:
        try:
            apply_res = mesh.apply_device_config_snapshot(snap)
            device_result = {"applied": bool(apply_res.get("ok")), "sections": apply_res, "nodedb": nodedb_result}
        except Exception as e:
            logger.error("factory_reset: config restore failed: %s", e)
            device_result = {"applied": False, "error": str(e), "nodedb": nodedb_result}

        # Reset owner: keep snapshot long_name, force short_name to Heltec default.
        try:
            long_name = ""
            if isinstance(snap.get("owner"), dict):
                long_name = (snap["owner"].get("long_name") or "").strip()
            if not long_name:
                long_name = (mesh.get_owner_info() or {}).get("long_name", "") or "Atlas Control"
            short_name = mesh.default_short_name()
            mesh.set_owner(long_name, short_name)
            device_result["owner"] = {"long_name": long_name, "short_name": short_name}
        except Exception as e:
            logger.error("factory_reset: owner reset failed: %s", e)
            device_result["owner_error"] = str(e)

    # Push a UI nudge so connected clients drop their cached state, then cut
    # every live connection: nobody is paired any more.
    try:
        socketio.emit("factory_reset_done", {"device": device_result})
    except Exception:
        pass
    import gevent
    gevent.spawn_later(1.5, _disconnect_sockets)
    gevent.spawn(_restart_hotspot_with_label_credentials)

    return jsonify({"ok": True, "device": device_result, "incomplete": local_problems})


# ------------------------------------------------------------------ API: Privacy
@app.route("/api/privacy/retention", methods=["GET"])
def api_privacy_retention_get():
    return jsonify(retention.settings())


@app.route("/api/privacy/retention", methods=["PUT"])
def api_privacy_retention_put():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected a JSON object"}), 400
    try:
        return jsonify(retention.update(data))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/privacy/delete-ai-history", methods=["POST"])
def api_privacy_delete_ai_history():
    if not _confirmed():
        return _needs_confirmation("delete all Ray conversations")
    counts = retention.delete_ai_history()
    audit_log.record("ai_history_deleted", audit_log.actor_from(request.environ), **counts)
    socketio.emit("ai_history_deleted", {}, to="paired", namespace="/")
    return jsonify({"ok": True, **counts})


@app.route("/api/privacy/delete-location-history", methods=["POST"])
def api_privacy_delete_location_history():
    if not _confirmed():
        return _needs_confirmation("delete Atlas's location history")
    counts = retention.delete_location_history()
    audit_log.record("location_history_deleted", audit_log.actor_from(request.environ), **counts)
    socketio.emit("location_history_deleted", {}, to="paired", namespace="/")
    return jsonify({"ok": True, **counts})


@app.route("/api/audit")
def api_audit():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
    except ValueError:
        limit = 100
    return jsonify(audit_log.recent(limit))

# ------------------------------------------------------------------ SocketIO
@socketio.on("connect")
def handle_connect(auth=None):
    # Host, Origin and the real client address were already established by
    # request_guard.EdgeMiddleware for this /socket.io/ request.
    if not _is_local(request.remote_addr or ""):
        logger.warning("Rejected WebSocket client from non-local address %s", request.remote_addr or "-")
        return False
    client = pairing.authenticate(request.environ)
    if client is None and isinstance(auth, dict) and auth.get("token"):
        client = pairing.lookup(str(auth["token"]))
    if client is None and pairing.required():
        return False
    from flask_socketio import join_room
    join_room("paired")
    _socket_clients[request.sid] = client["id"] if client else None
    logger.info("WebSocket client connected")


# Live Socket.IO sessions → paired client id, so removing a device (or a
# factory reset) also cuts its open connection instead of letting it keep
# receiving live data until it happens to reconnect.
_socket_clients = {}


@socketio.on("disconnect")
def handle_disconnect(*_args):
    _socket_clients.pop(request.sid, None)
    _system_room.discard(request.sid)


def _disconnect_sockets(client_id=None):
    for sid, cid in list(_socket_clients.items()):
        if client_id is None or cid == client_id:
            _socket_clients.pop(sid, None)
            try:
                socketio.server.disconnect(sid, namespace="/")
            except Exception:
                pass

@socketio.on("join_rooms")
def handle_join_rooms(data):
    """Client calls this to subscribe to specific event rooms (e.g. mesh, chat, map)."""
    from flask_socketio import join_room, leave_room
    ALL_ROOMS = {"mesh", "chat", "map", "telemetry", "system"}
    rooms = data.get("rooms", []) if isinstance(data, dict) else []
    for room in ALL_ROOMS:
        try:
            leave_room(room)
        except Exception:
            pass
    for room in rooms:
        if room in ALL_ROOMS:
            join_room(room)
    if "system" in rooms:
        _system_room.add(request.sid)
    else:
        _system_room.discard(request.sid)


# Live stats go to whoever has the System or Power page open (room "system");
# with nobody watching, nothing is collected.
_system_room = set()
STATS_PUSH_S = 3


def _push_stats_once():
    if not _system_room:
        return False
    try:
        with perf_metrics.timer("stats.collect"):
            stats = atlas_blocking.call(system_stats.get_stats)
        socketio.emit("system_stats", stats, to="system", namespace="/")
        return True
    except Exception as exc:
        logger.debug("stats push failed: %s", exc)
        return False


def _stats_push_loop():
    import gevent
    while True:
        gevent.sleep(STATS_PUSH_S)
        _push_stats_once()

@socketio.on("send_message")
def handle_send(data):
    if mesh:
        mesh.send_message(data.get("text", ""), data.get("destination"), data.get("channel"))

# ------------------------------------------------------------------ calculator

def safe_calc(expression):
    """Evaluate a calculator expression; returns (result, error)."""
    result, err = safe_math.try_evaluate(expression)
    if err:
        return None, err
    if isinstance(result, float):
        if math.isnan(result):
            return None, "Result is NaN"
        if math.isinf(result):
            return str(result), None
    return result, None


@app.route("/api/calculator", methods=["POST"])
@limiter.limit("60 per minute")
def api_calculator():
    data = request.get_json(silent=True) or {}
    expr = data.get("expression", "")
    result, err = safe_calc(expr)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"result": result, "expression": expr})


# ------------------------------------------------------------------ calendar

@app.route("/api/calendar", methods=["GET"])
def api_calendar_list():
    start = request.args.get("start", type=int)
    end = request.args.get("end", type=int)
    return jsonify(db.get_calendar_events(start_ts=start, end_ts=end))

@app.route("/api/calendar", methods=["POST"])
def api_calendar_create():
    data = (request.get_json(silent=True) or {})
    if not data.get("title") or not data.get("start_ts"):
        return jsonify({"error": "title and start_ts required"}), 400
    event_id = db.create_calendar_event(data)
    return jsonify({"id": event_id}), 201

@app.route("/api/calendar/<int:event_id>", methods=["PUT"])
def api_calendar_update(event_id):
    data = (request.get_json(silent=True) or {})
    db.update_calendar_event(event_id, data)
    return jsonify({"ok": True})

@app.route("/api/calendar/<int:event_id>", methods=["DELETE"])
def api_calendar_delete(event_id):
    db.delete_calendar_event(event_id)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ scheduled jobs

def _compute_next_run(job, now):
    """Return the next Unix epoch at which this job should fire, or None if done."""
    import datetime as _dt
    stype = job.get('schedule_type')
    if stype == 'once':
        return None  # disable after first run
    elif stype == 'interval':
        secs = job.get('interval_seconds') or 3600
        return now + secs
    elif stype == 'daily':
        tod = job.get('run_at') or 0   # seconds since midnight
        today = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ts = int((today + _dt.timedelta(seconds=tod)).timestamp())
        if ts <= now:
            ts += 86400
        return ts
    elif stype == 'weekly':
        tod  = job.get('run_at') or 0
        mask = job.get('days_of_week') or 0
        today = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(1, 8):
            day = today + _dt.timedelta(days=i)
            if mask & (1 << day.weekday()):
                return int((day + _dt.timedelta(seconds=tod)).timestamp())
    return now + 86400


def _scheduler_loop():
    """Background thread: wakes every 30 s, fires any due jobs."""
    while True:
        time.sleep(30)
        try:
            for job in db.get_due_jobs():
                now = int(time.time())
                try:
                    if job['job_type'] == 'message' and mesh:
                        result = mesh.send_message(
                            job['message_text'] or '',
                            destination=job['node_id'],
                            channel=int(job['channel'] or 0),
                        )
                        if result.get("ok"):
                            logger.info(f"Scheduler: fired message job {job['id']} → {job['node_id']}")
                        else:
                            logger.warning(f"Scheduler: message job {job['id']} failed: {result.get('error')}")
                    elif job['job_type'] == 'gps' and mesh:
                        ok = mesh.send_position(
                            float(job['gps_lat'] or 0),
                            float(job['gps_lon'] or 0),
                            alt=int(job['gps_alt'] or 0),
                            destination_id=job['node_id'] if job['node_id'] else None,
                        )
                        if ok:
                            logger.info(f"Scheduler: fired GPS job {job['id']} → {job['node_id']}")
                        else:
                            logger.warning(f"Scheduler: GPS job {job['id']} failed")
                except Exception as exc:
                    logger.warning(f"Scheduler: job {job['id']} execution error: {exc}")
                next_run = _compute_next_run(job, now)
                db.update_job_after_run(job['id'], now, next_run, 1 if next_run else 0)
        except Exception as exc:
            logger.warning(f"Scheduler loop error: {exc}")


@app.route("/api/jobs", methods=["GET"])
def api_get_jobs():
    return jsonify(db.get_jobs())


@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    data = (request.get_json(silent=True) or {})
    now = int(time.time())
    stype = data.get('schedule_type')
    if stype == 'once':
        data['next_run'] = data.get('run_at') or now
    elif stype == 'interval':
        data['next_run'] = now + (data.get('interval_seconds') or 3600)
    else:
        data['next_run'] = _compute_next_run(data, now)
    job_id = db.create_job(data)
    return jsonify({"id": job_id}), 201


@app.route("/api/jobs/<int:job_id>", methods=["PUT"])
def api_update_job(job_id):
    data = (request.get_json(silent=True) or {})
    now = int(time.time())
    stype = data.get('schedule_type')
    if stype == 'once':
        data['next_run'] = data.get('run_at') or now
    elif stype == 'interval':
        data['next_run'] = now + (data.get('interval_seconds') or 3600)
    else:
        data['next_run'] = _compute_next_run(data, now)
    db.update_job(job_id, data)
    return jsonify({"ok": True})


@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
def api_delete_job(job_id):
    db.delete_job(job_id)
    return jsonify({"ok": True})


@app.route("/api/jobs/<int:job_id>/toggle", methods=["POST"])
def api_toggle_job(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    enabled = 0 if job['enabled'] else 1
    now = int(time.time())
    # Recompute next_run when re-enabling so it isn't stale
    next_run = job['next_run']
    if enabled and (not next_run or next_run < now):
        next_run = _compute_next_run(job, now) or job['run_at']
    db.update_job(job_id, {**job, 'enabled': enabled, 'next_run': next_run})
    return jsonify({"enabled": enabled})


# ── WiFi & Hotspot ─────────────────────────────────────────────────────────

_HOTSPOT_CFG_PATH = atlas_paths.data_file("hotspot_config.json")

def _load_hotspot_cfg():
    """The owner's hotspot settings; until they change them (and again after
    a factory reset), the Wi-Fi name and password printed on Atlas's label."""
    try:
        with open(_HOTSPOT_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return dict(device_identity.hotspot() or {})

def _save_hotspot_cfg(ssid, password):
    try:
        # Created 0600 from the first byte: the file holds the Wi-Fi password.
        tmp = _HOTSPOT_CFG_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"ssid": ssid, "password": password}, f)
        os.replace(tmp, _HOTSPOT_CFG_PATH)
    except Exception as e:
        logging.warning("Could not save hotspot config: %s", e)

def _current_hotspot_state(include_password=False):
    """Hotspot status. The password is returned only when explicitly asked
    for (the owner's "show Wi-Fi password" view) — never in status, bootstrap,
    beacon or Bluetooth payloads."""
    out, _ = _nmcli("-t", "-f", "NAME,TYPE,STATE", "con", "show", "--active")
    active = any("hotspot" in line.lower() for line in out.splitlines())
    cfg = _load_hotspot_cfg()
    ssid = cfg.get("ssid", "")
    password = cfg.get("password", "")
    if not ssid or (include_password and not password):
        info, rc = _nmcli(
            "--show-secrets",
            "--escape",
            "yes",
            "-t",
            "-f",
            "802-11-wireless.ssid,802-11-wireless-security.psk",
            "con",
            "show",
            "Hotspot",
        )
        if rc == 0:
            for line in info.splitlines():
                if line.startswith("802-11-wireless.ssid:"):
                    ssid = line.split(":", 1)[1].strip()
                elif line.startswith("802-11-wireless-security.psk:") and not password:
                    password = line.split(":", 1)[1].strip()
    label = device_identity.hotspot() or {}
    state = {"active": active, "ssid": ssid, "passwordSet": bool(password),
             # Still the standard password every Atlas ships with (anyone who
             # knows it can join): the page reminds the owner to set their own.
             "standard_password": bool(label.get("shared") and password and password == label.get("password"))}
    if include_password:
        state["password"] = password
    return state


def _normalize_ip_value(value):
    return (value or "").split("/", 1)[0].strip() or None


def _wifi_device_state(iface):
    if not iface:
        return None
    out, rc = _nmcli("-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", iface)
    if rc != 0:
        return None
    state = ""
    connection = ""
    ip = None
    for line in out.splitlines():
        if line.startswith("GENERAL.STATE:"):
            state = line.split(":", 1)[1].strip()
        elif line.startswith("GENERAL.CONNECTION:"):
            connection = line.split(":", 1)[1].strip()
        elif line.startswith("IP4.ADDRESS"):
            ip = _normalize_ip_value(line.split(":", 1)[1])
    return {"state": state, "connection": connection, "ip": ip, "iface": iface}


def _current_lan_wifi_state():
    iface = _wifi_iface()
    out, _ = _nmcli("-t", "-f", "NAME,TYPE,DEVICE,STATE", "con", "show", "--active")
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[1] == "802-11-wireless" and parts[2] == (iface or ""):
            name = parts[0]
            if "hotspot" in name.lower():
                continue
            ip_out, _ = _nmcli("-t", "-f", "IP4.ADDRESS", "con", "show", name)
            ip = None
            for l in ip_out.splitlines():
                if l.startswith("IP4.ADDRESS"):
                    ip = _normalize_ip_value(l.split(":", 1)[1])
                    break
            if ip:
                return {"ssid": name, "state": parts[3], "ip": ip, "iface": iface}
    device = _wifi_device_state(iface)
    if not device:
        return None
    connection = device.get("connection") or ""
    state = device.get("state") or ""
    ip = device.get("ip")
    if "hotspot" in connection.lower():
        return None
    if state.startswith("100") and connection and ip:
        return {"ssid": connection, "state": state, "ip": ip, "iface": iface}
    return None


def _current_lan_link_state():
    """Return an active non-hotspot LAN link across Wi-Fi or Ethernet."""
    wifi = _current_lan_wifi_state()
    if wifi:
        wifi["kind"] = "wifi"
        return wifi

    out, _ = _nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", "status")
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        iface, dev_type, state, connection = [p.strip() for p in parts[:4]]
        if dev_type != "ethernet" or not iface or not connection:
            continue
        if "connected" not in state.lower():
            continue
        if "hotspot" in connection.lower():
            continue
        info, rc = _nmcli("-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", iface)
        if rc != 0:
            continue
        ip = None
        active_connection = connection
        for entry in info.splitlines():
            if entry.startswith("GENERAL.CONNECTION:"):
                active_connection = entry.split(":", 1)[1].strip() or connection
            elif entry.startswith("IP4.ADDRESS") and not ip:
                ip = _normalize_ip_value(entry.split(":", 1)[1])
        if not ip:
            continue
        return {
            "kind": "ethernet",
            "ssid": active_connection,
            "state": state,
            "ip": ip,
            "iface": iface,
        }
    return None


def _preferred_local_urls(lan=None):
    urls = []
    if lan and lan.get("ip"):
        ip = _normalize_ip_value(lan["ip"])
        if ip:
            urls.append(f"https://{ip}")
    urls.append(f"https://{device_identity.device().get('hostname') or 'atlas'}.local")
    return urls


def _set_wifi_switch_state(ssid="", pending=False, ok=None, message="", result=None):
    now = int(time.time())
    with _WIFI_SWITCH_LOCK:
        _WIFI_SWITCH_STATE.update({
            "pending": bool(pending),
            "ssid": ssid or "",
            "startedAt": now if pending else (_WIFI_SWITCH_STATE.get("startedAt") or now),
            "finishedAt": 0 if pending else now,
            "ok": ok,
            "message": message or "",
            "result": dict(result) if isinstance(result, dict) else result,
        })


def _wifi_switch_snapshot():
    with _WIFI_SWITCH_LOCK:
        state = dict(_WIFI_SWITCH_STATE)
    result = state.get("result")
    if isinstance(result, dict):
        state["result"] = dict(result)
    return state


def _network_connectivity_state():
    out, rc = _nmcli("-t", "-f", "CONNECTIVITY", "general")
    if rc != 0:
        return {"state": "unknown", "online": False, "checked": False}
    state = ""
    for line in out.splitlines():
        value = (line or "").strip()
        if value:
            state = value.lower()
            break
    if not state:
        state = "unknown"
    return {"state": state, "online": state == "full", "checked": state != "unknown"}


def _lan_ip_unreachable(ip):
    """True for a LAN IP that is not usable at all.

    Only a link-local lease (169.254.0.0/16) qualifies: it means DHCP failed
    outright, so the address is not routable and Atlas got no real network.
    We fall back to the always-reachable hotspot in that case.

    NOTE: CGNAT / RFC6598 shared addresses (100.64.0.0/10) are deliberately
    NOT treated as unreachable.  Managed/apartment networks (e.g. WhiteSky)
    hand these out routinely while still allowing local access, and rejecting
    them stranded Atlas's primary network in a connect→reject→hotspot flap.
    A CGNAT address is reachable via its IP, atlas.local and Tailscale, so we
    stay on it like any other LAN.  See _RFC6598_SHARED_NET, which still marks
    these as *local* for discovery (_is_local).
    """
    ip = _normalize_ip_value(ip)
    if not ip:
        return False
    try:
        addr = _ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if addr.version == 4:
        return addr.is_link_local
    return False


def _set_connection_autoconnect(name, enabled):
    """Best-effort toggle of a saved connection's autoconnect flag so Atlas
    does not keep rejoining a network we've classified as unreachable.  Failures
    (e.g. sudo/polkit denial) are non-fatal — the failover loop self-corrects on
    the next boot regardless."""
    if not name:
        return
    try:
        _, rc = _nmcli("con", "modify", name, "connection.autoconnect",
                       "yes" if enabled else "no", timeout=10)
        if rc != 0:
            logger.debug("Could not set autoconnect=%s on %s (rc=%s)", enabled, name, rc)
    except Exception as e:
        logger.debug("Failed to set autoconnect=%s on %s: %s", enabled, name, e)


def _defer_auto_hotspot(seconds):
    global _AUTO_HOTSPOT_GRACE_UNTIL
    with _AUTO_HOTSPOT_LOCK:
        _AUTO_HOTSPOT_GRACE_UNTIL = max(_AUTO_HOTSPOT_GRACE_UNTIL, time.time() + max(0, seconds))


def _auto_hotspot_allowed():
    with _AUTO_HOTSPOT_LOCK:
        return time.time() >= _AUTO_HOTSPOT_GRACE_UNTIL


def _stop_hotspot(grace_seconds=120, timeout=10):
    hotspot = _current_hotspot_state()
    if not hotspot.get("active"):
        return {"ok": True, "wasActive": False}
    _defer_auto_hotspot(grace_seconds)
    out, rc = _nmcli("con", "down", "Hotspot", timeout=timeout)
    if rc != 0:
        return {"ok": False, "error": out or "Failed to stop hotspot", "wasActive": True}
    return {"ok": True, "wasActive": True}


def _start_hotspot_with_saved_config():
    iface = _wifi_iface()
    if not iface:
        return {"ok": False, "error": "No WiFi interface found"}
    cfg = _load_hotspot_cfg()
    ssid = (cfg.get("ssid") or "Atlas-Hotspot").strip()
    password = (cfg.get("password") or "").strip()
    if not ssid:
        return {"ok": False, "error": "SSID required"}
    if len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters (save your config first)"}
    _save_hotspot_cfg(ssid, password)
    _nmcli("con", "delete", "Hotspot")
    # Brief pause so NetworkManager can process the delete before we recreate.
    atlas_blocking.sleep(0.5)
    out, rc = _nmcli(
        "dev", "wifi", "hotspot",
        "ifname", iface, "ssid", ssid, "password", password,
        timeout=25,
    )
    if rc != 0:
        return {"ok": False, "error": out or "Failed to start hotspot"}
    return {"ok": True, "ssid": ssid}


def _restore_hotspot_access(reason=""):
    wifi = _current_lan_wifi_state()
    iface = (wifi or {}).get("iface") or _wifi_iface()
    if iface:
        _nmcli("dev", "disconnect", iface, timeout=10)
    result = _start_hotspot_with_saved_config()
    if reason:
        if result.get("ok"):
            logger.warning("Restored hotspot access after %s", reason)
        else:
            logger.warning("Failed to restore hotspot access after %s: %s", reason, result.get("error"))
    return result


def _wait_for_wifi_online(expected_ssid, timeout=20, interval=2):
    deadline = time.time() + max(2, timeout)
    last_lan = None
    last_connectivity = _network_connectivity_state()
    while time.time() < deadline:
        lan = _current_lan_wifi_state()
        connectivity = _network_connectivity_state()
        if lan:
            last_lan = lan
        last_connectivity = connectivity
        if lan and (not expected_ssid or lan.get("ssid") == expected_ssid) and connectivity.get("online"):
            return lan, connectivity
        atlas_blocking.sleep(max(1, interval))
    return last_lan or _current_lan_wifi_state(), last_connectivity or _network_connectivity_state()


def _wait_for_hotspot_release(iface=None, timeout=10, interval=1):
    deadline = time.time() + max(2, timeout)
    while time.time() < deadline:
        hotspot = _current_hotspot_state()
        device = _wifi_device_state(iface) if iface else None
        connection = ((device or {}).get("connection") or "").lower()
        if not hotspot.get("active") and "hotspot" not in connection:
            return True
        atlas_blocking.sleep(max(0.5, interval))
    return False


def _perform_wifi_connect(ssid, password="", stop_hotspot=True, require_online=True):
    if not ssid:
        return {"ok": False, "error": "ssid required"}
    iface = _wifi_iface()
    hotspot = _current_hotspot_state()
    hotspot_was_active = bool(stop_hotspot and hotspot.get("active"))
    # Grace must cover the full connect (35 s) + verify (30 s) window so the
    # failover loop does not race and restore the hotspot mid-operation.
    _defer_auto_hotspot(90)
    if hotspot_was_active:
        stopped = _stop_hotspot(grace_seconds=90, timeout=10)
        if not stopped.get("ok"):
            return {"ok": False, "error": stopped.get("error") or "Failed to stop hotspot"}
        _wait_for_hotspot_release(iface=iface, timeout=10, interval=1)
    args = ["dev", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    if iface:
        args += ["ifname", iface]
    out = ""
    rc = -1
    attempts = 3 if hotspot_was_active else 2
    for attempt in range(attempts):
        if attempt > 0:
            if iface:
                _nmcli("dev", "disconnect", iface, timeout=10)
            atlas_blocking.sleep(min(3, attempt + 1))
        out, rc = _nmcli(*args, timeout=35)
        if rc == 0:
            break
    if rc != 0:
        restored = _restore_hotspot_access(f"failed Wi-Fi join to {ssid}") if hotspot_was_active else {"ok": False}
        error = out or "Connection failed"
        if restored.get("ok"):
            error += " Atlas hotspot was restored so you can retry setup."
        return {
            "ok": False,
            "error": error,
            "hotspotRestored": bool(restored.get("ok")),
        }

    lan, connectivity = _wait_for_wifi_online(ssid, timeout=30, interval=2)
    # If we got no IP at all the connect truly failed — restore hotspot so the
    # user can retry via the setup UI.
    if not lan:
        restored = _restore_hotspot_access(f"no IP on {ssid}") if hotspot_was_active else {"ok": False}
        error = f"Connected to {ssid} but could not obtain an IP address."
        if restored.get("ok"):
            error += " Atlas returned to hotspot mode so you can retry."
        return {
            "ok": False,
            "error": error,
            "connectivity": connectivity,
            "hotspotRestored": bool(restored.get("ok")),
        }

    ip = _normalize_ip_value(lan.get("ip"))
    lan["kind"] = lan.get("kind") or "wifi"
    # A link-local lease (169.254/16) means DHCP failed outright — the address
    # is not routable, so the join didn't really succeed.  Drop it, stop
    # auto-rejoining it, and fall back to the hotspot instead of reporting a
    # hollow "success".  (CGNAT/100.64 addresses are NOT rejected here; see
    # _lan_ip_unreachable — they are valid local networks.)
    if _lan_ip_unreachable(ip):
        _set_connection_autoconnect(ssid, False)
        restored = _restore_hotspot_access(f"no routable IP on {ssid} ({ip})")
        error = (
            f"Connected to {ssid}, but DHCP failed — it only assigned a "
            f"link-local address ({ip}) that is not routable, so Atlas has no "
            f"real network."
        )
        if restored.get("ok"):
            error += " Atlas returned to hotspot mode so you stay reachable."
        else:
            error += " Reconnect to Atlas over USB (https://192.168.55.1) and retry."
        return {
            "ok": False,
            "error": error,
            "ip": ip,
            "lan": lan,
            "connectivity": connectivity,
            "isolated": True,
            "hotspotRestored": bool(restored.get("ok")),
        }
    # If we have a LAN connection (IP obtained) but no internet, stay on the LAN
    # rather than restoring the hotspot.  Atlas is reachable via the local
    # network; forcing a hotspot restore when the user deliberately chose a
    # LAN without internet (e.g. a local mesh or field network) just breaks things.
    limited = require_online and connectivity.get("checked") and not connectivity.get("online")
    if limited:
        logger.info(
            "Connected to %s (IP %s) but no internet (%s); staying on LAN",
            ssid, ip, connectivity.get("state") or "unknown",
        )
    _save_lan_hint(ssid, ip)
    # Force avahi to re-announce on the freshly bound interface.  Without this
    # the initial mDNS announcement timing is non-deterministic and can land
    # inside the 3 s gap between mobile-app discovery passes, causing the
    # apps' DNS-SD browse to miss Atlas's first appearance on the new LAN.
    _avahi_reload_quiet()
    # Bump the LAN beacon to 1 Hz for 90 s so the mobile apps' UDP listener
    # picks up Atlas's new IP immediately — this is the primary fast path on
    # real devices; emulators get the same descriptor via probe-reply.
    if lan_beacon is not None:
        try:
            lan_beacon.announce_burst(90.0)
        except Exception as e:
            logger.debug("lan_beacon.announce_burst failed: %s", e)
    return {
        "ok": True,
        "limited": limited,
        "message": (
            f"Connected to {ssid} (no internet detected — local access only)."
            if limited else out
        ),
        "ip": ip,
        "lan": lan,
        "accessUrls": _preferred_local_urls(lan),
        "connectivity": connectivity,
        "hotspotStopped": hotspot_was_active,
    }


def _avahi_reload_quiet():
    """Best-effort avahi-daemon reload to refresh mDNS announcements.
    Runs as a background subprocess so the WiFi-switch response isn't blocked
    on the (sometimes 1-2 s) reload.  Failures are logged but never raised —
    avahi may not be installed in dev environments and the apps still have
    subnet-scan fallback in that case."""
    def _run():
        out, rc = atlas_helper.run("avahi.reload", timeout=15)
        if rc != 0:
            logging.info("avahi reload skipped: %s", out[:120])
    threading.Thread(target=_run, daemon=True, name="avahi-reload").start()

def _candidate_mobile_hosts():
    """HTTPS URLs this Atlas answers on, numeric addresses first.

    Chromium WebView cannot resolve .local names, so IP literals lead. There
    are no plain-HTTP URLs: the app talks TLS only and pins this device's key
    (see device_identity), so an address change never weakens anything.
    """
    ip_hosts = []
    try:
        _, addrs = _discover_local_networks()
        for addr in addrs:
            a = str(addr)
            if not _is_local(a):
                continue
            ip_hosts.append(f"https://[{a}]" if ":" in a else f"https://{a}")
    except Exception:
        pass
    hostname = device_identity.device().get("hostname") or "atlas"
    names = [f"https://{hostname}.local"]
    if hostname != "atlas":
        names.append("https://atlas.local")
    return ip_hosts + names


def _mobile_bootstrap_manifest():
    """What discovery (BLE, UDP beacon, /api/mobile/bootstrap) tells a phone.

    Enough to find, identify and pin this Atlas — never Wi-Fi or hotspot
    credentials, never anything personal.
    """
    import atlas_version
    dev = device_identity.device()
    hosts = _candidate_mobile_hosts()
    return {
        "device": {
            "id": dev.get("device_id"),
            "name": dev.get("name") or "Atlas",
            "shortName": dev.get("short") or "ATLS",
        },
        "api": {
            "version": 2,
            "preferredBaseUrl": hosts[0] if hosts else None,
            "baseUrls": hosts,
            "bootstrapPath": "/api/mobile/bootstrap",
        },
        "tls": {"spkiSha256": dev.get("tls_spki_sha256")},
        "pairing": {"required": pairing.required()},
        "software": {"version": atlas_version.version()},
        "generatedAt": int(time.time()),
    }


_NMCLI_MUTATING = {"connect", "hotspot", "disconnect", "down", "delete", "modify", "up", "add"}
_WIFI_LIST_PREFIX = ["--escape", "yes", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]


def _nmcli_privileged(arg_list, timeout):
    """Map the few privileged nmcli invocations Atlas makes onto atlas-helper
    verbs. Returns (output, rc), or None when the command is read-only.

    The service account has no sudo; the helper validates every argument and
    receives passwords in the request body, never on a command line.
    """
    a = arg_list
    if a[:2] == ["con", "modify"] and len(a) == 5 and a[3] == "connection.autoconnect":
        return atlas_helper.run("net.set_autoconnect", name=a[2], enabled=(a[4] == "yes"))
    if a == ["con", "down", "Hotspot"]:
        return atlas_helper.run("net.hotspot_down")
    if a == ["con", "delete", "Hotspot"]:
        return "", 0          # net.hotspot_up replaces the profile itself
    if a[:3] == ["dev", "wifi", "hotspot"]:
        opts = dict(zip(a[3::2], a[4::2]))
        return atlas_helper.run("net.hotspot_up", timeout=timeout + 10, iface=opts.get("ifname"),
                                ssid=opts.get("ssid"), password=opts.get("password"))
    if a[:2] == ["dev", "disconnect"] and len(a) == 3:
        return atlas_helper.run("net.disconnect", iface=a[2])
    if a[:3] == ["dev", "wifi", "connect"] and len(a) >= 4:
        opts = dict(zip(a[4::2], a[5::2]))
        return atlas_helper.run("net.wifi_connect", timeout=timeout + 15, ssid=a[3],
                                password=opts.get("password"), iface=opts.get("ifname"),
                                timeout_s=timeout)
    if a[:len(_WIFI_LIST_PREFIX)] == _WIFI_LIST_PREFIX:
        rest = a[len(_WIFI_LIST_PREFIX):]
        opts = dict(zip(rest[::2], rest[1::2]))
        out, rc = atlas_helper.run("net.wifi_list", timeout=timeout + 10, iface=opts.get("ifname"),
                                   rescan=opts.get("--rescan") == "yes")
        if rc != 126:          # helper present: its answer stands
            return out, rc
        return None            # developer checkout: try an unprivileged scan
    if any(x in _NMCLI_MUTATING for x in a):
        logging.warning("_nmcli: refusing unrecognised privileged nmcli call %s", a[:3])
        return "operation not permitted", 1
    return None


def _nmcli(*args, timeout=20):
    """Run nmcli without stalling the gevent hub.

    Read-only queries run as the service account. The handful of changes Atlas
    makes (join Wi-Fi, run the hotspot, disconnect, autoconnect) go through
    atlas-helper — see _nmcli_privileged(). subprocess is NOT monkey-patched
    (see the patch_all() call at the top of this file), so atlas_blocking runs
    nmcli on gevent's native thread pool when called from the hub.
    """
    arg_list = list(args)
    privileged = _nmcli_privileged(arg_list, timeout)
    if privileged is not None:
        return privileged
    try:
        r = atlas_blocking.run(["nmcli"] + arg_list, capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip()
        err = r.stderr.strip()
        return (out + ("\n" + err if err else "")).strip(), r.returncode
    except Exception as e:
        logging.warning("_nmcli exception (%s): %s", args[:3], e)
        return str(e), -1

def _wifi_iface():
    """Return the first active WiFi interface name."""
    out, _ = _nmcli("-t", "-f", "DEVICE,TYPE", "dev")
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    return None

@app.route("/api/wifi/status")
def api_wifi_status():
    iface = _wifi_iface()
    current = _current_lan_link_state()
    return jsonify({
        "iface": iface,
        "current": current,
        "accessUrls": _preferred_local_urls(current),
        "connectivity": _network_connectivity_state(),
        "wifiSwitch": _wifi_switch_snapshot(),
    })

@app.route("/api/wifi/my_ips")
def api_wifi_my_ips():
    """Return all IPv4 addresses currently bound to active network interfaces.
    Called by the Android app just before a LAN switch while the hotspot is
    still up, so the app can pre-cache Atlas's real LAN IPs and skip the full
    subnet scan on dual-radio hardware."""
    try:
        _, addrs = _discover_local_networks()
        ips = [str(a) for a in addrs if _is_local(str(a))]
    except Exception:
        ips = []
    urls = []
    for ip in ips:
        urls.append(f"https://{ip}")
    return jsonify({"ips": ips, "urls": urls})


@app.route("/api/wifi/networks")
def api_wifi_networks():
    iface = _wifi_iface()
    base = ["--escape", "yes", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]
    # Skip forced rescans when a connect/hotspot operation holds the ops lock.
    # An active --rescan yes competes with nmcli connect on the same interface
    # and can cause the first connect attempt to fail.
    ops_busy = not _WIFI_OPS_LOCK.acquire(blocking=False)
    if not ops_busy:
        _WIFI_OPS_LOCK.release()
    attempts = []
    # Try the freshest/most specific scan first, then progressively relax.
    if iface:
        if not ops_busy:
            attempts.append(base + ["ifname", iface, "--rescan", "yes"])
        attempts.append(base + ["ifname", iface])
    if not ops_busy:
        attempts.append(base + ["--rescan", "yes"])
    attempts.append(base)

    out = ""
    rc = -1
    used_args = None
    for args in attempts:
        out, rc = _nmcli(*args, timeout=30)
        if rc == 0:
            used_args = args
            break
    if rc != 0:
        logging.warning("Wi-Fi scan failed on %s after %d attempts: %s", iface or "auto", len(attempts), out[:300])
        return jsonify({"error": out or "Wi-Fi scan failed", "networks": []}), 503
    logging.info("Wi-Fi scan ok on %s via %s", iface or "auto", " ".join(used_args or attempts[-1]))
    networks = []
    seen = set()
    for line in out.splitlines():
        # nmcli --escape yes escapes ':' in values as '\:', split on bare ':'
        parts = line.split(":")
        if len(parts) < 4:
            continue
        in_use = parts[0].strip() == "*"
        ssid = parts[1].replace("\\:", ":").strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(parts[2].strip())
        except Exception:
            signal = 0
        security = parts[3].strip()
        networks.append({"ssid": ssid, "signal": signal,
                         "security": security, "in_use": in_use})
    networks.sort(key=lambda n: (-int(n["in_use"]), -n["signal"]))
    return jsonify({"networks": networks})

@app.route("/api/wifi/connect", methods=["POST"])
@limiter.limit("10 per minute")
def api_wifi_connect():
    data = request.json or {}
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    stop_hotspot = bool(data.get("stopHotspot", True))
    background = bool(data.get("background", False))
    if not ssid:
        return jsonify({"error": "ssid required"}), 400
    # Single-interface hardware (one WiFi radio) cannot run AP + STA simultaneously.
    # If the hotspot is active and the caller asked to keep it (stopHotspot=False),
    # NetworkManager will force the interface out of AP mode anyway — killing the
    # hotspot mid-request so the HTTP response never reaches the phone.
    # Upgrade to background=True + stop_hotspot=True so the 200 is delivered first.
    if not stop_hotspot and _current_hotspot_state().get("active"):
        stop_hotspot = True
        background = True
    hotspot_active = bool(stop_hotspot and _current_hotspot_state().get("active"))
    if background and hotspot_active:
        hint_ip = _get_lan_hint(ssid)
        hint_lan = {"ip": hint_ip} if hint_ip else {"ip": None}
        hint_urls = _preferred_local_urls(hint_lan)
        _set_wifi_switch_state(
            ssid=ssid,
            pending=True,
            ok=None,
            message=f"Atlas is switching to {ssid}.",
            result={
                "lan": {"kind": "wifi", "ssid": ssid},
                "accessUrls": hint_urls,
                "hintIp": hint_ip,
            },
        )

        def _run_background_connect():
            with _WIFI_OPS_LOCK:
                result = _perform_wifi_connect(ssid, password=password, stop_hotspot=stop_hotspot, require_online=True)
            _set_wifi_switch_state(
                ssid=ssid,
                pending=False,
                ok=bool(result.get("ok")),
                message=result.get("message") or result.get("error") or "",
                result=result,
            )
            if result.get("ok"):
                logger.info("Background Wi-Fi setup joined %s (limited=%s)", ssid, result.get("limited"))
            else:
                logger.warning("Background Wi-Fi setup failed for %s: %s", ssid, result.get("error"))

        # Pre-arm the LAN beacon's burst window: the actual nmcli switch can
        # take 10–20 s before _perform_wifi_connect's success path runs, and
        # we want Atlas already shouting by the time it rebinds on the new
        # LAN so the mobile UDP listener catches the very first heartbeat.
        if lan_beacon is not None:
            try:
                lan_beacon.announce_burst(120.0)
            except Exception as e:
                logger.debug("lan_beacon.announce_burst (pre-switch) failed: %s", e)
        threading.Thread(target=_run_background_connect, daemon=True, name="wifi-setup-connect").start()
        return jsonify({
            "ok": True,
            "pending": True,
            "lan": {"kind": "wifi", "ssid": ssid},
            "accessUrls": hint_urls,
            "hintIp": hint_ip,
            "message": f"Atlas is switching to {ssid}. If it cannot connect, it will fall back to hotspot mode.",
        })

    with _WIFI_OPS_LOCK:
        result = _perform_wifi_connect(ssid, password=password, stop_hotspot=stop_hotspot, require_online=True)
    _set_wifi_switch_state(
        ssid=ssid,
        pending=False,
        ok=bool(result.get("ok")),
        message=result.get("message") or result.get("error") or "",
        result=result,
    )
    if not result.get("ok"):
        return jsonify(result), 500
    return jsonify(result)

@app.route("/api/wifi/disconnect", methods=["POST"])
def api_wifi_disconnect():
    if not _confirmed():
        return _needs_confirmation("disconnect Atlas from this network")
    iface = _wifi_iface()
    if not iface:
        return jsonify({"error": "No WiFi interface found"}), 500
    _defer_auto_hotspot(60)
    with _WIFI_OPS_LOCK:
        out, rc = _nmcli("dev", "disconnect", iface, timeout=10)
    if rc != 0:
        return jsonify({"error": out or "Disconnect failed"}), 500
    _set_wifi_switch_state(ssid="", pending=False, ok=None, message="", result=None)
    return jsonify({"ok": True})

@app.route("/api/hotspot/status")
def api_hotspot_status():
    return jsonify(_current_hotspot_state())


@app.route("/api/hotspot/credentials")
def api_hotspot_credentials():
    """The hotspot SSID and password, for the owner's Wi-Fi settings page."""
    resp = jsonify(_current_hotspot_state(include_password=True))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/api/mobile/bootstrap")
def api_mobile_bootstrap():
    return jsonify(_mobile_bootstrap_manifest())

@app.route("/api/mobile/status")
def api_mobile_status():
    if not mobile_bridge:
        return jsonify({"running": False, "dbusAvailable": False, "lastError": "bridge not started"})
    return jsonify(mobile_bridge.status())

@app.route("/api/mobile/handoff")
def api_mobile_handoff():
    """Lightweight liveness probe for post-LAN-switch reconnect.
    Android polls this immediately after the hotspot drops to confirm it has
    found Atlas on the new network. Returns current access URLs so the app can
    update its saved URL list with the real IP."""
    hosts = _candidate_mobile_hosts()
    return jsonify({"ok": True, "accessUrls": hosts})

@app.route("/api/hotspot/config", methods=["POST"])
@limiter.limit("20 per minute")
def api_hotspot_config():
    """Save hotspot SSID and password without starting the hotspot."""
    if not _confirmed():
        return _needs_confirmation("change Atlas's Wi-Fi name or password")
    data = request.json or {}
    import re as _re
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    if not ssid:
        return jsonify({"error": "SSID required"}), 400
    if len(ssid) > 32:
        return jsonify({"error": "SSID too long (max 32 characters)"}), 400
    if not _re.match(r'^[\w\s\-\.@#!]+$', ssid):
        return jsonify({"error": "SSID contains invalid characters"}), 400
    if password and len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if len(password) > 63:
        return jsonify({"error": "Password too long (max 63 characters)"}), 400
    _save_hotspot_cfg(ssid, password)
    return jsonify({"ok": True})

@app.route("/api/hotspot/start", methods=["POST"])
@limiter.limit("10 per minute")
def api_hotspot_start():
    data = request.json or {}
    import re as _re
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    # Fall back to saved config if caller didn't supply values
    if not ssid or not password:
        cfg = _load_hotspot_cfg()
        ssid = ssid or cfg.get("ssid", "Atlas-Hotspot")
        password = password or cfg.get("password", "")
    if not ssid:
        return jsonify({"error": "SSID required"}), 400
    if len(ssid) > 32:
        return jsonify({"error": "SSID too long (max 32 characters)"}), 400
    if not _re.match(r'^[\w\s\-\.@#!]+$', ssid):
        return jsonify({"error": "SSID contains invalid characters"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters (save your config first)"}), 400
    if len(password) > 63:
        return jsonify({"error": "Password too long (max 63 characters)"}), 400
    _defer_auto_hotspot(30)
    _save_hotspot_cfg(ssid, password)
    with _WIFI_OPS_LOCK:
        result = _start_hotspot_with_saved_config()
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "Failed to start hotspot"}), 500
    _set_wifi_switch_state(ssid="", pending=False, ok=None, message="", result=None)
    return jsonify(result)

@app.route("/api/hotspot/stop", methods=["POST"])
def api_hotspot_stop():
    if not _confirmed():
        return _needs_confirmation("turn off Atlas's Wi-Fi hotspot")
    with _WIFI_OPS_LOCK:
        result = _stop_hotspot(grace_seconds=120, timeout=10)
    if not result.get("ok"):
        return jsonify({"error": result.get("error") or "Failed to stop hotspot"}), 500
    return jsonify(result)


def _wifi_failover_loop():
    """Keep Atlas reachable by re-enabling hotspot when there is no LAN at all.

    The loop only triggers hotspot restore when Atlas has NO active LAN connection
    (no IP address).  A LAN connection without internet is still useful — the
    device is reachable on the local network — so we leave it alone and do not
    force a hotspot restore just because the WAN is down.
    """
    missing_lan_since = None
    lan_with_hotspot_since = None
    last_start_attempt = 0.0
    last_state = None
    while True:
        time.sleep(8)
        try:
            hotspot = _current_hotspot_state()
            lan = _current_lan_link_state()
            connectivity = _network_connectivity_state()
            now = time.time()
            state = {
                "lan": bool(lan),
                "lan_name": (lan or {}).get("ssid", ""),
                "lan_kind": (lan or {}).get("kind", ""),
                "hotspot": bool(hotspot.get("active")),
                "internet": bool(connectivity.get("online")),
                "internet_checked": bool(connectivity.get("checked")),
                "connectivity": connectivity.get("state") or "unknown",
                "allowed": _auto_hotspot_allowed(),
            }
            if state != last_state:
                logger.info(
                    "Wi-Fi failover state: lan=%s kind=%s name=%s hotspot=%s internet=%s connectivity=%s allowed=%s",
                    state["lan"],
                    state["lan_kind"] or "-",
                    state["lan_name"] or "-",
                    state["hotspot"],
                    state["internet"],
                    state["connectivity"],
                    state["allowed"],
                )
                last_state = state

            lan_ip = (lan or {}).get("ip")
            lan_unreachable = bool(lan) and _lan_ip_unreachable(lan_ip)
            if lan and not lan_unreachable:
                # Reachable LAN — Atlas is reachable. Leave the hotspot alone so
                # mobile devices already connected to it stay connected.
                missing_lan_since = None
                lan_with_hotspot_since = None
                continue

            if not lan:
                lan_with_hotspot_since = None

            # No *reachable* LAN past this point: either no link at all, or a
            # link-local-only lease (DHCP failed) that gives Atlas no real network.
            if hotspot.get("active"):
                missing_lan_since = None
                continue
            if not state["allowed"]:
                missing_lan_since = None
                continue
            if missing_lan_since is None:
                missing_lan_since = now
                if lan_unreachable:
                    logger.warning(
                        "LAN %s has an unreachable address (%s); hotspot fallback timer started",
                        (lan or {}).get("ssid") or "-", lan_ip or "-",
                    )
                else:
                    logger.warning("LAN link lost; hotspot fallback timer started")
                continue
            if now - missing_lan_since < 30:
                continue
            if now - last_start_attempt < 30:
                continue
            # Try to acquire the ops lock non-blocking so we don't race with
            # an in-progress connect/disconnect initiated by an API call.
            if not _WIFI_OPS_LOCK.acquire(blocking=False):
                continue
            last_start_attempt = now
            try:
                if lan_unreachable:
                    # Link-local-only lease (DHCP failed): drop it and stop
                    # auto-rejoining, then bring the hotspot back so Atlas stays
                    # reachable.
                    _set_connection_autoconnect((lan or {}).get("ssid"), False)
                    result = _restore_hotspot_access(
                        f"no routable IP on LAN {(lan or {}).get('ssid') or ''} ({lan_ip or '?'})"
                    )
                else:
                    result = _start_hotspot_with_saved_config()
            finally:
                _WIFI_OPS_LOCK.release()
            if result.get("ok"):
                logger.info("Auto Wi-Fi fallback enabled hotspot SSID %s", result.get("ssid"))
                missing_lan_since = None
            else:
                logger.warning("Auto Wi-Fi fallback failed to start hotspot: %s", result.get("error"))
        except Exception as e:
            logger.warning("Auto Wi-Fi fallback loop error: %s", e)


# ------------------------------------------------------------------ main
def main():
    global mesh, gps_manager, ai_manager, routing_node, nav_node, mobile_bridge, lan_beacon

    parser = argparse.ArgumentParser(description="Atlas Control")
    parser.add_argument("--port", default="AUTO", help="Meshtastic serial port (AUTO = scan)")
    parser.add_argument("--gps-port", default=None, help="GPS serial port (default: AUTO = scan)")
    parser.add_argument("--gps-baud", default=None, type=int, help="GPS baud rate (default: 9600)")
    # nginx is the only network entry point; Flask listens on loopback.
    parser.add_argument("--host", default=os.environ.get("ATLAS_BIND", "127.0.0.1"),
                        help="Listen address (default 127.0.0.1; nginx fronts the network)")
    parser.add_argument("--web-port", default=None, type=int, help="Web port")
    parser.add_argument("--demo", action="store_true", help="Run with demo data")
    args = parser.parse_args()

    db.init_db()
    if pairing.bind_identity():
        # A new identity (storage cloned from another unit): the old unit's
        # pairings are gone; its hotspot and network hints must go too.
        for path in (_HOTSPOT_CFG_PATH, _LAN_HINTS_PATH):
            try:
                os.remove(path)
            except OSError:
                pass
        logger.warning("This Atlas has a new identity; devices paired with the previous one must pair again.")

    # Per-subsystem kill switches so we can bisect what's pinning CPU.
    # Touch the corresponding file under data/ to disable, remove to re-enable.
    def _kill(name: str) -> bool:
        return os.path.exists(os.path.join(atlas_paths.DATA_DIR, name))

    _minimal       = _kill("minimal_mode")
    _disable_ai    = _minimal or _kill("ai_disabled")
    _disable_ble   = _minimal or _kill("ble_disabled")
    _disable_beacon= _minimal or _kill("beacon_disabled")
    _disable_bg    = _minimal or _kill("bg_disabled")     # prune, jobs, updates, wifi failover
    if _minimal:
        logger.warning("Minimal mode active — every background subsystem is gated off.")

    settings = db.get_app_settings()
    ai_settings = db.ai_get_settings()
    ai_updates = {}
    # The model needs no fixing up here: a saved model that is no longer
    # offered falls back to the standard one at warmup (with a notice), and
    # each model's context/sampling come from model_profiles.
    # Migrate the embedder to qwen3-embedding:0.6b. nomic (768-dim) and qwen3
    # (1024-dim) vectors are incompatible, so switching wipes stored doc
    # embeddings; the background re-embed thread regenerates them on startup.
    if ai_settings.get("embed_model") in (None, "", "nomic-embed-text"):
        if ai_settings.get("embed_model") != "qwen3-embedding:0.6b":
            ai_updates["embed_model"] = "qwen3-embedding:0.6b"
            _conn = db.get_db()
            _conn.execute("UPDATE ai_documents SET embedding=NULL")
            _conn.commit()
            db._ai_docs_changed()
    if ai_updates:
        db.ai_set_settings(ai_updates)
    # The port nginx proxies to is fixed; a stored web_port setting (writable
    # through the settings API) must not be able to move or break it.
    web_port = args.web_port or int(os.environ.get("ATLAS_WEB_PORT", "5000"))
    mesh_port = args.port if args.port != "AUTO" else settings.get("serial_port", "AUTO")
    gps_port = args.gps_port or settings.get("gps_port", "AUTO")
    gps_baud = args.gps_baud or int(settings.get("gps_baud", 9600))

    mesh = MeshManager(port=mesh_port, socketio=socketio)

    # Kill switch: if /atlas_data/atlas-control/data/mesh_disabled exists,
    # don't try to talk to the radio. The Heltec V4 firmware can spew
    # continuous bytes (over USB-CDC or the UART) that aren't valid Meshtastic
    # frames; the library's reader thread then pulls those bytes one at a time
    # in a tight Python loop and pins a core, starving the gevent WSGI worker.
    # Touch the file to disable, remove it to re-enable, then restart.
    _mesh_kill = os.path.join(atlas_paths.DATA_DIR, "mesh_disabled")
    if args.demo:
        mesh.port = "DEMO"
        mesh._load_demo_data()
        mesh.connected = True
        mesh.my_node_id = "!a1b2c3d4"
    elif os.path.exists(_mesh_kill):
        logger.warning("Mesh disabled via kill switch (%s); skipping radio connect.", _mesh_kill)
    else:
        threading.Thread(target=mesh.connect, daemon=True, name="mesh-connect").start()

    gps_manager = GpsManager(port=gps_port, baud=gps_baud, socketio=socketio, mesh_manager=mesh)
    # Restore GPS sharing config from database
    gps_manager.set_share_config(
        mode         = settings.get("gps_share_mode", "off"),
        nodes_str    = settings.get("gps_share_nodes", ""),
        interval     = int(settings.get("gps_share_interval", "30")),
        channels_str = settings.get("gps_share_channels", ""),
    )
    _gps_kill = os.path.join(atlas_paths.DATA_DIR, "gps_disabled")
    if os.path.exists(_gps_kill):
        logger.warning("GPS disabled via kill switch (%s); skipping reader.", _gps_kill)
    else:
        gps_manager.start()

    # ── Routing + navigation ──────────────────────────────────────────────────
    routing_node = RoutingNode(states_dir=_STATES_DIR, active_json=_ACTIVE_PATH)
    nav_node = NavigationNode(socketio=socketio, routing_node=routing_node,
                              mesh_manager=mesh)

    def _on_navigation_ended():
        """Reload Ray when a trip ends by arrival.

        Arrival never routes through api_nav_cancel(), so if the memory valve in
        api_nav_start() had unloaded Ray, completing a trip normally would leave
        it unloaded until the user explicitly cancelled or restarted.
        _warmup() is idempotent, so calling it when Ray is already resident is
        harmless.
        """
        if ai_manager:
            threading.Thread(target=ai_manager._warmup, daemon=True).start()

    nav_node.on_navigation_ended = _on_navigation_ended
    nav_node.start()

    # Feed every GPS fix into the navigation engine so step-advance, off-route
    # detection, and rerouting all work automatically.
    gps_manager.on_fix(lambda fix: nav_node.update_position(
        fix["latitude"], fix["longitude"],
        fix.get("speed"), fix.get("heading")))

    # Prewarm OSRM for the current region if we already have a GPS fix
    # (e.g. after a restart with cached position).
    _boot_fix = gps_manager.current_fix
    if _boot_fix:
        threading.Thread(
            target=routing_node.prewarm_for_position,
            args=(_boot_fix["latitude"], _boot_fix["longitude"]),
            daemon=True,
        ).start()

    ai_manager = AIManager(mesh_manager=mesh, socketio=socketio, gps_manager=gps_manager)
    if _disable_ai:
        logger.warning("AI manager disabled (ai_disabled or minimal_mode).")
    else:
        ai_manager.start()
    mobile_bridge = AtlasMobileBridge(_mobile_bootstrap_manifest, logger=logger)
    if _disable_ble:
        logger.warning("Mobile BLE bridge disabled (ble_disabled or minimal_mode).")
    else:
        mobile_bridge.start()

    # UDP "shout-and-receive" beacon. Replies to ATLAS-DISCOVER probes on
    # port 5050 and broadcasts unsolicited beacons so the mobile setup
    # wizard can find Atlas after a hotspot→LAN switch even when avahi
    # re-announce is delayed on the single-radio Jetson.
    lan_beacon = LanBeacon(_mobile_bootstrap_manifest, logger=logger)
    if _disable_beacon:
        logger.warning("LAN beacon disabled (beacon_disabled or minimal_mode).")
    else:
        lan_beacon.start()

    def _nightly_prune():
        import time as _time
        # Run once shortly after startup so a crash-recovery restart immediately
        # reclaims space, then repeat every 24 h.
        _time.sleep(60)
        while True:
            try:
                db.prune_old_data()
                removed = retention.apply()
                audit_log.prune()
                logger.info("Data prune complete (%s)", ", ".join(f"{k} {v}" for k, v in removed.items()) or "nothing due")
            except Exception as e:
                logger.warning(f"Data prune failed: {e}")
            _time.sleep(86400)

    if _disable_bg:
        logger.warning("Background loops disabled (bg_disabled or minimal_mode).")
    else:
        threading.Thread(target=_nightly_prune, daemon=True).start()
        threading.Thread(target=_scheduler_loop, daemon=True).start()
        # Update checks use gevent sockets, so they run in a greenlet.
        import gevent
        gevent.spawn(_update_background_loop)
        gevent.spawn(_stats_push_loop)
        threading.Thread(target=_wifi_failover_loop, daemon=True, name="wifi-failover").start()

    logger.info(f"Dashboard at http://{args.host}:{web_port}")
    socketio.run(app, host=args.host, port=web_port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == "__main__":
    main()
