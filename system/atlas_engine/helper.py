"""
atlas-helper: the Atlas app's only route to root.

systemd listens on /run/atlas/helper.sock (root:atlas 0660) and starts one
atlas-helper@ instance per connection with the socket as stdin/stdout. The
helper checks the peer's credentials, reads exactly one JSON request
({"verb": ..., "args": {...}}), runs one narrowly-defined operation and
writes one JSON response. There is no generic "run this command" verb and
no sudo anywhere, so a compromised app process can do no more than these
verbs allow.
"""
import grp
import json
import os
import pwd
import socket
import struct
import sys
import time

from . import fsutil, netctl, paths

MAX_REQUEST = 64 * 1024


def _peer_uid(sock):
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def _allowed_uids():
    """Root and the Atlas service account. On developer units only, also the
    members of the atlas group — the developer account, which has sudo there
    anyway — so tools/dev/deploy works without sudo. Never on retail units:
    group membership alone must not reach root actions."""
    uids = {0}
    try:
        uids.add(pwd.getpwnam(paths.SERVICE_USER).pw_uid)
    except KeyError:
        pass
    if paths.dev_mode():
        try:
            members = grp.getgrnam(paths.SERVICE_USER).gr_mem
        except KeyError:
            members = []
        for name in members:
            try:
                uids.add(pwd.getpwnam(name).pw_uid)
            except KeyError:
                pass
    return uids


def _run(cmd, timeout=30):
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return {"rc": r.returncode, "out": (r.stdout or "").strip()[-20000:],
            "err": (r.stderr or "").strip()[-2000:]}


# ── verbs ────────────────────────────────────────────────────────────────
def v_ping(args):
    return {"ok": True, "pong": True}


def v_net_wifi_list(args):
    return netctl.wifi_list(args.get("iface"), bool(args.get("rescan")))


def v_net_wifi_connect(args):
    return netctl.wifi_connect(args.get("ssid"), args.get("password"), args.get("iface"),
                               timeout=min(int(args.get("timeout_s") or 35), 60))


def v_net_disconnect(args):
    return netctl.disconnect(args.get("iface"))


def v_net_set_autoconnect(args):
    return netctl.set_autoconnect(args.get("name"), bool(args.get("enabled")))


def v_net_hotspot_up(args):
    return netctl.hotspot_up(args.get("iface"), args.get("ssid"), args.get("password"))


def v_net_hotspot_down(args):
    return netctl.hotspot_down()


def v_avahi_reload(args):
    return _run(["systemctl", "reload-or-restart", "avahi-daemon"], timeout=20)


def v_avahi_reclaim(args):
    # A reload only re-reads service files. Once a name conflict has renamed
    # the host (atlas-2.local) only a restart probes for the real name again.
    return _run(["systemctl", "restart", "avahi-daemon"], timeout=20)


def v_smart_read(args):
    # One fixed device, read-only attributes. rc 4 = warnings, data still valid.
    return _run(["smartctl", "-A", "-H", "--json", "/dev/nvme0n1"], timeout=10)


def v_power_reboot(args):
    return _run(["systemctl", "reboot", "--no-block"], timeout=15)


def v_power_shutdown(args):
    return _run(["systemctl", "poweroff", "--no-block"], timeout=15)


def v_update_apply(args):
    """Hand the verified-by-the-app download to the root engine.

    The app cannot name paths: the engine reads only the fixed hand-off
    directory, copies the files into root-owned space and verifies them again.
    """
    state = fsutil.read_json(paths.JOURNAL, {}) or {}
    if state.get("state") not in (None, "IDLE"):
        return {"ok": True, "already_running": True}
    os.makedirs(paths.UPDATE_STATE, mode=0o711, exist_ok=True)
    try:
        os.unlink(os.path.join(paths.UPDATE_STATE, "cancel"))
    except FileNotFoundError:
        pass
    fsutil.atomic_write_json(paths.REQUEST, {"requested": time.time(), "handoff_dir": paths.HANDOFF},
                             mode=0o600)
    r = _run(["systemctl", "start", "--no-block", "atlas-update-engine.service"], timeout=20)
    return {"ok": r["rc"] == 0, "error": r["err"] or None}


def v_update_cancel(args):
    os.makedirs(paths.UPDATE_STATE, mode=0o711, exist_ok=True)
    fsutil.atomic_write_bytes(os.path.join(paths.UPDATE_STATE, "cancel"), b"1\n", mode=0o600)
    return {"ok": True}


def v_update_status(args):
    journal = fsutil.read_json(paths.JOURNAL, {}) or {}
    return {
        "ok": True,
        "status": fsutil.read_json(paths.STATUS),
        "last_result": fsutil.read_json(paths.LAST_RESULT),
        "installed": fsutil.read_json(paths.INSTALLED),
        "engine_state": journal.get("state", "IDLE"),
    }


def v_net_forget_all(args):
    return netctl.forget_all_wifi()


def v_logs_vacuum(args):
    # Factory reset: the system journal can name the owner's networks,
    # devices and places. Archive the active files, then delete every archive.
    _run(["journalctl", "--rotate"], timeout=60)
    return _run(["journalctl", "--vacuum-time=1s"], timeout=120)


def v_perf_boost(args):
    """GPU and memory clocks to maximum while Ray answers (lease; see perf.py)."""
    from . import perf
    return dict(perf.boost(run=lambda cmd: _run(cmd, timeout=15)), ok=True)


def v_perf_release(args):
    from . import perf
    return dict(perf.release(run=lambda cmd: _run(cmd, timeout=15)), ok=True)


def v_clock_set(args):
    """A GPS fix or a paired phone's clock, while NTP is not available. The
    rules (GPS either way, phone only forward, NTP always wins) are in clock.py."""
    from . import clock
    try:
        result = clock.set_time(args.get("epoch"), args.get("source"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return dict(result, ok=True)


VERBS = {
    "ping": v_ping,
    "net.wifi_list": v_net_wifi_list,
    "net.wifi_connect": v_net_wifi_connect,
    "net.disconnect": v_net_disconnect,
    "net.set_autoconnect": v_net_set_autoconnect,
    "net.hotspot_up": v_net_hotspot_up,
    "net.hotspot_down": v_net_hotspot_down,
    "net.forget_all": v_net_forget_all,
    "avahi.reload": v_avahi_reload,
    "avahi.reclaim": v_avahi_reclaim,
    "smart.read": v_smart_read,
    "power.reboot": v_power_reboot,
    "power.shutdown": v_power_shutdown,
    "update.apply": v_update_apply,
    "update.cancel": v_update_cancel,
    "update.status": v_update_status,
    "clock.set": v_clock_set,
    "logs.vacuum": v_logs_vacuum,
    "perf.boost": v_perf_boost,
    "perf.release": v_perf_release,
}


def handle(request):
    """Validate and dispatch one decoded request. Never raises."""
    if not isinstance(request, dict):
        return {"ok": False, "error": "bad request"}
    verb = request.get("verb")
    args = request.get("args") or {}
    fn = VERBS.get(verb) if isinstance(verb, str) else None
    if fn is None or not isinstance(args, dict):
        return {"ok": False, "error": "unknown operation"}
    try:
        result = fn(args)
        result.setdefault("ok", result.get("rc", 0) == 0)
        return result
    except netctl.NetError as exc:
        return {"ok": False, "error": str(exc), "rc": 2, "out": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}", "rc": 1, "out": "operation failed"}


def serve_stdin():
    """Entry point for atlas-helper@.service (Accept=yes, socket on fd 0)."""
    sock = socket.socket(fileno=0)
    try:
        if _peer_uid(sock) not in _allowed_uids():
            sock.sendall(b'{"ok": false, "error": "not permitted"}\n')
            return 1
        sock.settimeout(10)
        buf = b""
        while b"\n" not in buf and len(buf) <= MAX_REQUEST:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if len(buf) > MAX_REQUEST:
            response = {"ok": False, "error": "request too large"}
        else:
            try:
                request = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                request = None
            response = handle(request)
            verb = request.get("verb") if isinstance(request, dict) else "?"
            # Verb and outcome only — never arguments (they carry passwords).
            print(f"atlas-helper: {verb} -> {'ok' if response.get('ok') else 'error'}",
                  file=sys.stderr, flush=True)
        sock.settimeout(60)
        sock.sendall((json.dumps(response) + "\n").encode())
        return 0
    finally:
        try:
            sock.close()
        except OSError:
            pass
