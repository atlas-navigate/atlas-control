"""Ray recovers by itself when its startup warmup fails (seen after a power
cut: Ollama aborted a model load when NTP stepped the clock forward)."""
import threading
import unittest
from unittest import mock

import database as db
from ai_manager import AIManager


class FakeTimer:
    made = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.cancelled, self.daemon = delay, fn, False, False
        FakeTimer.made.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


def _manager():
    m = AIManager.__new__(AIManager)
    m.mesh_manager = m.gps_manager = m.socketio = None
    m._startup_warmup_done = threading.Event()
    m._startup_state_lock = threading.Lock()
    m._startup_phase, m._startup_ready, m._startup_last_error = "init", False, ""
    m._startup_started_at, m._startup_finished_at = 0.0, None
    m._warmup_lock = threading.Lock()
    m._warmup_retry_timer = None
    m._warmup_failures = 0
    m._ollama_lock = threading.Lock()
    m._embed_lock = threading.Lock()
    return m


class WarmupRetryTests(unittest.TestCase):
    def setUp(self):
        db._local.conn = None
        db.init_db()
        FakeTimer.made = []
        self.m = _manager()
        self.outcomes = []
        self.loads = 0
        patches = [
            mock.patch("ai_manager.threading.Timer", FakeTimer),
            mock.patch.object(self.m, "_models_resident", return_value=False),
            mock.patch.object(self.m, "_ensure_model_present", return_value=True),
            mock.patch.object(self.m, "_fall_back_if_missing", side_effect=lambda raw, s: s),
            mock.patch.object(self.m, "_load_pair", side_effect=self._load),
            mock.patch.object(AIManager, "_ai_enabled", staticmethod(lambda: True)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _load(self, settings):
        self.loads += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome:
            raise outcome

    def test_a_failed_warmup_is_retried_until_ray_is_ready(self):
        self.outcomes = [RuntimeError("HTTP Error 500"), RuntimeError("HTTP Error 500")]
        self.m._warmup()
        self.assertEqual(self.m.startup_status()["phase"], "degraded")
        self.assertEqual([t.delay for t in FakeTimer.made], [10])
        FakeTimer.made[-1].fire()                    # second failure: back off
        self.assertEqual([t.delay for t in FakeTimer.made], [10, 30])
        FakeTimer.made[-1].fire()                    # third attempt works
        self.assertTrue(self.m.is_ready())
        self.assertEqual(self.m._warmup_failures, 0)
        self.assertEqual(len(FakeTimer.made), 2)     # nothing more scheduled

    def test_backoff_settles_at_five_minutes(self):
        self.outcomes = [RuntimeError("down")] * 6
        self.m._warmup()
        for _ in range(5):
            FakeTimer.made[-1].fire()
        self.assertEqual([t.delay for t in FakeTimer.made], [10, 30, 60, 300, 300, 300])

    def test_a_clock_step_retries_at_once(self):
        self.outcomes = [RuntimeError("Load failed")]
        self.m._warmup()
        pending = FakeTimer.made[-1]
        with mock.patch("ai_manager.threading.Thread") as thread:
            thread.side_effect = lambda target, **kw: mock.Mock(start=target)
            self.m.retry_warmup_if_degraded("clock stepped forward")
        self.assertTrue(pending.cancelled)
        self.assertTrue(self.m.is_ready())

    def test_no_retry_once_ready_or_when_ray_is_switched_off(self):
        self.m._warmup()
        self.assertTrue(self.m.is_ready())
        self.assertEqual(FakeTimer.made, [])
        self.m.retry_warmup_if_degraded("clock stepped forward")   # ready: nothing to do
        self.m._set_startup_state("degraded", ready=False)
        loads = self.loads
        with mock.patch.object(AIManager, "_ai_enabled", staticmethod(lambda: False)):
            self.m._retry_warmup()
        self.assertEqual(self.loads, loads)          # owner turned Ray off: no reload

    def test_only_one_warmup_runs_at_a_time(self):
        self.m._warmup_lock.acquire()
        try:
            self.m._warmup()                        # returns at once
        finally:
            self.m._warmup_lock.release()
        self.assertEqual(self.m.startup_status()["phase"], "init")


class ReadinessResponseTests(unittest.TestCase):
    def test_a_failed_start_is_reported_as_retryable(self):
        import app
        fake = mock.Mock(is_ready=lambda: False, switch_state=lambda: None,
                         startup_status=lambda: {"phase": "degraded", "last_error": "HTTP Error 500",
                                                 "warming_up": False})
        with app.app.test_request_context(), mock.patch.object(app, "ai_manager", fake):
            resp, status = app._require_ai_ready()
        self.assertEqual(status, 503)
        body = resp.get_json()
        self.assertTrue(body["retryable"])
        self.assertEqual(body["reason"], "startup_failed")


if __name__ == "__main__":
    unittest.main()
