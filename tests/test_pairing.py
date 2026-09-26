"""Pairing replaces logins: one-time proof of physical access, then no prompts."""
import ipaddress
import json
import os
import unittest
from unittest import mock

from engine_sandbox import Sandbox

import device_identity
import pairing

SECRET = "p" * 48
HOST = {}
BASE = "https://192.168.1.20"
ENV = {"REMOTE_ADDR": "127.0.0.1"}


class PairingTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        from atlas_engine import identity
        identity.ensure()
        cls.identity_dir = cls.sb.paths.IDENTITY
        with open(os.path.join(cls.identity_dir, "setup_code")) as f:
            cls.setup_code = f.read().strip()

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def setUp(self):
        import app
        self.app = app
        patches = [mock.patch.object(device_identity, "IDENTITY_DIR", self.identity_dir),
                   mock.patch.dict(os.environ, {"ATLAS_PAIRING": "on"})]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        app.db._local.conn = None
        app.db.init_db()
        pairing.revoke_all()
        pairing._failures.clear(); pairing._global_failures.clear()
        app.limiter.reset()
        self.client = app.app.test_client()

    def get(self, path, **kw):
        return self.client.get(path, base_url=BASE, environ_base=kw.pop("environ_base", ENV), **kw)

    def post(self, path, **kw):
        return self.client.post(path, base_url=BASE, environ_base=kw.pop("environ_base", ENV), **kw)

    def pair_browser(self):
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "name": "Test browser"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()


class GateTests(PairingTestBase):
    def test_unpaired_client_sees_only_the_pairing_screen(self):
        self.assertEqual(self.get("/api/nodes").status_code, 401)
        self.assertEqual(self.get("/api/hotspot/credentials").status_code, 401)
        self.assertEqual(self.get("/api/pair/clients").status_code, 401)
        self.assertEqual(self.get("/").status_code, 200)            # the page itself (pairing UI)
        info = self.get("/api/device/info").get_json()
        self.assertTrue(info["pairing_required"])
        self.assertTrue(info["tls_spki_sha256"])
        self.assertEqual(self.get("/api/device").get_json(), {"app": "atlas-control", "pairing_required": True})
        self.assertFalse(self.get("/api/pair/status").get_json()["paired"])

    def test_no_account_password_or_pin_anywhere(self):
        status = self.get("/api/pair/status").get_json()
        for key in ("password", "username", "pin", "account", "login"):
            self.assertNotIn(key, json.dumps(status).lower())


class SetupCodeTests(PairingTestBase):
    def test_setup_code_pairs_and_the_cookie_then_works(self):
        body = self.pair_browser()
        self.assertNotIn("token", body)                              # browsers: HttpOnly cookie only
        cookie = self.client.get_cookie(pairing.COOKIE, domain="192.168.1.20")
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Strict")
        self.assertEqual(self.get("/api/nodes").status_code, 200)
        status = self.get("/api/pair/status").get_json()
        self.assertTrue(status["paired"])
        self.assertEqual(status["csrf"], body["csrf"])

    def test_code_is_accepted_with_or_without_dashes_and_case(self):
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code.replace("-", "").lower()})
        self.assertEqual(r.status_code, 200)

    def test_wrong_codes_are_refused_and_rate_limited(self):
        for _ in range(5):
            r = self.post("/api/pair/claim", json={"setup_code": "AAAA-BBBB-CCCC"})
            self.assertEqual(r.status_code, 403)
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code})
        self.assertEqual(r.status_code, 403)                         # locked out for a while
        self.assertIn("Too many", r.get_json()["error"])

    def test_wrong_code_message_points_at_the_right_line_on_the_label(self):
        r = self.post("/api/pair/claim", json={"setup_code": "WRONG-CODE-HERE", "kind": "android"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("not the Wi-Fi password", r.get_json()["error"])

    def test_throttled_pairing_answers_429_json(self):
        codes = [self.post("/api/pair/claim", json={"setup_code": "WRONG-CODE-HERE", "kind": "android"}).status_code
                 for _ in range(12)]
        self.assertEqual(codes[-1], 429)
        r = self.post("/api/pair/claim", json={"setup_code": "WRONG-CODE-HERE", "kind": "android"})
        self.assertEqual(r.get_json()["code"], "rate_limited")

    def test_native_app_gets_a_bearer_token_that_needs_no_csrf(self):
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "kind": "android"})
        token = r.get_json()["token"]
        self.client.delete_cookie(pairing.COOKIE)
        auth = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.get("/api/nodes", headers=auth).status_code, 200)
        self.assertEqual(self.post("/api/calculator", headers=auth, json={"expression": "1+1"}).status_code, 200)


class CsrfTests(PairingTestBase):
    def test_cookie_writes_need_the_csrf_value(self):
        body = self.pair_browser()
        r = self.post("/api/calculator", json={"expression": "1+1"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "csrf")
        r = self.post("/api/calculator", json={"expression": "1+1"}, headers={"X-Atlas-CSRF": "1"})
        self.assertEqual(r.status_code, 403)
        r = self.post("/api/calculator", json={"expression": "1+1"}, headers={"X-Atlas-CSRF": body["csrf"]})
        self.assertEqual(r.status_code, 200)


class ApprovalTests(PairingTestBase):
    def test_new_device_waits_for_allow_on_a_paired_device(self):
        owner = self.pair_browser()
        owner_cookie = self.client.get_cookie(pairing.COOKIE, domain="192.168.1.20").value
        newcomer = self.app.app.test_client()
        r = newcomer.post("/api/pair/request", json={"name": "Kid's phone", "client_nonce": "abc"},
                          base_url=BASE, environ_base=ENV)
        req = r.get_json()
        self.assertRegex(req["match_code"], r"^\d{4}$")
        poll = lambda: newcomer.get(f"/api/pair/request/{req['request_id']}?secret={req['secret']}",
                                    base_url=BASE, environ_base=ENV).get_json()
        self.assertEqual(poll()["status"], "pending")
        # The owner sees it and allows it.
        pending = self.get("/api/pair/requests").get_json()
        self.assertEqual(pending[0]["match_code"], req["match_code"])
        r = self.post(f"/api/pair/request/{req['request_id']}/approve", headers={"X-Atlas-CSRF": owner["csrf"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(poll()["status"], "approved")
        self.assertEqual(newcomer.get("/api/nodes", base_url=BASE, environ_base=ENV).status_code, 200)
        self.assertEqual(poll()["status"], "issued")                 # token handed out once
        del owner_cookie

    def test_wrong_secret_cannot_collect_the_token(self):
        self.pair_browser()
        fresh = self.app.app.test_client()
        req = fresh.post("/api/pair/request", json={"name": "x"}, base_url=BASE, environ_base=ENV).get_json()
        pairing.decide(req["request_id"], True, "someone")
        other = self.app.app.test_client()
        r = other.get(f"/api/pair/request/{req['request_id']}?secret=guess", base_url=BASE, environ_base=ENV)
        self.assertEqual(r.get_json()["status"], "unknown")

    def test_unpaired_device_cannot_approve_itself(self):
        req = self.post("/api/pair/request", json={"name": "x"}).get_json()
        r = self.post(f"/api/pair/request/{req['request_id']}/approve")
        self.assertEqual(r.status_code, 401)

    def test_match_code_changes_if_the_tls_key_seen_differs(self):
        a = device_identity.match_code("n1", "c1")
        b = device_identity.match_code("n1", "c1", spki="attacker-key")
        self.assertNotEqual(a, b)


class HotspotTests(PairingTestBase):
    def via_nginx(self, client_ip, server_addr):
        return {"headers": {"X-Atlas-Proxy": SECRET, "X-Real-IP": client_ip, "X-Atlas-Local-Addr": server_addr},
                "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

    def setUp(self):
        super().setUp()
        import request_guard
        fd = os.path.join(self.identity_dir, "proxy_secret")
        self._secret = request_guard.ProxySecret(fd)
        mw = self.app.app.wsgi_app
        old = mw.proxy_secret
        mw.proxy_secret = self._secret
        self.addCleanup(setattr, mw, "proxy_secret", old)
        global SECRET
        SECRET = open(fd).read().strip()

    def test_phone_on_atlas_hotspot_pairs_with_one_tap(self):
        hs = ipaddress.ip_interface("10.42.0.1/24")
        with mock.patch.object(self.app, "_hotspot_interface", return_value=hs):
            r = self.post("/api/pair/hotspot", json={"name": "Phone"}, **self.via_nginx("10.42.0.37", "10.42.0.1"))
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            # the same request from the home LAN is refused
            r = self.post("/api/pair/hotspot", json={}, **self.via_nginx("192.168.1.50", "192.168.1.20"))
            self.assertEqual(r.status_code, 403)
        with mock.patch.object(self.app, "_hotspot_interface", return_value=None):
            r = self.post("/api/pair/hotspot", json={}, **self.via_nginx("10.42.0.37", "10.42.0.1"))
            self.assertEqual(r.status_code, 403)


class RevokeAndSocketTests(PairingTestBase):
    def test_revoked_device_loses_access(self):
        body = self.pair_browser()
        clients = self.get("/api/pair/clients").get_json()
        self.assertTrue(clients[0]["this"])
        r = self.client.delete(f"/api/pair/clients/{clients[0]['id']}", headers={"X-Atlas-CSRF": body["csrf"]},
                               base_url=BASE, environ_base=ENV)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get("/api/nodes").status_code, 401)

    def test_socketio_refuses_unpaired_clients(self):
        # The Socket.IO test client has no REMOTE_ADDR; the LAN check is not what's tested here.
        p = mock.patch.object(self.app, "_is_local", return_value=True)
        p.start(); self.addCleanup(p.stop)
        sio = self.app.socketio.test_client(self.app.app, headers={"Host": "192.168.1.20"})
        self.assertFalse(sio.is_connected())
        self.pair_browser()
        cookie = self.client.get_cookie(pairing.COOKIE, domain="192.168.1.20").value
        sio = self.app.socketio.test_client(self.app.app, headers={"Host": "192.168.1.20",
                                                                   "Cookie": f"{pairing.COOKIE}={cookie}"})
        self.assertTrue(sio.is_connected())
        sio.disconnect()


class PersistenceTests(PairingTestBase):
    """Pairing is done once: restarts, updates and resets of the app keep it."""

    def pair_phone(self):
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "name": "Pixel", "kind": "android"})
        self.assertEqual(r.status_code, 200)
        return r.get_json()["token"]

    def paired(self, token):
        r = self.client.get("/api/pair/status", base_url=BASE, environ_base=ENV,
                            headers={"Authorization": f"Bearer {token}"})
        return r.get_json()["paired"]

    def restart(self):
        """What a reboot or a new release sees: a fresh process, the same data/ and identity."""
        pairing._cache.clear()
        self.app.db._local.conn = None
        self.app.db.init_db()
        return pairing.bind_identity()

    def test_pairing_survives_restarts_and_new_releases(self):
        token = self.pair_phone()
        self.assertFalse(self.restart())
        self.assertFalse(self.restart())
        self.assertTrue(self.paired(token))
        info = self.get("/api/device/info").get_json()
        self.assertEqual(info["pairing"], 1)

    def test_first_start_on_an_existing_unit_only_records_the_identity(self):
        token = self.pair_phone()
        conn = self.app.db.get_db()
        conn.execute("DELETE FROM app_settings WHERE key=?", (pairing.IDENTITY_SETTING,))
        conn.commit()
        self.assertFalse(self.restart())
        self.assertTrue(self.paired(token))

    def test_a_new_identity_ends_pairings_made_with_the_old_one(self):
        token = self.pair_phone()
        self.restart()
        cloned = dict(device_identity.device(), device_id="f" * 32, created_reason="cloned-storage")
        with mock.patch.object(device_identity, "device", return_value=cloned):
            self.assertTrue(self.restart())
            self.assertFalse(self.paired(token))
            self.assertFalse(self.restart())                     # once, not on every start
        rows = self.app.db.get_db().execute("SELECT action FROM audit_log WHERE action='identity_changed'").fetchall()
        self.assertEqual(len(rows), 1)

    def test_factory_reset_keeps_the_identity_binding(self):
        self.restart()
        self.app.db.factory_reset_data()
        row = self.app.db.get_db().execute("SELECT value FROM app_settings WHERE key=?",
                                           (pairing.IDENTITY_SETTING,)).fetchone()
        self.assertEqual(row["value"], device_identity.device()["device_id"])
