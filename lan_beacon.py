"""
LAN Beacon — UDP "shout-and-receive" discovery for the Atlas mobile apps.

Why this exists
---------------
The Jetson Orin Nano has a single Wi-Fi radio. When Atlas drops its hotspot
to join a brand-new LAN, avahi (mDNS) re-announces are unreliable in the
first 5–30 s window — exactly when the mobile setup wizard is most active,
and the same window in which a port-5000 fingerprint sweep across a /24
costs the most (the phone has only just rejoined its own LAN). The beacon
plugs that gap with two complementary UDP behaviours:

  1. **Reply** to `ATLAS-DISCOVER` probes that arrive on UDP port 5050.
     The mobile apps fan out unicast probes across a curated list of likely
     /24 prefixes; whichever one Atlas actually lives on receives the
     packet and answers with a JSON descriptor containing its bound IP and
     access URLs. This path works inside the Android AVD (which cannot
     receive broadcasts but routes outbound unicast UDP through host NAT)
     and on the iOS Simulator (which shares host networking).
  2. **Shout** an unsolicited beacon to the LAN broadcast address every
     1 s during a "burst window" (90 s after a successful wifi/connect)
     and every 10 s otherwise. Real devices on the LAN receive these
     directly. Mobile listeners pick up the broadcast without having to
     guess the right /24.

Verification
------------
The beacon payload is JSON, unsigned. The mobile apps treat any URL it
returns as a *candidate* — they re-verify by hitting `/api/device` over
TCP and checking the existing `app == "atlas-control"` fingerprint. That
matches the trust model of the existing port-5000 sweep.

A `nonce` field is echoed from the probe back into the reply so the
mobile app can correlate stale responses across rapid retry cycles, but
it is not used as a cryptographic guarantee.
"""

from __future__ import annotations

import errno
import json
import logging
import socket
import struct
import threading
import time
from typing import Callable, Optional

# Resolve a native (non-gevent-patched) socket constructor. The listener and
# shouter threads here are native threading.Thread workers, not greenlets.
# gevent's patched ``socket.socket.recvfrom`` lazily spins up a per-thread
# gevent hub when called from a non-greenlet thread, and that hub busy-loops
# in libev — pegging a CPU core and starving the WSGI worker. Using the
# stdlib socket directly keeps recvfrom in a plain kernel-blocking syscall.
try:
    from gevent.monkey import get_original as _gevent_get_original  # type: ignore
    _native_socket = _gevent_get_original("socket", "socket")
except Exception:
    _native_socket = socket.socket

BEACON_PORT = 5050
PROBE_TOKEN = b"ATLAS-DISCOVER"
_RECV_BUF = 2048


class LanBeacon:
    """Background UDP responder + announcer.

    Construction never raises. ``start()`` may fail to bind (port in use,
    capability denied, etc.) in which case the beacon disables itself
    silently — the rest of Atlas keeps working and the apps fall back to
    the existing port-5000 sweep.

    Args:
        manifest_fn: Zero-arg callable returning the same dict shape that
            ``/api/mobile/bootstrap`` returns. Used to populate the
            beacon's ``accessUrls`` and device metadata. Called lazily on
            every probe / shout so newly bound IPs are reflected without
            restarting the beacon.
        logger: Optional logger; defaults to ``logging.getLogger("atlas-beacon")``.
    """

    def __init__(
        self,
        manifest_fn: Callable[[], dict],
        logger: Optional[logging.Logger] = None,
    ):
        self.manifest_fn = manifest_fn
        self.logger = logger or logging.getLogger("atlas-beacon")
        self._stop = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None
        self._shouter_thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._shout_sock: Optional[socket.socket] = None
        # Burst announce window — bumped after a Wi-Fi switch so the apps
        # see Atlas's new IP within ~1 s instead of waiting up to 10 s for
        # the next heartbeat shout.
        self._burst_until_monotonic = 0.0
        self._burst_lock = threading.Lock()

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Bind sockets and launch the listener + shouter threads.

        Returns True if at least the listener socket bound successfully.
        Returns False if the beacon could not start at all (in which case
        the mobile apps continue to work via the port-5000 sweep).
        """
        if self._listener_thread is not None:
            return True
        try:
            self._sock = self._open_listener()
        except OSError as e:
            self.logger.warning(
                "LanBeacon: UDP listener bind on :%d failed (%s) — "
                "discovery beacons disabled.", BEACON_PORT, e
            )
            return False
        # Broadcast socket is best-effort. If it can't open we still reply
        # to probes; only the unsolicited shout path is lost.
        try:
            self._shout_sock = self._open_shouter()
        except OSError as e:
            self.logger.info(
                "LanBeacon: broadcast socket unavailable (%s) — "
                "probe replies still active.", e
            )
            self._shout_sock = None
        self._listener_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="lan-beacon-listen"
        )
        self._shouter_thread = threading.Thread(
            target=self._shout_loop, daemon=True, name="lan-beacon-shout"
        )
        self._listener_thread.start()
        self._shouter_thread.start()
        self.logger.info("LanBeacon active on UDP :%d", BEACON_PORT)
        return True

    def stop(self) -> None:
        self._stop.set()
        for s in (self._sock, self._shout_sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._sock = None
        self._shout_sock = None

    def announce_burst(self, duration_seconds: float = 90.0) -> None:
        """Bump shout cadence to 1 Hz for [duration_seconds]. Idempotent —
        repeated calls extend the window rather than restarting it."""
        with self._burst_lock:
            target = time.monotonic() + max(1.0, float(duration_seconds))
            if target > self._burst_until_monotonic:
                self._burst_until_monotonic = target

    # ── socket setup ──────────────────────────────────────────────────────

    @staticmethod
    def _open_listener() -> socket.socket:
        s = _native_socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEPORT lets a second process (e.g., during deploy) bind
        # alongside us briefly without an EADDRINUSE flap. Not present on
        # every Linux build — the try/except is intentional.
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Short timeout so the listener can react to ``stop()`` quickly.
        s.settimeout(0.5)
        s.bind(("0.0.0.0", BEACON_PORT))
        return s

    @staticmethod
    def _open_shouter() -> socket.socket:
        s = _native_socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        return s

    # ── payload ───────────────────────────────────────────────────────────

    def _build_payload(self, request_nonce: Optional[str] = None) -> bytes:
        """Build the JSON payload sent in a probe reply or unsolicited shout."""
        manifest: dict = {}
        try:
            m = self.manifest_fn()
            if isinstance(m, dict):
                manifest = m
        except Exception as e:  # pragma: no cover — manifest_fn is third-party
            self.logger.debug("LanBeacon manifest_fn raised: %s", e)
            manifest = {}

        device = manifest.get("device") if isinstance(manifest.get("device"), dict) else {}
        api = manifest.get("api") if isinstance(manifest.get("api"), dict) else {}
        access_urls = api.get("baseUrls") if isinstance(api.get("baseUrls"), list) else []

        payload: dict = {
            "app": "atlas-control",
            "v": 1,
            "name": device.get("name") or "",
            "shortName": device.get("shortName") or "",
            "port": 5000,
            "accessUrls": access_urls,
            "ts": int(time.time()),
        }
        if request_nonce:
            payload["nonce"] = request_nonce
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # ── listener: reply to probes ─────────────────────────────────────────

    def _listen_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(_RECV_BUF)
            except socket.timeout:
                continue
            except OSError as e:
                if e.errno in (errno.EBADF, errno.ENOTSOCK):
                    return  # socket closed by stop()
                self.logger.debug("LanBeacon listen OSError: %s", e)
                continue
            except Exception as e:  # pragma: no cover
                self.logger.debug("LanBeacon listen error: %s", e)
                continue

            if not data.startswith(PROBE_TOKEN):
                continue

            nonce = self._parse_probe_nonce(data[len(PROBE_TOKEN):])
            try:
                resp = self._build_payload(request_nonce=nonce)
                sock.sendto(resp, addr)
            except OSError as e:
                self.logger.debug("LanBeacon reply to %s failed: %s", addr, e)

    @staticmethod
    def _parse_probe_nonce(tail: bytes) -> Optional[str]:
        """Tolerantly extract a nonce from the tail of a probe packet.

        Accepts:
            (empty)                       -> no nonce
            ``\\n{"nonce":"abc"}``         -> "abc"
            ``\\nabc``                     -> "abc" (raw token form)
        """
        tail = tail.strip()
        if not tail:
            return None
        try:
            obj = json.loads(tail.decode("utf-8", errors="replace"))
            if isinstance(obj, dict):
                n = obj.get("nonce")
                if isinstance(n, str):
                    return n[:64]
        except (ValueError, UnicodeDecodeError):
            pass
        # Fallback: treat the whole tail as a literal nonce string.
        return tail.decode("utf-8", errors="replace")[:64]

    # ── shouter: unsolicited broadcasts ───────────────────────────────────

    def _broadcast_targets(self) -> list[str]:
        """Compute the list of broadcast addresses to shout to.

        Always includes 255.255.255.255 (handled by the kernel via the
        default-route interface). When possible, augments with each
        directly-attached IPv4 interface's broadcast address so a
        multi-homed Atlas (hotspot + LAN simultaneously, on dual-radio
        installs) reaches both segments.
        """
        targets = ["255.255.255.255"]
        for bcast in _enumerate_iface_broadcasts():
            if bcast and bcast not in targets:
                targets.append(bcast)
        return targets

    def _shout_loop(self) -> None:
        if self._shout_sock is None:
            return
        while not self._stop.is_set():
            in_burst = time.monotonic() < self._burst_until_monotonic
            payload = self._build_payload()
            for addr in self._broadcast_targets():
                try:
                    self._shout_sock.sendto(payload, (addr, BEACON_PORT))
                except OSError as e:
                    # Some interfaces (e.g., a cold WLAN that just dropped
                    # association) refuse the send. Don't log every cycle.
                    self.logger.debug("LanBeacon shout to %s failed: %s", addr, e)
                except Exception as e:  # pragma: no cover
                    self.logger.debug("LanBeacon shout error: %s", e)
            wait = 1.0 if in_burst else 10.0
            if self._stop.wait(wait):
                return


# ── interface enumeration helpers ─────────────────────────────────────────

def _enumerate_iface_broadcasts() -> list[str]:
    """Best-effort enumeration of IPv4 broadcast addresses for every active
    interface. Returns an empty list on platforms / fallbacks where neither
    ``psutil`` nor ``ip -j addr`` is available; the global broadcast still
    works in that case."""
    # Try psutil first if installed (cheapest, cross-platform).
    try:
        import psutil  # type: ignore
        out: list[str] = []
        for _name, snics in psutil.net_if_addrs().items():
            for snic in snics:
                if snic.family == socket.AF_INET and snic.broadcast:
                    out.append(snic.broadcast)
        if out:
            return out
    except ImportError:
        pass
    except Exception:
        pass

    # Fall back to ``ip -j addr`` (Linux). Atlas's deploy target is Linux
    # so this nearly always succeeds when psutil isn't around.
    try:
        import subprocess
        r = subprocess.run(
            ["ip", "-4", "-j", "addr"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0 or not r.stdout:
            return []
        ifaces = json.loads(r.stdout)
        out = []
        for iface in ifaces:
            if iface.get("operstate") and iface.get("operstate") == "DOWN":
                continue
            for addr in iface.get("addr_info", []):
                if addr.get("family") != "inet":
                    continue
                bcast = addr.get("broadcast")
                if bcast:
                    out.append(bcast)
        return out
    except Exception:
        return []
