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
import ast
import re
import json
import math
import time
import logging
import argparse
import operator
import threading
import subprocess
import socket
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

import database as db
import meshtastic
import meshtastic.util
from mesh_manager import MeshManager
from gps_node import GpsNode as GpsManager      # ground-up rewrite with UBX + Kalman + self-DR
from ai_manager import AIManager
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
_LAN_HINTS_PATH = os.path.join(os.path.dirname(__file__), "data", "wifi_hints.json")
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
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".secret_key")
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
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "static",
        "data",
        "nps_trails.db",
    )


def _hiking_db_mtime(db_path):
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return None

app.config["SECRET_KEY"] = _load_secret_key()

# Trust X-Real-IP / X-Forwarded-For from the nginx reverse proxy (127.0.0.1)
# so request.remote_addr shows the real client IP for VPN-IP tracking
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

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
        out = subprocess.check_output(["ip", "-o", "addr", "show", "up"], text=True, timeout=3)
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
            out = subprocess.check_output(["hostname", "-I"], text=True, timeout=2)
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


def _is_trusted_local_host(host):
    host = (host or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "atlas", "atlas.local") or host.endswith(".local"):
        return True
    try:
        return _is_local(host)
    except Exception:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = sockaddr[0]
        if _is_local(ip):
            return True
    return False


def _cors_origin_allowed(origin):
    """Restrict WebSocket origins to local/private network addresses only."""
    if not origin:
        return True
    try:
        from urllib.parse import urlparse
        host = urlparse(origin).hostname or ""
        return _is_trusted_local_host(host)
    except Exception:
        return False

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="gevent",
    http_compression=True,       # gzip-compress engine.io polling payloads
    compression_threshold=512,   # only compress payloads > 512 bytes
    ping_timeout=60,
    ping_interval=25,
)
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
    # Content-Security-Policy: allow only local sources; no external connections
    # unsafe-inline is required for the in-browser Babel/React SPA (no build step)
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "worker-src blob:; "
        "frame-ancestors 'none';"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    return response


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
_TILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "tiles")

# Pre-decoded 1×1 transparent PNG — returned for any missing topo tile so
# Leaflet shows the base map cleanly instead of broken-tile icons.
# Decoding at import time avoids a base64 decode on every missing-tile request.
import base64 as _b64
_TRANSPARENT_PNG = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
    "AABjkB6QAAAABJRU5ErkJggg=="
)

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")

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
        # Drop the Babel loader
        html = html.replace('<script src="/static/vendor/babel.min.js"></script>\n', "")
        # Replace the inline JSX block with the pre-compiled external script.
        # The block spans from <script type="text/babel"> … </script>.
        import re as _re
        html = _re.sub(
            r'<script type="text/babel">.*?</script>',
            '<script src="/static/app.compiled.js"></script>',
            html,
            flags=_re.DOTALL,
        )
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
        # Only recompile if index.html is newer than the compiled output
        if os.path.exists(_COMPILED_JS):
            if os.path.getmtime(_INDEX_HTML) <= os.path.getmtime(_COMPILED_JS):
                logger.info("JSX pre-compile: up-to-date, skipping")
                with _MOBILE_HTML_LOCK:
                    _MOBILE_HTML = _build_mobile_html()
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
    else:
        resp = send_file(os.path.join(os.path.dirname(__file__), "templates", "index.html"))
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
    return send_file(os.path.join(os.path.dirname(__file__), "templates", "tracker.html"))


# ------------------------------------------------------------------ API: device
@app.route("/api/device")
def api_device():
    info = mesh.get_device_info() if mesh else {"connected": False}
    # Stable identifier the mobile apps probe for during LAN discovery.
    # Without it, a port-5000 fingerprint sweep can't distinguish Atlas
    # from any other HTTP server that happens to 200 on /api/device.
    info["app"] = "atlas-control"
    return jsonify(info)

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
    return jsonify({
        "running": running,
        "ai_ready": ai_ready,
        "models": models,
        "loaded_models": loaded_models,
        "doc_count": doc_count,
        "chat_count": chat_count,
        "settings": settings,
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

@app.route("/api/ai/settings", methods=["PUT"])
def api_ai_settings_put():
    data = request.json or {}
    old_model = db.ai_get_settings().get("model")
    db.ai_set_settings(data)
    new_model = data.get("model")
    model_changed = new_model and new_model != old_model
    if model_changed and ai_manager:
        _captured_old = old_model  # capture before thread starts
        def _switch_model():
            ai_manager.unload(model=_captured_old)
            ai_manager._warmup()
        threading.Thread(target=_switch_model, daemon=True).start()
    return jsonify({"ok": True, "settings": db.ai_get_settings(), "warming_up": bool(model_changed)})

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

def _km_doc_passage_vectors(raw_embedding):
    """Extract the list of passage vectors from a stored document embedding.

    Mirrors ai_manager._get_doc_embeddings: documents are now embedded
    per-passage and stored as {"v":3,"chunks":[{"e":vector},…]}, but a legacy
    flat whole-doc vector (a bare JSON list) is still accepted so the knowledge
    map keeps drawing links across the embed-format migration. Returns a list of
    float vectors (one per passage), or [] if the embedding is missing/unusable.
    """
    if not raw_embedding:
        return []
    try:
        parsed = json.loads(raw_embedding)
    except Exception:
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get("chunks"), list):
        return [c["e"] for c in parsed["chunks"]
                if isinstance(c, dict) and c.get("e")]
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], (int, float)):
        return [parsed]
    return []


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


@app.route("/api/ai/knowledge-map")
def api_ai_knowledge_map():
    """Return nodes and high-cosine edges for the knowledge-map visualisation."""
    docs = db.ai_get_documents_with_embeddings()
    nodes = []
    # id -> list of unit-normalised passage vectors (so cosine == plain dot product)
    units_by_id = {}
    for doc in docs:
        slug, color, label = _km_category_for(doc.get("tags") or "", doc.get("title") or "")
        nodes.append({
            "id":       doc["id"],
            "title":    doc["title"],
            "category": slug,
            "cat_label": label,
            "color":    color,
        })
        units = []
        for v in _km_doc_passage_vectors(doc.get("embedding")):
            n = math.sqrt(sum(x * x for x in v))
            if n:
                units.append([x / n for x in v])
        if units:
            units_by_id[doc["id"]] = units

    # Cosine similarity between two docs = their best-matching passage pair (max
    # cosine), mirroring RAG's "a doc is scored by its single best chunk" rule.
    # Vectors are pre-normalised above, so each passage comparison is a dot
    # product. Every unordered pair is scored once into sim[].
    ids = list(units_by_id.keys())
    peers = {id_: [] for id_ in ids}
    sim = {}
    for i in range(len(ids)):
        id1 = ids[i]
        passages1 = units_by_id[id1]
        for j in range(i + 1, len(ids)):
            id2 = ids[j]
            best = 0.0
            for a in passages1:
                for b in units_by_id[id2]:
                    if len(a) != len(b):
                        continue
                    s = sum(x * y for x, y in zip(a, b))
                    if s > best:
                        best = s
            if best >= 0.55:
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

    return jsonify({"nodes": nodes, "edges": edges,
                    "categories": {k: {"label": v["label"], "color": v["color"]}
                                   for k, v in _KM_CATEGORIES.items()}})

# ------------------------------------------------------------------ API: System stats
@app.route("/api/system/stats")
def api_system_stats():
    return jsonify(system_stats.get_stats())

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
_PLACES_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static", "data", "places.db")

# "<thing> near me" → coarse category column in places.db.
_CATEGORY_SYNONYMS = [
    ("fuel", ("gas station", "petrol", "gasoline", "diesel", "fuel",
              "fill up", "charging station", "ev charger")),
    ("food", ("restaurant", "fast food", "place to eat", "somewhere to eat",
              "food", "diner", "coffee", "cafe", "pizza", "burger",
              "breakfast", "lunch", "dinner", "pub")),
    ("grocery", ("grocery", "groceries", "supermarket", "food store", "market")),
    ("finance", ("atm", "bank", "cash machine")),
    ("health", ("hospital", "emergency room", "urgent care", "clinic",
                "doctor", "pharmacy", "drugstore", "dentist", "medical")),
    ("lodging", ("hotel", "motel", "lodging", "place to stay", "campground",
                 "campsite", "camp site", "camping", "hostel")),
    ("emergency", ("police station", "police", "fire station", "sheriff")),
    ("facility", ("bathroom", "restroom", "toilet", "drinking water",
                  "post office", "rest area")),
    ("shop", ("hardware store", "shopping mall", "shopping", "store")),
]
_CAT_STOPWORDS = set((
    "gas station petrol gasoline diesel fuel fill up charging ev charger "
    "restaurant fast food place eat somewhere diner coffee cafe pizza burger "
    "breakfast lunch dinner pub grocery groceries supermarket store market atm "
    "bank cash machine hospital emergency room urgent care clinic doctor "
    "pharmacy drugstore dentist medical hotel motel lodging stay campground "
    "campsite camp site camping hostel police sheriff fire bathroom restroom "
    "toilet drinking water post office rest area shopping mall hardware "
    "near me around here nearby the and for"
).split())


def _detect_category(q_lower):
    for cat, cues in _CATEGORY_SYNONYMS:
        for cue in cues:
            if cue in q_lower:
                return cat
    return None


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
    _CITIES_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "static", "data", "us_cities.db")
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
    _NPS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "static", "data", "nps_trails.db")
    _LEGACY_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "static", "data", "trailheads.db")
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

_ACTIVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osrm_active.json")
_STATES_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osrm-data", "states")


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

    result = nav_node.start_navigation(
        from_lat, from_lon, to_lat, to_lon,
        profile=profile, destination_name=dest_name, waypoints=waypoints)

    # Prewarm routing for destination state
    if routing_node:
        threading.Thread(
            target=routing_node.prewarm_for_position,
            args=(to_lat, to_lon), daemon=True).start()

    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.route("/api/nav/navigate", methods=["DELETE"])
def api_nav_cancel():
    """Cancel active navigation."""
    if nav_node:
        nav_node.cancel_navigation()
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
    nps_db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static", "data", "nps_trails.db")
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
    legacy_db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "data", "trailheads.db")
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

    # ── Offline fallback: nearest city from local DB ──────────────────────────
    def _nearest_local(lat, lon):
        """Return display_name for the nearest city/trailhead in the local DB."""
        import sqlite3 as _sq
        deg = 0.5   # ~55 km bounding box each side
        for db_path, sql in [
            (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static", "data", "us_cities.db"),
             ("SELECT name, state_name, lat, lon FROM cities "
              "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
              "ORDER BY ((lat-?)*(lat-?) + (lon-?)*(lon-?)) LIMIT 1")),
            (os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static", "data", "trailheads.db"),
             ("SELECT name, state, lat, lon FROM trailheads "
              "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
              "ORDER BY ((lat-?)*(lat-?) + (lon-?)*(lon-?)) LIMIT 1")),
        ]:
            if not os.path.exists(db_path):
                continue
            try:
                con = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
                row = con.execute(sql, (lat-deg, lat+deg, lon-deg, lon+deg,
                                        lat, lat, lon, lon)).fetchone()
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
    return jsonify(db.get_app_settings())

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
    record = {
        "id":        device_id,
        "name":      name,
        "color":     data.get("color", "#3b82f6"),
        "latitude":  data.get("latitude"),
        "longitude": data.get("longitude"),
        "accuracy":  data.get("accuracy"),
        "altitude":  data.get("altitude"),
        "speed":     data.get("speed"),
        "heading":   data.get("heading"),
        "battery":   data.get("battery"),
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
            threading.Thread(target=ai_manager.unload, daemon=True).start()
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
@app.route("/api/device/config")
def api_device_config_get():
    if not mesh:
        return jsonify({"connected": False})
    return jsonify(mesh.get_device_config())

@app.route("/api/device/config/<section>", methods=["PUT"])
def api_device_config_put(section):
    ALLOWED = {"device", "lora", "position", "power", "bluetooth", "network"}
    if section not in ALLOWED:
        return jsonify({"error": f"Unknown section '{section}'"}), 400
    data = request.json or {}
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
import re as _update_re

_UPDATE_LOG_PATH = os.path.join(_BASE_DIR, "logs", "update.log")
_UPDATE_LAUNCHER = "/usr/local/sbin/atlas-update"
_UPDATE_UNIT = "atlas-update.service"
_UPDATE_CHECK_INTERVAL_SECONDS = 30 * 60
_UPDATE_CHECK_RETRY_SECONDS = 5 * 60
_UPDATE_CHECK_POLL_SECONDS = 60
# Wall-clock of the last in-app launch; lets /api/update/status distinguish
# "never ran" from "launched and died silently". Survives the failure case
# (the app only restarts when an update actually progresses).
_UPDATE_LAUNCHED_AT = [0.0]
_ANSI_RE = _update_re.compile(r"\x1b\[[0-9;]*m")
_UPDATE_CHECK_RUN_LOCK = threading.Lock()
_UPDATE_CHECK_STATE_LOCK = threading.Lock()
_UPDATE_CHECK_STATE = {
    "checking": False,
    "last_attempt_at": None,
    "last_check_error": None,
    "result": None,
}


def _update_git(*args, timeout=10):
    """Run a git command in the app checkout; returns (rc, stdout)."""
    try:
        r = subprocess.run(
            ["git", "-C", _BASE_DIR, *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return r.returncode, r.stdout.strip()
    except Exception as exc:
        logging.warning("update git %s failed: %s", args[:2], exc)
        return -1, ""


def _update_local_info():
    rc, head = _update_git("rev-parse", "--short", "HEAD")
    info = {
        "version": head if rc == 0 else None,
        "is_git_checkout": rc == 0,
        "installed_at": None,
    }
    try:
        with open(os.path.join(_BASE_DIR, ".atlas_installed"), encoding="utf-8") as f:
            parts = f.read().split()
            if len(parts) >= 2:
                info["installed_at"] = parts[1]
    except OSError:
        pass
    return info


def _update_unit_active():
    try:
        r = subprocess.run(
            ["systemctl", "is-active", _UPDATE_UNIT],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() in ("active", "activating")
    except Exception:
        return False


def _update_check_snapshot():
    """Return the public, immutable-enough view of the cached check state."""
    with _UPDATE_CHECK_STATE_LOCK:
        state = {
            "checking": _UPDATE_CHECK_STATE["checking"],
            "last_attempt_at": _UPDATE_CHECK_STATE["last_attempt_at"],
            "last_check_error": _UPDATE_CHECK_STATE["last_check_error"],
        }
        result = _UPDATE_CHECK_STATE.get("result")
        if result:
            state.update(result)
            state["commits"] = [dict(commit) for commit in result.get("commits", [])]
    return state


def _update_public_info():
    """Current local version plus the most recent background check result."""
    info = _update_local_info()
    info.update({
        "launcher_installed": os.path.exists(_UPDATE_LAUNCHER),
        "update_running": _update_unit_active(),
        "auto_check_interval_seconds": _UPDATE_CHECK_INTERVAL_SECONDS,
    })
    info.update(_update_check_snapshot())
    return info


def _finish_update_check(result=None, error=None):
    with _UPDATE_CHECK_STATE_LOCK:
        _UPDATE_CHECK_STATE["checking"] = False
        _UPDATE_CHECK_STATE["last_check_error"] = error
        if result is not None:
            _UPDATE_CHECK_STATE["result"] = result


def _automatic_update_check_due(snapshot, now):
    """Whether an automatic check is due, including a shorter failure retry."""
    if snapshot.get("last_check_error") and snapshot.get("last_attempt_at"):
        return now - snapshot["last_attempt_at"] >= _UPDATE_CHECK_RETRY_SECONDS
    if snapshot.get("checked_at"):
        return now - snapshot["checked_at"] >= _UPDATE_CHECK_INTERVAL_SECONDS
    return True


def _run_software_update_check(automatic=False):
    """Serialize update checks and return (payload, status)."""
    if automatic:
        _UPDATE_CHECK_RUN_LOCK.acquire()
    elif not _UPDATE_CHECK_RUN_LOCK.acquire(blocking=False):
        info = _update_public_info()
        info["error"] = "A software update check is already in progress."
        return info, 409
    try:
        return _run_software_update_check_locked(automatic=automatic)
    except Exception as exc:
        error = "Software update check failed unexpectedly."
        logger.exception("%s %s", error, exc)
        _finish_update_check(error=error)
        info = _update_public_info()
        info["error"] = error
        return info, 500
    finally:
        _UPDATE_CHECK_RUN_LOCK.release()


def _run_software_update_check_locked(automatic=False):
    """Fetch and compare origin/main while the caller holds the run lock."""
    now = int(time.time())
    if automatic and not _automatic_update_check_due(_update_check_snapshot(), now):
        return None, None

    info = _update_local_info()
    info.update({
        "launcher_installed": os.path.exists(_UPDATE_LAUNCHER),
        "update_running": _update_unit_active(),
        "auto_check_interval_seconds": _UPDATE_CHECK_INTERVAL_SECONDS,
    })
    if info["update_running"]:
        info.update(_update_check_snapshot())
        info["error"] = "A software update is already running."
        return info, 409

    with _UPDATE_CHECK_STATE_LOCK:
        _UPDATE_CHECK_STATE["checking"] = True
        _UPDATE_CHECK_STATE["last_attempt_at"] = now
        _UPDATE_CHECK_STATE["last_check_error"] = None

    if not info["is_git_checkout"]:
        error = "Not a git checkout — re-run install.sh to enable updates."
        _finish_update_check(error=error)
        info.update(_update_check_snapshot())
        info["error"] = error
        return info, 400

    # Fetch the published branch, then compare locally. This works
    # anonymously for the public repo and provides an exact changelog.
    rc, _ = _update_git("fetch", "origin", "main", timeout=30)
    if rc != 0:
        error = "Could not reach GitHub — check the internet connection."
        _finish_update_check(error=error)
        info.update(_update_check_snapshot())
        info["error"] = error
        return info, 502

    remote_rc, remote = _update_git("rev-parse", "--short", "FETCH_HEAD")
    behind_rc, behind = _update_git("rev-list", "--count", "HEAD..FETCH_HEAD")
    ahead_rc, ahead = _update_git("rev-list", "--count", "FETCH_HEAD..HEAD")
    # Untracked files (e.g. the .atlas_installed marker) never block a
    # fast-forward pull — only modified tracked files count as "dirty".
    dirty_rc, dirty = _update_git("status", "--porcelain", "--untracked-files=no")
    log_rc, log = _update_git(
        "log", "--format=%h%x09%cs%x09%s", "--max-count=20", "HEAD..FETCH_HEAD"
    )
    try:
        behind_count = int(behind)
        ahead_count = int(ahead)
    except (TypeError, ValueError):
        behind_count = ahead_count = None
    if any(rc != 0 for rc in (remote_rc, behind_rc, ahead_rc, dirty_rc, log_rc)) \
            or not remote or behind_count is None or ahead_count is None:
        error = "Fetched GitHub but could not compare software versions."
        _finish_update_check(error=error)
        info.update(_update_check_snapshot())
        info["error"] = error
        return info, 500

    commits = [
        dict(zip(("rev", "date", "subject"), line.split("\t", 2)))
        for line in log.splitlines() if line.strip()
    ]
    result = {
        "latest": remote,
        "behind": behind_count,
        "ahead": ahead_count,
        "update_available": behind_count > 0,
        "local_changes": bool(dirty.strip()),
        "commits": commits,
        "checked_at": int(time.time()),
    }
    _finish_update_check(result=result)
    info.update(_update_check_snapshot())
    return info, 200


def _automatic_software_update_check_once():
    """Run one due automatic check when NetworkManager confirms internet."""
    now = int(time.time())
    if not _automatic_update_check_due(_update_check_snapshot(), now):
        return "not_due"
    if _update_unit_active():
        return "update_running"
    if not _network_connectivity_state().get("online"):
        return "offline"

    result, status = _run_software_update_check(automatic=True)
    if result is None:
        return "not_due"
    if status == 200:
        if result.get("update_available"):
            logger.info(
                "Automatic software update check: %d commit(s) available at %s",
                result.get("behind", 0), result.get("latest") or "unknown",
            )
        else:
            logger.info("Automatic software update check: Atlas is up to date")
        return "checked"

    logger.warning(
        "Automatic software update check failed: %s",
        result.get("error") or f"HTTP {status}",
    )
    return "failed"


def _software_update_check_loop():
    """Check GitHub on startup/connection and every 30 minutes while online."""
    while True:
        try:
            _automatic_software_update_check_once()
        except Exception as exc:
            logger.warning("Automatic software update check error: %s", exc)
        time.sleep(_UPDATE_CHECK_POLL_SECONDS)


@app.route("/api/update/check", methods=["GET", "POST"])
def api_update_check():
    """Return cached update info; POST forces an immediate GitHub check."""
    if request.method == "GET":
        return jsonify(_update_public_info())

    info, status = _run_software_update_check()
    return jsonify(info), status


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """Launch install.sh --update via the root launcher (detached unit)."""
    if not _UPDATE_CHECK_RUN_LOCK.acquire(blocking=False):
        return jsonify({
            "error": "A software update check is in progress — try again in a moment.",
        }), 409
    try:
        if _update_unit_active():
            return jsonify({"started": True, "already_running": True})
        if not os.path.exists(_UPDATE_LAUNCHER):
            return jsonify({
                "error": "Update launcher not installed — run `sudo ./install.sh --update` "
                         "once from a terminal to enable in-app updates.",
            }), 409
        try:
            r = subprocess.run(
                ["sudo", "-n", _UPDATE_LAUNCHER],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as exc:
            return jsonify({"error": f"Failed to launch update: {exc}"}), 500
        if r.returncode != 0:
            detail = (r.stderr or r.stdout).strip()[:300]
            return jsonify({"error": f"Update launcher failed: {detail}"}), 500
        logging.info("Software update launched via web UI")
        _UPDATE_LAUNCHED_AT[0] = time.time()
        return jsonify({"started": True})
    finally:
        _UPDATE_CHECK_RUN_LOCK.release()


@app.route("/api/update/status")
def api_update_status():
    """Poll while an update runs; the UI tails the log and watches the rev."""
    running = _update_unit_active()
    tail = ""
    try:
        with open(_UPDATE_LOG_PATH, encoding="utf-8", errors="replace") as f:
            tail = _ANSI_RE.sub("", "".join(f.readlines()[-60:]))
    except OSError:
        pass
    state = "running" if running else "idle"
    if not running and tail:
        if "Atlas Control updated to" in tail or "installation complete" in tail:
            state = "success"
        elif "[✗]" in tail:
            state = "failed"
    elif not running and not tail.strip():
        # The unit died before writing a single byte (it runs with --collect,
        # so systemd forgets its exit status). Without this the UI polls an
        # ever-"idle" state and looks hung.
        if _UPDATE_LAUNCHED_AT[0] and time.time() - _UPDATE_LAUNCHED_AT[0] < 600:
            state = "failed"
            tail = ("[✗] The update process exited before producing any output.\n"
                    "Run `sudo bash install.sh --update` in a terminal to see the error.")
    return jsonify({
        "state": state,
        "running": running,
        "log": tail,
        "version": _update_local_info().get("version"),
    })


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
        "snapshot": snap,
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
    return jsonify({"ok": True, "snapshot": snap})

@app.route("/api/factory-reset", methods=["POST"])
def api_factory_reset():
    """Wipe local data and restore Heltec to its captured default config.

    Steps:
      1. Ensure a Heltec snapshot exists (capture lazily if missing).
      2. Wipe messaging / AI chat / node / GPS data from the local DB.
      3. Re-write the snapshot to the radio.
      4. Reset short_name to the firmware default (last 4 hex of node ID).
    """
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

    # Push a UI nudge so connected clients drop their cached state.
    try:
        socketio.emit("factory_reset_done", {"device": device_result})
    except Exception:
        pass

    return jsonify({"ok": True, "device": device_result})

# ------------------------------------------------------------------ SocketIO
@socketio.on("connect")
def handle_connect():
    origin = request.headers.get("Origin", "")
    if not _is_local(request.remote_addr or ""):
        logger.warning("Rejected WebSocket client from non-local address %s", request.remote_addr or "-")
        return False
    if origin and not _cors_origin_allowed(origin):
        logger.warning("Rejected WebSocket origin %s for client %s", origin, request.remote_addr or "-")
        return False
    logger.info("WebSocket client connected")

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

@socketio.on("send_message")
def handle_send(data):
    if mesh:
        mesh.send_message(data.get("text", ""), data.get("destination"), data.get("channel"))

# ------------------------------------------------------------------ calculator

_CALC_SAFE_FUNCS = {
    "abs": abs, "round": round, "pow": pow,
    "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "degrees": math.degrees, "radians": math.radians,
    "hypot": math.hypot, "factorial": math.factorial,
    "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
}

_CALC_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}

def _calc_eval_node(node):
    """Recursively evaluate an AST node using only allowed math operations."""
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric literals allowed")
        return node.value
    if isinstance(node, ast.Name):
        val = _CALC_SAFE_FUNCS.get(node.id)
        if val is None:
            raise ValueError(f"Unknown name: {node.id!r}")
        return val
    if isinstance(node, ast.BinOp):
        op = _CALC_BINOPS.get(type(node.op))
        if op is None:
            raise ValueError("Unsupported operator")
        return op(_calc_eval_node(node.left), _calc_eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_calc_eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +_calc_eval_node(node.operand)
        raise ValueError("Unsupported unary operator")
    if isinstance(node, ast.Call):
        func = _calc_eval_node(node.func)
        if not callable(func):
            raise ValueError("Not callable")
        if node.keywords:
            raise ValueError("Keyword arguments not allowed")
        return func(*[_calc_eval_node(a) for a in node.args])
    raise ValueError(f"Unsupported expression: {type(node).__name__}")

def safe_calc(expression):
    """Evaluate a math expression safely via AST parsing (no eval/exec)."""
    expr = str(expression).strip()
    if not expr:
        return None, "Empty expression"
    if len(expr) > 256:
        return None, "Expression too long (max 256 characters)"
    expr = expr.replace("^", "**")  # user convenience
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return None, f"Syntax error: {e}"
    try:
        result = _calc_eval_node(tree.body)
    except (ValueError, TypeError, OverflowError) as e:
        return None, str(e)
    except ZeroDivisionError:
        return None, "Division by zero"
    except Exception as e:
        return None, f"Calculation error: {e}"
    if not isinstance(result, (int, float)):
        return None, "Expression must return a number"
    if math.isnan(result):
        return None, "Result is NaN"
    if math.isinf(result):
        return str(result), None
    return result, None


@app.route("/api/calculator", methods=["POST"])
@limiter.limit("60 per minute")
def api_calculator():
    data = request.get_json(force=True)
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
    data = request.get_json(force=True)
    if not data.get("title") or not data.get("start_ts"):
        return jsonify({"error": "title and start_ts required"}), 400
    event_id = db.create_calendar_event(data)
    return jsonify({"id": event_id}), 201

@app.route("/api/calendar/<int:event_id>", methods=["PUT"])
def api_calendar_update(event_id):
    data = request.get_json(force=True)
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
    data = request.get_json(force=True)
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
    data = request.get_json(force=True)
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

_HOTSPOT_CFG_PATH = os.path.join(os.path.dirname(__file__), "hotspot_config.json")

def _load_hotspot_cfg():
    try:
        with open(_HOTSPOT_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_hotspot_cfg(ssid, password):
    try:
        with open(_HOTSPOT_CFG_PATH, "w") as f:
            json.dump({"ssid": ssid, "password": password}, f)
        os.chmod(_HOTSPOT_CFG_PATH, 0o600)
    except Exception as e:
        logging.warning("Could not save hotspot config: %s", e)

def _current_hotspot_state():
    out, _ = _nmcli("-t", "-f", "NAME,TYPE,STATE", "con", "show", "--active")
    active = any("hotspot" in line.lower() for line in out.splitlines())
    cfg = _load_hotspot_cfg()
    ssid = cfg.get("ssid", "")
    password = cfg.get("password", "")
    if not ssid:
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
    return {"active": active, "ssid": ssid, "password": password}


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
    urls.append("https://atlas.local")
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
    time.sleep(0.5)
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
        time.sleep(max(1, interval))
    return last_lan or _current_lan_wifi_state(), last_connectivity or _network_connectivity_state()


def _wait_for_hotspot_release(iface=None, timeout=10, interval=1):
    deadline = time.time() + max(2, timeout)
    while time.time() < deadline:
        hotspot = _current_hotspot_state()
        device = _wifi_device_state(iface) if iface else None
        connection = ((device or {}).get("connection") or "").lower()
        if not hotspot.get("active") and "hotspot" not in connection:
            return True
        time.sleep(max(0.5, interval))
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
            time.sleep(min(3, attempt + 1))
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
        for cmd in (
            ["sudo", "-n", "systemctl", "reload-or-restart", "avahi-daemon"],
            ["systemctl", "--user", "reload-or-restart", "avahi-daemon"],
            ["sudo", "-n", "avahi-daemon", "--reload"],
            ["avahi-daemon", "--reload"],
        ):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return
            except Exception:
                continue
        logging.info("avahi reload skipped — daemon unavailable or no privileges")
    threading.Thread(target=_run, daemon=True, name="avahi-reload").start()

def _candidate_mobile_hosts():
    # Ordering matters: these become the apps' baseUrls and preferredBaseUrl
    # (hosts[0]), and the mobile WebView loads whichever it ends up on.
    #
    #  - Numeric-IP URLs first: Chromium WebView cannot resolve .local via mDNS
    #    but reaches a direct IP URL fine.
    #  - For each IP, the plain-HTTP :5000 URL BEFORE the https one. Flask binds
    #    0.0.0.0:5000 in the clear and is always up; the https/443 path depends
    #    on nginx running with a cert the WebView trusts, which on a self-signed
    #    box it does not — Chromium cancels the page (this was a primary cause of
    #    the mobile "can't connect"/crash loop). Probing clients trust all certs
    #    so https stays a usable fallback, just not the preferred base.
    #
    # IPv6 literals are bracketed so the URLs are well-formed.
    ip_hosts = []
    try:
        _, addrs = _discover_local_networks()
        for addr in addrs:
            a = str(addr)
            if not _is_local(a):
                continue
            host = f"[{a}]" if ":" in a else a  # bracket IPv6 literals
            ip_hosts.append(f"http://{host}:5000")
            ip_hosts.append(f"https://{host}")
    except Exception:
        pass
    return ip_hosts + ["http://atlas.local:5000", "https://atlas.local"]

def _mobile_bootstrap_manifest():
    owner = {}
    if mesh:
        try:
            owner = mesh.get_owner_info() or {}
        except Exception:
            owner = {}
    hosts = _candidate_mobile_hosts()
    return {
        "device": {
            "name": owner.get("long_name") or "Atlas Control",
            "shortName": owner.get("short_name") or "ATLS",
        },
        "api": {
            "preferredBaseUrl": hosts[0],
            "baseUrls": hosts,
            "bootstrapPath": "/api/mobile/bootstrap",
        },
        "hotspot": _current_hotspot_state(),
        "capabilities": {
            "mesh": bool(mesh),
            "gps": bool(gps_manager),
            "ai": bool(ai_manager),
            "navigation": bool(nav_node),
            "wifi": True,
            "hotspot": True,
        },
        "generatedAt": int(time.time()),
    }

def _nmcli(*args, timeout=20):
    """Run nmcli cooperatively from the current gevent greenlet.

    monkey.patch_all() patches subprocess (subprocess=True by default), so
    subprocess.run here is gevent's cooperative version — it yields the hub
    while waiting for nmcli output. The threadpool approach was wrong: running
    gevent's patched subprocess from a real OS thread causes it to return empty
    output immediately because gevent's select doesn't work in non-greenlet threads.

    Mutating commands (connect, hotspot, disconnect, con down/delete) require
    NetworkManager authorization. We run those via sudo (requires NOPASSWD entry
    in /etc/sudoers.d/atlas-control-nm) to bypass polkit session restrictions.
    """
    _NEEDS_SUDO = {"connect", "hotspot", "disconnect", "down", "delete", "modify"}
    arg_list = list(args)
    use_sudo = any(a in _NEEDS_SUDO for a in arg_list)
    # On the Atlas service account, NetworkManager may deny Wi-Fi scans even for
    # read-only list operations unless nmcli runs with sudo. Without this, the
    # UI quietly receives an empty network list and looks broken.
    if arg_list[:3] == ["dev", "wifi", "list"]:
        use_sudo = True
    cmds = [(["sudo"] + ["nmcli"] + arg_list) if use_sudo else (["nmcli"] + arg_list)]
    # Wi-Fi scans can work either with elevated NetworkManager access or as a
    # normal user, depending on the host's polkit/sudoers setup. Try sudo
    # first for service contexts, but fall back to plain nmcli before failing.
    if use_sudo and arg_list[:3] == ["dev", "wifi", "list"]:
        cmds.append(["nmcli"] + arg_list)
    last_out = ""
    last_rc = -1
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = r.stdout.strip()
            err = r.stderr.strip()
            merged = (out + ("\n" + err if err else "")).strip()
            if r.returncode == 0:
                return merged, 0
            last_out = merged
            last_rc = r.returncode
        except Exception as e:
            logging.warning("_nmcli exception (%s): %s", args[:3], e)
            last_out = str(e)
            last_rc = -1
    return last_out, last_rc

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
        urls.append(f"http://{ip}:5000")
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
    parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--web-port", default=None, type=int, help="Web port")
    parser.add_argument("--demo", action="store_true", help="Run with demo data")
    args = parser.parse_args()

    db.init_db()

    # Per-subsystem kill switches so we can bisect what's pinning CPU.
    # Touch the corresponding file under data/ to disable, remove to re-enable.
    def _kill(name: str) -> bool:
        return os.path.exists(os.path.join(_BASE_DIR, "data", name))

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
    if ai_settings.get("model") in (None, "", "llama3.2:3b", "qwen3:4b", "qwen3.5:2b", "qwen3.5:4b"):
        ai_updates["model"] = "qwen2.5:3b"
        # Qwen2.5 sampling: stored low-temp values from older models
        # cause repetition loops; use Qwen2.5's recommended defaults
        ai_updates["temperature"] = "0.7"
        ai_updates["top_p"] = "0.8"
        ai_updates["top_k"] = "20"
        # Auto layer placement: forcing 99 layers hard-fails on the 8 GB Jetson
        ai_updates["num_gpu"] = "-1"
    # Stale 2048-token context from pre-Qwen3.5 defaults truncates the system
    # prompt once RAG docs + 8-turn history are injected
    if ai_settings.get("num_ctx") == "2048":
        ai_updates["num_ctx"] = "4096"
    # Migrate the embedder to qwen3-embedding:0.6b. nomic (768-dim) and qwen3
    # (1024-dim) vectors are incompatible, so switching wipes stored doc
    # embeddings; the background re-embed thread regenerates them on startup.
    if ai_settings.get("embed_model") in (None, "", "nomic-embed-text"):
        if ai_settings.get("embed_model") != "qwen3-embedding:0.6b":
            ai_updates["embed_model"] = "qwen3-embedding:0.6b"
            _conn = db.get_db()
            _conn.execute("UPDATE ai_documents SET embedding=NULL")
            _conn.commit()
    if ai_updates:
        db.ai_set_settings(ai_updates)
    web_port = args.web_port or int(settings.get("web_port", 5000))
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
    _mesh_kill = os.path.join(_BASE_DIR, "data", "mesh_disabled")
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
    _gps_kill = os.path.join(_BASE_DIR, "data", "gps_disabled")
    if os.path.exists(_gps_kill):
        logger.warning("GPS disabled via kill switch (%s); skipping reader.", _gps_kill)
    else:
        gps_manager.start()

    # ── Routing + navigation ──────────────────────────────────────────────────
    routing_node = RoutingNode(states_dir=_STATES_DIR, active_json=_ACTIVE_PATH)
    nav_node = NavigationNode(socketio=socketio, routing_node=routing_node,
                              mesh_manager=mesh)
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
                logger.info("Data prune complete")
            except Exception as e:
                logger.warning(f"Data prune failed: {e}")
            _time.sleep(86400)

    if _disable_bg:
        logger.warning("Background loops disabled (bg_disabled or minimal_mode).")
    else:
        threading.Thread(target=_nightly_prune, daemon=True).start()
        threading.Thread(target=_scheduler_loop, daemon=True).start()
        threading.Thread(
            target=_software_update_check_loop,
            daemon=True,
            name="software-update-check",
        ).start()
        threading.Thread(target=_wifi_failover_loop, daemon=True, name="wifi-failover").start()

    logger.info(f"Dashboard at http://{args.host}:{web_port}")
    socketio.run(app, host=args.host, port=web_port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == "__main__":
    main()
