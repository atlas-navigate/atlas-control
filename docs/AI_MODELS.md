# Ray's AI models

Owners choose Ray's language model in **Ray → Settings → Model**. They never
choose a quantization, context size or sampling setting: Atlas tunes each
model for the hardware it runs on.

| Model | Build on an 8 GB Orin Nano | Context | Answer speed | Memory left for Atlas |
|---|---|---|---|---|
| **Qwen2.5 3B** (standard) | `qwen2.5:3b` Q4_K_M, 1.9 GB | 4,096 tokens | 21 tokens/s | 2.3 GB |
| **Qwen3.5 4B** | `qwen3.5:4b` Q4_K_M, 3.4 GB | 4,096 tokens | 14.8 tokens/s | 0.75 GB |

Both run wholly on the GPU. The embedder (`qwen3-embedding:0.6b`, for
knowledge-base search) is on the GPU with Qwen2.5 and on the CPU with Qwen3.5
(0.5 s a query). Measured on the development unit (Orin Nano 8 GB Super,
Ollama 0.32.1, the full Atlas stack running) with `tools/bench/models.py`.

## Where the settings come from

`ai_models.json` (shipped in every release) is the catalog; `model_profiles.py`
turns "the owner chose Qwen3.5 4B" into Ollama options for this unit:

- **Build (quantization)**: the best one this unit's memory holds. The catalog
  lists builds best-first with the RAM each needs: Qwen3.5 4B is Q8_0 on
  16 GB-class hardware and Q4_K_M on 8 GB (the only build that fits; `nvfp4`
  needs a Blackwell GPU, `bf16` is 9 GB).
- **Context window**: 4,096 tokens. Ray's prompt (system, live data, up to
  7,000 characters of passages, recent turns) fits with 1,024 tokens left for
  the answer. 8,192 costs little memory on either model but doubles
  worst-case prompt time, so it is allowed as an override, not the default.
- **Threads**: one per CPU core (6 on Orin Nano, 8 on Orin NX).
- **Batch**: 512. **GPU layers**: automatic (a forced count can fail to load).
- **Sampling**: each model's own published settings for direct answers:
  - Qwen2.5 3B: temperature 0.7, top-p 0.8, top-k 20, repeat penalty 1.05.
  - Qwen3.5 4B: temperature 0.7, top-p 0.8, top-k 20, presence penalty 1.5
    (the "non-thinking" set; Ollama's defaults are the thinking-mode ones).
- **Thinking off** for both: answers start at once. Thinking would add
  hundreds of tokens before every answer (tens of seconds on this hardware).
- **Load order and the embedder's place**:
  - Ollama 0.32 runs Qwen3.5 through `llama-server` with its vision encoder,
    which reserves about 1 GB. `llama-server` also keeps 1 GB free.
  - With the embedder already resident, only 9 of 34 layers fit on the GPU:
    5 tokens/s, and 150 MB left for the rest of Atlas.
  - Loaded first, Qwen3.5 fits wholly on the GPU, and the embedder then runs
    on the CPU. The profile encodes this (`embedder.load_first`, `on_gpu`).
  - Qwen2.5 keeps today's embedder-first, all-GPU arrangement.

| Qwen3.5 4B on 8 GB (ctx 4096) | GPU share | Generation | Prompt | Free after |
|---|---|---|---|---|
| Embedder (GPU) loaded first — Ollama's natural order | 34% | 5.1 tok/s | 281 tok/s | 137 MB |
| Qwen3.5 first, embedder on the CPU (Atlas's profile) | 100% | 14.8 tok/s | 1,270 tok/s | 751 MB |
| Qwen3.5 first, embedder on the GPU afterwards | 100% / embedder 53% | 14.8 tok/s | 1,182 tok/s | 670 MB |

Quality spot-check (`tools/bench/models.py --quality`), same passage and question:

| | Qwen2.5 3B | Qwen3.5 4B |
|---|---|---|
| Used the passage's facts (boil 1 min, 2 drops/L, wait 30 min) | 2 of 3 | 3 of 3 |
| `[CALC: …]` extraction format | 3 of 3 | 2 of 3 (one conversion misread) |

Ray's calculator checks every `[CALC:]` expression itself, so a wrong
extraction gives no answer rather than a wrong number.

## Switching

`POST /api/ai/model` starts a switch job (`ai_manager.switch_model`). Paired
clients see its progress (Socket.IO `ai_model`):

1. **Check**: the model is offered, and this Ollama can run it (Qwen3.5
   needs 0.17.1+). It is refused if the memory it needs isn't free, counting
   what is about to be unloaded; the model that works stays loaded.
2. **Download**, only if missing and only with Internet: progress in percent,
   a disk-space check, and resumable (Ollama keeps partial layers).
3. **Wait** for an answer in progress to finish (Ray's generation slot).
4. **Unload** the old model (and the embedder if its place changes), and wait
   until Ollama reports it gone.
5. **Load** the new one with its profile in the right order, then a short
   **test answer**: this proves it works and measures its speed (shown on its
   card).
6. **Save** the choice (`ai_settings.model`, in `data/`).

Any failure puts the previous model back, in its own order, and says why.
While the job runs, chat requests get "Ray is switching to … — it will answer
again in a minute."

The choice survives reboots and Atlas updates. Boot warm-up loads the saved
model with its profile. If the saved model is no longer offered, or its files
are gone and can't be fetched, Ray runs the standard model and the settings
page says so.

## Expert overrides

Settings → Ray → Expert overrides (collapsed) lets an owner change a model's
temperature, top-p, top-k, penalties, response length, context, batch, threads
and GPU layers:

- **Per model.** Tuning Qwen2.5 never affects Qwen3.5.
- **Kept within what this unit can run.** Context is capped at 8,192 tokens,
  threads at the core count, and the page shows any value that was adjusted.
- **Applied like a switch.** The model is reloaded and tested, and the
  overrides are saved only if it works.
- **"Reset to optimized"** removes them.

The embedding model can be changed there too. The job checks that the new model
is an embedding model, then rebuilds the knowledge index, because vectors from
two embedders can't be compared.

Upgrading from a release with global settings (migration 4): values the owner
had changed from the old defaults become Qwen2.5 3B overrides; everything else
follows the profiles.

## Adding a model

1. Add it to `ai_models.json`:
   - builds, best first, with `min_ram_mb`;
   - official non-thinking sampling;
   - `min_ollama`;
   - `measured_mb` from `tools/bench/models.py`;
   - the embedder plan.
2. Measure both load orders on an 8 GB unit with the full stack running. The
   profile must keep it wholly on the GPU with at least ~700 MB free, or it
   does not ship for 8 GB units.
3. `install.sh` and `atlas-capture.sh` pick it up from the catalog: new units
   carry it, and capture refuses a payload without it. Units already sold get
   it through Settings → Ray when they are online, since the model files are
   not part of a release.

## Where Ray's time goes, and what was tuned (October 2026)

Measured on the development unit: Orin Nano 8 GB Super, MAXN_SUPER, Ollama
0.32.1, Qwen2.5 3B, Ray-sized prompts of ~2,300 tokens. The tool is
`tools/bench/ray_bench.py`; results are in `tools/bench/results/`. It has three
modes:
- **app end-to-end**, paired, over the real SSE stream;
- **`--inproc`**, Ray's own pipeline in the bench process, on a copy of the
  database;
- **`direct`**, Ollama alone.

Rates come from llama-server's own timing lines (tokens actually processed;
Ollama's `prompt_eval_count` also counts cached tokens). Time to first token is
measured by the client.

| Where | Qwen2.5 3B | Notes |
|---|---|---|
| Generation | 19.6 tok/s at ~2.3k tokens of context (21.4 tok/s with a short prompt) | GPU ~970 MHz, memory controller ~3.15 GHz while generating: the governors do ramp |
| Prompt processing | ~800-825 tok/s | ~1,150 new tokens per turn → ~1.45 s |
| Building the context | ~0.54 s median | almost all of it the query embedding call |
| Ollama per request | ~0.39 s (embed) / ~0.5 s (chat) | CPU work before the request reaches the model, even when resident |
| Prompt-cache save | ~55-80 ms per request | llama-server saving the previous prompt to RAM |
| Time to first token | ~2.5 s median (warm) | context 0.54 + Ollama ~0.4 + prefill 1.45 + first token |

**What changed.**
- **CPU clocks.** Ollama's per-request work runs on the CPU, which idled at
  0.73 GHz. At 1.73 GHz it halves: embed 391 → 215 ms, chat 505 → 267 ms, about
  0.4 s per answer. Ray now raises the CPU, GPU and memory-controller floors
  from the moment a question arrives until 30 s after the last answer. This
  happens on mains power only, below 85 °C, under a 90 s system-enforced lease
  (`perf_boost.py`, engine `perf.py`).
- **llama-server's RAM prompt cache.** It grew ~48 MB (Qwen2.5) or ~134 MB
  (Qwen3.5) per distinct prompt, up to an 8 GiB default. On Qwen3.5, free
  memory went from 689 MB to 0 in 21 turns and the first word took 3× longer.
  It is now capped at 128 MiB (an Ollama drop-in shipped with releases).
- **Models stay loaded.** Keep-alive defaults to "Always": no 15–25 s reload
  after an idle night. Navigation still unloads Ray when it needs the memory.
  A warmup with the models already resident doesn't reload them or turn Ray
  away.
- **Warmup processes the start of Ray's real prompt** (its instructions and
  the day's date, byte-identical to a real turn), so the first question pays
  only for itself.
- **A failed startup is retried.** After a power cut, NTP stepped the clock
  3.5 days forward while Ollama was loading the embedder; Ollama aborted the
  load and Ray stayed down until a restart. The warmup is now retried after
  10 s, 30 s, 60 s, then every 5 minutes while Ray is down, and at once after
  a clock step.
- **Prompt layout: the original arrangement stays the default**
  (`ray_prompt.py`). An alternative ("turn", opt-in via `ray_prompt_layout`)
  moves per-turn material from the system message to the user message, so
  consecutive prompts share everything up to the previous question.
  - Within the 3,072-token budget Ray keeps only the last exchange or two, so
    the history window slides every turn. Measured reuse is the ~800–940
    token system prompt in both layouts, and conversation time to first
    token is unchanged (2.67 s either way).
  - An 8k context does let reuse grow (811 → 2,733 tokens by turn 6). But
    Ray then keeps more history, so first-token time stays at 2.3–3.2 s, and
    generation drops to ~18.2 tok/s on the longest prompts. That's more memory
    for Ray, not more speed.
  - Quality: "turn" was better on follow-ups and injection probes, but
    answered arithmetic word problems Ray doesn't flag as math tersely and
    wrongly ("1,356 m" for 343 m/s × 4.2 s). Through the app the calculator
    items fell from 4/4 to 1/4. With no speed to show for it, the original
    layout is the default.
- **Off the event loop.** Geocoder cache misses and the first load of its
  tables run in a worker thread; the tables preload at startup.
- **The browser re-renders** a streaming answer at most once per frame.

**What was tried and left alone.**
- **Batch size and threads.** Batch 512 vs 1024 and 2/4/6 threads made no
  difference: generation 19.3–19.7 tok/s, prefill 824–839 tok/s. Batch 1024
  costs 80 MB.
- **KV cache q8_0 vs f16 was not measured.** A standalone runner could not
  load Ollama's CUDA backend. At a 4k context the KV cache is ~4% of the
  memory read per token, so the possible effect is small.
- **The embedder's context** stays at 1,024 tokens. It was already sized to
  the longest passage, and 1,024 is where its memory curve bends.

**Quality guard** (`tools/bench/ray_eval.json`: 25 conversations with
seed-document facts, calculator answers, live data, multi-turn follow-ups and
prompt-injection probes; temperature 0.7, three runs each):

| Qwen2.5 3B | Original layout (default) | "turn" layout (opt-in) |
|---|---|---|
| All | 59/75 | 62/75 |
| Knowledge-base facts | 29/36 | 30/36 |
| Follow-ups across turns | 9/12 | 12/12 |
| Injection probes held | 3/6 | 4/6 |
| Retrieval recall | 33/36 | 33/36 |
| Calculator | 9/12 | 7/12 (4/12 with the calculator blocks in the user message) |

**Why the original layout stays.** With the calculator instruction anywhere
but last before the question, or with no calculator instruction at all for
a word problem, the 3B model in the "turn" layout answers arithmetic tersely
and wrongly ("1,356 m" for 343 × 4.2). In the original arrangement it shows
its working. Routing flagged math turns back to the original layout did not
cover the unflagged ones.

**Qwen3.5** always uses the original layout (it can only reuse a prompt at
its own checkpoints). Its regression run scored 22/25: every calculator, live and
multi-turn item passed. Generation is 14.4–14.7 tok/s, and prefill ~460 tok/s
on Ray-sized prompts.

**Found, not changed (Ray's reasoning, not speed).** The calculator agent
mis-extracts free-fall questions (√(2·g·h/4), h/(g/2)). The answer then
sometimes ends in "approximately *" after `[CALC:]` post-processing. It fails
in both layouts.
