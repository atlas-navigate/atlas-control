"""Ray's message layout (ray_prompt.py, AIManager._compose_messages): what the
model sees is unchanged, but consecutive prompts share their beginning so
the model server can reuse the processed history."""
import unittest
from unittest import mock

import database as db
import ray_prompt
from ai_manager import AIManager


def _manager():
    m = AIManager.__new__(AIManager)
    m.mesh_manager = m.gps_manager = m.socketio = None
    m._history_start = {}
    return m


def _settings(layout="turn", prefix_cache="any"):
    return {"num_ctx": "4096", "num_predict": "1024", "ray_prompt_layout": layout,
            "system_prompt": "You are Ray.", "_profile": {"prefix_cache": prefix_cache}}


def _ctx(turn_text, date="=== CURRENT DATE ===\nThursday, October 08, 2026\n=== END DATE ==="):
    parts = [date, turn_text]
    return "\n\n".join(parts), {"units": "metric", "date_block": date, "turn_parts": [turn_text],
                                "user_location": None}


class LayoutTests(unittest.TestCase):
    def setUp(self):
        db._local.conn = None
        db.init_db()
        self.chat = db.ai_create_chat()["id"]
        self.m = _manager()

    def turn(self, question, live, answer, settings=None):
        context, meta = _ctx(f"=== CURRENT POSITION ===\n{live}\n\nKNOWLEDGE BASE\n<<<{live}>>> passage")
        msgs, _ = self.m._compose_messages(self.chat, question, settings or _settings(), context, meta)
        db.ai_add_message(self.chat, "user", question)
        db.ai_add_message(self.chat, "assistant", answer + "\n\n---\nConfidence: HIGH | Source: KB")
        return msgs

    @staticmethod
    def rendered(msgs):
        return "".join(f"<{m['role']}>{m['content']}" for m in msgs)

    def test_consecutive_prompts_share_everything_before_the_previous_question(self):
        first = self.turn("How do I purify water?", "lat 39.29011", "Boil it for 1 minute.")
        second = self.turn("And with bleach?", "lat 39.29018", "2 drops per litre.")
        third = self.turn("How long do I wait?", "lat 39.29022", "30 minutes.")
        # The system message never changes between turns.
        self.assertEqual(first[0], second[0])
        self.assertEqual(second[0], third[0])
        # Turn 3 starts with turn 2's prompt up to (not including) its last user message.
        prefix = self.rendered(second[:-1])
        self.assertTrue(self.rendered(third).startswith(prefix))
        # Each turn's live data rides in its own user message only.
        self.assertIn("lat 39.29022", third[-1]["content"])
        self.assertNotIn("lat 39.29022", third[0]["content"])
        self.assertTrue(third[-1]["content"].endswith(ray_prompt.QUESTION_MARK + "\nHow long do I wait?"))

    def test_history_is_replayed_without_the_confidence_footer(self):
        self.turn("q1", "a", "answer one")
        msgs = self.turn("q2", "b", "answer two")
        assistant = [m for m in msgs if m["role"] == "assistant"]
        self.assertEqual(assistant[0]["content"], "answer one")

    def test_the_model_gets_the_same_material_in_either_layout(self):
        context, meta = _ctx("=== CURRENT POSITION ===\nlat 1\n\nKNOWLEDGE BASE\n<<<x>>> boil 1 minute")
        turn, _ = self.m._compose_messages(self.chat, "q?", _settings("turn"), context, meta, "CALC RESULTS 42")
        legacy, _ = self.m._compose_messages(self.chat, "q?", _settings("legacy"), context, meta, "CALC RESULTS 42")
        for needle in ("boil 1 minute", "lat 1", "CALC RESULTS 42", "THURSDAY".title(), "You are Ray.",
                       "Always express real-world measurements in metric units"):
            self.assertIn(needle, self.rendered(turn))
            self.assertIn(needle, self.rendered(legacy))
        self.assertIn("boil 1 minute", legacy[0]["content"])        # old: all in the system message
        self.assertNotIn("boil 1 minute", turn[0]["content"])

    def test_legacy_layout_is_exactly_the_old_prompt(self):
        context, meta = _ctx("X")
        msgs, _ = self.m._compose_messages(self.chat, "q?", _settings("legacy"), context, meta)
        self.assertEqual(msgs[-1]["content"], "q?")
        self.assertTrue(msgs[0]["content"].endswith("\n\n" + context))


class StickyHistoryTests(unittest.TestCase):
    def rows(self, n):
        return [{"id": i, "role": "user" if i % 2 else "assistant", "content": f"m{i}"} for i in range(1, n + 1)]

    def test_start_stays_put_and_never_keeps_less(self):
        rows = self.rows(10)
        default = rows[-8:]                    # what Ray sends without the sticky window
        chosen, start = ray_prompt.sticky_history(rows, default, 1, lambda c: True, 12)
        self.assertEqual(chosen, rows)         # keeps the window from message 1
        self.assertEqual(start, 1)
        self.assertTrue(set(m["id"] for m in default) <= set(m["id"] for m in chosen))

    def test_restarts_when_too_long_or_over_budget(self):
        rows = self.rows(14)
        default = rows[-8:]
        chosen, start = ray_prompt.sticky_history(rows, default, 1, lambda c: True, 12)
        self.assertEqual((chosen, start), (default, default[0]["id"]))
        chosen, start = ray_prompt.sticky_history(rows[:10], rows[2:10], 1, lambda c: False, 12)
        self.assertEqual((chosen, start), (rows[2:10], 3))

    def test_property_never_less_than_today(self):
        import random
        rnd = random.Random(7)
        for _ in range(500):
            n = rnd.randint(0, 14)
            rows = self.rows(n)
            k = rnd.randint(0, min(8, n))
            default = rows[n - k:] if k else []
            start = rnd.choice([None] + [r["id"] for r in rows])
            chosen, _ = ray_prompt.sticky_history(rows, default, start, lambda c: rnd.random() < 0.7, 12)
            self.assertTrue({m["id"] for m in default} <= {m["id"] for m in chosen})
            self.assertLessEqual(len(chosen), max(12, len(default)))

    def test_turn_layout_on_a_checkpoint_model_uses_the_plain_window(self):
        db._local.conn = None
        db.init_db()
        chat = db.ai_create_chat()["id"]
        m = _manager()
        for i in range(6):
            db.ai_add_message(chat, "user", f"q{i}")
            db.ai_add_message(chat, "assistant", f"a{i}")
        context, meta = _ctx("X")
        msgs, _ = m._compose_messages(chat, "q?", _settings("turn", "checkpoint"), context, meta)
        self.assertEqual(len(msgs), 1 + 8 + 1)
        self.assertEqual(m._history_start, {})


class WarmupTests(unittest.TestCase):
    def test_warmup_processes_the_same_system_message_a_real_turn_uses(self):
        db._local.conn = None
        db.init_db()
        chat = db.ai_create_chat()["id"]
        m = _manager()
        s = _settings()
        date_block, _ = m._date_block()
        context, meta = _ctx("LIVE", date=date_block)
        with mock.patch.object(db, "get_app_settings", return_value={"units": "metric"}):
            warm = m._warmup_messages(s)
        real, _ = m._compose_messages(chat, "q?", s, context, meta)
        self.assertEqual(warm[0], real[0])
        self.assertEqual(m._warmup_messages(_settings("legacy")), [{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
