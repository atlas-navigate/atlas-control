"""Pairing replaces logins: one-time proof of physical access, then no prompts."""
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
        conn = app.db.get_db()
        conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)", (pairing.FAILURES_SETTING, pairing.IDENTITY_SETTING))
        conn.execute("DELETE FROM audit_log WHERE action='identity_changed'")
        conn.commit()
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
        self.app.app.test_client().post("/api/pair/claim", json={"setup_code": self.setup_code},
                                        base_url=BASE, environ_base=ENV)       # someone else is primary
        req = self.post("/api/pair/request", json={"name": "x"}).get_json()
        r = self.post(f"/api/pair/request/{req['request_id']}/approve")
        self.assertEqual(r.status_code, 401)

    def test_match_code_changes_if_the_tls_key_seen_differs(self):
        a = device_identity.match_code("n1", "c1")
        b = device_identity.match_code("n1", "c1", spki="attacker-key")
        self.assertNotEqual(a, b)


class HotspotTests(PairingTestBase):
    def test_joining_atlas_wifi_no_longer_pairs(self):
        # Its password is the same on every unit: protocol-1 apps must update.
        r = self.post("/api/pair/hotspot", json={"name": "Phone", "kind": "ios"})
        self.assertEqual(r.status_code, 410)
        self.assertEqual(r.get_json()["code"], "update_app")
        self.assertFalse(pairing.claimed())


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
        self.assertEqual(info["pairing"], pairing.VERSION)

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

    def test_a_database_from_another_unit_leaves_this_one_unclaimed(self):
        # A golden drive never carries the database (atlas-capture.sh excludes
        # it), but if one did, its primary user is no one's here: the next
        # owner pairs with this unit's button.
        self.pair_phone()
        self.restart()
        self.assertEqual(self.get("/api/device/info").get_json()["setup_state"], "owned")
        cloned = dict(device_identity.device(), device_id="e" * 32, created_reason="new-unit")
        with mock.patch.object(device_identity, "device", return_value=cloned):
            self.assertTrue(self.restart())
            self.assertIsNone(pairing.owner_id())
            self.assertEqual(self.get("/api/device/info").get_json()["setup_state"], "unclaimed")

    def test_factory_reset_keeps_the_identity_binding(self):
        self.restart()
        self.app.db.factory_reset_data()
        row = self.app.db.get_db().execute("SELECT value FROM app_settings WHERE key=?",
                                           (pairing.IDENTITY_SETTING,)).fetchone()
        self.assertEqual(row["value"], device_identity.device()["device_id"])


class HotspotReminderTests(PairingTestBase):
    """Atlas's Wi-Fi starts with the code in its name as the password; the
    owner is asked once after pairing, and the page reminds them, to set their
    own until they do (again after a factory reset)."""

    def test_reminder_follows_the_running_password(self):
        import tempfile
        cfg = os.path.join(tempfile.mkdtemp(), "hotspot_config.json")
        with mock.patch.object(self.app, "_HOTSPOT_CFG_PATH", cfg), \
                mock.patch.object(self.app, "_nmcli", return_value=("", 0)):
            state = self.app._current_hotspot_state()
            self.assertTrue(state["standard_password"])
            self.assertNotIn("password", state)                       # never in status
            self.app._save_hotspot_cfg(state["ssid"], "our-private-wifi")
            self.assertFalse(self.app._current_hotspot_state()["standard_password"])
            os.remove(cfg)                                            # what factory reset does
            self.assertTrue(self.app._current_hotspot_state()["standard_password"])

    def test_the_new_owner_is_asked_once_to_set_their_own_wifi_password(self):
        import tempfile
        cfg = os.path.join(tempfile.mkdtemp(), "hotspot_config.json")
        p = mock.patch.object(self.app, "_HOTSPOT_CFG_PATH", cfg)
        p.start(); self.addCleanup(p.stop)
        q = mock.patch.object(self.app, "_nmcli", return_value=("", 0))
        q.start(); self.addCleanup(q.stop)
        self.app.db.get_db().execute("DELETE FROM app_settings WHERE key=?", (self.app.HOTSPOT_PROMPT_DONE,))
        self.app.db.get_db().commit()
        owner = self.pair_browser()
        status = self.get("/api/hotspot/status").get_json()
        self.assertTrue(status["standard_password"] and status["prompt_own_password"])
        self.assertTrue(status["ssid"].endswith(".local"))
        # Another device is never asked, and can't answer for the owner.
        member = self.app.app.test_client()
        req = member.post("/api/pair/request", json={"name": "Second phone", "client_nonce": "n"},
                          base_url=BASE, environ_base=ENV).get_json()
        self.post(f"/api/pair/request/{req['request_id']}/approve", headers={"X-Atlas-CSRF": owner["csrf"]})
        member.get(f"/api/pair/request/{req['request_id']}?secret={req['secret']}", base_url=BASE, environ_base=ENV)
        self.assertFalse(member.get("/api/hotspot/status", base_url=BASE, environ_base=ENV)
                         .get_json()["prompt_own_password"])
        member_csrf = member.get("/api/pair/status", base_url=BASE, environ_base=ENV).get_json()["csrf"]
        r = member.post("/api/hotspot/prompt-done", json={}, headers={"X-Atlas-CSRF": member_csrf},
                        base_url=BASE, environ_base=ENV)
        self.assertEqual(r.status_code, 403)
        # Skipping (or saving) ends the prompt; the page's reminder stays until the password changes.
        r = self.post("/api/hotspot/prompt-done", json={}, headers={"X-Atlas-CSRF": owner["csrf"]})
        self.assertEqual(r.status_code, 200)
        status = self.get("/api/hotspot/status").get_json()
        self.assertEqual((status["prompt_own_password"], status["standard_password"]), (False, True))
        # A factory reset asks the next owner again.
        self.app.db.factory_reset_data()
        self.assertIsNone(self.app.db.get_app_settings().get(self.app.HOTSPOT_PROMPT_DONE))


class PrimaryUserTests(PairingTestBase):
    """The first device to pair is the primary user; only it can let others in."""

    def other(self):
        return self.app.app.test_client()

    def request_from(self, client, name="Kid's phone"):
        return client.post("/api/pair/request", json={"name": name, "client_nonce": "n"},
                           base_url=BASE, environ_base=ENV).get_json()

    def test_first_device_is_primary_and_setup_code_then_stops_working(self):
        owner = self.pair_browser()
        self.assertTrue(pairing.claimed())
        status = self.get("/api/pair/status").get_json()
        self.assertEqual(status["client"]["role"], "owner")
        self.assertEqual(status["setup_state"], "owned")
        self.assertEqual(self.get("/api/device/info").get_json()["setup_state"], "owned")
        r = self.other().post("/api/pair/claim", json={"setup_code": self.setup_code},
                              base_url=BASE, environ_base=ENV)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "claimed")
        del owner

    def test_unclaimed_atlas_sends_new_devices_to_the_button(self):
        r = self.post("/api/pair/request", json={"name": "x"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "unclaimed")
        self.assertEqual(self.get("/api/device/info").get_json()["setup_state"], "unclaimed")

    def test_only_the_primary_user_can_allow_a_device(self):
        owner = self.pair_browser()
        member = self.other()
        req = self.request_from(member, "Second phone")
        self.post(f"/api/pair/request/{req['request_id']}/approve", headers={"X-Atlas-CSRF": owner["csrf"]})
        member.get(f"/api/pair/request/{req['request_id']}?secret={req['secret']}", base_url=BASE, environ_base=ENV)
        member_csrf = member.get("/api/pair/status", base_url=BASE, environ_base=ENV).get_json()["csrf"]
        self.assertEqual(member.get("/api/pair/status", base_url=BASE, environ_base=ENV)
                         .get_json()["client"]["role"], "member")
        third = self.request_from(self.other(), "Third")
        r = member.post(f"/api/pair/request/{third['request_id']}/approve", headers={"X-Atlas-CSRF": member_csrf},
                        base_url=BASE, environ_base=ENV)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "owner_required")
        self.assertEqual(member.get("/api/pair/requests", base_url=BASE, environ_base=ENV).status_code, 403)
        self.assertEqual(len(self.get("/api/pair/requests").get_json()), 1)

    def test_primary_role_can_be_handed_over_and_is_never_left_empty(self):
        owner = self.pair_browser()
        member = self.other()
        req = self.request_from(member)
        self.post(f"/api/pair/request/{req['request_id']}/approve", headers={"X-Atlas-CSRF": owner["csrf"]})
        member_id = member.get(f"/api/pair/request/{req['request_id']}?secret={req['secret']}",
                               base_url=BASE, environ_base=ENV).get_json()["client_id"]
        me = [c for c in self.get("/api/pair/clients").get_json() if c["this"]][0]
        r = self.client.delete(f"/api/pair/clients/{me['id']}", headers={"X-Atlas-CSRF": owner["csrf"]},
                               base_url=BASE, environ_base=ENV)
        self.assertEqual(r.status_code, 409)                      # would leave nobody to allow devices
        r = self.post(f"/api/pair/clients/{member_id}/primary", headers={"X-Atlas-CSRF": owner["csrf"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(pairing.owner_id(), member_id)
        roles = {c["id"]: c["role"] for c in self.get("/api/pair/clients").get_json()}
        self.assertEqual(roles, {member_id: "owner", me["id"]: "member"})

    def test_a_database_without_a_primary_promotes_the_oldest_device(self):
        self.pair_browser()
        self.request_from(self.other())
        conn = self.app.db.get_db()
        conn.execute("UPDATE paired_clients SET role=NULL")
        conn.commit()
        oldest = conn.execute("SELECT id FROM paired_clients ORDER BY created, rowid LIMIT 1").fetchone()["id"]
        self.assertEqual(pairing.owner_id(), oldest)


class ColorPairingTests(PairingTestBase):
    """Press the Atlas button, then pick the colour its light shows."""

    def start(self, client=None, kind="browser"):
        r = (client or self.client).post("/api/pair/color/start", json={"name": "Pixel", "kind": kind},
                                         base_url=BASE, environ_base=ENV)
        return r

    def answer(self, view, color, client=None):
        return (client or self.client).post(f"/api/pair/color/{view['request_id']}/answer",
                                            json={"secret": view["secret"], "color": color},
                                            base_url=BASE, environ_base=ENV)

    def shown(self):
        return pairing.color_state()["pair_color"]

    def test_press_then_pick_the_colour_pairs_the_primary_user(self):
        r = self.start(kind="android")
        self.assertEqual(r.status_code, 200)
        view = r.get_json()
        self.assertEqual(view["state"], "press_button")
        self.assertEqual({c["id"] for c in view["palette"]}, set(pairing.COLOR_IDS))
        self.assertIsNone(self.shown())
        self.assertEqual(pairing.button_pressed(), "bound")
        poll = self.get(f"/api/pair/color/{view['request_id']}?secret={view['secret']}").get_json()
        self.assertEqual(poll["state"], "choose_color")
        self.assertEqual(sorted(poll["choices"]), sorted(pairing.COLOR_IDS))
        color = self.shown()
        self.assertIn(color, pairing.COLOR_IDS)
        self.assertNotIn(color, ("red", "amber"))
        r = self.answer(view, color)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["token"])                    # native app: bearer token
        self.assertTrue(pairing.claimed())
        self.assertIsNone(self.shown())
        self.assertEqual(pairing.list_clients()[0]["role"], "owner")
        self.assertEqual(pairing.list_clients()[0]["proof"], "button_color")

    def test_one_pick_per_press(self):
        view = self.start().get_json()
        pairing.button_pressed()
        wrong = next(c for c in pairing.COLOR_IDS if c != self.shown())
        r = self.answer(view, wrong)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["code"], "wrong")
        self.assertIsNone(self.shown())
        r = self.answer(view, "green")                             # no second guess on the same press
        self.assertEqual(r.status_code, 403)
        self.assertFalse(pairing.claimed())

    def test_nobody_can_pick_before_the_button_is_pressed(self):
        view = self.start().get_json()
        for color in pairing.COLOR_IDS:
            r = self.answer(view, color)
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.get_json()["code"], "press_button")
        self.assertFalse(pairing.claimed())

    def test_two_devices_waiting_is_a_conflict(self):
        a = self.start().get_json()
        b = self.start(self.app.app.test_client()).get_json()
        self.assertEqual(pairing.button_pressed(), "conflict")
        self.assertIsNone(self.shown())
        for view in (a, b):
            poll = self.get(f"/api/pair/color/{view['request_id']}?secret={view['secret']}").get_json()
            self.assertEqual(poll["state"], "conflict")

    def test_press_first_then_open_the_app(self):
        self.assertEqual(pairing.button_pressed(), "window")
        self.assertTrue(pairing.color_state()["press_window"])
        view = self.start().get_json()
        self.assertEqual(view["state"], "choose_color")
        self.assertEqual(self.answer(view, self.shown()).status_code, 200)

    def test_a_second_device_during_the_window_is_a_conflict(self):
        pairing.button_pressed()
        first = self.start().get_json()
        second = self.start(self.app.app.test_client()).get_json()
        self.assertEqual(second["state"], "conflict")
        poll = self.get(f"/api/pair/color/{first['request_id']}?secret={first['secret']}").get_json()
        self.assertEqual(poll["state"], "conflict")

    def test_attempts_expire(self):
        view = self.start().get_json()
        with mock.patch.object(pairing.time, "monotonic", return_value=pairing.time.monotonic() + 3600):
            self.assertEqual(pairing.button_pressed(), "window")
            poll = self.get(f"/api/pair/color/{view['request_id']}?secret={view['secret']}").get_json()
        self.assertIn(poll["state"], ("expired", "unknown"))

    def test_a_wrong_secret_sees_nothing(self):
        view = self.start().get_json()
        pairing.button_pressed()
        poll = self.get(f"/api/pair/color/{view['request_id']}?secret=guess").get_json()
        self.assertEqual(poll, {"state": "unknown"})

    def test_button_does_nothing_once_atlas_is_set_up(self):
        self.pair_browser()
        self.assertEqual(pairing.button_pressed(), "claimed")
        r = self.start(self.app.app.test_client())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "claimed")


class LockoutTests(PairingTestBase):
    def test_wrong_setup_codes_stay_counted_across_a_restart(self):
        for _ in range(5):
            pairing._record_failure("10.42.0.9")
        pairing._cache.clear()
        self.app.db._local.conn = None                        # what a restart sees
        self.app.db.init_db()
        self.assertTrue(pairing._rate_limited("10.42.0.9"))
        self.assertFalse(pairing._rate_limited("10.42.0.10"))


class PrimaryUserMigrationTests(unittest.TestCase):
    def test_oldest_paired_device_becomes_primary(self):
        import sqlite3
        import database
        conn = sqlite3.connect(":memory:")
        database._migration_pairing(conn)
        for cid, created in (("b", 200), ("a", 100), ("c", 300)):
            conn.execute("INSERT INTO paired_clients (id, name, kind, token_hash, created) VALUES (?, ?, 'ios', ?, ?)",
                         (cid, cid, cid * 8, created))
        conn.execute("UPDATE paired_clients SET revoked=1 WHERE id='a'")
        database._migration_primary_user(conn)
        roles = dict(conn.execute("SELECT id, role FROM paired_clients").fetchall())
        self.assertEqual(roles, {"a": "member", "b": "owner", "c": "member"})
        database._migration_primary_user(conn)                 # running it again changes nothing
        self.assertEqual(dict(conn.execute("SELECT id, role FROM paired_clients").fetchall()), roles)
