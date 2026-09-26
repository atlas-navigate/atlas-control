#!/usr/bin/env python3
"""
Measure Ray's chat models on this Atlas, directly against Ollama, the way
Ray runs them (embedder resident beside the chat model, thinking off).

    tools/bench/models.py [--models qwen2.5:3b,qwen3.5:4b] [--ctx 2048,4096,8192]
                          [--batch 256,512] [--quality] [--json OUT]

For each model × context × batch it records:
  resident_mb      Ollama's own size for the loaded runner (/api/ps)
  gpu_pct          share of it placed on the GPU
  free_after_mb    MemAvailable with the model and the embedder loaded
  load_s           time to load the runner
  prompt_tps       prompt tokens/s for a Ray-sized prompt (system + passages)
  gen_tps          generated tokens/s
  first_token_s    load excluded: prompt processing + first token
  chars_per_token  of the prompt, for Ray's token budget

--quality adds fixed checks that matter to Ray: the [CALC: …] extraction
format, and whether an answer uses the numbers in the supplied passage.

Afterwards the chat model Atlas is configured for is loaded again. Talks to
Ollama on 127.0.0.1:11434 only; changes no Atlas setting.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EMBED = "qwen3-embedding:0.6b"
EMBED_CTX = 1024

PASSAGE = (
    "WATER PURIFICATION IN THE FIELD\n"
    "Boiling: bring water to a rolling boil for 1 minute; above 2,000 m (6,500 ft) boil for 3 minutes. "
    "Boiling kills bacteria, viruses and protozoa but does not remove chemicals.\n"
    "Chlorine bleach (unscented, 6%): 2 drops per litre of clear water, 4 drops if cloudy or cold; "
    "wait 30 minutes. A faint chlorine smell afterwards means it worked; if none, repeat and wait 15 minutes.\n"
    "Iodine tablets: follow the label; typically 1 tablet per litre, 30 minutes (60 minutes in cold water). "
    "Not for pregnant people or those with thyroid conditions, and not for more than a few weeks.\n"
    "Filters: a 0.1–0.2 micron hollow-fibre filter removes bacteria and protozoa but not viruses; "
    "combine with chemical treatment where viruses are a concern.\n"
    "UV pens: stir 90 seconds per litre of clear water. Useless in cloudy water — pre-filter through cloth.\n"
    "Solar (SODIS): clear PET bottle up to 2 litres, 6 hours of full sun or 2 days if overcast.\n"
    "Pre-treatment: let cloudy water settle, then pour through a cloth or coffee filter before any method.\n"
) * 3

QUESTION = "How do I purify water in the field, and how long do I wait after adding bleach?"

CALC_SYSTEM = (
    "You are a math expression extractor. "
    "When given a question, output ONLY the Python expressions needed to answer it numerically. "
    "Each expression must be on its own line in the format: [CALC: expression]\n"
    "Available functions: sqrt, sin, cos, tan, radians, degrees, log, log10, exp, pi, g=9.80665, "
    "mps_to_fps, fps_to_mps, cm_to_inches, km_to_miles, c_to_f, f_to_c, lbs_to_kg.\n"
    "Output NOTHING else — no prose, no labels, no explanations."
)
CALC_QUESTIONS = [
    ("How far does sound travel in 4.2 seconds at 343 m/s?", r"343\s*\*\s*4\.2|4\.2\s*\*\s*343"),
    ("Convert 72 degrees Fahrenheit to Celsius.", r"f_to_c\(\s*72\s*\)|\(\s*72\s*-\s*32\s*\)"),
    ("What is the square root of 2025?", r"sqrt\(\s*2025\s*\)"),
]


def call(path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OLLAMA + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def mem_available_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return None


def loaded():
    return {m["name"]: m for m in call("/api/ps").get("models", [])}


def unload(model):
    call("/api/generate", {"model": model, "keep_alive": 0})
    for _ in range(60):
        if model not in loaded():
            return
        time.sleep(0.5)


def ensure_embedder():
    if EMBED not in loaded():
        call("/api/embed", {"model": EMBED, "input": "warmup", "options": {"num_ctx": EMBED_CTX}})


def system_prompt():
    sys.path.insert(0, REPO)
    try:
        import database
        return database.AI_DEFAULTS["system_prompt"]
    except Exception:
        return "You are Ray, the assistant built into Atlas. Answer from the knowledge base."


def chat(model, messages, options, keep_alive="5m"):
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": keep_alive,
            "options": options}
    show = call("/api/show", {"model": model})
    if "thinking" in (show.get("capabilities") or []):
        body["think"] = False
    return call("/api/chat", body)


def measure(model, ctx, batch, threads, sysprompt):
    for name in list(loaded()):
        if name != EMBED:
            unload(name)
    ensure_embedder()
    opts = {"num_ctx": ctx, "num_batch": batch, "num_thread": threads, "temperature": 0.7,
            "top_p": 0.8, "top_k": 20, "num_predict": 200}
    prompt_chars = len(sysprompt) + len(PASSAGE) + len(QUESTION)
    messages = [{"role": "system", "content": sysprompt + "\n\nKNOWLEDGE BASE\n" + PASSAGE},
                {"role": "user", "content": QUESTION}]
    t = time.monotonic()
    try:
        r = chat(model, messages, opts)
    except Exception as e:
        return {"model": model, "ctx": ctx, "batch": batch, "error": str(e)[:200]}
    wall = time.monotonic() - t
    ps = loaded().get(model, {})
    ns = 1e9
    out = {
        "model": model, "ctx": ctx, "batch": batch,
        "resident_mb": round(ps.get("size", 0) / 2**20),
        "gpu_pct": round(100 * ps.get("size_vram", 0) / ps["size"]) if ps.get("size") else None,
        "free_after_mb": mem_available_mb(),
        "load_s": round(r.get("load_duration", 0) / ns, 2),
        "prompt_tokens": r.get("prompt_eval_count"),
        "prompt_tps": round(r["prompt_eval_count"] / (r["prompt_eval_duration"] / ns), 1)
        if r.get("prompt_eval_duration") else None,
        "gen_tokens": r.get("eval_count"),
        "gen_tps": round(r["eval_count"] / (r["eval_duration"] / ns), 1) if r.get("eval_duration") else None,
        "first_token_s": round((r.get("prompt_eval_duration", 0)) / ns, 2),
        "wall_s": round(wall, 1),
        "chars_per_token": round(prompt_chars / r["prompt_eval_count"], 2) if r.get("prompt_eval_count") else None,
        "prompt_truncated": bool(r.get("prompt_eval_count") and r["prompt_eval_count"] >= ctx - 8),
    }
    # A second, warm answer: speed without the load.
    try:
        r2 = chat(model, messages, opts)
        out["warm_first_token_s"] = round(r2.get("prompt_eval_duration", 0) / ns, 2)
        out["warm_gen_tps"] = round(r2["eval_count"] / (r2["eval_duration"] / ns), 1) if r2.get("eval_duration") else None
    except Exception as e:
        out["warm_error"] = str(e)[:120]
    out["answer_head"] = (r.get("message", {}).get("content") or "")[:240]
    return out


def quality(model, ctx, batch, threads, sysprompt):
    opts = {"num_ctx": ctx, "num_batch": batch, "num_thread": threads}
    res = {"model": model, "calc_ok": 0, "calc_total": len(CALC_QUESTIONS), "calc_samples": []}
    for q, pattern in CALC_QUESTIONS:
        r = chat(model, [{"role": "system", "content": CALC_SYSTEM},
                         {"role": "user", "content": f"Question: {q}\n\nList every expression that must be "
                                                     "computed. One [CALC: ...] per line only."}],
                 dict(opts, temperature=0.05, num_predict=300))
        text = r.get("message", {}).get("content", "")
        lines = [l for l in text.strip().splitlines() if l.strip()]
        ok = bool(lines) and all(l.strip().startswith("[CALC:") for l in lines) and re.search(pattern, text)
        res["calc_ok"] += bool(ok)
        res["calc_samples"].append(text.strip()[:120])
    r = chat(model, [{"role": "system", "content": sysprompt + "\n\nKNOWLEDGE BASE\n" + PASSAGE},
                     {"role": "user", "content": QUESTION}],
             dict(opts, temperature=0.7, top_p=0.8, top_k=20, num_predict=400))
    answer = r.get("message", {}).get("content", "")
    facts = {"boil 1 minute": r"\b1\s*min|one minute", "2 drops per litre": r"\b2\s*drops|two drops",
             "wait 30 minutes": r"30\s*min|thirty minutes"}
    res["grounded"] = {k: bool(re.search(p, answer, re.I)) for k, p in facts.items()}
    res["answer"] = answer[:600]
    return res


def restore():
    """Load the chat model Atlas is configured for, the way Ray loads it."""
    sys.path.insert(0, REPO)
    model, opts = "qwen2.5:3b", {"num_ctx": 4096, "num_batch": 512, "num_thread": 6}
    try:
        import model_profiles
        import database
        prof = model_profiles.resolve(database.ai_get_settings())
        model, opts = prof["tag"], {k: prof["options"][k] for k in ("num_ctx", "num_batch", "num_thread")}
    except Exception:
        pass
    for name in list(loaded()):
        if name not in (model, EMBED):
            unload(name)
    try:
        chat(model, [{"role": "user", "content": "hi"}], dict(opts, num_predict=1), keep_alive="10h")
    except Exception as e:
        print(f"could not reload {model}: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="qwen2.5:3b,qwen3.5:4b")
    ap.add_argument("--ctx", default="2048,4096,8192")
    ap.add_argument("--batch", default="512")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--quality", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()
    sysprompt = system_prompt()
    results, qual = [], []
    try:
        for model in args.models.split(","):
            for ctx in (int(c) for c in args.ctx.split(",")):
                for batch in (int(b) for b in args.batch.split(",")):
                    r = measure(model, ctx, batch, args.threads, sysprompt)
                    results.append(r)
                    print(json.dumps({k: v for k, v in r.items() if k != "answer_head"}), flush=True)
            if args.quality:
                q = quality(model, 4096, 512, args.threads, sysprompt)
                qual.append(q)
                print(json.dumps(q), flush=True)
    finally:
        restore()
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "runs": results, "quality": qual}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
