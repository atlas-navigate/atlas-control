"""The wall clock after power loss (system/atlas_engine/clock.py).

Simulates the boots seen on a real unit: the PMIC RTC has no backup cell, so
after power loss it reads 1970, registers ~15 s into boot and the kernel sets
the clock from it after systemd-timesyncd has restored its saved clock.
"""
import json
import os
import unittest
from unittest import mock

from engine_sandbox import Sandbox

SAVED = 1791432000          # 2026-10-08, when timesyncd last saved the clock
BUILT = 1790398820          # the release's build time (earlier)


class ClockSandbox:
    def __init__(self, sb):
        from atlas_engine import clock
        self.clock = clock
        self.paths = sb.paths
        self.wall = 34.0                # the RTC's 1970 reading
        self.up = 15.8
        self.sets = []
        self.cmds = []
        os.makedirs(os.path.join(sb.root, "proc", "device-tree", "aliases"), exist_ok=True)
        with open(os.path.join(sb.root, "proc", "device-tree", "aliases", "rtc0"), "w") as f:
            f.write("/bpmp/i2c/vrs@3c\0")
        os.makedirs(self.paths.CURRENT, exist_ok=True)
        with open(os.path.join(self.paths.CURRENT, "release.json"), "w") as f:
            json.dump({"version": "t", "built": BUILT}, f)
        self.patches = [
            mock.patch.object(clock, "now", lambda: self.wall),
            mock.patch.object(clock, "set_clock", self._set),
            mock.patch.object(clock, "uptime", lambda: self.up),
            mock.patch.object(clock, "sleep", self._sleep),
            mock.patch.object(clock, "run", lambda cmd, timeout=20: self.cmds.append(cmd) or 0),
        ]
        for p in self.patches:
            p.start()
        self.on_sleep = None

    def stop(self):
        for p in self.patches:
            p.stop()

    def _set(self, epoch):
        self.sets.append(epoch)
        self.wall = epoch

    def _sleep(self, s):
        self.up += s
        self.wall += s
        if self.on_sleep:
            self.on_sleep()

    def saved_clock(self, epoch=SAVED):
        os.makedirs(os.path.dirname(self.paths.TIMESYNC_CLOCK), exist_ok=True)
        open(self.paths.TIMESYNC_CLOCK, "w").close()
        os.utime(self.paths.TIMESYNC_CLOCK, (epoch, epoch))

    def rtc(self, since_epoch, hctosys=True, name="rtc0"):
        d = os.path.join(self.paths.RTC_CLASS, name)
        os.makedirs(d, exist_ok=True)
        for k, v in (("name", "nvvrs-pseq-rtc"), ("since_epoch", str(int(since_epoch))),
                     ("hctosys", "1" if hctosys else "0")):
            with open(os.path.join(d, k), "w") as f:
                f.write(v + "\n")

    def ntp_synced(self):
        os.makedirs(os.path.dirname(self.paths.TIMESYNC_SYNCED), exist_ok=True)
        open(self.paths.TIMESYNC_SYNCED, "w").close()


class BootTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.c = ClockSandbox(self.sb)
        self.addCleanup(self.c.stop)
        self.clock = self.c.clock

    def test_power_loss_boot_moves_clock_forward_to_the_saved_time(self):
        self.c.saved_clock()
        self.c.rtc(34)
        state = self.clock.boot()
        self.assertEqual(self.c.sets, [SAVED])
        self.assertEqual(state["state"], "restored")
        self.assertEqual(state["floor_from"], "saved")
        self.assertEqual(state["rtc"]["status"], "lost-power")
        self.assertEqual(state["rtc"]["wait"], "registered")
        self.assertEqual(self.clock.effective_state(), "restored")

    def test_without_a_saved_clock_the_build_time_is_the_floor(self):
        self.c.rtc(34)
        state = self.clock.boot()
        self.assertEqual(self.c.sets, [BUILT])
        self.assertEqual(state["floor_from"], "build")

    def test_an_rtc_that_kept_time_is_trusted_and_never_moved(self):
        self.c.saved_clock()
        self.c.wall = SAVED + 7200
        self.c.rtc(SAVED + 7200)
        state = self.clock.boot()
        self.assertEqual(self.c.sets, [])
        self.assertEqual(state["state"], "rtc")
        self.assertEqual(state["rtc"]["status"], "kept-time")

    def test_never_steps_backward(self):
        self.c.saved_clock()
        self.c.wall = SAVED + 10
        self.c.rtc(34)              # RTC lost power, but the clock is already later (e.g. timesyncd)
        self.clock.boot()
        self.assertEqual(self.c.sets, [])

    def test_waits_for_a_late_rtc_before_flooring(self):
        # The RTC registers 0.5 s into the wait and steps the clock back to 1970.
        self.c.saved_clock()
        self.c.wall = SAVED - 100     # what timesyncd/systemd left before the RTC step
        self.c.up = 15.2
        calls = {"n": 0}

        def register():
            calls["n"] += 1
            if calls["n"] == 5:
                self.c.rtc(34)
                self.c.wall = 34.5    # the kernel's hctosys step
        self.c.on_sleep = register
        state = self.clock.boot()
        self.assertEqual(state["rtc"]["wait"], "registered")
        self.assertEqual(self.c.sets, [SAVED])

    def test_wait_is_bounded_when_the_rtc_never_registers(self):
        self.c.saved_clock()
        self.c.up = 12.0
        state = self.clock.boot()
        self.assertEqual(state["rtc"]["wait"], "timeout")
        self.assertGreaterEqual(self.c.up, self.clock.BOOT_WAIT_UPTIME_S)
        self.assertLess(self.c.up, self.clock.BOOT_WAIT_UPTIME_S + 1)
        self.assertEqual(self.c.sets, [SAVED])

    def test_no_wait_on_a_board_without_the_rtc(self):
        os.remove(os.path.join(self.sb.root, "proc", "device-tree", "aliases", "rtc0"))
        self.c.saved_clock()
        self.c.up = 5.0
        state = self.clock.boot()
        self.assertEqual(state["rtc"]["wait"], "skipped")
        self.assertEqual(self.c.up, 5.0)

    def test_unreadable_rtc_stops_waiting_after_it_appears(self):
        self.c.saved_clock()
        self.c.rtc(34, hctosys=False)  # registered but the kernel could not read it
        up0 = self.c.up
        state = self.clock.boot()
        self.assertEqual(state["rtc"]["wait"], "registered")
        self.assertLessEqual(self.c.up - up0, self.clock.HCTOSYS_SETTLE_S + 0.2)

    def test_ntp_synchronized_wins(self):
        self.c.saved_clock()
        self.c.rtc(34)
        self.c.ntp_synced()
        self.clock.boot()
        self.assertEqual(self.clock.effective_state(), "ntp")

    def test_late_rtc_backstop_moves_forward_again_and_restarts_avahi(self):
        self.c.saved_clock()
        self.c.rtc(34)
        self.clock.boot()
        self.c.wall = 40.0           # a later hctosys stepped it back
        state = self.clock.rtc_added()
        self.assertEqual(self.c.wall, SAVED)
        self.assertIn(["systemctl", "try-restart", "avahi-daemon.service"], self.c.cmds)
        self.assertEqual(state["rtc"]["wait"], "late")

    def test_backstop_at_boot_is_a_no_op(self):
        self.c.saved_clock()
        self.c.rtc(34)
        first = self.clock.boot()
        state = self.clock.rtc_added()
        self.assertEqual(state["rtc"], first["rtc"])
        self.assertEqual(self.c.cmds, [])


class SetTimeTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.c = ClockSandbox(self.sb)
        self.addCleanup(self.c.stop)
        self.clock = self.c.clock
        self.c.saved_clock()
        self.c.rtc(34)
        self.clock.boot()           # restored at SAVED

    def test_phone_moves_a_restored_clock_forward(self):
        r = self.clock.set_time(SAVED + 86400, "phone")
        self.assertTrue(r["applied"])
        self.assertEqual(self.c.wall, SAVED + 86400)
        self.assertEqual(self.clock.effective_state(), "phone")
        self.assertNotIn(["systemctl", "try-restart", "avahi-daemon.service"], self.c.cmds)

    def test_phone_never_moves_the_clock_back(self):
        r = self.clock.set_time(SAVED - 3600, "phone")
        self.assertFalse(r["applied"])
        self.assertEqual(self.c.wall, SAVED)

    def test_phone_agreeing_within_seconds_confirms_without_a_step(self):
        r = self.clock.set_time(SAVED + 1, "phone")
        self.assertTrue(r["applied"])
        self.assertEqual(r["stepped_s"], 0.0)
        self.assertEqual(self.c.sets, [SAVED])
        self.assertEqual(self.clock.effective_state(), "phone")

    def test_phone_cannot_override_gps(self):
        self.clock.set_time(SAVED + 600, "gps")
        r = self.clock.set_time(SAVED + 86400, "phone")
        self.assertFalse(r["applied"])

    def test_gps_corrects_either_way_and_restarts_avahi_after_a_backward_step(self):
        self.clock.set_time(SAVED + 86400, "phone")      # a phone that was a day fast
        r = self.clock.set_time(SAVED + 600, "gps")
        self.assertTrue(r["applied"])
        self.assertLess(r["stepped_s"], 0)
        self.assertEqual(self.c.wall, SAVED + 600)
        self.assertIn(["systemctl", "try-restart", "avahi-daemon.service"], self.c.cmds)
        self.assertIn(["hwclock", "--systohc", "--utc"], self.c.cmds)
        self.assertEqual(self.clock.effective_state(), "gps")

    def test_nothing_overrides_ntp(self):
        self.c.ntp_synced()
        for source in ("gps", "phone"):
            r = self.clock.set_time(SAVED + 86400, source)
            self.assertFalse(r["applied"])
            self.assertEqual(r["reason"], "ntp")

    def test_implausible_times_are_refused(self):
        with self.assertRaises(ValueError):
            self.clock.set_time(float("nan"), "phone")
        with self.assertRaises(ValueError):
            self.clock.set_time(1000, "gps")               # GPS week-rollover style garbage
        with self.assertRaises(ValueError):
            self.clock.set_time(SAVED, "radio")
        r = self.clock.set_time(BUILT - 3 * 86400, "gps")  # before this software existed
        self.assertFalse(r["applied"])
        r = self.clock.set_time(BUILT + 30 * 365 * 86400, "phone")
        self.assertFalse(r["applied"])

    def test_helper_verb(self):
        from atlas_engine import helper
        ok = helper.handle({"verb": "clock.set", "args": {"epoch": SAVED + 300, "source": "phone"}})
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["applied"])
        bad = helper.handle({"verb": "clock.set", "args": {"epoch": "soon", "source": "phone"}})
        self.assertFalse(bad["ok"])


class UnitFileTests(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "system")

    def test_clock_unit_orders_before_timesyncd_and_avahi_but_not_after_network(self):
        text = open(os.path.join(self.ROOT, "systemd", "atlas-clock.service")).read()
        befores = " ".join(l.split("=", 1)[1] for l in text.splitlines() if l.startswith("Before="))
        for unit in ("systemd-timesyncd.service", "time-set.target", "avahi-daemon.service",
                     "atlas-control.service", "atlas-firstboot.service"):
            self.assertIn(unit, befores)
        afters = " ".join(l.split("=", 1)[1] for l in text.splitlines() if l.startswith("After="))
        for unit in ("network", "time-sync.target", "timesyncd"):
            self.assertNotIn(unit, afters)
        self.assertIn("DefaultDependencies=no", text)

    def test_shipped_and_enabled(self):
        spec = json.load(open(os.path.join(self.ROOT, "files.json")))
        srcs = {e["src"] for e in spec["files"]}
        self.assertIn("systemd/atlas-clock.service", srcs)
        self.assertIn("systemd/atlas-clock-rtc.service", srcs)
        self.assertIn("atlas-clock.service", spec["enable"])
        rules = open(os.path.join(self.ROOT, "udev", "99-atlas-devices.rules")).read()
        self.assertIn('SUBSYSTEM=="rtc"', rules)
        self.assertIn("atlas-clock-rtc.service", rules)


if __name__ == "__main__":
    unittest.main()
