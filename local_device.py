"""
This Atlas on the local network: its name, its .local hostname, its
addresses, and how far its clock can be trusted. One source of truth for
/api/device/local, the Settings section, the mobile apps, the LAN beacon and
the Host check, refreshed by one background thread (LocalDevice) that also:

- keeps avahi on this unit's own name after a conflict (the mDNS guard);
- publishes the legacy name atlas.local as an alias on units that used it,
  only while no other device on the network claims it;
- watches the wall clock for steps (avahi's timers follow it: after a
  backward step it is restarted) and offers GPS time to the root clock
  service while internet time is unavailable.

The clock's basis comes from /run/atlas-clock/state.json, written by the
root `atlas-engine clock` (system/atlas_engine/clock.py), and from
systemd-timesyncd's synchronized flag.
"""
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time

import atlas_helper
import device_identity

logger = logging.getLogger("atlas.local_device")

CLOCK_STATE = os.environ.get("ATLAS_CLOCK_STATE", "/run/atlas-clock/state.json")
TIMESYNC_SYNCED = os.environ.get("ATLAS_TIMESYNC_SYNCED", "/run/systemd/timesync/synchronized")

CLOCK_LABELS = {
    "ntp": "Set from internet time",
    "gps": "Set from GPS",
    "phone": "Set from a paired phone or browser",
    "rtc": "Kept by the clock chip",
    "restored": "Approximate — restored after the power was off",
    "unset": "Not set yet",
}
# Purging data by age is safe on these: a restored clock is at most behind
# the real time, which only ever deletes less. A phone-set clock could be
# ahead, and an unset one could be anything.
RETENTION_SAFE = {"ntp", "gps", "rtc", "restored"}

# Wait between attempts to win back the mDNS name: 30 s, 2 min, 10 min, then hourly.
MDNS_RECLAIM_BACKOFF = (30, 120, 600, 3600)
MDNS_CHECK_INTERVAL = 30

BACKWARD_STEP_S = 2.0
FORWARD_STEP_S = 60.0
GPS_RETRY_S = 600
GPS_RESYNC_S = 6 * 3600
PHONE_HINT_MIN_INTERVAL_S = 60


# ── clock ────────────────────────────────────────────────────────────────────
def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clock_info():
    state = _read_json(CLOCK_STATE)
    basis = "ntp" if os.path.exists(TIMESYNC_SYNCED) else state.get("state")
    if basis not in CLOCK_LABELS:
        basis = "unset"
    rtc = state.get("rtc") or {}
    return {
        "state": basis,
        "label": CLOCK_LABELS[basis],
        "verified": basis in ("ntp", "gps"),
        "approximate": basis in ("phone", "rtc", "restored"),
        "since": state.get("since"),
        "rtc_status": rtc.get("status"),
        "rtc_backed": rtc.get("status") == "kept-time",
        "managed": bool(state),          # False on a unit without atlas-clock
    }


def clock_retention_safe():
    """May age-based purges run? See RETENTION_SAFE. Units without the clock
    service (developer checkouts) keep today's behaviour."""
    info = clock_info()
    return info["state"] in RETENTION_SAFE or not info["managed"]


def date_qualifier():
    """How Ray should present today's date: "" (trust it), "approximately", or None (unknown)."""
    info = clock_info()
    if not info["managed"] or info["state"] in ("ntp", "gps", "phone", "rtc"):
        return ""
    if info["state"] == "restored":
        return "approximately"
    return None


# ── mDNS ─────────────────────────────────────────────────────────────────────
def mdns_reclaim_decision(advertised, expected, attempts):
    """(restart avahi now?, seconds until the next check) for the mDNS name guard.

    After a name conflict avahi renames itself (atlas-xxxx -> atlas-xxxx-2) and
    never tries the real name again, so the label's address silently stops
    resolving even once the conflict is long gone. A restart makes it probe
    again. Back off between attempts: if another device really holds the name,
    avahi just loses the probe again, and probing does not disturb it.
    """
    if not advertised or not expected or advertised == expected:
        return False, MDNS_CHECK_INTERVAL
    return True, MDNS_RECLAIM_BACKOFF[min(attempts, len(MDNS_RECLAIM_BACKOFF) - 1)]


def avahi_host_name():
    """The host name avahi is advertising right now, or "" if it can't be read."""
    try:
        r = subprocess.run(
            ["busctl", "--system", "call", "org.freedesktop.Avahi", "/",
             "org.freedesktop.Avahi.Server", "GetHostName"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return ""
    m = re.fullmatch(r's "(.*)"', r.stdout.strip()) if r.returncode == 0 else None
    return m.group(1) if m else ""


def configured_hostname():
    host = (device_identity.device().get("hostname") or "").strip()
    return host or os.uname().nodename.split(".")[0]


def aliases():
    """Legacy names this unit also answers to (atlas for units that used atlas.local)."""
    host = configured_hostname()
    out = []
    for a in device_identity.device().get("aliases") or []:
        a = str(a).strip().lower()
        if re.fullmatch(r"[a-z0-9-]{1,63}", a) and a != host:
            out.append(a)
    return out


# ── interfaces ───────────────────────────────────────────────────────────────
HOTSPOT_ADDR = "10.42.0.1"


def _kind(iface, addr):
    if addr == HOTSPOT_ADDR:
        return "hotspot"
    if iface.startswith(("wl", "wlan")):
        return "wifi"
    if iface.startswith(("eth", "en")):
        return "ethernet"
    if iface.startswith(("l4tbr", "usb", "rndis")):
        return "usb"
    return "other"


def interfaces():
    """IPv4 addresses Atlas can be reached on: [{iface, address, prefix, kind}]."""
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show", "up"], capture_output=True, text=True,
                             timeout=3).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        iface = parts[1].split("@")[0]
        try:
            ifa = ipaddress.ip_interface(parts[3])
        except ValueError:
            continue
        if ifa.ip.is_loopback or ifa.ip.is_link_local or iface.startswith(("docker", "veth", "br-")):
            continue
        rows.append({"iface": iface, "address": str(ifa.ip), "prefix": ifa.network.prefixlen,
                     "kind": _kind(iface, str(ifa.ip))})
    return rows


def default_route_iface(route_file="/proc/net/route"):
    best = None
    try:
        with open(route_file, encoding="ascii") as f:
            next(f, None)
            for line in f:
                p = line.split()
                if len(p) >= 8 and p[1] == "00000000" and int(p[3], 16) & 0x2:  # default, RTF_GATEWAY
                    metric = int(p[6])
                    if best is None or metric < best[1]:
                        best = (p[0], metric)
    except (OSError, ValueError):
        return None
    return best[0] if best else None


def primary(rows, route_iface):
    """The address a person on Atlas's network should use: the default-route
    interface (the network Atlas joined), else Wi-Fi/Ethernet, else the hotspot."""
    for r in rows:
        if r["iface"] == route_iface and r["kind"] != "hotspot":
            return r
    order = {"wifi": 0, "ethernet": 1, "hotspot": 2, "usb": 3, "other": 4}
    return min(rows, key=lambda r: order.get(r["kind"], 9)) if rows else None


# ── atlas.local alias ────────────────────────────────────────────────────────
class AvahiAlias:
    """Publish NAME.local -> this unit's IPv4 addresses through avahi's D-Bus
    API, without a reverse (PTR) record so the address still names the real
    host. Collisions (another device answers to NAME.local) back off for 10
    minutes; avahi restarts and address changes re-publish."""

    AVAHI_IF_UNSPEC = -1
    AVAHI_PROTO_INET = 0
    PUBLISH_NO_REVERSE = 16
    STATE_ESTABLISHED, STATE_COLLISION, STATE_FAILURE = 2, 3, 4
    COLLISION_BACKOFF_S = 600

    def __init__(self, name, bus_factory=None, mono=time.monotonic):
        self.fqdn = f"{name}.local"
        self._bus_factory = bus_factory or self._system_bus
        self._mono = mono
        self._bus = None
        self._group = None
        self._published = None
        self._retry_at = 0.0
        self.active = False

    @staticmethod
    def _system_bus():
        import dbus
        return dbus.SystemBus(private=True)

    def _server(self):
        if self._bus is None:
            self._bus = self._bus_factory()
        import dbus
        return dbus.Interface(self._bus.get_object("org.freedesktop.Avahi", "/"),
                              "org.freedesktop.Avahi.Server")

    def _drop(self):
        try:
            if self._group is not None:
                self._group.Free()
        except Exception:
            pass
        self._group = None
        self._published = None
        self.active = False

    def tick(self, addresses):
        """addresses: [(iface, ipv4)]. Returns True while the alias is published."""
        want = tuple(sorted(addresses))
        try:
            if self._group is not None:
                state = int(self._group.GetState())
                if state == self.STATE_COLLISION:
                    logger.info("mDNS: %s is in use by another device; not answering to it", self.fqdn)
                    self._drop()
                    self._retry_at = self._mono() + self.COLLISION_BACKOFF_S
                    return False
                if state == self.STATE_FAILURE:
                    self._drop()
                if self._group is not None and self._published == want:
                    self.active = state == self.STATE_ESTABLISHED
                    return self.active
                self._drop()
            if not want or self._mono() < self._retry_at:
                return False
            import dbus
            server = self._server()
            group = dbus.Interface(self._bus.get_object("org.freedesktop.Avahi", server.EntryGroupNew()),
                                   "org.freedesktop.Avahi.EntryGroup")
            for iface, addr in want:
                try:
                    idx = socket.if_nametoindex(iface)
                except OSError:
                    idx = self.AVAHI_IF_UNSPEC
                group.AddAddress(idx, self.AVAHI_PROTO_INET, dbus.UInt32(self.PUBLISH_NO_REVERSE),
                                 self.fqdn, addr)
            group.Commit()
            self._group, self._published = group, want
            return False   # established on a later tick, once probing succeeds
        except Exception as exc:     # avahi restarted or not running: start over next tick
            logger.debug("mDNS alias %s: %s", self.fqdn, exc)
            self._group = None
            self._published = None
            self._bus = None
            self.active = False
            return False


# ── the background keeper ────────────────────────────────────────────────────
class LocalDevice:
    def __init__(self, emit=None, gps_time=None, on_clock_step=None, helper=None,
                 now=time.time, mono=time.monotonic, sleep=time.sleep, alias_factory=AvahiAlias):
        self._emit = emit or (lambda event, data: None)
        self._gps_time = gps_time
        self._on_clock_step = on_clock_step or (lambda direction, seconds: None)
        self._helper = helper or atlas_helper.call
        self._now, self._mono, self._sleep = now, mono, sleep
        self._alias_factory = alias_factory
        self._lock = threading.Lock()
        self._snapshot = None
        self._offset = None
        self._own_step_until = 0.0
        self._mdns_attempts = 0
        self._mdns_next = 0.0
        self._net_next = 0.0
        self._gps_next = 0.0
        self._gps_last_set = None
        self._phone_last = None
        self._advertised = ""
        self._alias = {}
        self._last_emitted = None

    # public ---------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="local-device").start()
        return self

    def snapshot(self):
        with self._lock:
            snap = self._snapshot
        if snap is None:
            snap = self.refresh()
        return dict(snap, clock=clock_info(), updated=self._now())

    def advertised_hostname(self):
        return self._advertised

    def active_aliases(self):
        return [f"{name}.local" for name, a in self._alias.items() if a.active]

    def host_names(self):
        """Extra names the Host check should accept: what avahi advertises now
        (e.g. a conflict rename) and the aliases."""
        names = set(aliases())
        if self._advertised:
            names.add(self._advertised)
        return sorted(names)

    def phone_hint(self, epoch_ms):
        """A paired client's clock while Atlas has no better source. Returns the helper's verdict."""
        info = clock_info()
        if info["state"] in ("ntp", "gps"):
            return {"applied": False, "reason": info["state"]}
        m = self._mono()
        if self._phone_last is not None and m - self._phone_last < PHONE_HINT_MIN_INTERVAL_S:
            return {"applied": False, "reason": "rate limited"}
        try:
            epoch = float(epoch_ms) / 1000.0
        except (TypeError, ValueError):
            return {"applied": False, "reason": "bad time"}
        self._phone_last = m
        return self._set_clock(epoch, "phone")

    def refresh(self):
        rows = interfaces()
        main = primary(rows, default_route_iface())
        host = configured_hostname()
        advertised = avahi_host_name()
        self._advertised = advertised
        device = device_identity.device()
        shown = advertised or host
        snap = {
            "name": device.get("name") or "Atlas",
            "device_id": device.get("device_id"),
            "hostname": host,
            "advertised_hostname": advertised or None,
            "fqdn": f"{shown}.local",
            "local_url": f"https://{shown}.local",
            "conflict": bool(advertised) and advertised != host,
            "mdns_running": bool(advertised),
            "aliases": [{"fqdn": f"{a}.local", "active": bool(self._alias.get(a) and self._alias[a].active)}
                        for a in aliases()],
            "ips": rows,
            "primary_ip": main["address"] if main else None,
            "primary_kind": main["kind"] if main else None,
            "hotspot_active": any(r["kind"] == "hotspot" for r in rows),
        }
        with self._lock:
            self._snapshot = snap
        return snap

    # loop -----------------------------------------------------------------
    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("local device tick failed")
            self._sleep(2.0)

    def tick(self):
        m = self._mono()
        self._watch_clock()
        if m >= self._net_next:
            self._net_next = m + 10
            snap = self.refresh()
            self._tick_aliases(snap["ips"])
            self._maybe_emit(snap)
        if m >= self._mdns_next:
            self._mdns_guard()
        self._gps_clock()

    def _maybe_emit(self, snap):
        key = json.dumps({k: v for k, v in snap.items()}, sort_keys=True, default=str) + clock_info()["state"]
        if key != self._last_emitted:
            if self._last_emitted is not None:
                self._emit("device_network", dict(snap, clock=clock_info()))
            self._last_emitted = key

    def _watch_clock(self):
        offset = self._now() - self._mono()
        prev, self._offset = self._offset, offset
        if prev is None:
            return
        step = offset - prev
        if step < -BACKWARD_STEP_S:
            logger.warning("Clock stepped back %.0f s", -step)
            self._on_clock_step("backward", step)
            if self._mono() >= self._own_step_until:
                # avahi's timers follow the wall clock: restart it so its
                # announcements don't wait out the difference.
                self._call("avahi.reclaim", timeout=30)
            self._net_next = 0.0
            self._emit("device_clock", clock_info())
        elif step > FORWARD_STEP_S:
            logger.info("Clock stepped forward %.0f s", step)
            self._on_clock_step("forward", step)
            self._net_next = 0.0
            self._emit("device_clock", clock_info())

    def _mdns_guard(self):
        expected = configured_hostname()
        advertised = self._advertised or avahi_host_name()
        reclaim, delay = mdns_reclaim_decision(advertised, expected, self._mdns_attempts)
        if reclaim:
            logger.warning(
                "mDNS: avahi is advertising %s.local instead of %s.local after a name "
                "conflict; restarting it to reclaim the name (attempt %d)",
                advertised, expected, self._mdns_attempts + 1,
            )
            r = self._call("avahi.reclaim", timeout=30)
            if not r.get("ok"):
                logger.warning("avahi reclaim failed: %s", str(r.get("error") or r.get("out"))[:120])
            self._mdns_attempts += 1
            self._net_next = self._mono() + 5     # re-read the name once avahi is back
        elif advertised == expected:
            if self._mdns_attempts:
                logger.info("mDNS: %s.local reclaimed", expected)
            self._mdns_attempts = 0
        self._mdns_next = self._mono() + delay

    def _tick_aliases(self, rows):
        names = aliases()
        for gone in set(self._alias) - set(names):
            self._alias.pop(gone)._drop()
        addrs = [(r["iface"], r["address"]) for r in rows if r["kind"] != "usb"]
        for name in names:
            alias = self._alias.get(name)
            if alias is None:
                alias = self._alias[name] = self._alias_factory(name)
            alias.tick(addrs)

    def _gps_clock(self):
        if not self._gps_time:
            return
        m = self._mono()
        if m < self._gps_next:
            return
        state = clock_info()["state"]
        if state == "ntp":
            return
        if state == "gps" and self._gps_last_set is not None and m - self._gps_last_set < GPS_RESYNC_S:
            return
        t = self._gps_time()
        if not t:
            return
        self._gps_next = m + GPS_RETRY_S
        r = self._set_clock(t[0], "gps")
        if r.get("applied"):
            self._gps_last_set = m
            logger.info("Clock set from GPS (%+.1f s)", r.get("stepped_s") or 0.0)

    def _set_clock(self, epoch, source):
        self._own_step_until = self._mono() + 10   # the helper restarts avahi itself if needed
        r = self._call("clock.set", timeout=30, epoch=epoch, source=source)
        if r.get("applied"):
            self._offset = None                    # not a step to react to
            self._emit("device_clock", clock_info())
        return r

    def _call(self, verb, timeout=30, **args):
        try:
            return self._helper(verb, timeout=timeout, **args) or {}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
