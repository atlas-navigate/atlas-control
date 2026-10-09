"""mDNS name guard: win this unit's name back after avahi renames itself
(atlas-pjnt -> atlas-pjnt-2) because of a conflict (local_device.py)."""
import unittest
from unittest import mock

import local_device


class MdnsGuardTests(unittest.TestCase):
    def test_matching_name_needs_nothing(self):
        self.assertEqual(local_device.mdns_reclaim_decision("atlas", "atlas", 0), (False, 30))

    def test_unreadable_name_is_never_acted_on(self):
        # avahi mid-restart or not installed: no guessing.
        for advertised in ("", None):
            self.assertFalse(local_device.mdns_reclaim_decision(advertised, "atlas", 0)[0])

    def test_renamed_host_is_reclaimed_with_backoff(self):
        decisions = [local_device.mdns_reclaim_decision("atlas-2", "atlas", n) for n in range(6)]
        self.assertTrue(all(act for act, _ in decisions))
        self.assertEqual([delay for _, delay in decisions], [30, 120, 600, 3600, 3600, 3600])

    def test_reads_the_name_from_avahi_over_dbus(self):
        reply = mock.Mock(returncode=0, stdout='s "atlas-2"\n')
        with mock.patch.object(local_device.subprocess, "run", return_value=reply) as run:
            self.assertEqual(local_device.avahi_host_name(), "atlas-2")
        self.assertIn("GetHostName", run.call_args.args[0])

    def test_unreadable_name_reads_as_empty(self):
        with mock.patch.object(local_device.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="")):
            self.assertEqual(local_device.avahi_host_name(), "")
        with mock.patch.object(local_device.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(local_device.avahi_host_name(), "")

    def test_guard_restarts_avahi_through_the_helper_then_settles(self):
        calls = []
        mono = [100.0]
        dev = local_device.LocalDevice(helper=lambda verb, timeout=30, **a: calls.append(verb) or {"ok": True},
                                       mono=lambda: mono[0])
        with mock.patch.object(local_device, "configured_hostname", return_value="atlas-pjnt"), \
                mock.patch.object(local_device, "avahi_host_name", side_effect=["atlas-pjnt-2", "atlas-pjnt"]):
            dev._mdns_guard()                     # renamed: reclaim, check again in 30 s
            self.assertEqual(calls, ["avahi.reclaim"])
            self.assertEqual(dev._mdns_next, 130.0)
            mono[0] = 131.0
            dev._mdns_guard()                     # back on its own name
        self.assertEqual(calls, ["avahi.reclaim"])
        self.assertEqual(dev._mdns_attempts, 0)

    def test_expected_name_comes_from_the_identity_not_uname(self):
        with mock.patch.object(local_device.device_identity, "device", return_value={"hostname": "atlas-pjnt"}), \
                mock.patch.object(local_device.os, "uname", return_value=mock.Mock(nodename="atlas")):
            self.assertEqual(local_device.configured_hostname(), "atlas-pjnt")


if __name__ == "__main__":
    unittest.main()
