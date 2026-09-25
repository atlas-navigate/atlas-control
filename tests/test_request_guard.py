"""Edge security: proxy spoofing, Host/DNS rebinding, cross-site writes, Socket.IO."""
import os
import tempfile
import unittest

import app as atlas_app
import request_guard

SECRET = "s" * 48


class GuardTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        atlas_app.db._local.conn = None
        atlas_app.db.init_db()

    def setUp(self):
        fd, self.secret_file = tempfile.mkstemp()
        os.write(fd, SECRET.encode()); os.close(fd)
        self.addCleanup(os.remove, self.secret_file)
        mw = atlas_app.app.wsgi_app
        self.assertIsInstance(mw, request_guard.EdgeMiddleware)
        self._old_secret = mw.proxy_secret
        mw.proxy_secret = request_guard.ProxySecret(self.secret_file)
        self.addCleanup(setattr, mw, "proxy_secret", self._old_secret)
        self.client = atlas_app.app.test_client()


class ProxyTrustTests(GuardTestBase):
    def _remote_addr_for(self, environ_base, headers):
        """Run a request through a real EdgeMiddleware around a capturing app."""
        from werkzeug.test import Client
        captured = {}

        def capture_app(environ, start_response):
            captured["ip"] = environ.get("REMOTE_ADDR")
            captured["direct"] = environ.get("atlas.direct_loopback")
            captured["xff"] = environ.get("HTTP_X_FORWARDED_FOR")
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        mw = request_guard.EdgeMiddleware(
            capture_app, proxy_secret=request_guard.ProxySecret(self.secret_file))
        Client(mw).get("/", environ_base=environ_base, headers=headers)
        return captured

    def test_direct_client_cannot_forge_forwarded_for(self):
        seen = self._remote_addr_for({"REMOTE_ADDR": "203.0.113.9"},
                                     {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1",
                                      "Host": "192.168.1.20"})
        self.assertEqual(seen["ip"], "203.0.113.9")
        self.assertIsNone(seen["xff"])

    def test_spoofed_client_is_refused_by_the_app(self):
        # 8.8.8.8: a genuinely public peer (203.0.113.0/24 counts as private to ipaddress).
        r = self.client.get("/api/nodes", environ_base={"REMOTE_ADDR": "8.8.8.8"},
                            headers={"X-Forwarded-For": "127.0.0.1", "Host": "192.168.1.20"})
        self.assertEqual(r.status_code, 403)

    def test_loopback_peer_without_secret_is_not_trusted(self):
        seen = self._remote_addr_for({"REMOTE_ADDR": "127.0.0.1"},
                                     {"X-Real-IP": "192.168.1.50", "X-Atlas-Proxy": "wrong" * 10,
                                      "Host": "192.168.1.20"})
        self.assertEqual(seen["ip"], "127.0.0.1")
        self.assertFalse(seen["direct"])

    def test_nginx_with_secret_is_trusted(self):
        seen = self._remote_addr_for({"REMOTE_ADDR": "127.0.0.1"},
                                     {"X-Real-IP": "192.168.1.50", "X-Atlas-Proxy": SECRET,
                                      "Host": "192.168.1.20"})
        self.assertEqual(seen["ip"], "192.168.1.50")
        self.assertFalse(seen["direct"])

    def test_forwarded_header_from_non_loopback_peer_is_ignored_even_with_secret(self):
        seen = self._remote_addr_for({"REMOTE_ADDR": "192.168.1.77"},
                                     {"X-Real-IP": "127.0.0.1", "X-Atlas-Proxy": SECRET,
                                      "Host": "192.168.1.20"})
        self.assertEqual(seen["ip"], "192.168.1.77")

    def test_health_detail_only_for_direct_loopback(self):
        r = self.client.get("/api/health", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                            headers={"Host": "127.0.0.1:5000"})
        self.assertIn("schema_version", r.get_json())
        r = self.client.get("/api/health", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                            headers={"Host": "192.168.1.20", "X-Atlas-Proxy": SECRET,
                                     "X-Real-IP": "192.168.1.50"})
        self.assertNotIn("schema_version", r.get_json())
        self.assertIn("version", r.get_json())


class HostTests(GuardTestBase):
    def test_ip_literals_and_own_names_are_accepted(self):
        for host in ("192.168.1.20", "10.42.0.1", "127.0.0.1:5000", "[fe80::1]", "100.101.2.3",
                     "atlas.local", "localhost"):
            with self.subTest(host=host):
                r = self.client.get("/api/health", headers={"Host": host},
                                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
                self.assertEqual(r.status_code, 200)

    def test_rebinding_hostnames_are_refused(self):
        for host in ("attacker.example", "atlas.local.attacker.example", "evil.local.example"):
            with self.subTest(host=host):
                r = self.client.get("/api/health", headers={"Host": host},
                                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
                self.assertEqual(r.status_code, 421)

    def test_socketio_is_covered(self):
        r = self.client.get("/socket.io/?EIO=4&transport=polling", headers={"Host": "attacker.example"},
                            environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 421)


class CrossSiteTests(GuardTestBase):
    H = {"Host": "192.168.1.20"}
    ENV = {"REMOTE_ADDR": "127.0.0.1"}

    def post(self, path, headers=None, **kw):
        h = dict(self.H); h.update(headers or {})
        return self.client.post(path, headers=h, environ_base=self.ENV, **kw)

    def test_foreign_origin_is_refused(self):
        r = self.post("/api/calculator", {"Origin": "https://attacker.example"}, json={"expression": "1+1"})
        self.assertEqual(r.status_code, 403)

    def test_bodyless_cross_site_form_post_is_refused(self):
        for hdrs in ({"Origin": "https://attacker.example"},
                     {"Origin": "null"},
                     {"Sec-Fetch-Site": "cross-site"},
                     {"Referer": "https://attacker.example/page"}):
            with self.subTest(h=hdrs):
                self.assertEqual(self.post("/api/factory-reset", hdrs).status_code, 403)

    def test_text_plain_and_form_bodies_are_refused(self):
        r = self.post("/api/calculator", {"Origin": "https://192.168.1.20"},
                      data='{"expression": "1+1"}', content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        r = self.post("/api/calendar", {"Origin": "https://192.168.1.20"},
                      data="title=x&start_ts=1", content_type="application/x-www-form-urlencoded")
        self.assertEqual(r.status_code, 415)

    def test_same_origin_json_is_allowed(self):
        r = self.post("/api/calculator", {"Origin": "https://192.168.1.20", "Sec-Fetch-Site": "same-origin"},
                      json={"expression": "6*7"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["result"], 42)

    def test_native_client_without_origin_is_allowed(self):
        r = self.post("/api/calculator", json={"expression": "2+2"})
        self.assertEqual(r.status_code, 200)

    def test_cross_site_websocket_handshake_is_refused(self):
        r = self.client.get("/socket.io/?EIO=4&transport=polling",
                            headers={"Host": "192.168.1.20", "Origin": "https://attacker.example"},
                            environ_base=self.ENV)
        self.assertEqual(r.status_code, 403)


class TrackerValidationTests(GuardTestBase):
    ENV = {"REMOTE_ADDR": "127.0.0.1"}

    def checkin(self, **payload):
        body = {"device_id": "ph-test", "name": "Test"}
        body.update(payload)
        return self.client.post("/api/tracker/checkin", json=body, environ_base=self.ENV,
                                headers={"Host": "192.168.1.20"})

    def test_style_injection_in_color_is_refused(self):
        self.assertEqual(self.checkin(color="red;background:url(//x)").status_code, 400)

    def test_out_of_range_coordinates_are_refused(self):
        self.assertEqual(self.checkin(latitude=91, longitude=0).status_code, 400)
        self.assertEqual(self.checkin(latitude="nan", longitude=0).status_code, 400)
        self.assertEqual(self.checkin(latitude=10).status_code, 400)

    def test_valid_checkin(self):
        self.assertEqual(self.checkin(color="#12ab34", latitude=38.9, longitude=-77.4,
                                      battery=80).status_code, 200)
