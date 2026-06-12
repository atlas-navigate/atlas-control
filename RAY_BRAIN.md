# Ray's Brain — How the Atlas Control AI Thinks

Ray is the local AI assistant inside Atlas Control. It runs **100% offline** on the
Jetson Orin Nano's GPU via [Ollama](https://ollama.com) — no internet, no cloud, no API keys.
Everything below lives in [`ai_manager.py`](ai_manager.py).

> **Ask Ray directly.** Ray carries a copy of this architecture in its knowledge base.
> Open the AI chat and ask things like *"how do you think?"*, *"how do you retrieve
> information?"*, *"explain your thought process"*, or *"why did you say that?"* —
> the self-architecture doc is force-injected and Ray explains itself in first person.

---

## The Diagram

```
                                  ┌─────────────────────────┐
                                  │      YOUR MESSAGE       │
                                  └────────────┬────────────┘
                                               │
                      ┌────────────────────────▼────────────────────────┐
                      │           1. ROUTING (keyword scanners)         │
                      │  live-data? location? math? physics? ballistic? │
                      │  self-question about Ray's own brain?           │
                      └──┬─────────┬─────────┬─────────┬─────────┬──────┘
                         │         │         │         │         │
        ┌────────────────▼──┐ ┌────▼─────┐ ┌─▼───────┐ ┌▼────────▼─────────┐
        │ 2. LIVE SENSES    │ │ 3. GPS / │ │ 4. RAG  │ │ 5. CALC AGENT     │
        │                   │ │ LOCATION │ │ RECALL  │ │ (math cortex)     │
        │ • CPU/GPU/RAM/    │ │          │ │         │ │                   │
        │   temps/power     │ │ M9N fix +│ │ embed   │ │ ballistic? ─────┐ │
        │ • mesh nodes      │ │ offline  │ │ query → │ │  parse range/   │ │
        │   on/offline      │ │ reverse- │ │ cosine  │ │  zero/round →   │ │
        │ • channels        │ │ geocode  │ │ vs every│ │  G1 drag-model  │ │
        │ • recent messages │ │ (41k US  │ │ doc →   │ │  simulation     │ │
        │ • telemetry       │ │ ZIPs +   │ │ top-3   │ │ general math? ─┤ │
        │ • topology/SNR    │ │ 68k world│ │ ≥ 0.30  │ │  LLM extracts  │ │
        │ • alerts          │ │ cities)  │ │ score   │ │  [CALC:] exprs │ │
        │                   │ │          │ │         │ │  → sandboxed   │ │
        │ (SQLite, fresh)   │ │          │ │         │ │  evaluator     │ │
        └─────────┬─────────┘ └────┬─────┘ └────┬────┘ └───────┬─────────┘
                  │                │            │              │
                  └──────────┬─────┴──────┬─────┴──────────────┘
                             │            │
              ┌──────────────▼────────────▼───────────────┐
              │        6. CONTEXT ASSEMBLY (working       │
              │              memory for this reply)       │
              │                                           │
              │  system prompt                            │
              │   + SYSTEM STATUS                         │
              │   + CURRENT POSITION (+ nearest city)     │
              │   + MESH NETWORK STATE                    │
              │   + KNOWLEDGE BASE (retrieved docs)       │
              │   + SELF-KNOWLEDGE (if asked about Ray)   │
              │   + CALCULATOR RESULTS (pre-verified)     │
              │   + last 8 chat messages                  │
              └─────────────────────┬─────────────────────┘
                                    │
              ┌─────────────────────▼─────────────────────┐
              │   7. LANGUAGE CORE — qwen2.5:3b @ Ollama  │
              │   Jetson GPU, 4096-token window,          │
              │   temp 0.3, streams token by token        │
              └─────────────────────┬─────────────────────┘
                                    │
              ┌─────────────────────▼─────────────────────┐
              │           8. POST-PROCESSING              │
              │  • [CALC: expr] tags → computed values    │
              │  • confidence footer (cannot be faked):   │
              │    HIGH / MEDIUM / LOW + actual sources   │
              └─────────────────────┬─────────────────────┘
                                    │
                            ┌───────▼───────┐
                            │     ANSWER    │
                            └───────────────┘

   OFFLINE INDEXING (startup, background thread)
   ┌──────────────────────────────────────────────────────────────┐
   │ seed docs (survival, comms, ballistics, first aid, app usage,│
   │ Ray self-architecture) ──► nomic-embed-text ──► embedding    │
   │ vector stored next to the text in SQLite (ai_documents).     │
   │ Edited docs get their embedding cleared and re-embedded.     │
   └──────────────────────────────────────────────────────────────┘
```

Same flow as a Mermaid graph (renders on GitHub):

```mermaid
flowchart TD
    U[User message] --> R{1. Routing<br/>keyword scanners}
    R -->|always| S[2. Live senses<br/>system stats + mesh state]
    R -->|always| G[3. GPS + offline<br/>reverse geocode]
    R -->|knowledge question| RAG[4. RAG recall<br/>embed query, cosine top-3 ≥ 0.30]
    R -->|physics / ballistics| C[5. Calc agent<br/>G1 drag sim / sandboxed eval]
    R -->|asks about Ray itself| SK[Self-knowledge doc<br/>force-injected]
    S --> CTX[6. Context assembly<br/>+ last 8 chat messages]
    G --> CTX
    RAG --> CTX
    C --> CTX
    SK --> CTX
    CTX --> LLM[7. Language core<br/>qwen2.5:3b via Ollama on GPU]
    LLM --> P[8. Post-processing<br/>CALC tags + confidence footer]
    P --> A[Answer]

    subgraph Indexing at startup
        D[Seed docs] --> E[nomic-embed-text] --> DB[(SQLite<br/>text + embedding)]
    end
    DB -.-> RAG
```

---

## Stage by stage

### 1. Routing — `_is_location_query`, `_is_math_query`, `_is_physics_query`, `_is_ballistic_query`, `_is_self_query`
Before any model runs, cheap keyword scanners classify the message. This decides which
subsystems wake up: live-data questions skip RAG (the answer is already injected fresh),
physics questions trigger the calculator agent, and questions about Ray itself force-inject
the self-architecture doc.

### 2. Live senses — `build_context()`
Every reply gets fresh system stats (per-core CPU, GPU, RAM, disk, temperatures, power
draw, uptime) and the live mesh picture from SQLite: node online/offline status, battery,
SNR, channels, the last 10 messages, and active alerts. Telemetry, positions, and topology
are injected only when the question asks for them — keeping the context window lean.

### 3. Location grounding — `_build_location_prefix`, `_reverse_geocode`
The SparkFun M9N GPS fix is injected into **every** prompt, reverse-geocoded entirely
offline: US coordinates snap to the nearest of 41,000 ZIP-code centroids (skipping
military-base names when a civilian ZIP is close), everywhere else uses a
gravity-weighted lookup over 68,000 world cities so a nearby town beats a distant
metropolis. If you type coordinates or a place name, that overrides the device fix.

### 4. Indexing & retrieval (RAG) — `_embed_unembedded_docs`, `rag_search`
**Indexing:** at startup, every seeded knowledge doc is run through `nomic-embed-text`,
producing a vector "fingerprint of meaning" stored next to the text in SQLite. Changed
docs are automatically re-embedded.
**Retrieval:** your question is embedded the same way and compared against every doc by
cosine similarity. The top 3 docs scoring ≥ 0.30 are pasted into the context. Embeddings
are cached in RAM for 120 s so repeated queries don't hit the database.

### 5. The calculator agent — `_calc_agent_pass`, `_ballistic_direct_compute`
Ray does not trust a 3B-parameter model with arithmetic:
- **Ballistics:** range, zero distance, and ammunition are parsed straight from your
  message; a real point-mass simulation with the G1 drag table integrates the trajectory
  and hands Ray the drop in cm/inches/MOA/mrad before it writes a word.
- **General math:** a first pass at temperature 0.05 extracts bare `[CALC: …]`
  expressions, a sandboxed evaluator (math functions only, no builtins) computes them,
  and the verified numbers are injected with an instruction *not to recompute*.
- Any `[CALC: expr]` tag Ray emits in its answer is replaced with the computed value.

### 6–7. Working memory & generation — `chat()` / `chat_stream()`
The system prompt + all injected sections + the last 8 chat messages go to Ollama
(default `qwen2.5:3b`, 4096-token window, temperature 0.3, kept warm in VRAM for 10 h).
The answer streams token by token over the socket.

### 8. Confidence footer — `_confidence_label`
Every answer ends with `Confidence: HIGH|MEDIUM|LOW | Source: …` computed from what was
*actually* injected — live data or the self-doc means HIGH, a strong RAG match (≥ 0.70)
HIGH, moderate (≥ 0.50) MEDIUM, training-knowledge-only LOW. Ray can't inflate it; the
footer is appended after generation.

---

## Memory model

| Memory | Where | Lifetime |
|---|---|---|
| Conversation history | SQLite (`ai_chats` / `ai_messages`) | Permanent, but only the last 8 messages are re-read per reply |
| Knowledge base | SQLite (`ai_documents`, text + embedding) | Permanent; re-embedded when edited |
| Doc-embedding cache | RAM | 120 s TTL |
| Model weights | VRAM | `keep_alive` (default 10 h) |
| Across separate chats | — | None — each chat is isolated |

## Honest limits

- Routing is keyword-based; an oddly-phrased question can take the wrong path.
- Documents are embedded whole — no chunking — so retrieval is per-topic, not per-paragraph.
- Anything outside the knowledge base comes from the model's training data (marked LOW confidence).
- No internet: Ray cannot look anything up that isn't on the device.
