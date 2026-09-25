"""Is the release that was just started actually working?

Checked directly on loopback (where /api/health returns release details)
and again through nginx over TLS, so a broken proxy configuration fails the
update too. Ollama, GPS and the mesh radio are deliberately not part of
health: an Atlas without its radio plugged in must still update.
"""
import hashlib
import json
import os
import socket
import ssl
import time
import urllib.request

from . import paths


class HealthResult:
    def __init__(self, ok, detail):
        self.ok = ok
        self.detail = detail

    def __repr__(self):
        return f"HealthResult(ok={self.ok}, detail={self.detail!r})"


def _get_json(url, timeout=5, context=None, host=None):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
        return json.loads(r.read(64 * 1024).decode())


def _nginx_context(cert_path):
    """TLS context that accepts exactly this device's certificate."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _peer_matches(cert_path, port=443):
    if not os.path.exists(cert_path):
        return True
    with open(cert_path, "rb") as f:
        expected = ssl.PEM_cert_to_DER_cert(f.read().decode())
    ctx = _nginx_context(cert_path)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            return hashlib.sha256(tls.getpeercert(binary_form=True)).digest() == \
                hashlib.sha256(expected).digest()


def _unit_prop(runner, unit, prop):
    r = runner.run(["systemctl", "show", "-p", prop, "--value", unit], timeout=15)
    return (r.stdout or "").strip()


def check(runner, expected_version, release_dir, budget_s=180, stable_s=30,
          cert_path="/etc/atlas/identity/tls/cert.pem", sleep=time.sleep, via_nginx=True):
    """Poll until the release is healthy for `stable_s` seconds or the budget runs out."""
    deadline = time.monotonic() + budget_s
    release_real = os.path.realpath(release_dir)
    last = "not started"
    healthy_since = None
    restarts_at_healthy = None
    while time.monotonic() < deadline:
        try:
            if _unit_prop(runner, paths.APP_UNIT, "ActiveState") != "active":
                raise RuntimeError(f"{paths.APP_UNIT} is not active")
            body = _get_json(f"http://127.0.0.1:{paths.APP_PORT}/api/health", host="127.0.0.1")
            if body.get("version") != expected_version:
                raise RuntimeError(f"running version {body.get('version')!r}, expected {expected_version!r}")
            if os.path.realpath(body.get("release_path") or "") != release_real:
                raise RuntimeError("app is running from an unexpected directory")
            if not body.get("db_ok"):
                raise RuntimeError("database is not accessible")
            pid = _unit_prop(runner, paths.APP_UNIT, "MainPID")
            if pid and pid != "0":
                try:
                    with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
                        cmd = f.read().replace(b"\x00", b" ").decode(errors="replace")
                    if release_real not in cmd and "atlas-launch" not in cmd:
                        raise RuntimeError("service main process is not the new release")
                except (OSError, ValueError):
                    pass
            if via_nginx:
                if _unit_prop(runner, "nginx.service", "ActiveState") != "active":
                    raise RuntimeError("nginx is not active")
                if not _peer_matches(cert_path):
                    raise RuntimeError("nginx is presenting an unexpected certificate")
                proxied = _get_json("https://127.0.0.1/api/health", context=_nginx_context(cert_path),
                                    host="127.0.0.1")
                if proxied.get("version") != expected_version:
                    raise RuntimeError("nginx is not serving the new release")
            restarts = _unit_prop(runner, paths.APP_UNIT, "NRestarts")
            if healthy_since is None:
                healthy_since = time.monotonic()
                restarts_at_healthy = restarts
            elif restarts != restarts_at_healthy:
                raise RuntimeError("service restarted during the stability window")
            if time.monotonic() - healthy_since >= stable_s:
                return HealthResult(True, "healthy")
            last = "stabilizing"
        except Exception as exc:
            last = str(exc)
            healthy_since = None
        sleep(2)
    return HealthResult(False, last)
