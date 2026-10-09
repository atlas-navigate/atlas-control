"""
Raise the CPU, GPU and memory-controller clocks while Ray is answering.

All idle low and ramp with load. Ollama's own work on each request (~0.4-0.5
s per call, two calls per answer) and Ray's retrieval are CPU-bound and ran
at idle clocks; the prompt's processing ran while the GPU ramped. While at
least one answer is in progress — from the question's arrival, through
retrieval and generation — and only on mains power, the root helper raises
their floors (perf.boost, a 90 s lease enforced by the system as well); they
drop back 30 s after the last answer. Owners can turn it off (Ray setting
ray_clock_boost = off).

Helper calls run on a worker thread: boosting never delays an answer.
"""
import contextlib
import logging
import threading
import time

import atlas_helper

logger = logging.getLogger("atlas.perf_boost")

IDLE_RELEASE_S = 30.0
RENEW_S = 20.0


class PerfBoost:
    def __init__(self, enabled=lambda: True, on_mains=lambda: True, helper=None, mono=time.monotonic,
                 spawn=None, timer=threading.Timer):
        self._enabled = enabled
        self._on_mains = on_mains
        self._helper = helper or atlas_helper.call
        self._mono = mono
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="perf-boost").start())
        self._timer = timer
        self._lock = threading.Lock()
        self._active = 0
        self._boosted = False
        self._last_request = None
        self._release_timer = None

    @contextlib.contextmanager
    def generating(self):
        """Wrap one answer's generation."""
        self._begin()
        try:
            yield self
        finally:
            self._end()

    def renew(self):
        """Call while streaming: keeps the lease alive (cheap when not due)."""
        if self._boosted and self._last_request is not None and \
                self._mono() - self._last_request >= RENEW_S:
            self._request("perf.boost")

    # internals -------------------------------------------------------------
    def _begin(self):
        with self._lock:
            self._active += 1
            if self._release_timer is not None:
                self._release_timer.cancel()
                self._release_timer = None
        try:
            wanted = self._enabled() and self._on_mains()
        except Exception:
            wanted = False
        if wanted:
            self._request("perf.boost")
        elif self._boosted:
            self._request("perf.release")      # e.g. the UPS switched to battery

    def _end(self):
        with self._lock:
            self._active = max(0, self._active - 1)
            if self._active or not self._boosted:
                return
            t = self._timer(IDLE_RELEASE_S, self._idle_release)
            t.daemon = True
            self._release_timer = t
        t.start()

    def _idle_release(self):
        with self._lock:
            if self._active:
                return
            self._release_timer = None
        self._request("perf.release")

    def _request(self, verb):
        self._last_request = self._mono()
        boosting = verb == "perf.boost"
        self._boosted = boosting

        def call():
            try:
                r = self._helper(verb, timeout=10) or {}
                if boosting and not r.get("boosted"):
                    self._boosted = False
                    if r.get("reason"):
                        logger.info("Clock boost skipped: %s", r["reason"])
            except Exception as exc:
                self._boosted = False if boosting else self._boosted
                logger.debug("%s failed: %s", verb, exc)
        self._spawn(call)
