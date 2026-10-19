"""
Ray's chat models and how each one runs best on this Atlas.

ai_models.json is the catalog: the models an owner can choose, and for each
one its quantizations, context sizes, official sampling settings and the
memory it was measured to need on Atlas hardware. `resolve()` turns "the
owner chose Qwen3.5 4B" into everything Ollama needs — which build to load,
context window, batch, threads, sampling, thinking off — for the hardware it
finds, so nobody ever picks a quantization or a context size by hand.

Owners who want to can still override a model's settings (Settings → Ray →
Expert overrides). Overrides are kept per model and clamped to what this
hardware can hold, so one model's tuning never leaks into another and a typo
can't make Ray unusable.

Standard library only: install.sh runs it with the system python3
(`python3 model_profiles.py --preinstall`).
"""
import json
import os
import sys
import threading

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_models.json")

# What an expert may override, with the type and the bounds a value is kept in.
# None as an upper bound: the model's hardware limit (num_ctx) or the CPU count.
OVERRIDE_RANGES = {
    "num_ctx": (int, 512, None),
    "num_batch": (int, 32, 2048),
    "num_thread": (int, 1, None),
    "num_gpu": (int, -1, 999),
    "temperature": (float, 0.0, 2.0),
    "top_p": (float, 0.0, 1.0),
    "top_k": (int, 1, 200),
    "repeat_penalty": (float, 0.5, 2.0),
    "presence_penalty": (float, -2.0, 2.0),
    "num_predict": (int, 64, 4096),
}
OVERRIDE_PREFIX = "overrides:"

_lock = threading.Lock()
_catalog = {"mtime": None, "data": None}


def catalog():
    """The parsed ai_models.json (re-read when the file changes)."""
    mtime = os.stat(CATALOG_PATH).st_mtime
    with _lock:
        if _catalog["mtime"] != mtime:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                _catalog.update(mtime=mtime, data=json.load(f))
        return _catalog["data"]


def models():
    return catalog()["models"]


def default_id():
    return catalog()["default"]


def find(model_id):
    """The catalog entry for an id or any of its builds' tags, or None."""
    for m in models():
        if model_id == m["id"] or model_id in (v["tag"] for v in m["variants"]) or model_id in m.get("aliases", []):
            return m
    return None


# ── hardware ─────────────────────────────────────────────────────────────
def _meminfo(key):
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def hardware():
    """The facts profiles depend on. RAM is the unified CPU/GPU memory on Jetson."""
    board = ""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            board = f.read().replace(b"\x00", b"").decode(errors="replace").strip()
    except OSError:
        pass
    return {"ram_mb": _meminfo("MemTotal") or 0, "cpus": os.cpu_count() or 4, "board": board}


def available_mb():
    return _meminfo("MemAvailable")


def version_tuple(v):
    parts = []
    for p in str(v or "0").lstrip("v").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0])[:3])


# ── profiles ─────────────────────────────────────────────────────────────
def variant_for(model, hw):
    """The best build this hardware can hold (the catalog lists best first)."""
    for v in model["variants"]:
        if hw["ram_mb"] >= v.get("min_ram_mb", 0):
            return v
    return model["variants"][-1]


def context_for(model, hw):
    """The context window for this RAM: the catalog's measured step for the
    largest RAM class this unit reaches."""
    ctx = model["context"]["default"]
    for min_ram, steps_ctx in sorted(model["context"].get("by_ram", []), key=lambda x: x[0]):
        if hw["ram_mb"] >= min_ram:
            ctx = steps_ctx
    return ctx


def max_context(model, hw):
    """The largest context this hardware may run (an override's ceiling)."""
    return max([context_for(model, hw)] + [c for r, c in model["context"].get("limit_by_ram", [])
                                            if hw["ram_mb"] >= r])


def optimized(model, hw):
    """The tuned options for this model on this hardware (no overrides)."""
    v = variant_for(model, hw)
    opts = {
        "num_ctx": context_for(model, hw),
        "num_batch": model.get("num_batch", 512),
        # Jetson cores are all the same kind; one inference thread per core.
        "num_thread": max(1, min(hw["cpus"], model.get("max_threads", 8))),
        "num_gpu": -1,                        # Ollama places the layers (forcing a count can fail to load)
        "num_predict": model.get("num_predict", 1024),
    }
    opts.update(model["sampling"])
    return v, opts


def embedder_plan(model, hw):
    """Where the embedding model runs beside this chat model, and which loads
    first. On an 8 GB Jetson, Qwen3.5 4B only fits entirely on the GPU if it
    loads before the embedder, which then runs on the CPU (0.5 s a query);
    Qwen2.5 3B and the embedder both fit on the GPU, embedder first."""
    plan = model.get("embedder") or {}
    on_gpu = bool(plan.get("on_gpu", True)) or hw["ram_mb"] >= plan.get("gpu_from_ram_mb", 1 << 30)
    options = {"num_ctx": embedder().get("num_ctx", 1024)}
    if not on_gpu:
        options["num_gpu"] = 0
    return {"options": options, "on_gpu": on_gpu, "load_first": bool(plan.get("load_first", True))}


def _coerce(key, value, hw, model):
    kind, lo, hi = OVERRIDE_RANGES[key]
    try:
        value = kind(float(value)) if kind is int else kind(value)
    except (TypeError, ValueError):
        return None
    if hi is None:
        hi = max_context(model, hw) if key == "num_ctx" else hw["cpus"]
    return min(max(value, lo), hi)


def clean_overrides(model, raw, hw=None):
    """(kept, clamped) for an owner's overrides: unknown keys dropped, values
    kept within range. clamped maps key -> (asked, applied)."""
    hw = hw or hardware()
    kept, clamped = {}, {}
    for key, value in (raw or {}).items():
        if key not in OVERRIDE_RANGES or value in (None, ""):
            continue
        applied = _coerce(key, value, hw, model)
        if applied is None:
            continue
        kept[key] = applied
        try:
            if float(value) != float(applied):
                clamped[key] = (value, applied)
        except (TypeError, ValueError):
            pass
    return kept, clamped


def overrides_from_settings(settings, model_id):
    try:
        return json.loads(settings.get(OVERRIDE_PREFIX + model_id) or "{}")
    except (TypeError, ValueError):
        return {}


def resolve(settings, model_id=None, hw=None):
    """Everything needed to run the chosen (or given) model on this Atlas.

    settings: Ray's settings dict (ai_settings). The model falls back to the
    catalog default when the saved one is no longer offered.
    """
    hw = hw or hardware()
    wanted = model_id or settings.get("model") or default_id()
    model = find(wanted)
    fallback_from = None
    if model is None:
        fallback_from, model = wanted, find(default_id())
    variant, opts = optimized(model, hw)
    base = dict(opts)
    kept, clamped = clean_overrides(model, overrides_from_settings(settings, model["id"]), hw)
    opts.update(kept)
    keep_alive, keep_alive_hours = keep_alive_for(settings.get("keep_alive_hours"))
    return {
        "id": model["id"],
        "label": model["label"],
        "tag": variant["tag"],
        "quant": variant["quant"],
        "size_mb": variant["size_mb"],
        "options": opts,
        "optimized": base,
        "overrides": kept,
        "clamped": clamped,
        "think": bool(model.get("think", False)),
        # How llama-server can reuse an earlier prompt: "any" common prefix
        # (transformers), or only at its saved checkpoints ("checkpoint",
        # hybrid models such as Qwen3.5), which Ray's history can't use.
        "prefix_cache": model.get("prefix_cache", "any"),
        "keep_alive": keep_alive,
        "keep_alive_hours": keep_alive_hours,
        "chars_per_token": model.get("chars_per_token", 3.3),
        "memory_mb": estimate_mb(model, opts["num_ctx"], variant),
        "embedder": embedder_plan(model, hw),
        "measured": dict(model.get("measured") or {}),
        "fallback_from": fallback_from,
    }


def keep_alive_for(value):
    """(Ollama keep_alive, the setting as shown) for the owner's choice.

    "always" (the default) keeps Ray's models loaded until something needs
    the memory (navigation unloads them; see app.py) — a model reloads in
    15-25 s, which is what an idle timeout made every first question after a
    quiet night wait for. A number is hours, 1-24."""
    if str(value or "always").strip().lower() in ("always", "-1", "0"):
        return "-1m", "always"            # negative: no idle timeout
    try:
        hours = max(1.0, min(24.0, float(value)))
    except (TypeError, ValueError):
        hours = 10.0
    return f"{hours:g}h", f"{hours:g}"


def estimate_mb(model, ctx, variant=None):
    """Resident size of the chat model at this context: measured on Atlas
    where available, otherwise the build's size plus a per-token allowance."""
    variant = variant or model["variants"][0]
    measured = (variant.get("measured_mb") or {}).get(str(ctx))
    if measured:
        return measured
    steps = sorted((int(c), mb) for c, mb in (variant.get("measured_mb") or {}).items())
    per_token = model.get("mb_per_1k_ctx", 60) / 1024
    if steps:
        c0, mb0 = min(steps, key=lambda s: abs(s[0] - ctx))
        return int(mb0 + (ctx - c0) * per_token)
    return int(variant["size_mb"] * 1.1 + ctx * per_token + 300)


def embedder():
    return catalog()["embedder"]


def min_ollama():
    """The Ollama version every catalog model can run on."""
    return max((m.get("min_ollama", "0") for m in models()), key=version_tuple)


def tested_ollama():
    """The Ollama version Ray's figures were measured on (what install.sh installs)."""
    return (catalog().get("ollama") or {}).get("tested") or min_ollama()


def preinstall_tags(hw=None):
    """What a new unit must carry: every offered model's build for this
    hardware, and the embedder."""
    hw = hw or hardware()
    tags = [variant_for(m, hw)["tag"] for m in models() if m.get("preinstall", True)]
    return tags + [embedder()["id"]]


def _main(argv):
    if argv[:1] == ["--preinstall"]:
        print("\n".join(preinstall_tags()))
    elif argv[:1] == ["--min-ollama"]:
        print(min_ollama())
    elif argv[:1] == ["--tested-ollama"]:
        print(tested_ollama())
    elif argv[:1] == ["--summary"]:
        for m in models():
            p = resolve({}, m["id"])
            print(f"{m['label']:<12} {p['tag']:<18} {p['quant']:<7} ctx {p['options']['num_ctx']}")
    else:
        print("usage: model_profiles.py --preinstall | --min-ollama | --tested-ollama | --summary", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
