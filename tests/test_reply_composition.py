"""Unit tests for core.llm._compose_full_reply.

Run: python3 -m unittest tests.test_reply_composition  (from repo root)
  or: python3 tests/test_reply_composition.py

Regression coverage for the message-truncation bug: the stream-json "result"
field contains ONLY the last assistant turn (text after the final tool call),
so any narration the model wrote BEFORE a tool call was silently dropped from
what the user received. _compose_full_reply returns the full ordered narration
(accumulated in partial_text) whenever it is a strict superset of the result
field, otherwise it returns the result field unchanged.

Requiring the superset relation gives a strong invariant: the returned text
always contains final_result. A partial_text that does not contain the result
(for example a short confabulated preamble) is never emitted in its place.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import _compose_full_reply  # noqa: E402


class ComposeFullReplyTests(unittest.TestCase):
    def test_pre_tool_narration_preserved(self):
        # The bug: model narrated, called a tool, then wrote a final line.
        # result field = post-tool text only; partial_text has the full order.
        final_result = "TAILZZZ"
        partial_text = "BODYAAA\nTAILZZZ\n"
        out = _compose_full_reply(final_result, partial_text)
        self.assertIn("BODYAAA", out, "pre-tool narration must survive")
        self.assertIn("TAILZZZ", out, "post-tool text must survive")
        self.assertEqual(out, "BODYAAA\nTAILZZZ")

    def test_multiple_tool_roundtrips_preserve_all_text(self):
        # text1 -> tool -> text2 -> tool -> text3 : result = text3 only.
        final_result = "text3"
        partial_text = "text1\ntext2\ntext3\n"
        out = _compose_full_reply(final_result, partial_text)
        self.assertEqual(out, "text1\ntext2\ntext3")

    def test_no_pre_tool_text_returns_result_unchanged(self):
        # Common case: single turn, no tool calls. partial == result.
        final_result = "HELLO"
        partial_text = "HELLO\n"
        self.assertEqual(_compose_full_reply(final_result, partial_text), "HELLO")

    def test_empty_partial_falls_back_to_result(self):
        final_result = "HELLO"
        partial_text = ""
        self.assertEqual(_compose_full_reply(final_result, partial_text), "HELLO")

    def test_non_superset_partial_falls_back_to_clean_result(self):
        # If the accumulation does not contain the result field (e.g. a short
        # confabulated preamble that was later superseded), prefer the clean
        # result rather than emitting text that omits the real answer.
        final_result = "Here is the real answer to your question."
        partial_text = "already handled\n"
        self.assertEqual(
            _compose_full_reply(final_result, partial_text), final_result
        )

    def test_returned_text_always_contains_result(self):
        # Core invariant: whatever path is taken, the result is never lost.
        cases = [
            ("TAIL", "BODY\nTAIL\n"),       # superset -> full text
            ("ONLY", "ONLY\n"),             # equal -> result
            ("X", ""),                      # empty partial -> result
            ("REAL", "stale preamble\n"),   # non-superset -> result
        ]
        for final_result, partial_text in cases:
            out = _compose_full_reply(final_result, partial_text)
            self.assertIn(final_result, out)


if __name__ == "__main__":
    unittest.main()
