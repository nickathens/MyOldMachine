"""Single source of truth for how the CLI providers serialize conversation turns.

The Claude Code and Codex CLI providers can't take structured message objects --
they accept one flat text prompt. We delimit history turns with a unique sentinel
token instead of readable tags like ``<user>...</user>`` so that when the model
parrots the format and fabricates a *next user turn*, we can detect it with an
exact string match. The old approach used fuzzy ``User:`` / ``<user>`` regexes
that also ate screenplays, YAML files, transcripts, and any prose containing
those words.

Each marker carries a per-process random *nonce* (regenerated every time this
module loads, i.e. once per bot restart). The nonce is what keeps detection free
of false positives:

- The model only ever sees nonce-bearing markers in its prompt, so a parroted
  hallucination reproduces the *live* nonce and is caught exactly.
- Prose that merely *talks about* the marker format -- this docstring, or the
  bot explaining ``<|MOM-TURN:user|>`` to a user -- carries no live nonce, so it
  is never mistaken for a turn boundary and never truncated. (Before the nonce, a
  static sentinel meant the bot quoting its own marker would truncate its reply
  at that point.)

The only content still trimmed is text that reproduces the *live* nonce verbatim
-- e.g. echoing back the raw serialized prompt -- which requires the actual
in-flight token to be present, which is exactly the case we want to cut.

The producer (``core.llm``) and the consumer (``bot.sanitize_response``) both
import from here so the format can't drift between the two sides.
"""

import re
import secrets

# Per-process random tag embedded in every turn marker. 32 bits of hex is ample
# for collision-avoidance; this is not a security boundary -- the failure it
# guards is the model truncating its *own* reply by parroting the format, which
# is self-contained to a single user's turn.
_SESSION_NONCE = secrets.token_hex(4)

# A fabricated *next user turn* is the dominant hallucination signature: the
# model answers, then invents the user's following question. Detected by exact
# match; the live nonce means only a parroted (not a merely-described) marker
# triggers the cut.
NEXT_TURN_SENTINEL = f"<|MOM-TURN:user:{_SESSION_NONCE}|>"

# Any complete turn marker (open or close, any role) bearing the *live* nonce,
# e.g. ``<|MOM-TURN:user:ab12cd34|>`` or ``<|MOM-TURN:/assistant:ab12cd34|>``.
# Generic markers without the live nonce are intentionally left untouched.
_STRAY_MARK_RE = re.compile(rf"<\|MOM-TURN:[^|]*:{re.escape(_SESSION_NONCE)}\|>")

# A turn marker truncated by the token limit, left dangling and *unclosed* at the
# very end of the reply. Matches the ``<|MOM-TURN`` prefix followed by any run of
# characters that never forms a closing ``|>``, so a *complete* quoted marker
# (e.g. ``<|MOM-TURN:user|>`` mentioned in prose) is never grabbed, while a real
# truncation is caught however much of the role/nonce survived -- even if the cut
# landed before the nonce, mid-nonce, or on the closing ``|`` itself. The nonce
# can't gate this step (truncation may have eaten it), so it keys off the absence
# of a close instead.
_TRAILING_PARTIAL_RE = re.compile(r"<\|MOM-TURN(?:(?!\|>)[^\n])*$")


def wrap_turn(role: str, content: str) -> str:
    """Serialize one conversation turn with sentinel delimiters."""
    return (
        f"<|MOM-TURN:{role}:{_SESSION_NONCE}|>"
        f"{content}"
        f"<|MOM-TURN:/{role}:{_SESSION_NONCE}|>"
    )


# Worst-case character overhead wrap_turn() adds around a turn's content (open +
# close markers for the longest role label, "assistant", including the nonce).
# The prompt-size budget in bot.py adds this per message so its estimate tracks
# the real serialized size; over-estimating is the safe direction (trims slightly
# early).
TURN_OVERHEAD_CHARS = len(wrap_turn("assistant", ""))


def strip_hallucinated_turns(text: str) -> str:
    """Remove a hallucinated continuation the model appended to its reply.

    Cuts from the first fabricated next-user-turn marker, then strips any stray
    markers the model echoed around its own reply (without discarding the reply
    itself), then drops a trailing partial marker left by token truncation. Every
    step keys off the live per-process nonce, so legitimate output that merely
    describes the marker format is left intact.
    """
    cut = text.find(NEXT_TURN_SENTINEL)
    if cut != -1:
        text = text[:cut]
    text = _STRAY_MARK_RE.sub("", text)
    text = _TRAILING_PARTIAL_RE.sub("", text)
    return text
