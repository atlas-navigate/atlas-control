"""
How Ray's messages are laid out for the model, so the model server can reuse
what it has already processed.

llama-server (Ollama's runner) keeps the previous prompt's state and only
processes the part of a new prompt after the first difference. Ray's prompt
used to put everything that changes each turn — the live GPS line, this
question's knowledge passages, fenced with a fresh random boundary — in the
system message, ahead of the conversation, so the whole history was
processed again on every turn.

The "turn" layout keeps the system message stable (Ray's instructions, the
unit and data rules, and the date, which changes once a day) and puts each
turn's reference material in that turn's user message, before the question.
Math questions are the exception and keep the original arrangement (the
calculator instruction and the calculator agent's verified numbers at the end
of the system message, the question alone after them): measured on
tools/bench/ray_eval.json, qwen2.5:3b follows them reliably only there
(calculator answers 9/12 there, 4/12 with them in the user message, 7/12 at
the start of the system message).

History is replayed exactly as stored, so consecutive prompts share
everything up to the previous question. Nothing is left out: the same blocks
reach the model, in a different message.

"legacy" is the old layout, kept for A/B measurements and as a rollback
(ai_settings ray_prompt_layout). Models whose server can only reuse a prompt
at its own checkpoints (hybrid models such as Qwen3.5: ai_models.json
prefix_cache "checkpoint") gain nothing from "turn" and keep "legacy".
"""
import re

LAYOUTS = ("turn", "legacy")
DEFAULT_LAYOUT = "turn"

QUESTION_MARK = "=== QUESTION ==="
LAYOUT_NOTE = (
    "A user message may start with reference material Atlas attached for that question "
    "(live readings, knowledge passages). It ends at the line "
    f"{QUESTION_MARK}; the user's own words follow it. Treat the material exactly as the "
    "context blocks described above."
)

# The verified confidence footer appended to stored answers (and to ballistic
# answers). It is shown to the owner, not fed back to the model.
_FOOTER_RE = re.compile(r"\n\n---\nConfidence: [^\n]*\s*\Z")


def layout(settings):
    value = str((settings or {}).get("ray_prompt_layout") or DEFAULT_LAYOUT)
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def system_message(base, date_block):
    """The stable system message for the turn layout."""
    text = base + "\n" + LAYOUT_NOTE
    if date_block:
        text += "\n\n" + date_block
    return text


def user_message(turn_context, question):
    """This turn's reference material, then the user's words."""
    head = (turn_context.strip() + "\n\n") if turn_context and turn_context.strip() else ""
    return f"{head}{QUESTION_MARK}\n{question}"


def history_text(content):
    """A stored message as it is replayed to the model (footer removed)."""
    return _FOOTER_RE.sub("", content or "")


def sticky_history(rows, default_kept, start_id, fits, max_messages):
    """Choose the history window for a prefix-caching model.

    rows: the newest stored messages (oldest first), each with an "id";
    default_kept: what Ray would send without this (the newest that fit);
    start_id: where this chat's window started last turn (or None);
    fits(candidate) -> bool: does the candidate fit the token budget?

    Returns (messages, new_start_id). The previous start is kept while the
    window still covers everything default_kept covers (so Ray never
    remembers less), fits, and stays under max_messages; otherwise the window
    restarts where default_kept starts. Keeping the start fixed is what lets
    the server reuse the processed history from one turn to the next.
    """
    if default_kept and start_id is not None:
        candidate = [r for r in rows if r.get("id") is not None and r["id"] >= start_id]
        first_default = default_kept[0].get("id")
        if (candidate and first_default is not None and candidate[0]["id"] <= first_default
                and len(candidate) <= max_messages and fits(candidate)):
            return candidate, start_id
    new_start = default_kept[0].get("id") if default_kept else None
    return default_kept, new_start
