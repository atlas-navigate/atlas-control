"""Routes serving cached or pushed performance-sensitive data: knowledge
map, stats push, diagnostics."""
import json

import database as db
from test_pairing import BASE, ENV, PairingTestBase



class KnowledgeMapRouteTests(PairingTestBase):
    def test_edges_come_from_best_passage_pairs_and_are_cached(self):
        conn = db.get_db()
        conn.execute("DELETE FROM ai_documents")
        conn.commit()
        a = db.ai_add_document("Purify water", "x", "water", embedding=json.dumps(
            {"chunks": [{"h": "", "t": "a", "e": [1.0, 0.0, 0.0]}, {"h": "", "t": "b", "e": [0.0, 0.0, 1.0]}]}))
        b = db.ai_add_document("Boil water", "y", "water", embedding=json.dumps(
            {"chunks": [{"h": "", "t": "c", "e": [0.9, 0.1, 0.0]}]}))
        c = db.ai_add_document("Knots", "z", "rope", embedding=json.dumps(
            {"chunks": [{"h": "", "t": "d", "e": [0.0, 1.0, 0.0]}]}))
        r = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "kind": "android"})
        auth = {"Authorization": f"Bearer {r.get_json()['token']}"}
        get = lambda: self.client.get("/api/ai/knowledge-map", headers=auth, base_url=BASE, environ_base=ENV)
        body = get().get_json()
        self.assertEqual({n["id"] for n in body["nodes"]}, {a, b, c})
        edges = {(e["from"], e["to"]): e["weight"] for e in body["edges"]}
        self.assertEqual(list(edges), [(min(a, b), max(a, b))])
        self.assertAlmostEqual(edges[(min(a, b), max(a, b))], 0.994, places=3)
        payload = self.app._knowledge_map_cache["payload"]
        get()
        self.assertIs(self.app._knowledge_map_cache["payload"], payload)      # unchanged KB: cached
        db.ai_delete_document(c)
        get()
        self.assertIsNot(self.app._knowledge_map_cache["payload"], payload)
        perf = self.client.get("/api/diagnostics/performance", headers=auth, base_url=BASE, environ_base=ENV)
        self.assertIn("knowledge_map.build", perf.get_json()["stages_ms"])
        import pairing
        self.client.delete_cookie(pairing.COOKIE, domain="192.168.1.20")
        self.assertEqual(self.get("/api/diagnostics/performance").status_code, 401)   # unpaired


class StatsPushTests(PairingTestBase):
    def test_stats_are_pushed_only_to_open_system_pages(self):
        from unittest import mock
        import pairing
        p = mock.patch.object(self.app, "_is_local", return_value=True)
        p.start(); self.addCleanup(p.stop)
        token = self.post("/api/pair/claim", json={"setup_code": self.setup_code, "kind": "ios"}).get_json()["token"]
        self.client.delete_cookie(pairing.COOKIE, domain="192.168.1.20")
        sio = self.app.socketio.test_client(self.app.app, headers={"Host": "192.168.1.20"}, auth={"token": token})
        self.addCleanup(lambda: sio.is_connected() and sio.disconnect())
        with mock.patch.object(self.app.system_stats, "get_stats", return_value={"cpu_pct": 7}):
            self.assertFalse(self.app._push_stats_once())                 # nobody watching: no work
            sio.emit("join_rooms", {"rooms": ["system"]})
            self.assertTrue(self.app._push_stats_once())
            got = [m for m in sio.get_received() if m["name"] == "system_stats"]
            self.assertEqual(got[0]["args"][0], {"cpu_pct": 7})
            sio.emit("join_rooms", {"rooms": ["mesh"]})
            self.assertFalse(self.app._push_stats_once())
