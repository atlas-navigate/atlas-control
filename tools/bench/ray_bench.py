#!/usr/bin/env python3
"""
Measure Ray end to end and against Ollama directly, reproducibly, for
before/after comparisons. Run with the app's venv python on the Atlas itself.

    ray_bench.py pair                       # one-time: owner approves a bench client
    ray_bench.py e2e   --out FILE [--reps 5] [--scenarios short,rag,conversation,tool,live]
                       [--cold 2] [--background] [--soak-min 0]
    ray_bench.py eval  --out FILE [--runs 1] [--only id,id]
    ray_bench.py direct layout --out FILE [--model M]       # prompt layout A/B on Ollama
    ray_bench.py direct sweep  --out FILE [--batch 512,1024] [--threads 6,4,2]
    ray_bench.py direct decode --out FILE [--runs 5]        # prefill + decode at current settings
    ray_bench.py compare A.json B.json

What is measured, and where it comes from:
  ttft_s / e2e_s        client clock (time.monotonic) around the app's SSE stream:
                        includes everything the app does before the first token
  gen_tps               Ollama eval_count / eval_duration (the done frame)
  prefill_tokens/_s     llama-server's own "prompt eval time" line for the task
  cached_tokens         llama-server's "cached n_tokens" (prompt prefix reused)
  prompt_tokens         llama-server task.n_tokens (whole prompt)
  load_s                Ollama load_duration (direct mode; e2e cold runs report
                        ttft_cold - ttft_warm as well)
  hw.*                  sampler.py: CPU, GPU load/MHz, EMC MHz, MemAvailable,
                        llama-server and app RSS, max temperature, VDD_IN

E2E talks to the app the way a paired phone does — through nginx on
https://127.0.0.1 with a Bearer token — and checks the TLS key against this
unit's pin. It never bypasses pairing. Questions in each scenario rotate
through a pool so no request repeats the previous one verbatim (that would
let llama-server reuse the whole prompt and flatter the numbers).
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import models as obench  # noqa: E402  (Ollama helpers shared with models.py)
from sampler import Sampler, current_temp_c  # noqa: E402

HOST = "127.0.0.1"
TOKEN_FILE = os.path.expanduser("~/.config/atlas-dev/bench-token")
DEVICE_JSON = "/etc/atlas/identity/device.json"
DB_PATH = os.environ.get("ATLAS_DB", "/atlas_data/atlas-control/data/meshtastic.db")
EVAL_FILE = os.path.join(HERE, "ray_eval.json")

POOLS = {
    "short": ["Say hello in five words.", "Give me one quick tip for staying warm.",
              "What's a good name for a hiking dog? One suggestion.", "Tell me a one-line joke about maps.",
              "In one sentence, what are you good at?", "Name one thing to pack for a day hike."],
    "rag": ["How long should I boil water to make it safe?", "Where do I put a tourniquet?",
            "What are the early signs of hypothermia?", "How much drinking water does an adult need per day?",
            "What is the rule of 3s in survival?", "How fast does fallout radiation decay?",
            "Which US rattlesnake has the most toxic venom?", "How long does untreated gasoline last in storage?",
            "What SNR counts as excellent in Atlas?", "How do I signal for rescue with a mirror?"],
    "tool": ["If I drop a rock off a 45 meter cliff, how long until it hits the ground?",
             "How far does sound travel in 4.2 seconds at 343 m/s?",
             "A stone falls for 2.5 seconds. How far did it fall?",
             "How long does it take an object to fall 80 meters?"],
    "live": ["Where am I right now and how is the system doing?", "What's my altitude?",
             "How hot is the CPU right now?"],
}
CONVERSATIONS = [
    ["I'm planning a 3-day hike in the desert in summer.", "How much water should I carry per day?",
     "How would I treat water I find along the way?", "What are the signs of heat stroke?",
     "What should I do if my partner shows them?", "Summarise the three most important points."],
    ["I'm building an emergency kit for my car.", "What should go in it for winter?",
     "How do I avoid carbon monoxide if I'm stuck in snow with the engine running?",
     "How long can I run the engine safely each hour?", "What signals help rescuers find me?",
     "Give me a short checklist of all of that."],
]


# ── transport ────────────────────────────────────────────────────────────────
def _pin():
    try:
        with open(DEVICE_JSON) as f:
            return json.load(f).get("tls_spki_sha256")
    except OSError:
        return None


def _spki_pin(der):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert = x509.load_der_x509_certificate(der)
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.urlsafe_b64encode(hashlib.sha256(spki).digest()).decode().rstrip("=")


class Atlas:
    """HTTPS to this unit's nginx, pinned to its TLS key."""

    def __init__(self, token=None):
        self.token = token
        self.pin = _pin()
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE   # self-signed; the pin below is the check

    def _conn(self, timeout=330):
        c = http.client.HTTPSConnection(HOST, 443, context=self.ctx, timeout=timeout)
        c.connect()
        if self.pin:
            got = _spki_pin(c.sock.getpeercert(binary_form=True))
            if got != self.pin:
                c.close()
                raise SystemExit(f"TLS key mismatch: expected {self.pin}, got {got}")
        return c

    def _headers(self, body):
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def req(self, method, path, body=None, timeout=60):
        c = self._conn(timeout)
        try:
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=data, headers=self._headers(body))
            r = c.getresponse()
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"{}")
            except ValueError:
                return r.status, {"raw": raw[:200].decode(errors="replace")}
        finally:
            c.close()

    def stream(self, path, body):
        """POST and yield (t_monotonic, event_dict) for each SSE frame."""
        c = self._conn()
        try:
            c.request("POST", path, body=json.dumps(body).encode(), headers=self._headers(body))
            r = c.getresponse()
            if r.status != 200:
                raise RuntimeError(f"{path}: HTTP {r.status} {r.read()[:200]!r}")
            buf = b""
            while True:
                chunk = r.read1(65536) if hasattr(r, "read1") else r.read(1)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    for line in frame.split(b"\n"):
                        if line.startswith(b"data: "):
                            try:
                                yield time.monotonic(), json.loads(line[6:])
                            except ValueError:
                                pass
        finally:
            c.close()


def load_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        raise SystemExit(f"no bench token: run `{sys.argv[0]} pair` first")


def cmd_pair(_args):
    a = Atlas()
    nonce = secrets.token_hex(8)
    st, r = a.req("POST", "/api/pair/request", {"name": "Ray benchmark (developer)", "kind": "android",
                                                "client_nonce": nonce})
    if st != 200 or not r.get("ok"):
        raise SystemExit(f"pair request failed: {st} {r}")
    print(f"Approve 'Ray benchmark (developer)' on a paired device. Match code: {r['match_code']}", flush=True)
    deadline = time.monotonic() + r.get("expires_in", 180)
    while time.monotonic() < deadline:
        time.sleep(2)
        st, p = a.req("GET", f"/api/pair/request/{r['request_id']}?secret={r['secret']}&kind=android")
        if p.get("status") == "approved" and p.get("token"):
            os.makedirs(os.path.dirname(TOKEN_FILE), mode=0o700, exist_ok=True)
            fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(p["token"])
            print(f"paired; token saved to {TOKEN_FILE}")
            return 0
        if p.get("status") in ("denied", "expired"):
            raise SystemExit(f"pairing {p['status']}")
    raise SystemExit("pairing timed out")


# ── llama-server task metrics from the journal ───────────────────────────────
_RE_NEW = re.compile(r"task (\d+) \| new prompt, n_ctx_slot = (\d+), n_keep = \d+, task\.n_tokens = (\d+)")
_RE_CACHED = re.compile(r"task (\d+) \| cached n_tokens = (\d+)")
_RE_PEVAL = re.compile(r"task (\d+) \|\s+prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
_RE_EVAL = re.compile(r"task (\d+) \|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens")


def embedder_ctx():
    try:
        with open(os.path.join(REPO, "ai_models.json")) as f:
            return int(json.load(f)["embedder"]["num_ctx"])
    except (OSError, KeyError, ValueError):
        return 1024


def journal_tasks(since_wall, embed_ctx=None):
    """Chat tasks llama-server logged since `since_wall` (epoch seconds), in order."""
    embed_ctx = embed_ctx or embedder_ctx()
    out = subprocess.run(["journalctl", "-u", "ollama", "-o", "cat", "--no-pager",
                          "--since", "@%.3f" % since_wall], capture_output=True, text=True).stdout
    tasks, cur = [], {}
    for line in out.splitlines():
        m = _RE_NEW.search(line)
        if m:
            t = {"task": int(m.group(1)), "ctx": int(m.group(2)), "prompt_tokens": int(m.group(3)),
                 "cached_tokens": None, "prefill_tokens": None, "prefill_ms": None, "gen_tokens": None,
                 "gen_ms": None}
            cur[t["task"]] = t
            if t["ctx"] != embed_ctx:
                tasks.append(t)
            continue
        m = _RE_CACHED.search(line)
        if m and int(m.group(1)) in cur and cur[int(m.group(1))]["cached_tokens"] is None:
            cur[int(m.group(1))]["cached_tokens"] = int(m.group(2))
            continue
        m = _RE_PEVAL.search(line)
        if m and int(m.group(1)) in cur:
            cur[int(m.group(1))].update(prefill_ms=float(m.group(2)), prefill_tokens=int(m.group(3)))
            continue
        m = _RE_EVAL.search(line)
        if m and int(m.group(1)) in cur:
            cur[int(m.group(1))].update(gen_ms=float(m.group(2)), gen_tokens=int(m.group(3)))
    for t in tasks:
        if t["prefill_ms"] and t["prefill_tokens"]:
            t["prefill_tps"] = round(t["prefill_tokens"] / (t["prefill_ms"] / 1000), 1)
    return tasks


# ── app (end-to-end) ─────────────────────────────────────────────────────────
def ai_status(a):
    return a.req("GET", "/api/ai/status")[1]


def new_chat(a):
    st, r = a.req("POST", "/api/ai/chats", {})
    if st != 200:
        raise SystemExit(f"cannot create chat: {st} {r}")
    return r["id"]


def run_turn(a, sampler, chat_id, text):
    wall0 = time.time()
    t0 = time.monotonic()
    first = done = None
    content, frames, err = "", 0, None
    for t, ev in a.stream(f"/api/ai/chats/{chat_id}/stream", {"text": text}):
        frames += 1
        if "token" in ev:
            if first is None:
                first = t
            content += ev["token"]
        elif "replace" in ev:
            content = ev["replace"]
        elif "error" in ev:
            err = ev["error"]
        elif ev.get("done"):
            done = (t, ev)
    t1 = time.monotonic()
    time.sleep(0.6)  # let journald catch up with llama-server's timing lines
    tasks = journal_tasks(wall0 - 0.5)
    answer = tasks[-1] if tasks else {}
    meta = done[1] if done else {}
    row = {
        "text": text, "chat_id": chat_id, "error": err,
        "ttft_s": round(first - t0, 3) if first else None,
        "e2e_s": round(t1 - t0, 3),
        "gen_tokens": meta.get("eval_count"),
        "gen_tps": meta.get("tok_per_sec"),
        "prompt_tokens": answer.get("prompt_tokens"),
        "cached_tokens": answer.get("cached_tokens"),
        "prefill_tokens": answer.get("prefill_tokens"),
        "prefill_s": round(answer["prefill_ms"] / 1000, 3) if answer.get("prefill_ms") else None,
        "prefill_tps": answer.get("prefill_tps"),
        "llm_calls": len(tasks),
        "pre_calls": tasks[:-1],
        "timings": meta.get("timings"),
        "hw": sampler.window(t0, t1),
        "answer": content,
    }
    return row


def explain_sources(chat_id):
    """Titles of the passages Ray used for the newest answer in a chat (read-only DB)."""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT explain FROM ai_messages WHERE chat_id=? AND role='assistant' "
                          "ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
        con.close()
    except Exception:
        return None
    if not row or not row[0]:
        return []
    try:
        docs = (json.loads(row[0]).get("rag") or {}).get("docs") or []
    except ValueError:
        return []
    return [d.get("title") if isinstance(d, dict) else str(d) for d in docs]


def unload_all_models():
    for name in list(obench.loaded()):
        obench.unload(name)


def wait_cool(limit_c=55.0, max_wait=600):
    t = time.monotonic()
    while time.monotonic() - t < max_wait:
        c = current_temp_c()
        if c is None or c <= limit_c:
            return c
        time.sleep(10)
    return current_temp_c()


class Background:
    """Typical Atlas activity while Ray answers: stats polling, map tile reads,
    node list, and a road route every few seconds."""

    def __init__(self, a):
        self.a = a
        self.stop = threading.Event()
        self.counts = {"stats": 0, "tiles": 0, "nodes": 0, "route": 0, "errors": 0}

    def _loop(self, fn, period):
        while not self.stop.is_set():
            try:
                fn()
            except Exception:
                self.counts["errors"] += 1
            self.stop.wait(period)

    def _stats(self):
        self.a.req("GET", "/api/system/stats")
        self.counts["stats"] += 1

    def _nodes(self):
        self.a.req("GET", "/api/nodes")
        self.counts["nodes"] += 1

    def _route(self):
        self.a.req("GET", "/api/nav/route?from_lat=39.2904&from_lon=-76.6122&to_lat=38.9784&to_lon=-76.4922",
                   timeout=120)
        self.counts["route"] += 1

    def _tiles(self):
        c = self.a._conn(30)
        try:
            off = secrets.randbelow(200_000_000)
            h = self.a._headers(None)
            h["Range"] = f"bytes={off}-{off + 65535}"
            c.request("GET", "/tiles/map.pmtiles", headers=h)
            c.getresponse().read()
            self.counts["tiles"] += 1
        finally:
            c.close()

    def start(self):
        for fn, period in ((self._stats, 1.0), (self._tiles, 0.25), (self._nodes, 2.0), (self._route, 8.0)):
            threading.Thread(target=self._loop, args=(fn, period), daemon=True).start()
        return self

    def end(self):
        self.stop.set()
        return dict(self.counts)


def cmd_e2e(args):
    a = Atlas(load_token())
    status = ai_status(a)
    meta = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "e2e", "model": status.get("model"),
            "version": a.req("GET", "/api/health")[1].get("version"), "status": status,
            "start_temp_c": wait_cool()}
    sampler = Sampler().start()
    rows = []
    scenarios = [s for s in args.scenarios.split(",") if s]
    try:
        # Cold: both models unloaded, then the first question of the "rag" pool.
        for i in range(args.cold):
            unload_all_models()
            time.sleep(2)
            r = run_turn(a, sampler, new_chat(a), POOLS["rag"][i % len(POOLS["rag"])])
            r.update(scenario="cold", rep=i)
            rows.append(r)
            print(_brief(r), flush=True)
        bg = Background(a).start() if args.background else None
        for rep in range(args.reps):
            for sc in scenarios:
                if sc == "conversation":
                    conv = CONVERSATIONS[rep % len(CONVERSATIONS)]
                    cid = new_chat(a)
                    for turn, text in enumerate(conv):
                        r = run_turn(a, sampler, cid, text)
                        r.update(scenario="conversation", rep=rep, turn=turn)
                        rows.append(r)
                        print(_brief(r), flush=True)
                    continue
                pool = POOLS[sc]
                r = run_turn(a, sampler, new_chat(a), pool[rep % len(pool)])
                r.update(scenario=sc, rep=rep)
                rows.append(r)
                print(_brief(r), flush=True)
        if bg:
            meta["background"] = bg.end()
        if args.soak_min:
            end = time.monotonic() + args.soak_min * 60
            i = 0
            cid = new_chat(a)
            while time.monotonic() < end:
                if i % 6 == 0:
                    cid = new_chat(a)
                text = (POOLS["rag"] + POOLS["tool"])[i % (len(POOLS["rag"]) + len(POOLS["tool"]))]
                r = run_turn(a, sampler, cid, text)
                r.update(scenario="soak", rep=i)
                rows.append(r)
                if i % 10 == 0:
                    print(_brief(r), flush=True)
                i += 1
    finally:
        sampler.stop()
    meta["end_temp_c"] = current_temp_c()
    _write(args.out, {"meta": meta, "rows": rows, "summary": summarize(rows)})


def _brief(r):
    return (f"[{r.get('scenario')}#{r.get('rep')}{'/' + str(r['turn']) if 'turn' in r else ''}] "
            f"ttft={r['ttft_s']}s e2e={r['e2e_s']}s gen={r['gen_tps']}tok/s "
            f"prompt={r['prompt_tokens']} cached={r['cached_tokens']} prefill={r['prefill_tps']}tok/s "
            f"calls={r['llm_calls']} emc={r['hw'].get('emc_mhz_avg')} gpu={r['hw'].get('gpu_mhz_avg')}"
            + (f" ERROR {r['error']}" if r.get("error") else ""))


def _stats(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return {"n": len(v), "median": round(statistics.median(v), 3),
            "p90": round(v[min(len(v) - 1, int(0.9 * len(v)))], 3),
            "min": round(v[0], 3), "max": round(v[-1], 3)}


def summarize(rows):
    out = {}
    keys = ("ttft_s", "e2e_s", "gen_tps", "prefill_tps", "prefill_s", "prompt_tokens", "cached_tokens",
            "prefill_tokens")
    hwkeys = ("cpu_pct_avg", "gpu_load_pct_avg", "gpu_mhz_avg", "emc_mhz_avg", "mem_avail_mb_min",
              "llama_rss_mb_max", "app_rss_mb_max", "tj_c_max", "vdd_in_mw_avg")
    groups = {}
    for r in rows:
        groups.setdefault(r.get("scenario", "all"), []).append(r)
        if r.get("scenario") == "conversation" and r.get("turn", 0) >= 2:
            groups.setdefault("conversation_late", []).append(r)
    for name, rs in groups.items():
        s = {k: _stats([r.get(k) for r in rs]) for k in keys}
        s.update({"hw." + k: _stats([r["hw"].get(k) for r in rs if r.get("hw")]) for k in hwkeys})
        s["errors"] = sum(1 for r in rs if r.get("error"))
        out[name] = s
    return out


# ── quality eval ─────────────────────────────────────────────────────────────
def _check(answer, spec):
    fails = []
    for p in spec.get("facts", []):
        if not re.search(p, answer, re.I | re.M):
            fails.append("missing: " + p)
    if spec.get("any") and not any(re.search(p, answer, re.I | re.M) for p in spec["any"]):
        fails.append("none of: " + " | ".join(spec["any"]))
    for p in spec.get("banned", []):
        if re.search(p, answer, re.I | re.M):
            fails.append("banned: " + p)
    return fails


def cmd_eval(args):
    a = Atlas(load_token())
    with open(EVAL_FILE) as f:
        items = json.load(f)["items"]
    if args.only:
        keep = set(args.only.split(","))
        items = [i for i in items if i["id"] in keep]
    sampler = Sampler().start()
    results = []
    try:
        for run in range(args.runs):
            for it in items:
                cid = new_chat(a)
                turns = []
                for text in it["turns"]:
                    turns.append(run_turn(a, sampler, cid, text))
                checks = it.get("checks") or [{"turn": len(turns) - 1, **{k: it[k] for k in
                                                                          ("facts", "any", "banned") if k in it}}]
                fails = []
                for c in checks:
                    ans = turns[c["turn"]]["answer"] if c["turn"] < len(turns) else ""
                    fails += [f"t{c['turn']} {x}" for x in _check(ans, c)]
                sources = explain_sources(cid)
                src_ok = None
                if it.get("source") and sources is not None:
                    src_ok = any(it["source"].lower() in (s or "").lower() for s in sources)
                res = {"id": it["id"], "kind": it["kind"], "run": run, "pass": not fails, "fails": fails,
                       "source_expected": it.get("source"), "source_hit": src_ok, "sources": sources,
                       "answers": [t["answer"] for t in turns],
                       "ttft_s": [t["ttft_s"] for t in turns], "gen_tps": [t["gen_tps"] for t in turns]}
                results.append(res)
                print(f"{'PASS' if res['pass'] else 'FAIL'} {it['id']} run{run}"
                      f"{' src=' + str(src_ok) if src_ok is not None else ''} {'; '.join(fails)}", flush=True)
    finally:
        sampler.stop()
    by_kind = {}
    for r in results:
        k = by_kind.setdefault(r["kind"], {"pass": 0, "total": 0, "source_hit": 0, "source_total": 0})
        k["total"] += 1
        k["pass"] += r["pass"]
        if r["source_hit"] is not None:
            k["source_total"] += 1
            k["source_hit"] += r["source_hit"]
    summary = {"pass": sum(r["pass"] for r in results), "total": len(results), "by_kind": by_kind}
    print(json.dumps(summary))
    _write(args.out, {"meta": {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "eval",
                               "model": ai_status(a).get("model"),
                               "version": a.req("GET", "/api/health")[1].get("version")},
                      "results": results, "summary": summary})


# ── Ollama direct ────────────────────────────────────────────────────────────
def _ray_settings():
    """Ray's saved settings, read-only from the live database (no app import)."""
    import sqlite3
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    rows = con.execute("SELECT key, value FROM ai_settings").fetchall()
    con.close()
    return dict(rows)


def _ray_profile():
    sys.path.insert(0, REPO)
    import model_profiles
    prof = model_profiles.resolve(_ray_settings())
    return prof["tag"], dict(prof["options"])


def _restore_ray():
    """Hand the runners back to the app: unload ours, let Ray warm up its own pair."""
    unload_all_models()
    try:
        Atlas(load_token()).req("POST", "/api/ai/warmup", {}, timeout=300)
    except SystemExit:
        print("no bench token: Ray reloads on its next question", file=sys.stderr)


def _chat_direct(model, messages, options, keep_alive="30m"):
    wall0 = time.time()
    t0 = time.monotonic()
    r = obench.chat(model, messages, options, keep_alive=keep_alive)
    dt = time.monotonic() - t0
    time.sleep(0.6)
    tasks = journal_tasks(wall0 - 0.3)
    task = tasks[-1] if tasks else {}
    ns = 1e9
    return {
        "wall_s": round(dt, 3),
        "load_s": round(r.get("load_duration", 0) / ns, 3),
        "prompt_tokens": task.get("prompt_tokens"),
        "cached_tokens": task.get("cached_tokens"),
        "prefill_tokens": r.get("prompt_eval_count"),
        "prefill_s": round(r.get("prompt_eval_duration", 0) / ns, 3),
        "prefill_tps": round(r["prompt_eval_count"] / (r["prompt_eval_duration"] / ns), 1)
        if r.get("prompt_eval_duration") else None,
        "gen_tokens": r.get("eval_count"),
        "gen_tps": round(r["eval_count"] / (r["eval_duration"] / ns), 1) if r.get("eval_duration") else None,
        "answer": (r.get("message") or {}).get("content", ""),
    }


def _seed_passages():
    import sqlite3
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    rows = con.execute("SELECT title, content FROM ai_documents WHERE is_seed=1 ORDER BY id").fetchall()
    con.close()
    return rows


def _context_block(i, passages):
    """A Ray-shaped per-turn context: live GPS line + fenced passages (both change per turn)."""
    fence = secrets.token_hex(4)
    gps = (f"=== CURRENT POSITION ===\nLat 39.{29040 + i * 7:05d}, Lon -76.{61220 + i * 3:05d}, "
           f"alt {120 + i * 1.3:.1f} m, {7 + i % 3} satellites, speed {i % 4 * 0.4:.1f} m/s, 3D fix")
    docs = "\n\n".join(f"<<<{fence} SOURCE: {t}>>>\n{c[:1400]}\n<<<END {fence}>>>" for t, c in passages)
    return gps + "\n\n=== KNOWLEDGE BASE ===\n" + docs


def cmd_direct_layout(args):
    """Same 6-turn conversation in Ray's current layout (context in the system
    message) and in the per-turn layout (context in the final user message)."""
    model, opts = _ray_profile()
    if args.model:
        model = args.model
    opts = dict(opts, num_predict=160, seed=7)
    sysprompt = obench.system_prompt()
    seeds = _seed_passages()
    conv = CONVERSATIONS[0]
    out = {"meta": {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "direct-layout", "model": model,
                    "options": opts}, "rows": []}
    sampler = Sampler().start()
    try:
        for rep in range(args.reps):
            for layout in ("legacy", "turn"):
                history = []
                for turn, q in enumerate(conv):
                    passages = seeds[(rep * 13 + turn * 5) % len(seeds):][:3]
                    ctx = _context_block(turn, passages)
                    if layout == "legacy":
                        msgs = [{"role": "system", "content": sysprompt + "\n\n" + ctx}] + history + \
                               [{"role": "user", "content": q}]
                    else:
                        msgs = [{"role": "system", "content": sysprompt}] + history + \
                               [{"role": "user", "content": ctx + "\n\n=== QUESTION ===\n" + q}]
                    t0 = time.monotonic()
                    r = _chat_direct(model, msgs, opts)
                    r.update(layout=layout, rep=rep, turn=turn, hw=sampler.window(t0, time.monotonic()))
                    out["rows"].append(r)
                    print(f"{layout} rep{rep} turn{turn}: prompt={r['prompt_tokens']} cached={r['cached_tokens']} "
                          f"prefill={r['prefill_tokens']}@{r['prefill_tps']} t/s ({r['prefill_s']}s) "
                          f"gen={r['gen_tps']}", flush=True)
                    history += [{"role": "user", "content": q}, {"role": "assistant", "content": r.pop("answer")}]
    finally:
        sampler.stop()
    out["summary"] = {lay: {k: _stats([r[k] for r in out["rows"] if r["layout"] == lay and r["turn"] >= 1])
                            for k in ("prefill_s", "prefill_tokens", "cached_tokens", "prompt_tokens", "gen_tps")}
                      for lay in ("legacy", "turn")}
    _write(args.out, out)


def _fresh_prompt(sysprompt, seeds, n):
    nonce = secrets.token_hex(6)  # at the very start: nothing can be reused
    ctx = _context_block(n, seeds[(n * 7) % len(seeds):][:4])
    return [{"role": "system", "content": f"[{nonce}] " + sysprompt + "\n\n" + ctx},
            {"role": "user", "content": "Write a detailed, step-by-step field guide (at least 300 words) "
                                        "that uses the knowledge base above."}]


def cmd_direct_decode(args):
    model, opts = _ray_profile()
    if args.model:
        model = args.model
    opts = dict(opts, num_predict=256, seed=11)
    sysprompt, seeds = obench.system_prompt(), _seed_passages()
    out = {"meta": {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "direct-decode", "model": model,
                    "options": opts, "label": args.label, "start_temp_c": wait_cool()}, "rows": []}
    sampler = Sampler().start()
    try:
        for i in range(args.runs):
            t0 = time.monotonic()
            r = _chat_direct(model, _fresh_prompt(sysprompt, seeds, i), opts)
            r.pop("answer")
            r.update(run=i, hw=sampler.window(t0, time.monotonic()))
            out["rows"].append(r)
            print(f"run{i}: prefill {r['prefill_tokens']}@{r['prefill_tps']} t/s gen {r['gen_tokens']}@{r['gen_tps']} t/s "
                  f"emc={r['hw'].get('emc_mhz_avg')} gpu={r['hw'].get('gpu_mhz_avg')} tj={r['hw'].get('tj_c_max')}",
                  flush=True)
    finally:
        sampler.stop()
    out["summary"] = {k: _stats([r[k] for r in out["rows"]]) for k in ("prefill_tps", "gen_tps", "prefill_s")}
    _write(args.out, out)


def cmd_direct_sweep(args):
    model, base = _ray_profile()
    if args.model:
        model = args.model
    sysprompt, seeds = obench.system_prompt(), _seed_passages()
    out = {"meta": {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "direct-sweep", "model": model,
                    "base_options": base}, "rows": []}
    sampler = Sampler().start()
    try:
        for batch in (int(b) for b in args.batch.split(",")):
            for threads in (int(t) for t in args.threads.split(",")):
                opts = dict(base, num_batch=batch, num_thread=threads, num_predict=256, seed=11)
                obench.unload(model)
                runs = []
                for i in range(args.runs):
                    t0 = time.monotonic()
                    r = _chat_direct(model, _fresh_prompt(sysprompt, seeds, i), opts)
                    r.pop("answer")
                    r["hw"] = sampler.window(t0, time.monotonic())
                    runs.append(r)
                ps = obench.loaded().get(model, {})
                row = {"num_batch": batch, "num_thread": threads,
                       "load_s": runs[0]["load_s"],
                       "resident_mb": round(ps.get("size", 0) / 2**20),
                       "gpu_pct": round(100 * ps.get("size_vram", 0) / ps["size"]) if ps.get("size") else None,
                       "mem_avail_mb": obench.mem_available_mb(),
                       "prefill_tps": _stats([r["prefill_tps"] for r in runs[1:]]),
                       "gen_tps": _stats([r["gen_tps"] for r in runs[1:]]),
                       "cpu_pct": _stats([r["hw"].get("cpu_pct_avg") for r in runs[1:]])}
                out["rows"].append(row)
                print(json.dumps(row), flush=True)
    finally:
        sampler.stop()
        _restore_ray()
    _write(args.out, out)


# ── compare ──────────────────────────────────────────────────────────────────
def cmd_compare(args):
    a, b = (json.load(open(p)) for p in (args.a, args.b))
    sa, sb = a.get("summary", {}), b.get("summary", {})
    print(f"A: {args.a} ({a['meta'].get('version')}, {a['meta'].get('model')})")
    print(f"B: {args.b} ({b['meta'].get('version')}, {b['meta'].get('model')})")
    if "pass" in sa:
        print(f"eval: A {sa['pass']}/{sa['total']}  B {sb['pass']}/{sb['total']}")
        for k in sorted(set(sa["by_kind"]) | set(sb["by_kind"])):
            print(f"  {k}: A {sa['by_kind'].get(k)}  B {sb['by_kind'].get(k)}")
        return 0
    for group in sorted(set(sa) & set(sb)):
        print(f"\n== {group}")
        ga, gb = sa[group], sb[group]
        if not isinstance(ga, dict):
            continue
        for k in sorted(set(ga) & set(gb)):
            va, vb = ga[k], gb[k]
            if not (isinstance(va, dict) and isinstance(vb, dict) and "median" in va and "median" in vb):
                continue
            delta = (vb["median"] - va["median"]) / va["median"] * 100 if va["median"] else 0.0
            # Noise flag: the medians differ by less than either side's min-max spread.
            spread = max(va["max"] - va["min"], vb["max"] - vb["min"])
            noisy = abs(vb["median"] - va["median"]) < spread / 2
            print(f"  {k:24s} {va['median']:>10} -> {vb['median']:<10} {delta:+7.1f}%"
                  f"{'  (within noise)' if noisy else ''}")
    return 0


def _write(path, obj):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pair")
    e = sub.add_parser("e2e")
    e.add_argument("--out")
    e.add_argument("--reps", type=int, default=5)
    e.add_argument("--scenarios", default="short,rag,conversation,tool,live")
    e.add_argument("--cold", type=int, default=2)
    e.add_argument("--background", action="store_true")
    e.add_argument("--soak-min", type=float, default=0)
    v = sub.add_parser("eval")
    v.add_argument("--out")
    v.add_argument("--runs", type=int, default=1)
    v.add_argument("--only")
    d = sub.add_parser("direct")
    d.add_argument("what", choices=("layout", "sweep", "decode"))
    d.add_argument("--out")
    d.add_argument("--model")
    d.add_argument("--reps", type=int, default=3)
    d.add_argument("--runs", type=int, default=5)
    d.add_argument("--batch", default="512,1024")
    d.add_argument("--threads", default="6,4,2")
    d.add_argument("--label", default="")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "pair":
        return cmd_pair(args)
    if args.cmd == "e2e":
        return cmd_e2e(args)
    if args.cmd == "eval":
        return cmd_eval(args)
    if args.cmd == "compare":
        return cmd_compare(args)
    return {"layout": cmd_direct_layout, "sweep": cmd_direct_sweep, "decode": cmd_direct_decode}[args.what](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
