"""mDNS name guard: win atlas.local back after avahi renames itself (atlas-2.local)."""
import unittest
from unittest import mock


class _Stop(Exception):
    pass


class MdnsGuardTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app

    def test_matching_name_needs_nothing(self):
        self.assertEqual(self.app._mdns_reclaim_decision("atlas", "atlas", 0), (False, 30))

    def test_unreadable_name_is_never_acted_on(self):
        # avahi mid-restart or not installed: no guessing.
        for advertised in ("", None):
            self.assertFalse(self.app._mdns_reclaim_decision(advertised, "atlas", 0)[0])

    def test_renamed_host_is_reclaimed_with_backoff(self):
        decisions = [self.app._mdns_reclaim_decision("atlas-2", "atlas", n) for n in range(6)]
        self.assertTrue(all(act for act, _ in decisions))
        self.assertEqual([delay for _, delay in decisions], [30, 120, 600, 3600, 3600, 3600])

    def test_reads_the_name_from_avahi_over_dbus(self):
        reply = mock.Mock(returncode=0, stdout='s "atlas-2"\n')
        with mock.patch.object(self.app.subprocess, "run", return_value=reply) as run:
            self.assertEqual(self.app._avahi_host_name(), "atlas-2")
        self.assertIn("GetHostName", run.call_args.args[0])

    def test_unreadable_name_reads_as_empty(self):
        with mock.patch.object(self.app.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="")):
            self.assertEqual(self.app._avahi_host_name(), "")
        with mock.patch.object(self.app.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.app._avahi_host_name(), "")

    def test_loop_restarts_avahi_through_the_helper_then_settles(self):
        with mock.patch.object(self.app, "_avahi_host_name", side_effect=["atlas-2", "atlas"]), \
                mock.patch.object(self.app.os, "uname", return_value=mock.Mock(nodename="atlas")), \
                mock.patch.object(self.app.atlas_helper, "run", return_value=("", 0)) as helper, \
                mock.patch.object(self.app.time, "sleep", side_effect=[None, _Stop]) as sleep:
            with self.assertRaises(_Stop):
                self.app._mdns_name_guard_loop()
        helper.assert_called_once_with("avahi.reclaim", timeout=30)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [30, 30])


if __name__ == "__main__":
    unittest.main()
