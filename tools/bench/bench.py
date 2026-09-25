#!/usr/bin/env python3
"""
Measure an Atlas's hot paths from the outside, for before/after comparisons.

    tools/bench/bench.py URL [--token BEARER] [--insecure] [--runs N] [--json OUT]

  page          bytes and time for the app page and every script it loads
  stats         /api/system/stats latency (p50/p95 over --runs)
  knowledge_map first and repeat /api/ai/knowledge-map
  concurrent    20 parallel GET /api/nodes: wall time and slowest request
  event_loop    /api/health latency while the knowledge map is requested
                (a request that computes on the event loop stalls everyone)
  diagnostics   /api/diagnostics/performance when the server has it

Read-only: only GET requests. Use --token for a paired device's bearer token
on units that require pairing.
"""
import argparse
import concurrent.futures
import json
import re
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request


def make_get(base, token, insecure):
    ctx = ssl._create_unverified_context() if insecure else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def get(path, timeout=120):
        t = time.perf_counter()
        req = urllib.request.Request(base.rstrip("/") + path, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read()
            return r.status, body, time.perf_counter() - t
    return get


def pct(values, p):
    v = sorted(values)
    return v[min(len(v) - 1, int(p * len(v)))]


def ms(x):
    return round(x * 1000, 1)


def bench_page(get):
    status, html, t = get("/")
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html.decode("utf-8", "replace"))
    blocking = [s for s in re.findall(r"<script[^>]*>", html.decode("utf-8", "replace"))
                if "src=" in s and " defer" not in s and " async" not in s]
    total = len(html)
    for src in scripts:
        total += len(get(src)[1])
    return {"html_bytes": len(html), "scripts": len(scripts), "render_blocking_scripts": len(blocking),
            "first_load_bytes": total, "html_ms": ms(t), "babel": any("babel" in s for s in scripts)}


def bench_stats(get, runs):
    times = [get("/api/system/stats")[2] for _ in range(runs)]
    return {"p50_ms": ms(pct(times, 0.5)), "p95_ms": ms(pct(times, 0.95)), "runs": runs}


def bench_knowledge_map(get):
    first = get("/api/ai/knowledge-map")[2]
    again = get("/api/ai/knowledge-map")[2]
    return {"first_ms": ms(first), "repeat_ms": ms(again)}


def bench_concurrent(get, n=20):
    t = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(n) as pool:
        durations = list(pool.map(lambda _: get("/api/nodes")[2], range(n)))
    return {"requests": n, "wall_ms": ms(time.perf_counter() - t), "slowest_ms": ms(max(durations))}


def bench_event_loop(get):
    """Latency of a trivial request while a knowledge map is being built."""
    lat, stop = [], threading.Event()
    try:
        get("/api/health")
        probe = "/api/health"
    except urllib.error.HTTPError:
        probe = "/api/device"                 # releases before /api/health

    def ping():
        while not stop.is_set():
            lat.append(get(probe)[2])
            time.sleep(0.05)
    th = threading.Thread(target=ping, daemon=True)
    th.start()
    time.sleep(0.3)
    km = get("/api/ai/knowledge-map")[2]
    stop.set()
    th.join(5)
    return {"knowledge_map_ms": ms(km), "health_p50_ms": ms(statistics.median(lat)),
            "health_max_ms": ms(max(lat)), "samples": len(lat)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--token")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed unit)")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--skip", default="", help="comma-separated sections to skip")
    ap.add_argument("--json", help="also write the results here")
    args = ap.parse_args()
    get = make_get(args.url, args.token, args.insecure)
    skip = set(filter(None, args.skip.split(",")))
    results = {"url": args.url, "when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    sections = [("page", lambda: bench_page(get)), ("stats", lambda: bench_stats(get, args.runs)),
                ("knowledge_map", lambda: bench_knowledge_map(get)), ("concurrent", lambda: bench_concurrent(get)),
                ("event_loop", lambda: bench_event_loop(get))]
    for name, fn in sections:
        if name in skip:
            continue
        try:
            results[name] = fn()
        except Exception as exc:
            results[name] = {"error": str(exc)[:200]}
        print(f"{name:14} {json.dumps(results[name])}", flush=True)
    try:
        results["diagnostics"] = json.loads(get("/api/diagnostics/performance")[1])
        print(f"{'diagnostics':14} rss {results['diagnostics'].get('rss_mb')} MB")
    except Exception:
        pass
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
