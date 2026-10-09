"""Ray's clock boost: only while answering, only on mains, always released."""
import json
import os
import unittest

from engine_sandbox import Sandbox

import perf_boost


class FakeTimer:
    made = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.cancelled, self.daemon = delay, fn, False, False
        FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


class AppSideTests(unittest.TestCase):
    def setUp(self):
        FakeTimer.made = []
        self.calls = []
        self.mono = [100.0]
        self.mains = True
        self.enabled = True
        self.reply = {"boosted": True}
        self.b = perf_boost.PerfBoost(
            enabled=lambda: self.enabled, on_mains=lambda: self.mains,
            helper=lambda verb, timeout=10: self.calls.append(verb) or self.reply,
            mono=lambda: self.mono[0], spawn=lambda fn: fn(), timer=FakeTimer)

    def test_boosts_while_answering_and_releases_after_idle(self):
        with self.b.generating():
            self.assertEqual(self.calls, ["perf.boost"])
        self.assertEqual(self.calls, ["perf.boost"])            # not yet: 30 s idle first
        self.assertEqual(FakeTimer.made[-1].delay, perf_boost.IDLE_RELEASE_S)
        FakeTimer.made[-1].fire()
        self.assertEqual(self.calls, ["perf.boost", "perf.release"])

    def test_a_new_answer_cancels_the_pending_release(self):
        with self.b.generating():
            pass
        pending = FakeTimer.made[-1]
        with self.b.generating():
            self.assertTrue(pending.cancelled)
        pending.fire()
        self.assertNotIn("perf.release", self.calls)

    def test_lease_is_renewed_while_streaming(self):
        with self.b.generating():
            self.b.renew()
            self.mono[0] += perf_boost.RENEW_S
            self.b.renew()
        self.assertEqual(self.calls, ["perf.boost", "perf.boost"])

    def test_never_on_battery_and_switch_off(self):
        self.mains = False
        with self.b.generating():
            pass
        self.enabled, self.mains = False, True
        with self.b.generating():
            pass
        self.assertEqual(self.calls, [])

    def test_a_refused_boost_is_not_renewed_or_released(self):
        self.reply = {"boosted": False, "reason": "hot (86 °C)"}
        with self.b.generating():
            self.mono[0] += 60
            self.b.renew()
        self.assertEqual(self.calls, ["perf.boost"])
        self.assertEqual(FakeTimer.made, [])

    def test_overlapping_answers_release_once_after_the_last(self):
        with self.b.generating():
            with self.b.generating():
                pass
            self.assertEqual(FakeTimer.made, [])
        FakeTimer.made[-1].fire()
        self.assertEqual(self.calls.count("perf.release"), 1)


class RootSideTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        from atlas_engine import perf
        self.perf = perf
        for node, lo, hi in (("17000000.gpu", 306000000, 1020000000), ("bwmgr", 204000000, 3199000000)):
            d = os.path.join(self.sb.paths.DEVFREQ, node)
            os.makedirs(d)
            open(os.path.join(d, "min_freq"), "w").write(f"{lo}\n")
            open(os.path.join(d, "max_freq"), "w").write(f"{hi}\n")
        self.zone = os.path.join(self.sb.paths.THERMAL, "thermal_zone0")
        os.makedirs(self.zone)
        self.temp(50)
        self.cmds = []

    def temp(self, c):
        open(os.path.join(self.zone, "temp"), "w").write(f"{int(c * 1000)}\n")

    def min_freq(self, node):
        return open(os.path.join(self.sb.paths.DEVFREQ, node, "min_freq")).read().strip()

    def test_boost_raises_floors_and_release_restores_them(self):
        r = self.perf.boost(run=self.cmds.append)
        self.assertTrue(r["boosted"])
        self.assertEqual(self.min_freq("17000000.gpu"), "1020000000")
        self.assertEqual(self.min_freq("bwmgr"), "3199000000")
        self.assertIn(["systemctl", "restart", "atlas-perf-release.timer"], self.cmds)
        self.perf.boost(run=self.cmds.append)                  # renewal keeps the original floors saved
        self.perf.release(run=self.cmds.append)
        self.assertEqual(self.min_freq("17000000.gpu"), "306000000")
        self.assertEqual(self.min_freq("bwmgr"), "204000000")
        self.assertIsNone(self.perf.state())

    def test_hot_unit_is_not_boosted(self):
        self.temp(86)
        r = self.perf.boost(run=self.cmds.append)
        self.assertFalse(r["boosted"])
        self.assertEqual(self.min_freq("bwmgr"), "204000000")

    def test_helper_verbs(self):
        from atlas_engine import helper
        from unittest import mock
        with mock.patch.object(helper, "_run", return_value={"rc": 0, "out": "", "err": ""}):
            self.assertTrue(helper.handle({"verb": "perf.boost", "args": {}})["boosted"])
            self.assertTrue(helper.handle({"verb": "perf.release", "args": {}})["released"])

    def test_lease_units_ship_without_being_enabled(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = json.load(open(os.path.join(repo, "system", "files.json")))
        srcs = {e["src"] for e in spec["files"]}
        self.assertIn("systemd/atlas-perf-release.timer", srcs)
        self.assertNotIn("atlas-perf-release.timer", spec["enable"])
        timer = open(os.path.join(repo, "system", "systemd", "atlas-perf-release.timer")).read()
        self.assertIn("OnActiveSec=90s", timer)


if __name__ == "__main__":
    unittest.main()
