"""This Atlas on the local network (local_device.py): clock basis and what it
gates, reactions to clock steps, GPS and phone time, the atlas.local alias,
the address snapshot, and the routes that serve it."""
import json
import os
import unittest
from unittest import mock

import local_device


def write_clock(state=None, synced=False, rtc_status="lost-power"):
    os.makedirs(os.path.dirname(local_device.CLOCK_STATE), exist_ok=True)
    if state is None:
        if os.path.exists(local_device.CLOCK_STATE):
            os.remove(local_device.CLOCK_STATE)
    else:
        with open(local_device.CLOCK_STATE, "w") as f:
            json.dump({"state": state, "since": 1791432000, "rtc": {"status": rtc_status}}, f)
    if synced:
        open(local_device.TIMESYNC_SYNCED, "w").close()
    elif os.path.exists(local_device.TIMESYNC_SYNCED):
        os.remove(local_device.TIMESYNC_SYNCED)


class ClockBasisTests(unittest.TestCase):
    def tearDown(self):
        write_clock(None)

    def test_labels_and_trust(self):
        write_clock("restored")
        info = local_device.clock_info()
        self.assertEqual(info["state"], "restored")
        self.assertTrue(info["approximate"])
        self.assertFalse(info["verified"])
        write_clock("restored", synced=True)
        self.assertEqual(local_device.clock_info()["state"], "ntp")
        self.assertTrue(local_device.clock_info()["verified"])

    def test_retention_waits_for_a_trustworthy_clock(self):
        for state, safe in (("ntp", True), ("gps", True), ("rtc", True), ("restored", True),
                            ("phone", False), ("unset", False)):
            write_clock(state)
            self.assertEqual(local_device.clock_retention_safe(), safe, state)
        write_clock(None)                       # developer checkout: no clock service
        self.assertTrue(local_device.clock_retention_safe())

    def test_date_qualifier_for_ray(self):
        write_clock("restored")
        self.assertEqual(local_device.date_qualifier(), "approximately")
        write_clock("unset")
        self.assertIsNone(local_device.date_qualifier())
        write_clock("phone")
        self.assertEqual(local_device.date_qualifier(), "")


class FakeClock:
    def __init__(self):
        self.wall = 1791432000.0
        self.mono = 500.0

    def now(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, s):
        self.wall += s
        self.mono += s


class KeeperTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeClock()
        self.calls = []
        self.events = []
        self.steps = []
        self.helper_reply = {"ok": True, "applied": True, "stepped_s": 0.0}
        self.gps = None
        self.dev = local_device.LocalDevice(
            emit=lambda e, d: self.events.append(e),
            gps_time=lambda: self.gps,
            on_clock_step=lambda d, s: self.steps.append(d),
            helper=lambda verb, timeout=30, **a: self.calls.append((verb, a)) or dict(self.helper_reply),
            now=self.c.now, mono=self.c.monotonic)
        write_clock("restored")
        self.addCleanup(write_clock, None)

    def test_backward_step_restarts_avahi_and_tells_the_app(self):
        self.dev._watch_clock()
        self.c.advance(2)
        self.c.wall -= 3600                       # e.g. GPS corrected a phone that was an hour fast
        self.dev._watch_clock()
        self.assertEqual(self.steps, ["backward"])
        self.assertEqual([v for v, _ in self.calls], ["avahi.reclaim"])
        self.assertIn("device_clock", self.events)

    def test_forward_step_needs_no_avahi_restart(self):
        self.dev._watch_clock()
        self.c.wall += 86400 * 3                  # NTP after three days unplugged
        self.dev._watch_clock()
        self.assertEqual(self.steps, ["forward"])
        self.assertEqual(self.calls, [])

    def test_small_jitter_is_not_a_step(self):
        self.dev._watch_clock()
        self.c.advance(2)
        self.c.wall += 0.5
        self.dev._watch_clock()
        self.assertEqual(self.steps, [])

    def test_gps_time_sets_the_clock_once_then_rests(self):
        self.gps = (1791440000.0, "ubx", 0.0)
        self.dev._gps_clock()
        self.assertEqual(self.calls, [("clock.set", {"epoch": 1791440000.0, "source": "gps"})])
        write_clock("gps")
        self.c.advance(700)
        self.dev._gps_clock()                     # verified by GPS: no resync for hours
        self.assertEqual(len(self.calls), 1)
        self.c.advance(local_device.GPS_RESYNC_S)
        self.dev._gps_clock()
        self.assertEqual(len(self.calls), 2)

    def test_gps_is_not_used_while_internet_time_is(self):
        write_clock("restored", synced=True)
        self.gps = (1791440000.0, "ubx", 0.0)
        self.dev._gps_clock()
        self.assertEqual(self.calls, [])

    def test_failed_gps_attempts_back_off(self):
        self.helper_reply = {"ok": True, "applied": False, "reason": "before this software was built"}
        self.gps = (1791440000.0, "ubx", 0.0)
        self.dev._gps_clock()
        self.c.advance(60)
        self.dev._gps_clock()
        self.assertEqual(len(self.calls), 1)

    def test_own_clock_change_is_not_mistaken_for_a_step(self):
        self.dev._watch_clock()
        self.gps = (1791440000.0, "ubx", 0.0)
        self.dev._gps_clock()
        self.c.wall = 1791440000.0                # what the helper just did
        self.dev._watch_clock()
        self.assertEqual(self.steps, [])

    def test_phone_hint_is_forwarded_and_rate_limited(self):
        r = self.dev.phone_hint(1791500000123)
        self.assertTrue(r["applied"])
        self.assertEqual(self.calls[0], ("clock.set", {"epoch": 1791500000.123, "source": "phone"}))
        self.assertEqual(self.dev.phone_hint(1791500001000)["reason"], "rate limited")
        write_clock("gps")
        self.c.advance(120)
        self.assertEqual(self.dev.phone_hint(1791500001000)["reason"], "gps")
        self.assertEqual(self.dev.phone_hint("soon")["reason"], "gps")


class FakeGroup:
    def __init__(self, bus):
        self.bus = bus
        self.addrs = []
        self.state = 1
        self.freed = False

    def AddAddress(self, idx, proto, flags, name, addr):
        self.addrs.append((name, addr, int(flags)))

    def Commit(self):
        self.state = 1

    def GetState(self):
        if self.bus.restarted:
            raise RuntimeError("org.freedesktop.Avahi: unknown object")
        return self.state

    def Free(self):
        self.freed = True


class FakeBus:
    def __init__(self):
        self.groups = []
        self.restarted = False

    def get_object(self, service, path):
        return self

    # Server
    def EntryGroupNew(self):
        g = FakeGroup(self)
        self.groups.append(g)
        return f"/Client1/EntryGroup{len(self.groups)}"


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.mono = [0.0]
        self.bus = FakeBus()
        patch = mock.patch("dbus.Interface", lambda obj, iface: self.bus.groups[-1]
                           if iface == "org.freedesktop.Avahi.EntryGroup" else obj, create=True)
        try:
            import dbus  # noqa: F401
        except ImportError:
            self.skipTest("dbus-python not installed")
        patch.start()
        self.addCleanup(patch.stop)
        self.alias = local_device.AvahiAlias("atlas", bus_factory=lambda: self.bus, mono=lambda: self.mono[0])

    def test_publishes_without_reverse_records_and_reports_established(self):
        self.assertFalse(self.alias.tick([("wlan0", "192.168.1.20")]))
        g = self.bus.groups[-1]
        self.assertEqual(g.addrs, [("atlas.local", "192.168.1.20", 16)])
        g.state = 2
        self.assertTrue(self.alias.tick([("wlan0", "192.168.1.20")]))
        self.assertTrue(self.alias.active)

    def test_collision_backs_off(self):
        self.alias.tick([("wlan0", "192.168.1.20")])
        self.bus.groups[-1].state = 3              # another device answers to atlas.local
        self.assertFalse(self.alias.tick([("wlan0", "192.168.1.20")]))
        self.assertTrue(self.bus.groups[-1].freed)
        self.mono[0] = 300
        self.alias.tick([("wlan0", "192.168.1.20")])
        self.assertEqual(len(self.bus.groups), 1)  # still backing off
        self.mono[0] = 700
        self.alias.tick([("wlan0", "192.168.1.20")])
        self.assertEqual(len(self.bus.groups), 2)

    def test_republishes_after_an_address_change_or_avahi_restart(self):
        self.alias.tick([("wlan0", "192.168.1.20")])
        self.bus.groups[-1].state = 2
        self.alias.tick([("wlan0", "192.168.1.20")])
        self.alias.tick([("wlan0", "192.168.1.31")])
        self.assertEqual(self.bus.groups[-1].addrs[0][1], "192.168.1.31")
        self.bus.restarted = True
        self.assertFalse(self.alias.tick([("wlan0", "192.168.1.31")]))
        self.bus.restarted = False
        self.alias.tick([("wlan0", "192.168.1.31")])
        self.assertEqual(len(self.bus.groups), 3)


class AddressTests(unittest.TestCase):
    IP_OUT = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "3: wlP1p1s0    inet 192.168.1.20/24 brd 192.168.1.255 scope global dynamic wlP1p1s0\n"
        "4: wlan1    inet 10.42.0.1/24 brd 10.42.0.255 scope global wlan1\n"
        "5: l4tbr0    inet 192.168.55.1/24 scope global l4tbr0\n"
        "6: docker0    inet 172.17.0.1/16 scope global docker0\n"
    )

    def test_interfaces_and_primary(self):
        with mock.patch.object(local_device.subprocess, "run",
                               return_value=mock.Mock(stdout=self.IP_OUT, returncode=0)):
            rows = local_device.interfaces()
        kinds = {r["address"]: r["kind"] for r in rows}
        self.assertEqual(kinds, {"192.168.1.20": "wifi", "10.42.0.1": "hotspot", "192.168.55.1": "usb"})
        self.assertEqual(local_device.primary(rows, "wlP1p1s0")["address"], "192.168.1.20")
        self.assertEqual(local_device.primary([r for r in rows if r["kind"] != "wifi"], None)["address"],
                         "10.42.0.1")

    def test_default_route(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                    "wlP1p1s0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\n"
                    "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n"
                    "wlP1p1s0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\n")
        self.addCleanup(os.remove, f.name)
        self.assertEqual(local_device.default_route_iface(f.name), "eth0")

    def test_snapshot_reports_conflict_and_aliases(self):
        dev = local_device.LocalDevice(helper=lambda *a, **k: {})
        with mock.patch.object(local_device, "interfaces", return_value=[
                {"iface": "wlan0", "address": "192.168.1.20", "prefix": 24, "kind": "wifi"}]), \
                mock.patch.object(local_device, "default_route_iface", return_value="wlan0"), \
                mock.patch.object(local_device, "avahi_host_name", return_value="atlas-pjnt-2"), \
                mock.patch.object(local_device.device_identity, "device", return_value={
                    "name": "Atlas-PJNT", "hostname": "atlas-pjnt", "aliases": ["atlas"], "device_id": "d"}):
            snap = dev.refresh()
            self.assertEqual(snap["fqdn"], "atlas-pjnt-2.local")
            self.assertTrue(snap["conflict"])
            self.assertEqual(snap["primary_ip"], "192.168.1.20")
            self.assertEqual(snap["aliases"], [{"fqdn": "atlas.local", "active": False}])
            self.assertEqual(dev.host_names(), ["atlas", "atlas-pjnt-2"])


class RouteTests(unittest.TestCase):
    """The routes are paired-only and serve the background snapshot."""

    def setUp(self):
        import app
        self.app = app
        self.client = app.app.test_client()
        app.limiter.reset()

    def test_paired_only(self):
        with mock.patch.dict(os.environ, {"ATLAS_PAIRING": "on"}):
            with mock.patch.object(self.app.pairing, "required", return_value=True):
                r = self.client.get("/api/device/local", base_url="https://192.168.1.20",
                                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 401)

    def test_snapshot_and_clock_hint(self):
        snap = {"name": "Atlas-PJNT", "hostname": "atlas-pjnt", "fqdn": "atlas-pjnt.local",
                "local_url": "https://atlas-pjnt.local", "ips": [], "primary_ip": None}
        with mock.patch.object(self.app.pairing, "required", return_value=False), \
                mock.patch.object(self.app.local_device_state, "_snapshot", snap), \
                mock.patch.object(self.app.local_device_state, "phone_hint",
                                  return_value={"applied": True}) as hint:
            body = self.client.get("/api/device/local", base_url="https://192.168.1.20",
                                   environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
            self.assertEqual(body["fqdn"], "atlas-pjnt.local")
            self.assertIn("clock", body)
            r = self.client.post("/api/device/clock-hint", json={"epoch_ms": 1791500000000},
                                 base_url="https://192.168.1.20", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertTrue(r.get_json()["applied"])
        hint.assert_called_once_with(1791500000000)

    def test_host_check_accepts_the_advertised_name_and_aliases(self):
        with mock.patch.object(self.app.local_device_state, "host_names", return_value=["atlas-pjnt-2"]):
            policy = self.app.request_guard.HostPolicy(extra_names=self.app._extra_hostnames, ttl=0)
            self.assertTrue(policy.allowed("atlas-pjnt-2.local"))
            self.assertFalse(policy.allowed("evil.example"))


if __name__ == "__main__":
    unittest.main()
