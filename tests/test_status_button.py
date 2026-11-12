"""The Atlas button: detection, presses, the 15-second reset hold, the light."""
import unittest
from unittest import mock

import status_button as sb


class FakeModule:
    """One DFR0991 on the fake buses."""

    def __init__(self):
        self.pressed = False
        self.rgb = None
        self.writes = []
        self.dead = False


class FakeSMBusFactory:
    """Stands in for the smbus module: SMBus(bus) talks to modules[bus]."""

    def __init__(self, modules):
        self.modules = modules

    def SMBus(self, bus):
        factory = self

        class _Bus:
            def _module(self, addr):
                module = factory.modules.get(bus)
                if module is None or addr != sb.DEFAULT_ADDR or module.dead:
                    raise OSError(121, "Remote I/O error")
                return module

            def read_i2c_block_data(self, addr, reg, length):
                module = self._module(addr)
                if reg == sb.REG_PART_ID:
                    return [sb.PART_ID >> 8, sb.PART_ID & 0xFF][:length]
                if reg == sb.REG_PRESSED:
                    return [1 if module.pressed else 0]
                return [0] * length

            def write_i2c_block_data(self, addr, reg, data):
                module = self._module(addr)
                assert reg == sb.REG_RGB and len(data) == 3
                module.rgb = tuple(data)
                module.writes.append(tuple(data))

            def close(self):
                pass
        return _Bus()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class DetectTests(unittest.TestCase):
    def test_found_on_the_configured_bus(self):
        smbus = FakeSMBusFactory({1: FakeModule()})
        with mock.patch.dict("os.environ", {"ATLAS_BUTTON_I2C_BUS": "1"}):
            self.assertEqual(sb.detect(smbus_module=smbus), {"bus": 1, "addr": 0x2A, "part_id": sb.PART_ID})

    def test_found_on_the_wrong_pins_is_still_reported(self):
        smbus = FakeSMBusFactory({7: FakeModule()})
        found = sb.detect(smbus_module=smbus)
        self.assertEqual(found["bus"], 7)
        self.assertNotIn(7, sb.SLOW_BUSES)

    def test_absent(self):
        self.assertIsNone(sb.detect(smbus_module=FakeSMBusFactory({})))

    def test_palette_never_offers_the_reset_colours(self):
        rgbs = {rgb for _label, rgb, _swatch in sb.PAIR_COLORS.values()}
        self.assertNotIn(sb.RED, rgbs)
        self.assertNotIn(sb.AMBER, rgbs)
        self.assertEqual(len(sb.palette()), 6)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.module = FakeModule()
        self.smbus = FakeSMBusFactory({1: self.module})
        self.clock = Clock()
        self.state = {"unclaimed": False}
        self.presses = []
        self.resets = []
        self.reset_result = True
        self.threads = mock.patch.object(sb.threading, "Thread", side_effect=self._inline_thread)
        self.threads.start()
        self.addCleanup(self.threads.stop)
        self.c = sb.ButtonController(state_fn=lambda: dict(self.state), on_press=lambda: self.presses.append(1),
                                     on_reset=self._reset, smbus_module=self.smbus, clock=self.clock,
                                     detect_fn=lambda: sb.detect(smbus_module=self.smbus))

    def _inline_thread(self, target=None, **_kw):
        thread = mock.Mock()
        thread.start = target            # the reset worker runs at once
        return thread

    def _reset(self):
        self.resets.append(self.module.rgb)
        return self.reset_result

    def run_for(self, seconds, step=0.05):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.c.tick()
            self.clock.t += step

    def test_attaches_and_reports_ok(self):
        self.c.tick()
        self.assertEqual(self.c.snapshot(), {"state": "ok", "bus": 1, "addr": 0x2A})

    def test_short_press_is_a_press(self):
        self.c.tick()
        self.module.pressed = True
        self.run_for(0.5)
        self.module.pressed = False
        self.run_for(0.1)
        self.assertEqual(self.presses, [1])
        self.assertEqual(self.resets, [])

    def test_releasing_before_15_seconds_cancels_the_reset(self):
        self.c.tick()
        self.module.pressed = True
        self.run_for(14.0)
        self.assertIn(sb.AMBER, self.module.writes)          # warned while held
        self.module.pressed = False
        self.run_for(0.2)
        self.assertEqual(self.resets, [])
        self.assertEqual(self.presses, [])                    # a long hold is not a press either

    def test_holding_15_seconds_turns_red_and_resets_once(self):
        self.c.tick()
        self.module.pressed = True
        self.run_for(15.2)
        self.assertEqual(self.resets, [sb.RED])               # red before the reset began
        self.run_for(3.0)                                     # still held: no second reset
        self.module.pressed = False
        self.run_for(0.2)
        self.assertEqual(len(self.resets), 1)
        self.assertEqual(self.presses, [])

    def test_refused_reset_flashes_red(self):
        self.reset_result = False
        self.c.tick()
        self.module.pressed = True
        self.run_for(15.1)
        self.module.pressed = False
        self.module.writes.clear()
        self.run_for(1.0)
        self.assertIn(sb.RED, self.module.writes)
        self.assertIn(sb.OFF, self.module.writes)

    def test_light_shows_what_atlas_is_waiting_for(self):
        self.state = {"unclaimed": True}
        self.run_for(0.1)
        self.assertEqual(self.module.rgb, sb.DIM_WHITE)
        self.state = {"unclaimed": True, "pair_color": "purple"}
        self.run_for(0.1)
        self.assertEqual(self.module.rgb, sb.PAIR_COLORS["purple"][1])
        self.state = {"unclaimed": False}
        self.c.flash_success()
        self.run_for(0.6)
        self.assertIn(sb.SUCCESS, self.module.writes)
        self.run_for(3.0)
        self.assertEqual(self.module.rgb, sb.OFF)
        self.state = {"approval_pending": True}
        self.module.writes.clear()
        self.run_for(1.5)
        self.assertIn(sb.APPROVAL, self.module.writes)

    def test_the_light_is_only_written_when_it_changes(self):
        self.state = {"unclaimed": True}
        self.run_for(2.0)
        self.assertEqual(self.module.writes, [sb.DIM_WHITE])

    def test_the_light_comes_back_after_something_else_set_it(self):
        self.state = {"unclaimed": True}
        self.run_for(0.1)
        self.module.rgb = sb.OFF                              # e.g. status_button.py --flash
        self.run_for(sb.REFRESH_S + 0.2)
        self.assertEqual(self.module.rgb, sb.DIM_WHITE)

    def test_a_frozen_button_is_reported_and_picked_up_again(self):
        self.state = {"unclaimed": True}
        self.run_for(0.1)
        self.module.dead = True
        self.run_for(0.5)
        self.assertEqual(self.c.snapshot()["state"], "not_responding")
        self.module.dead = False                              # unplugged and plugged back in
        self.module.writes.clear()
        self.run_for(sb.RETRY_S + 0.2)
        self.assertEqual(self.c.snapshot()["state"], "ok")
        self.assertEqual(self.module.writes, [sb.DIM_WHITE])  # its light is restored

    def test_absent_button_is_looked_for_again(self):
        smbus = FakeSMBusFactory({})
        c = sb.ButtonController(state_fn=dict, on_press=lambda: None, on_reset=lambda: True,
                                smbus_module=smbus, clock=self.clock, detect_fn=lambda: sb.detect(smbus_module=smbus))
        c.tick()
        self.assertEqual(c.snapshot()["state"], "absent")
        smbus.modules[1] = FakeModule()
        self.clock.t += sb.ABSENT_RETRY_S + 1
        c.tick()
        self.assertEqual(c.snapshot()["state"], "ok")

    def test_wrong_pins_are_reported(self):
        smbus = FakeSMBusFactory({7: FakeModule()})
        c = sb.ButtonController(state_fn=dict, on_press=lambda: None, on_reset=lambda: True,
                                smbus_module=smbus, clock=self.clock, detect_fn=lambda: sb.detect(smbus_module=smbus))
        with self.assertLogs("status_button", "WARNING") as logs:
            c.tick()
        self.assertEqual(c.snapshot()["state"], "wrong_pins")
        self.assertIn("pins 27/28", "\n".join(logs.output))


class AppButtonResetTests(unittest.TestCase):
    """What app.py does when the button has been held for 15 seconds."""

    def setUp(self):
        import os
        import tempfile
        import app
        self.app = app
        self.tmp = tempfile.mkdtemp(prefix="atlas-button-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.dev_mode = os.path.join(self.tmp, "dev-mode")

        hub = mock.Mock()                      # the gevent hub runs the callback at once
        hub.loop.run_callback_threadsafe = lambda spawn, fn: fn()
        self.resets = []

        def reset(actor, after=None):
            self.resets.append((actor, after))
            return self.reset_answer
        self.reset_answer = ({"ok": True}, 200)
        self.audits = []
        for target, attr, value in ((app, "_MAIN_HUB", hub), (app, "_run_factory_reset", reset),
                                    (app, "DEV_MODE_FILE", self.dev_mode),
                                    (app.audit_log, "record", lambda *a, **k: self.audits.append((a, k))),
                                    (app.time, "sleep", lambda s: None)):
            patch = mock.patch.object(target, attr, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_developer_unit_only_pretends(self):
        open(self.dev_mode, "w").close()
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("ATLAS_BUTTON_RESET_DRYRUN", None)
            self.assertTrue(self.app._button_reset())
        self.assertEqual(self.resets, [])
        self.assertEqual(self.audits, [(("factory_reset", "Atlas button"), {"dry_run": True})])

    def test_a_developer_unit_can_ask_for_the_real_reset(self):
        open(self.dev_mode, "w").close()
        with mock.patch.dict("os.environ", {"ATLAS_BUTTON_RESET_DRYRUN": "0"}):
            self.assertTrue(self.app._button_reset())
        self.assertEqual(self.resets, [("Atlas button", self.app._button_reset_hotspot)])

    def test_a_retail_unit_always_resets(self):
        with mock.patch.dict("os.environ", {"ATLAS_BUTTON_RESET_DRYRUN": "1"}):   # no dev-mode: ignored
            self.assertTrue(self.app._button_reset())
        self.assertEqual(len(self.resets), 1)
        self.reset_answer = ({"ok": False, "error": "An update is installing"}, 409)
        self.assertFalse(self.app._button_reset())                            # the light blinks red


class ProbeTests(unittest.TestCase):
    def test_probe_exit_codes(self):
        with mock.patch.object(sb, "detect", return_value=None):
            self.assertEqual(sb.main(["--probe"]), 2)
        with mock.patch.object(sb, "detect", return_value={"bus": 1, "addr": 0x2A, "part_id": sb.PART_ID}):
            self.assertEqual(sb.main(["--probe"]), 0)
        with mock.patch.object(sb, "detect", return_value={"bus": 7, "addr": 0x2A, "part_id": sb.PART_ID}):
            self.assertEqual(sb.main(["--probe"]), 3)


if __name__ == "__main__":
    unittest.main()
