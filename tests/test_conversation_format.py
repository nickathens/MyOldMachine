"""Unit tests for core.conversation_format.

Covers the sentinel turn delimiter that replaced readable ``<user>`` / ``User:``
tags in the CLI provider prompts. Each marker carries a per-process random nonce,
so the fixtures here derive the nonce from the live sentinel rather than
hardcoding it. Tests cover:
- wrap_turn() serialization shape
- strip_hallucinated_turns() trims a fabricated next user turn
- legitimate content that merely contains "User:" / "<user>" / "user:" survives
  (the regression the sentinel exists to prevent)
- generic (nonce-free) talk *about* the marker format survives -- the regression
  the per-process nonce exists to prevent (the bot quoting its own sentinel used
  to truncate its reply at that point)
- stray markers the model echoes around its own reply are removed, reply kept
- a trailing partial marker (token-limit truncation) is dropped
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.conversation_format import (  # noqa: E402
    NEXT_TURN_SENTINEL,
    strip_hallucinated_turns,
    wrap_turn,
)

# The nonce is random per process; derive it from the public sentinel so the
# fixtures never hardcode the random value.
_PREFIX = "<|MOM-TURN:user:"
_SUFFIX = "|>"
_NONCE = NEXT_TURN_SENTINEL[len(_PREFIX):-len(_SUFFIX)]


def _open(role: str) -> str:
    return f"<|MOM-TURN:{role}:{_NONCE}|>"


def _close(role: str) -> str:
    return f"<|MOM-TURN:/{role}:{_NONCE}|>"


class WrapTurnTests(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(
            wrap_turn("user", "hello"),
            _open("user") + "hello" + _close("user"),
        )

    def test_user_open_marker_matches_sentinel(self):
        # A wrapped user turn must begin with the exact sentinel the consumer
        # detects, otherwise producer and consumer have drifted.
        self.assertTrue(wrap_turn("user", "x").startswith(NEXT_TURN_SENTINEL))

    def test_content_with_special_chars_is_not_escaped(self):
        body = "code: a < b and x | y"
        self.assertEqual(
            wrap_turn("assistant", body),
            _open("assistant") + body + _close("assistant"),
        )


class StripHallucinatedTurnsTests(unittest.TestCase):
    def test_clean_reply_unchanged(self):
        reply = "Here is your answer.\n\nIt has two paragraphs."
        self.assertEqual(strip_hallucinated_turns(reply), reply)

    def test_fabricated_next_user_turn_is_cut(self):
        reply = "The capital of France is Paris."
        hallucination = reply + "\n" + wrap_turn("user", "And Spain?")
        self.assertEqual(strip_hallucinated_turns(hallucination), reply + "\n")

    def test_everything_after_first_fabricated_turn_is_cut(self):
        reply = "Real answer."
        tail = (
            "\n" + wrap_turn("user", "fake q1")
            + "\n" + wrap_turn("assistant", "fake a1")
            + "\n" + wrap_turn("user", "fake q2")
        )
        self.assertEqual(strip_hallucinated_turns(reply + tail), reply + "\n")

    def test_screenplay_with_user_marker_survives(self):
        # The exact failure the old regex caused: a screenplay line starting
        # with "User:" used to delete everything after it.
        screenplay = (
            "INT. OFFICE - DAY\n\n"
            "User: clicks the red button.\n"
            "ASSISTANT: the screen flickers.\n"
            "Human: walks away."
        )
        self.assertEqual(strip_hallucinated_turns(screenplay), screenplay)

    def test_yaml_user_key_survives(self):
        yaml_doc = "config:\n  user: admin\n  role: assistant\n  human: false\n"
        self.assertEqual(strip_hallucinated_turns(yaml_doc), yaml_doc)

    def test_html_user_tag_survives(self):
        html = "<div><user>name</user></div>\nMore markup follows here."
        self.assertEqual(strip_hallucinated_turns(html), html)

    def test_pasted_transcript_with_user_lines_survives(self):
        # A user pasting a chat transcript for the assistant to analyze must not
        # have it silently truncated at the first "User:" line.
        transcript = (
            "Please summarize this support chat:\n\n"
            "User: my order never arrived\n"
            "Agent: I'm sorry to hear that, let me check\n"
            "User: it's been two weeks\n"
            "Agent: I've issued a refund"
        )
        self.assertEqual(strip_hallucinated_turns(transcript), transcript)

    def test_generic_token_in_prose_survives(self):
        # THE regression the per-process nonce fixes: the bot explaining the
        # marker format must not truncate its own reply. A generic token carries
        # no live nonce, so it is not a turn boundary.
        doc = (
            "I delimit turns with a sentinel like <|MOM-TURN:user|> and close "
            "it with <|MOM-TURN:/user|> so I can spot fabricated turns."
        )
        self.assertEqual(strip_hallucinated_turns(doc), doc)

    def test_generic_token_at_end_of_reply_survives(self):
        # The trailing-partial path must also ignore a generic (single-colon)
        # marker that lands at the very end of the message.
        doc = "For example, a user turn opens with <|MOM-TURN:user|>"
        self.assertEqual(strip_hallucinated_turns(doc), doc)

    def test_generic_token_with_placeholder_nonce_survives(self):
        # Documenting the *shape* including a placeholder nonce (not the live
        # one) must also survive untouched.
        doc = "Markers look like <|MOM-TURN:user:NONCE|> at runtime."
        self.assertEqual(strip_hallucinated_turns(doc), doc)

    def test_stray_self_wrap_markers_removed_reply_kept(self):
        # If the model wraps its OWN reply in (live, parroted) assistant
        # markers, strip the markers but keep the reply (don't blank the whole
        # response).
        wrapped = wrap_turn("assistant", "My real answer.")
        self.assertEqual(strip_hallucinated_turns(wrapped), "My real answer.")

    def test_trailing_partial_marker_dropped(self):
        # Token-limit truncation cuts a parroted marker mid-nonce; drop the
        # dangling fragment.
        truncated = "My answer ends here.\n" + f"<|MOM-TURN:user:{_NONCE[:4]}"
        self.assertEqual(
            strip_hallucinated_turns(truncated),
            "My answer ends here.\n",
        )

    def test_roundtrip_history_plus_parroted_turn(self):
        # Build a serialized history exactly as the producer would, then
        # simulate the model echoing it and appending a fabricated turn.
        history = (
            wrap_turn("user", "hi") + "\n"
            + wrap_turn("assistant", "hello") + "\n"
            + wrap_turn("user", "what is 2+2?") + "\n"
        )
        model_reply = "4"
        fabricated = "\n" + wrap_turn("user", "what is 3+3?")
        # The consumer only sees the model's reply, not the history prompt.
        # strip_hallucinated_turns leaves trailing whitespace for the caller's
        # .strip() in sanitize_response, so assert on content, not exact bytes.
        result = strip_hallucinated_turns(model_reply + fabricated)
        self.assertNotIn("3+3", result)
        self.assertEqual(result.strip(), model_reply)
        # And the history itself, if it were ever passed through, collapses to
        # empty because it begins with a user turn -- confirming the sentinel is
        # what triggers the cut, not the content.
        self.assertEqual(strip_hallucinated_turns(history), "")


if __name__ == "__main__":
    unittest.main()
