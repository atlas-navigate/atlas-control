"""Ray's passage index: same scores as per-passage cosine, rebuilt only when
the knowledge base changes."""
import json
import math
import unittest

import numpy as np

import database as db
import vector_store


def _cos(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _doc(i, vectors, legacy=False):
    emb = vectors[0] if legacy else {"v": 4, "chunks": [{"h": f"h{j}", "t": f"t{j}", "e": v}
                                                         for j, v in enumerate(vectors)]}
    return {"id": i, "title": f"doc {i}", "content": "body", "embedding": json.dumps(emb)}


class PassageIndexTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.vecs = {i: [rng.standard_normal(8).tolist() for _ in range(n)] for i, n in ((1, 3), (2, 1), (3, 2))}
        self.docs = [_doc(i, v) for i, v in self.vecs.items()]

    def test_scores_match_per_passage_cosine(self):
        index = vector_store.PassageIndex(self.docs)
        q = np.random.default_rng(3).standard_normal(8).tolist()
        for (doc, chunks), scores in zip(index.entries, index.scores(q)):
            self.assertNotIn("embedding", doc)                    # raw JSON not kept in memory
            for j, v in enumerate(scores):
                self.assertAlmostEqual(v, _cos(q, self.vecs[doc["id"]][j]), places=5)
                self.assertEqual(chunks[j]["t"], f"t{j}")

    def test_other_dimensions_and_zero_vectors_are_harmless(self):
        docs = self.docs + [_doc(9, [[1.0, 2.0, 3.0]]), _doc(10, [[0.0] * 8])]
        index = vector_store.PassageIndex(docs)
        scores = dict((doc["id"], s) for (doc, _), s in zip(index.entries, index.scores([1.0] * 8)))
        self.assertEqual(scores[9], [None])                        # left over from another model
        self.assertEqual(scores[10], [0.0])
        self.assertTrue(all(v == 0.0 for v in sum(index.scores([0.0] * 8), []) if v is not None))

    def test_legacy_whole_document_vectors_still_work(self):
        index = vector_store.PassageIndex([_doc(5, [[1.0, 0.0]], legacy=True)])
        self.assertAlmostEqual(index.scores([1.0, 0.0])[0][0], 1.0, places=6)
        self.assertEqual(index.entries[0][1][0]["t"], "body")

    def test_doc_similarity_is_best_passage_pair(self):
        index = vector_store.PassageIndex(self.docs)
        ids, sims = index.doc_similarity()
        for a, ida in enumerate(ids):
            for b, idb in enumerate(ids):
                if a != b:
                    want = max(_cos(x, y) for x in self.vecs[ida] for y in self.vecs[idb])
                    self.assertAlmostEqual(float(sims[a, b]), want, places=5)


class RebuildTests(unittest.TestCase):
    def setUp(self):
        db._local.conn = None
        db.init_db()
        vector_store.invalidate()

    def test_rebuilt_only_when_documents_change(self):
        first = vector_store.current()
        self.assertIs(vector_store.current(), first)
        doc_id = db.ai_add_document("Water", "Boil it.", "water", embedding=json.dumps([1.0, 0.0]))
        second = vector_store.current()
        self.assertIsNot(second, first)
        self.assertIn(doc_id, [d["id"] for d, _ in second.entries])
        db.ai_update_document_embedding(doc_id, json.dumps([0.0, 1.0]))
        third = vector_store.current()
        self.assertIsNot(third, second)
        db.ai_delete_document(doc_id)
        self.assertNotIn(doc_id, [d["id"] for d, _ in vector_store.current().entries])


if __name__ == "__main__":
    unittest.main()


from test_pairing import BASE, ENV, PairingTestBase  # noqa: E402


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
