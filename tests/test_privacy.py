"""Privacy: retention, deleting history for real, the local security log,
explicit confirmation for destructive actions, and a resale-safe factory reset."""
import json
import os
import secrets
import time
import unittest

import audit_log
import database as db
import pairing
import retention
from test_pairing import BASE, ENV, PairingTestBase

DAY = 86400


def _now():
    return int(time.time())


class _DbTest(unittest.TestCase):
    def setUp(self):
        db._local.conn = None
        db.init_db()
        conn = db.get_db()
        for table in ("positions", "tracker_devices", "hike_sessions", "waypoints", "ai_messages", "ai_chats",
                      "calendar_events", "audit_log", "paired_clients"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'retention_%'")
        conn.commit()

    def add_chat(self, content="hello", age_days=0, explain=None):
        conn = db.get_db()
        ts = _now() - age_days * DAY
        chat = conn.execute("INSERT INTO ai_chats (title, created_at, updated_at) VALUES ('t', ?, ?)", (ts, ts)).lastrowid
        conn.execute("INSERT INTO ai_messages (chat_id, role, content, created_at, explain) VALUES (?, 'user', ?, ?, ?)",
                     (chat, content, ts, explain))
        conn.commit()
        return chat

    def count(self, table, where="1"):
        return db.get_db().execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


class RetentionTests(_DbTest):
    def test_defaults_are_privacy_leaning(self):
        self.assertEqual(retention.settings(), {"retention_ai_days": 0, "retention_explain_days": 30,
                                                "retention_location_days": 7, "retention_tracker_days": 30})

    def test_invalid_values_are_refused(self):
        for bad in (-1, 5000, "soon"):
            with self.assertRaises(ValueError):
                retention.update({"retention_location_days": bad})
        self.assertEqual(retention.update({"retention_location_days": 1})["retention_location_days"], 1)

    def test_nightly_pass_removes_only_what_is_due(self):
        conn = db.get_db()
        conn.execute("INSERT INTO positions (node_id, timestamp, latitude, longitude) VALUES ('gps', ?, 1, 1)", (_now() - 10 * DAY,))
        conn.execute("INSERT INTO positions (node_id, timestamp, latitude, longitude) VALUES ('gps', ?, 1, 1)", (_now() - DAY,))
        conn.execute("INSERT INTO tracker_devices (id, name, last_seen) VALUES ('old', 'o', ?)", (_now() - 40 * DAY,))
        conn.execute("INSERT INTO tracker_devices (id, name, last_seen) VALUES ('new', 'n', ?)", (_now() - DAY,))
        conn.commit()
        old_chat = self.add_chat(age_days=400, explain='{"gps": "x"}')
        self.add_chat(explain='{"gps": "y"}')
        retention.apply()
        self.assertEqual(self.count("positions"), 1)
        self.assertEqual(self.count("tracker_devices"), 1)
        self.assertEqual(self.count("ai_chats"), 2)                   # conversations: until deleted
        self.assertEqual(self.count("ai_messages", "explain IS NOT NULL"), 1)
        retention.update({"retention_ai_days": 365})
        retention.apply()
        self.assertEqual(self.count("ai_chats", f"id={old_chat}"), 0)
        self.assertEqual(self.count("ai_chats"), 1)

    def test_deleted_conversations_do_not_linger_in_the_database_files(self):
        marker = "private-" + secrets.token_hex(12)
        self.add_chat(content=marker)
        db.get_db().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.add_chat(content=marker + "-wal")                          # one copy still only in the WAL
        self.assertEqual(retention.delete_ai_history()["conversations"], 2)
        blob = b""
        for suffix in ("", "-wal"):
            if os.path.exists(db.DB_PATH + suffix):
                blob += open(db.DB_PATH + suffix, "rb").read()
        self.assertNotIn(marker.encode(), blob)

    def test_location_history_deletion_keeps_saved_places(self):
        conn = db.get_db()
        conn.execute("INSERT INTO positions (node_id, timestamp, latitude, longitude) VALUES ('gps', ?, 1, 1)", (_now(),))
        conn.execute("INSERT INTO tracker_devices (id, name, last_seen) VALUES ('p', 'p', ?)", (_now(),))
        conn.execute("INSERT INTO hike_sessions (name, start_time) VALUES ('walk', ?)", (_now(),))
        conn.execute("INSERT INTO waypoints (name, latitude, longitude) VALUES ('camp', 1, 1)")
        conn.commit()
        self.add_chat(explain='{"gps": "near camp"}')
        counts = retention.delete_location_history()
        self.assertEqual(counts, {"positions": 1, "trackers": 1, "hikes": 1})
        self.assertEqual(self.count("waypoints"), 1)
        self.assertEqual(self.count("ai_messages", "explain IS NOT NULL"), 0)


class FactoryResetDataTests(_DbTest):
    def test_new_owner_gets_a_clean_atlas_but_keeps_reference_and_hardware_settings(self):
        conn = db.get_db()
        seed = conn.execute("INSERT INTO ai_documents (title, content, is_seed) VALUES ('First aid', 'x', 1)").lastrowid
        mine = conn.execute("INSERT INTO ai_documents (title, content, is_seed) VALUES ('My notes', 'y', 0)").lastrowid
        for doc in (seed, mine):
            conn.execute("INSERT INTO ai_documents_fts(rowid, title, tags, content) VALUES (?, 't', '', 'c')", (doc,))
        conn.execute("INSERT INTO calendar_events (title, start_ts, created_at) VALUES ('dentist', 1, 1)")
        conn.commit()
        db.set_app_settings({"units": "imperial", "serial_port": "/dev/ttyTHS1", "retention_location_days": "30"})
        db.ai_set_settings({"temperature": "0.9"})
        pairing._issue("Old phone", "android", "setup_code")
        self.add_chat()
        db.factory_reset_data()
        self.assertEqual(self.count("ai_documents", f"id={seed}"), 1)
        self.assertEqual(self.count("ai_documents", f"id={mine}"), 0)
        self.assertEqual(self.count("ai_documents_fts", f"rowid={mine}"), 0)
        for table in ("calendar_events", "paired_clients", "ai_chats", "audit_log"):
            self.assertEqual(self.count(table), 0, table)
        settings = db.get_app_settings()
        self.assertEqual(settings["serial_port"], "/dev/ttyTHS1")
        self.assertEqual(settings["units"], "metric")
        self.assertEqual(retention.settings()["retention_location_days"], 7)
        self.assertNotEqual(db.ai_get_settings().get("temperature"), "0.9")
        self.assertIsNotNone(conn.execute("SELECT value FROM ai_settings WHERE key='embed_format_v'").fetchone())


class AuditLogTests(_DbTest):
    def test_only_short_plain_values_are_kept(self):
        audit_log.record("x", "Phone", version="1.2.3", text="t" * 500, nested={"a": 1}, keys=["units"])
        self.assertEqual(audit_log.recent(1)[0]["detail"], {"version": "1.2.3", "keys": ["units"]})

    def test_log_is_capped(self):
        conn = db.get_db()
        conn.executemany("INSERT INTO audit_log (ts, action) VALUES (?, 'x')",
                         [(_now(),)] * (audit_log.MAX_ROWS + 50) + [(_now() - 400 * DAY,)])
        conn.commit()
        audit_log.prune()
        self.assertEqual(self.count("audit_log"), audit_log.MAX_ROWS)
        self.assertEqual(self.count("audit_log", f"ts < {_now() - 200 * DAY}"), 0)


class PrivacyRouteTests(PairingTestBase):
    """Routes, as a paired Android app (bearer token: no CSRF header needed)."""

    def setUp(self):
        super().setUp()
        conn = db.get_db()
        conn.execute("DELETE FROM audit_log")
        conn.commit()
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "kind": "android", "name": "Sam's phone"})
        self.auth = {"Authorization": f"Bearer {r.get_json()['token']}"}
        self.client.delete_cookie(pairing.COOKIE, domain="192.168.1.20")

    def send(self, method, path, body=None):
        return self.client.open(path, method=method, json=body, headers=self.auth, base_url=BASE, environ_base=ENV)

    def test_destructive_requests_need_an_explicit_confirmation(self):
        for path in ("/api/wifi/disconnect", "/api/hotspot/stop", "/api/hotspot/config",
                     "/api/privacy/delete-ai-history", "/api/privacy/delete-location-history"):
            for body in ({}, {"confirm": "true"}, {"confirm": 1}, None):
                r = self.send("POST", path, body)
                self.assertEqual(r.status_code, 400, (path, body))
                self.assertTrue(r.get_json()["confirm_required"])
        for body in ({}, {"confirm": True}, {"confirm": "reset"}):
            r = self.send("POST", "/api/factory-reset", body)
            self.assertEqual(r.status_code, 400, body)

    def test_cross_site_form_cannot_reach_confirmed_routes(self):
        r = self.client.post("/api/factory-reset", data="confirm=RESET", content_type="application/x-www-form-urlencoded",
                             headers=self.auth, base_url=BASE, environ_base=ENV)
        self.assertIn(r.status_code, (400, 403, 415))

    def test_privacy_deletes_work_and_are_logged(self):
        chat = self.send("POST", "/api/ai/chats", {}).get_json()
        self.assertTrue(chat.get("id"))
        r = self.send("POST", "/api/privacy/delete-ai-history", {"confirm": True})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.get_json()["conversations"], 1)
        self.assertEqual(self.send("GET", "/api/ai/chats").get_json(), [])
        self.assertEqual(self.send("POST", "/api/privacy/delete-location-history", {"confirm": True}).status_code, 200)
        actions = [(e["action"], e["actor"]) for e in self.send("GET", "/api/audit").get_json()]
        self.assertIn(("ai_history_deleted", "Sam's phone"), actions)
        self.assertIn(("location_history_deleted", "Sam's phone"), actions)

    def test_retention_route_validates_and_is_logged(self):
        self.assertEqual(self.send("PUT", "/api/privacy/retention", {"retention_location_days": -5}).status_code, 400)
        r = self.send("PUT", "/api/privacy/retention", {"retention_location_days": 30})
        self.assertEqual(r.get_json()["retention_location_days"], 30)
        entry = self.send("GET", "/api/audit").get_json()[0]
        self.assertEqual((entry["action"], entry["detail"]["keys"]), ("retention_changed", ["retention_location_days"]))

    def test_security_log_names_the_device_but_never_values(self):
        self.send("PUT", "/api/settings", {"dashboard_title": "Hidden-Title-7731"})
        self.send("POST", "/api/hotspot/config", {"ssid": "Cabin-Net-4410", "password": "pw-never-logged-99", "confirm": True})
        log = self.send("GET", "/api/audit").get_json()
        text = json.dumps(log)
        for secret in ("Hidden-Title-7731", "Cabin-Net-4410", "pw-never-logged-99", self.setup_code):
            self.assertNotIn(secret, text)
        actions = [e["action"] for e in log]
        self.assertIn("settings_changed", actions)
        self.assertIn("hotspot_changed", actions)
        self.assertIn("device_paired", actions)
        paired = next(e for e in log if e["action"] == "device_paired")
        self.assertEqual(paired["detail"], {"kind": "android", "method": "setup_code"})

    def test_unpaired_clients_cannot_read_the_security_log(self):
        self.assertEqual(self.get("/api/audit").status_code, 401)

    def test_wrong_setup_code_is_logged(self):
        self.post("/api/pair/claim", json={"setup_code": "WRONG-CODE", "kind": "android"})
        self.assertEqual(self.send("GET", "/api/audit").get_json()[0]["action"], "pairing_code_rejected")

    def test_factory_reset_is_resale_safe(self):
        app = self.app
        import atlas_paths
        os.makedirs(atlas_paths.BACKUP_DIR, exist_ok=True)
        open(os.path.join(atlas_paths.BACKUP_DIR, "pre-migration-v2.db"), "w").write("old owner data")
        app._save_hotspot_cfg("Owner-Net", "owner-password-1")
        db.get_db().execute("INSERT INTO calendar_events (title, start_ts, created_at) VALUES ('x', 1, 1)")
        db.get_db().commit()
        os.makedirs(atlas_paths.LOG_DIR, exist_ok=True)
        os.makedirs(os.path.join(atlas_paths.CACHE_DIR, "geocode"), exist_ok=True)
        open(os.path.join(atlas_paths.LOG_DIR, "update.log"), "w").write("joined Owner-Net\n")
        open(os.path.join(atlas_paths.CACHE_DIR, "geocode", "near-home.json"), "w").write("{}")
        from unittest import mock
        helper_calls = []
        real_run = app.atlas_helper.run
        def helper(verb, timeout=60, **args):
            helper_calls.append(verb)
            return real_run(verb, timeout=timeout, **args)
        with mock.patch.object(app.atlas_helper, "run", side_effect=helper):
            r = self.send("POST", "/api/factory-reset", {"confirm": "RESET"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(open(os.path.join(atlas_paths.LOG_DIR, "update.log")).read(), "")
        self.assertEqual(os.listdir(atlas_paths.CACHE_DIR), [])
        self.assertIn("logs.vacuum", helper_calls)
        self.assertEqual(self.send("GET", "/api/nodes").status_code, 401)      # every device pairs again
        self.assertFalse(os.path.exists(atlas_paths.BACKUP_DIR))
        self.assertFalse(os.path.exists(app._HOTSPOT_CFG_PATH))
        label = app.device_identity.hotspot()
        self.assertEqual(app._load_hotspot_cfg()["password"], label["password"])  # back to the label
        self.assertEqual(db.get_db().execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 0)
        self.assertEqual([e["action"] for e in audit_log.recent()], ["factory_reset"])

    def test_removing_a_device_cuts_its_live_connection(self):
        from unittest import mock
        p = mock.patch.object(self.app, "_is_local", return_value=True)
        p.start(); self.addCleanup(p.stop)
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "kind": "ios", "name": "Old iPhone"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        token = r.get_json()["token"]
        self.client.delete_cookie(pairing.COOKIE, domain="192.168.1.20")
        sio = self.app.socketio.test_client(self.app.app, headers={"Host": "192.168.1.20"}, auth={"token": token})
        self.assertTrue(sio.is_connected())
        victim = next(c for c in self.send("GET", "/api/pair/clients").get_json() if c["name"] == "Old iPhone")
        self.assertEqual(self.send("DELETE", f"/api/pair/clients/{victim['id']}").status_code, 200)
        self.assertFalse(sio.is_connected())


if __name__ == "__main__":
    unittest.main()
