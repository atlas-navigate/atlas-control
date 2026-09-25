"""Discovery channels (UDP beacon, BLE, bootstrap) must not leak credentials."""
import json
import unittest
from unittest import mock

import lan_beacon


class BeaconTests(unittest.TestCase):
    def test_manifest_is_cached_between_probes(self):
        calls = []

        def manifest():
            calls.append(1)
            return {"device": {"name": "Atlas"}, "api": {"baseUrls": ["https://10.42.0.1"]},
                    "hotspot": {"ssid": "x", "password": "should-never-appear"}}

        b = lan_beacon.LanBeacon(manifest)
        for _ in range(20):
            payload = json.loads(b._build_payload(request_nonce="n"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("should-never-appear", json.dumps(payload))

    def test_replies_are_rate_limited_per_source(self):
        b = lan_beacon.LanBeacon(lambda: {})
        allowed = [b._reply_allowed("192.168.1.9") for _ in range(20)]
        self.assertEqual(sum(allowed), lan_beacon._REPLY_BURST)
        self.assertTrue(b._reply_allowed("192.168.1.10"))


class AppPayloadTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        self.client = app.app.test_client()
        app.db._local.conn = None
        app.db.init_db()

    def _fake_hotspot(self, include_password=False):
        state = {"active": True, "ssid": "Atlas-7KQ4", "passwordSet": True}
        if include_password:
            state["password"] = "correct-horse-battery"
        return state

    def test_status_and_bootstrap_do_not_include_the_password(self):
        with mock.patch.object(self.app, "_current_hotspot_state", side_effect=self._fake_hotspot):
            for path in ("/api/hotspot/status", "/api/mobile/bootstrap"):
                with self.subTest(path=path):
                    body = self.client.get(path, headers={"Host": "10.42.0.1"},
                                           environ_base={"REMOTE_ADDR": "10.42.0.5"}).get_data(as_text=True)
                    self.assertNotIn("correct-horse-battery", body)
            bridge_bytes = self.app.AtlasMobileBridge(self.app._mobile_bootstrap_manifest).manifest_bytes()
            self.assertNotIn(b"correct-horse-battery", bridge_bytes)

    def test_radio_secrets_are_redacted(self):
        cfg = {"connected": True, "network": {"wifi_ssid": "home", "wifi_psk": "radio-secret"},
               "bluetooth": {"enabled": True, "fixed_pin": 123456}}
        out = self.app._redact_radio_config(cfg)
        self.assertIsNone(out["network"]["wifi_psk"])
        self.assertTrue(out["network"]["wifi_psk_set"])
        self.assertIsNone(out["bluetooth"]["fixed_pin"])
        self.assertEqual(cfg["network"]["wifi_psk"], "radio-secret")  # original untouched
