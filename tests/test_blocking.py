"""atlas_blocking must keep the gevent hub responsive during blocking work."""
import threading
import time
import unittest

import gevent

import atlas_blocking


class HubStaysResponsiveTests(unittest.TestCase):
    def _ticks_during(self, blocking_fn):
        """Count how often a sibling greenlet runs while blocking_fn executes."""
        ticks = []
        stop = []

        def ticker():
            while not stop:
                ticks.append(1)
                gevent.sleep(0.01)

        g = gevent.spawn(ticker)
        gevent.sleep(0)
        blocking_fn()
        stop.append(1)
        g.join()
        return len(ticks)

    def test_subprocess_on_hub_does_not_block_other_greenlets(self):
        n = self._ticks_during(lambda: atlas_blocking.run(["sleep", "0.3"], check=True))
        self.assertGreater(n, 10)

    def test_sleep_on_hub_yields(self):
        n = self._ticks_during(lambda: atlas_blocking.sleep(0.3))
        self.assertGreater(n, 10)

    def test_wait_event_on_hub_yields_and_times_out(self):
        ev = threading.Event()
        t0 = time.monotonic()
        n = self._ticks_during(lambda: self.assertFalse(atlas_blocking.wait_event(ev, 0.3)))
        self.assertGreater(n, 10)
        self.assertGreaterEqual(time.monotonic() - t0, 0.29)

    def test_wait_event_returns_true_when_set(self):
        ev = threading.Event()
        gevent.spawn_later(0.05, ev.set)
        self.assertTrue(atlas_blocking.wait_event(ev, 2))

    def test_native_thread_runs_inline(self):
        result = {}

        def worker():
            result["on_hub"] = atlas_blocking.on_hub_thread()
            result["out"] = atlas_blocking.check_output(["echo", "hi"], text=True).strip()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertFalse(result["on_hub"])
        self.assertEqual(result["out"], "hi")

    def test_call_returns_value_and_propagates_errors(self):
        self.assertEqual(atlas_blocking.call(lambda a, b=0: a + b, 2, b=3), 5)
        with self.assertRaises(ZeroDivisionError):
            atlas_blocking.call(lambda: 1 / 0)
