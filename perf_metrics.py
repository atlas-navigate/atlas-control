"""
Local performance counters: how long Ray's stages, stats and other hot paths
take on this device. Kept in memory only, never sent anywhere; shown to
paired devices at /api/diagnostics/performance.
"""
import collections
import contextlib
import os
import threading
import time

_WINDOW = 200
_lock = threading.Lock()
_samples = collections.defaultdict(lambda: collections.deque(maxlen=_WINDOW))
_values = collections.defaultdict(lambda: collections.deque(maxlen=_WINDOW))
_started = time.time()


def record(stage, seconds):
    with _lock:
        _samples[stage].append(float(seconds))


def observe(name, value):
    """A non-timing measurement (e.g. prompt tokens)."""
    if value is None:
        return
    with _lock:
        _values[name].append(float(value))


@contextlib.contextmanager
def timer(stage):
    t = time.perf_counter()
    try:
        yield
    finally:
        record(stage, time.perf_counter() - t)


def _summary(values, scale=1.0, digits=1):
    v = sorted(values)
    if not v:
        return None

    def pct(p):
        return round(v[min(len(v) - 1, int(p * len(v)))] * scale, digits)
    return {"count": len(v), "p50": pct(0.5), "p95": pct(0.95), "max": round(v[-1] * scale, digits)}


def _rss_mb():
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def snapshot():
    with _lock:
        stages = {k: _summary(v, 1000.0) for k, v in _samples.items()}      # milliseconds
        values = {k: _summary(v, 1.0, 0) for k, v in _values.items()}
    return {"uptime_s": int(time.time() - _started), "rss_mb": _rss_mb(),
            "stages_ms": stages, "values": values, "window": _WINDOW}


def reset():
    with _lock:
        _samples.clear()
        _values.clear()
