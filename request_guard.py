"""
The single gate every HTTP request and Socket.IO connection passes through.

Installed as the *outermost* WSGI layer (outside Flask-SocketIO's own
middleware, which otherwise answers /socket.io/ before Flask's
before_request hooks ever run), EdgeMiddleware:

1. Establishes who the client is. Production Flask listens on 127.0.0.1 only
   and nginx is the one entry point; nginx forwards the real client address
   in X-Real-IP together with a per-device secret (X-Atlas-Proxy). The
   forwarded address is believed only when the TCP peer is loopback *and*
   the secret matches — a client can never forge X-Forwarded-For/X-Real-IP
   to look local. Client-supplied copies of those headers are always
   discarded.

2. Enforces Host. Accepted: any IP literal (DHCP, hotspot and VPN addresses
   all change, and an IP literal cannot be DNS-rebound), this device's mDNS
   names, localhost, plus owner-configured extra names. Anything else — e.g.
   attacker.example resolving to Atlas's LAN address — is refused, which
   defeats DNS rebinding.

3. Refuses cross-site state changes. For POST/PUT/PATCH/DELETE (and for every
   Socket.IO request, since a WebSocket handshake is a GET):
   Sec-Fetch-Site must not say cross-site; Origin, when sent, must name this
   same host; otherwise Referer, when sent, must; bodies must be JSON (or
   multipart on explicitly allowed upload routes). CORS is not relied on.

Pairing (who may use Atlas at all) plugs in via `client_check` once the
pairing subsystem is enabled; see pairing.py.
"""
import hmac
import ipaddress
import json
import os
import socket
import threading
import time
from urllib.parse import urlsplit

PROXY_SECRET_FILE = os.environ.get("ATLAS_PROXY_SECRET_FILE", "/etc/atlas/identity/proxy_secret")
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
JSON_TYPES = ("application/json",)
MULTIPART_TYPES = ("multipart/form-data",)
FORWARDING_HEADERS = ("HTTP_X_ATLAS_PROXY", "HTTP_X_REAL_IP", "HTTP_X_FORWARDED_FOR",
                      "HTTP_X_FORWARDED_PROTO", "HTTP_X_ATLAS_LOCAL_ADDR", "HTTP_FORWARDED")


def _ip(value):
    try:
        addr = ipaddress.ip_address((value or "").strip().strip("[]"))
    except ValueError:
        return None
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr


def is_loopback(value):
    addr = _ip(value)
    return bool(addr and addr.is_loopback)


def host_without_port(host):
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


class ProxySecret:
    """Reads the nginx↔Flask shared secret, re-reading when the file changes."""

    def __init__(self, path=PROXY_SECRET_FILE):
        self.path = path
        self._value = None
        self._mtime = None
        self._checked = 0.0
        self._lock = threading.Lock()

    def get(self):
        now = time.monotonic()
        with self._lock:
            if now - self._checked < 5:
                return self._value
            self._checked = now
            try:
                mtime = os.stat(self.path).st_mtime
                if mtime != self._mtime:
                    with open(self.path, encoding="utf-8") as f:
                        value = f.read().strip()
                    self._value = value if len(value) >= 32 else None
                    self._mtime = mtime
            except OSError:
                self._value = None
                self._mtime = None
            return self._value


class HostPolicy:
    """Which Host header values are this Atlas."""

    def __init__(self, extra_names=None, ttl=10.0):
        self._extra_names = extra_names or (lambda: ())
        self._ttl = ttl
        self._cache = None
        self._cached_at = 0.0

    def own_names(self):
        now = time.monotonic()
        if self._cache is not None and now - self._cached_at < self._ttl:
            return self._cache
        self._cache = self._compute_names()
        self._cached_at = now
        return self._cache

    def _compute_names(self):
        names = {"localhost", "atlas", "atlas.local"}
        try:
            h = socket.gethostname().strip().lower()
            if h:
                names.add(h)
                names.add(h.split(".")[0] + ".local")
        except OSError:
            pass
        try:
            for n in self._extra_names() or ():
                n = (n or "").strip().lower().rstrip(".")
                if n:
                    names.add(n)
        except Exception:
            pass
        return names

    def allowed(self, host):
        host = host_without_port(host)
        if not host:
            return False
        if _ip(host) is not None:
            return True
        return host.rstrip(".") in self.own_names()


def _deny(start_response, status, message, code):
    body = json.dumps({"error": message, "code": code}).encode()
    start_response(status, [("Content-Type", "application/json"),
                            ("Content-Length", str(len(body))),
                            ("Cache-Control", "no-store")])
    return [body]


class EdgeMiddleware:
    def __init__(self, app, proxy_secret=None, host_policy=None, client_check=None,
                 multipart_paths=(), logger=None):
        self.app = app
        self.proxy_secret = proxy_secret or ProxySecret()
        self.host_policy = host_policy or HostPolicy()
        self.client_check = client_check
        self.multipart_paths = tuple(multipart_paths)
        self.logger = logger

    # ── identity of the client ───────────────────────────────────────────
    def _resolve_client(self, environ):
        peer = environ.get("REMOTE_ADDR", "")
        supplied = environ.get("HTTP_X_ATLAS_PROXY", "")
        secret = self.proxy_secret.get()
        via_proxy = False
        if supplied and secret and is_loopback(peer) and hmac.compare_digest(
                supplied.encode(), secret.encode()):
            real = _ip(environ.get("HTTP_X_REAL_IP", ""))
            if real is not None:
                environ["REMOTE_ADDR"] = str(real)
                via_proxy = True
                if environ.get("HTTP_X_FORWARDED_PROTO", "").lower() == "https":
                    environ["wsgi.url_scheme"] = "https"
                local = _ip(environ.get("HTTP_X_ATLAS_LOCAL_ADDR", ""))
                if local is not None:
                    environ["atlas.server_addr"] = str(local)
        environ["atlas.via_proxy"] = via_proxy
        environ["atlas.direct_loopback"] = is_loopback(peer) and not supplied
        environ["atlas.peer"] = peer
        for key in FORWARDING_HEADERS:
            environ.pop(key, None)

    # ── cross-site checks ────────────────────────────────────────────────
    def _same_host(self, url, host):
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        return host_without_port(parts.netloc) == host_without_port(host)

    def _cross_site_reason(self, environ):
        host = environ.get("HTTP_HOST", "")
        fetch_site = environ.get("HTTP_SEC_FETCH_SITE", "").lower()
        if fetch_site in ("cross-site", "same-site"):
            return "cross-site request refused"
        origin = environ.get("HTTP_ORIGIN")
        if origin is not None:
            if not self._same_host(origin, host):
                return "request from a foreign web origin refused"
            return None
        referer = environ.get("HTTP_REFERER")
        if referer and not self._same_host(referer, host):
            return "request from a foreign web page refused"
        return None

    def _body_type_reason(self, environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        chunked = "chunked" in environ.get("HTTP_TRANSFER_ENCODING", "").lower()
        if length <= 0 and not chunked:
            return None
        ctype = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
        if ctype in JSON_TYPES:
            return None
        path = environ.get("PATH_INFO", "")
        if ctype in MULTIPART_TYPES and any(path.startswith(p) for p in self.multipart_paths):
            return None
        return "request body must be JSON"

    def __call__(self, environ, start_response):
        self._resolve_client(environ)
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET").upper()

        if not self.host_policy.allowed(environ.get("HTTP_HOST", "")):
            return _deny(start_response, "421 Misdirected Request",
                         "This address is not an Atlas.", "bad_host")

        is_socket = path.startswith("/socket.io")
        if method in MUTATING or is_socket:
            reason = self._cross_site_reason(environ)
            if reason:
                self._log("Refused %s %s from %s: %s", method, path,
                          environ.get("REMOTE_ADDR"), reason)
                return _deny(start_response, "403 Forbidden", reason, "cross_site")
        if method in MUTATING:
            reason = self._body_type_reason(environ)
            if reason:
                return _deny(start_response, "415 Unsupported Media Type", reason, "bad_content_type")

        if self.client_check is not None:
            denied = self.client_check(environ)
            if denied:
                status, message, code = denied
                return _deny(start_response, status, message, code)
        return self.app(environ, start_response)

    def _log(self, *args):
        if self.logger:
            self.logger.warning(*args)
