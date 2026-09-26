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
