"""
Client for atlas-helper, the root-owned service behind /run/atlas/helper.sock.

The Atlas service account has no sudo rights. Anything that needs root —
joining a Wi-Fi network, running the hotspot, reading SMART data, rebooting,
installing an update — is one request to the helper, which validates it and
performs exactly that operation.

On the gevent hub thread the connection uses the monkey-patched (cooperative)
socket; on native worker threads it uses the original blocking socket, since
a patched socket on a non-hub thread would spin up a busy-looping hub.
"""
import json
import os
import socket as _socket_module

import atlas_blocking

SOCKET_PATH = os.environ.get("ATLAS_HELPER_SOCKET", "/run/atlas/helper.sock")


class HelperUnavailable(RuntimeError):
    """No helper socket (a developer checkout that was never migrated)."""


def available():
    return os.path.exists(SOCKET_PATH)


def _socket_class():
    if atlas_blocking.on_hub_thread():
        return _socket_module.socket
    try:
        from gevent import monkey
        if monkey.is_module_patched("socket"):
            return monkey.get_original("socket", "socket")
    except Exception:
        pass
    return _socket_module.socket


def call(verb, timeout=60, **args):
    """Send one request; return the helper's JSON response (a dict)."""
    if not available():
        raise HelperUnavailable(f"{SOCKET_PATH} is not available")
    sock = _socket_class()(_socket_module.AF_UNIX, _socket_module.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps({"verb": verb, "args": args}) + "\n").encode())
        chunks = []
        size = 0
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
            size += len(data)
            if size > 4 * 1024 * 1024:
                raise RuntimeError("helper response too large")
        raw = b"".join(chunks).split(b"\n", 1)[0]
        return json.loads(raw.decode("utf-8")) if raw else {"ok": False, "error": "no response"}
    finally:
        sock.close()


def run(verb, timeout=60, **args):
    """Helper call shaped like the old nmcli wrapper: returns (output, rc)."""
    try:
        r = call(verb, timeout=timeout, **args)
    except HelperUnavailable as exc:
        return str(exc), 126
    except Exception as exc:
        return f"helper error: {exc}", 125
    if "rc" in r:
        return (r.get("out") or r.get("error") or "").strip(), int(r["rc"])
    return (r.get("error") or ""), (0 if r.get("ok") else 1)
