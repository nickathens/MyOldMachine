"""Single source of truth for how the CLI providers serialize conversation turns.

The Claude Code and Codex CLI providers can't take structured message objects --
they accept one flat text prompt. We delimit turns with a unique sentinel token
instead of readable tags like ``<user>...</user>``. Two reasons:

1. When the model parrots the format and fabricates a next turn, the sentinel
   lets us detect it with an exact string match. The old approach used fuzzy
   ``User:`` / ``<user>`` regexes that also ate screenplays, YAML files, and any
   prose containing those words.
2. The token never collides with real content, so trimming is free of false
   positives.

The producer (``core.llm``) and the consumer (``bot.sanitize_response``) both
import from here so the format can't drift between the two sides.
"""

import re

# A fabricated *next user turn* is the dominant hallucination signature: the
# model answers, then invents the user's following question. The token is an
# ASCII special-token style marker (cf. ChatML's ``<|im_start|>``) -- the model
# reproduces it reliably when parroting, yet it never appears in real content.
NEXT_TURN_SENTINEL = "<|MOM-TURN:user|>"

# Any complete turn marker (open or close, any role), e.g. ``<|MOM-TURN:user|>``
# or ``<|MOM-TURN:/assistant|>``.
_STRAY_MARK_RE = re.compile(r"<\|MOM-TURN:[^|]*\|>")

# A turn marker truncated by the token limit, dangling at the end of the reply.
_TRAILING_PARTIAL_RE = re.compile(r"<\|MOM-TURN[^\n]*$")


def wrap_turn(role: str, content: str) -> str:
    """Serialize one conversation turn with sentinel delimiters."""
    return f"<|MOM-TURN:{role}|>{content}<|MOM-TURN:/{role}|>"


# Worst-case character overhead wrap_turn() adds around a turn's content (open +
# close markers for the longest role label, "assistant"). The prompt-size budget
# in bot.py adds this per message so its estimate tracks the real serialized
# size; over-estimating is the safe direction (trims slightly early).
TURN_OVERHEAD_CHARS = len(wrap_turn("assistant", ""))


def strip_hallucinated_turns(text: str) -> str:
    """Remove a hallucinated continuation the model appended to its reply.

    Cuts from the first fabricated next-user-turn marker, then strips any stray
    markers the model echoed around its own reply (without discarding the reply
    itself), then drops a trailing partial marker left by token truncation. The
    sentinel never appears in legitimate output, so this never truncates real
    content.
    """
    cut = text.find(NEXT_TURN_SENTINEL)
    if cut != -1:
        text = text[:cut]
    text = _STRAY_MARK_RE.sub("", text)
    text = _TRAILING_PARTIAL_RE.sub("", text)
    return text
