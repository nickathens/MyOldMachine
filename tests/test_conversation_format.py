"""Unit tests for core.conversation_format.

Covers the sentinel turn delimiter that replaced readable ``<user>`` / ``User:``
tags in the CLI provider prompts:
- wrap_turn() serialization shape
- strip_hallucinated_turns() trims a fabricated next user turn
- legitimate content that merely contains "User:" / "<user>" / "user:" survives
  (the regression the sentinel exists to prevent)
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


class WrapTurnTests(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(
            wrap_turn("user", "hello"),
            "<|MOM-TURN:user|>hello<|MOM-TURN:/user|>",
        )

    def test_user_open_marker_matches_sentinel(self):
        # A wrapped user turn must begin with the exact sentinel the consumer
        # detects, otherwise producer and consumer have drifted.
        self.assertTrue(wrap_turn("user", "x").startswith(NEXT_TURN_SENTINEL))

    def test_content_with_special_chars_is_not_escaped(self):
        body = "code: a < b and x | y"
        self.assertEqual(
            wrap_turn("assistant", body),
            f"<|MOM-TURN:assistant|>{body}<|MOM-TURN:/assistant|>",
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

    def test_stray_self_wrap_markers_removed_reply_kept(self):
        # If the model wraps its OWN reply in assistant markers, strip the
        # markers but keep the reply (don't blank the whole response).
        wrapped = "<|MOM-TURN:assistant|>My real answer.<|MOM-TURN:/assistant|>"
        self.assertEqual(strip_hallucinated_turns(wrapped), "My real answer.")

    def test_trailing_partial_marker_dropped(self):
        truncated = "My answer ends here.\n<|MOM-TURN:us"
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
