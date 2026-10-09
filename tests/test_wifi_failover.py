"""Wi-Fi failover: only a real loss of the LAN starts the hotspot fallback,
never NetworkManager failing to answer (e.g. while the unit is swapping)."""
import unittest
from unittest import mock


class _Stop(Exception):
    pass


class FailoverTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app

    def run_ticks(self, lan_states, nmcli_fails):
        """Run the loop for len(lan_states) ticks; nmcli_fails[i] simulates a timed-out query."""
        app = self.app
        ticks = iter(range(len(lan_states)))
        current = {"i": 0}

        def lan():
            i = current["i"]
            if nmcli_fails[i]:
                app._NMCLI_FAILURES += 1
            return lan_states[i]

        def sleep(_s):
            try:
                current["i"] = next(ticks)
            except StopIteration:
                raise _Stop()
        clock = iter(float(t) for t in range(0, 1000, 40))
        with mock.patch.object(app, "_current_hotspot_state", return_value={"active": False}), \
                mock.patch.object(app, "_current_lan_link_state", side_effect=lan), \
                mock.patch.object(app, "_network_connectivity_state", return_value={"online": False}), \
                mock.patch.object(app, "_auto_hotspot_allowed", return_value=True), \
                mock.patch.object(app, "_start_hotspot_with_saved_config", return_value={"ok": True, "ssid": "Atlas-X"}) as restore, \
                mock.patch.object(app.time, "sleep", side_effect=sleep), \
                mock.patch.object(app.time, "monotonic", side_effect=lambda: next(clock)), \
                self.assertLogs("atlas-control", level="INFO") as logs:
            with self.assertRaises(_Stop):
                app._wifi_failover_loop()
        return restore, "\n".join(logs.output)

    def test_nmcli_timeouts_are_not_a_lost_lan(self):
        restore, log = self.run_ticks([None] * 4, [True] * 4)
        restore.assert_not_called()
        self.assertNotIn("LAN link lost", log)
        self.assertIn("NetworkManager did not answer", log)

    def test_a_real_loss_still_falls_back(self):
        restore, log = self.run_ticks([None] * 4, [False] * 4)
        self.assertIn("LAN link lost", log)
        restore.assert_called()


if __name__ == "__main__":
    unittest.main()
