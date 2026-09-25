"""
Keep Ray's prompt inside the model's context window.

A prompt longer than num_ctx is truncated silently by Ollama, and what goes
first is the start of the conversation or of the prompt itself. The token
budget is num_ctx − num_predict (room is kept for the answer), filled in
priority order:

  1. the system prompt and the question (always)
  2. live data already chosen for this question (GPS, stats, mesh, …)
  3. knowledge-base passages, up to their own cap, leaving HISTORY_FLOOR_TOKENS
  4. earlier turns, newest first, while they fit

Token counts are estimated from characters. Measured on the Qwen tokenizer
Atlas ships with: 3.8 characters per token on average, 2.3 for dense tables.
3.3 errs towards dropping an old turn rather than overflowing. The real
prompt_eval_count of each answer is recorded (perf_metrics) to check this.
"""
CHARS_PER_TOKEN = 3.3
MESSAGE_OVERHEAD_TOKENS = 6          # chat-template markers around each message
HISTORY_FLOOR_TOKENS = 400           # recent turns keep at least this much room
MIN_RAG_CHARS = 1500                 # one or two passages even on a tiny context


def tokens(text):
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


def prompt_budget(settings, defaults=None):
    defaults = defaults or {}

    def num(key, fallback):
        try:
            return int(settings.get(key, defaults.get(key, fallback)))
        except (TypeError, ValueError):
            return fallback
    return max(512, num("num_ctx", 4096) - num("num_predict", 1024))


def rag_char_budget(settings, fixed_texts, question, cap, defaults=None):
    """Characters available for knowledge-base passages."""
    left = (prompt_budget(settings, defaults) - sum(tokens(t) for t in fixed_texts) - tokens(question)
            - HISTORY_FLOOR_TOKENS - 3 * MESSAGE_OVERHEAD_TOKENS)
    return max(MIN_RAG_CHARS, min(cap, int(left * CHARS_PER_TOKEN)))


def fit_history(system, question, history, settings, defaults=None):
    """The newest turns of `history` that fit beside `system` and `question`.
    Returns (kept, info)."""
    budget = prompt_budget(settings, defaults)
    used = tokens(system) + tokens(question) + 2 * MESSAGE_OVERHEAD_TOKENS
    kept = []
    for msg in reversed(history):
        cost = tokens(msg.get("content")) + MESSAGE_OVERHEAD_TOKENS
        if used + cost > budget:
            break
        kept.append(msg)
        used += cost
    kept.reverse()
    return kept, {"budget": budget, "estimated": used, "history_kept": len(kept),
                  "history_dropped": len(history) - len(kept), "over": used > budget}
