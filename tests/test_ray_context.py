"""Ray's prompt: knowledge-base search is never switched off by a stray
word, and the prompt is fitted to the model's context window."""
import unittest
from unittest import mock

import database as db
import token_budget
from ai_manager import AIManager, _LIVE_DATA_RE


def _bare_manager():
    m = AIManager.__new__(AIManager)
    m.mesh_manager = m.gps_manager = m.socketio = None
    return m


class LiveQueryRoutingTests(unittest.TestCase):
    def setUp(self):
        db._local.conn = None
        db.init_db()
        # Defaults only: other tests change Ray's settings (e.g. rag_top_k) in the shared test DB.
        db.get_db().execute("DELETE FROM ai_settings")
        db.get_db().commit()

    def test_whole_words_only(self):
        for text in ("build a frame shelter", "which state am i in", "recover a program",
                     "temporary splint"):
            self.assertIsNone(_LIVE_DATA_RE.search(text), text)
        for text in ("what is the cpu temperature", "is the mesh online", "show node status"):
            self.assertIsNotNone(_LIVE_DATA_RE.search(text), text)

    def test_survival_questions_that_mention_a_live_word_still_search(self):
        m = _bare_manager()
        seen = []

        def fake_search(query, top_k=5, **kw):
            seen.append((query, top_k))
            return [], 0.0
        with mock.patch.object(m, "rag_search", side_effect=fake_search):
            for q in ("how do I apply pressure to a wound", "what is the recovery position",
                      "how do I treat altitude sickness", "how do I use a signal mirror",
                      "how do I build a frame shelter"):
                m.build_context(q)
        tops = dict(seen)
        self.assertEqual(len(tops), 5)                                      # every one searched
        self.assertEqual(tops["how do I build a frame shelter"], 5)
        self.assertEqual(tops["how do I apply pressure to a wound"], 2)     # live word: fewer passages


class TokenBudgetTests(unittest.TestCase):
    SETTINGS = {"num_ctx": "4096", "num_predict": "1024"}

    def test_newest_turns_are_kept_and_the_window_respected(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 2000}
                   for i in range(8)]
        kept, info = token_budget.fit_history("s" * 3000, "question", history, self.SETTINGS)
        self.assertLess(len(kept), 8)
        self.assertEqual(kept, history[-len(kept):])                        # newest, in order
        self.assertLessEqual(info["estimated"], info["budget"])
        self.assertEqual(info["history_dropped"], 8 - len(kept))

    def test_short_conversations_are_untouched(self):
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        kept, info = token_budget.fit_history("system", "q", history, self.SETTINGS)
        self.assertEqual(kept, history)
        self.assertFalse(info["over"])

    def test_passages_shrink_when_the_prompt_is_already_large(self):
        roomy = token_budget.rag_char_budget(self.SETTINGS, ["s" * 3000], "q", 7000)
        tight = token_budget.rag_char_budget(self.SETTINGS, ["s" * 3000, "live" * 1500], "q", 7000)
        self.assertLess(tight, roomy)
        self.assertGreaterEqual(tight, token_budget.MIN_RAG_CHARS)
        small = token_budget.rag_char_budget({"num_ctx": "2048", "num_predict": "1024"}, ["s" * 3000], "q", 7000)
        self.assertEqual(small, token_budget.MIN_RAG_CHARS)


class HistoryQueryTests(unittest.TestCase):
    def setUp(self):
        db._local.conn = None
        db.init_db()

    def test_recent_messages_use_a_limit_and_a_stable_order(self):
        chat = db.ai_create_chat()["id"]
        conn = db.get_db()
        for i in range(12):   # same timestamp: order must come from the id
            conn.execute("INSERT INTO ai_messages (chat_id, role, content, created_at, explain) VALUES (?, ?, ?, 100, ?)",
                         (chat, "user" if i % 2 == 0 else "assistant", f"m{i}", '{"big": "trace"}'))
        conn.commit()
        recent = db.ai_get_recent_messages(chat, 8)
        self.assertEqual([m["content"] for m in recent], [f"m{i}" for i in range(4, 12)])
        self.assertNotIn("explain", db.ai_get_messages(chat)[0])


if __name__ == "__main__":
    unittest.main()
