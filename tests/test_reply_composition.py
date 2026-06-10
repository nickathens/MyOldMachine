"""Unit tests for core.llm._compose_full_reply.

Run: python3 -m unittest tests.test_reply_composition  (from repo root)
  or: python3 tests/test_reply_composition.py

History of the behavior under test:
1. Original bug: stream-json's "result" field contains ONLY the last assistant
   turn (text after the final tool call), so anything written BEFORE a tool
   call was silently dropped (a full deliverable was lost to a trailing
   tool call).
2. First fix overcorrected: returning EVERY accumulated text block glued all
   working narration ("Reading the file...", "Tests green. Committing:") onto
   the final answer, producing unreadable walls of text.
3. Middle ground (current): keep the clean result, prepend only pre-tool
   blocks long enough to be deliverables (>= _DELIVERABLE_MIN_CHARS), skip
   blocks already contained in the result. The composed reply always ends
   with the result, so the real answer is never lost or replaced.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import _DELIVERABLE_MIN_CHARS, _compose_full_reply  # noqa: E402

# A realistic deliverable-sized block (scope document, report section).
DELIVERABLE = (
    "## Migration plan\n\n"
    "Step one inventories every call site and records its current behavior. "
    "Step two introduces the new interface behind a feature flag so both "
    "paths stay comparable. Step three flips the default and watches logs. "
) * 4
assert len(DELIVERABLE) >= _DELIVERABLE_MIN_CHARS


class ComposeFullReplyTests(unittest.TestCase):
    def test_long_pre_tool_deliverable_preserved(self):
        # The original truncation bug: deliverable -> tool call -> short
        # sign-off. The deliverable must survive.
        final_result = "Plan delivered above, your call on step one."
        blocks = [DELIVERABLE, final_result]
        out = _compose_full_reply(final_result, blocks)
        self.assertIn("feature flag", out, "deliverable must survive")
        self.assertTrue(out.endswith(final_result))

    def test_short_narration_dropped(self):
        # The overcorrection: working notes must NOT reach the user.
        final_result = "All done. The fix is committed and tests pass."
        blocks = [
            "Reading the stream parser before editing.",
            "Edits in. Running the tests now.",
            "9/9 green. Committing:",
            final_result,
        ]
        out = _compose_full_reply(final_result, blocks)
        self.assertEqual(out, final_result)

    def test_mixed_narration_and_deliverable(self):
        # Narration dropped, deliverable kept, result last.
        final_result = "That is the full plan. Want me to start?"
        blocks = [
            "Checking the docs first.",
            DELIVERABLE,
            "Saving a note about this.",
            final_result,
        ]
        out = _compose_full_reply(final_result, blocks)
        self.assertNotIn("Checking the docs", out)
        self.assertNotIn("Saving a note", out)
        self.assertIn("feature flag", out)
        self.assertTrue(out.endswith(final_result))
        self.assertEqual(out, DELIVERABLE.strip() + "\n\n" + final_result)

    def test_no_tool_calls_returns_result_unchanged(self):
        # Common case: single turn, the only block IS the result.
        final_result = "HELLO"
        self.assertEqual(_compose_full_reply(final_result, ["HELLO"]), "HELLO")

    def test_empty_blocks_falls_back_to_result(self):
        self.assertEqual(_compose_full_reply("HELLO", []), "HELLO")

    def test_final_turn_block_not_duplicated(self):
        # A long block that is PART of the result (the final turn itself)
        # must not be prepended again.
        final_result = "Intro paragraph.\n\n" + DELIVERABLE
        blocks = ["Short narration.", DELIVERABLE, final_result]
        out = _compose_full_reply(final_result, blocks)
        self.assertEqual(out.count("feature flag"), final_result.count("feature flag"))

    def test_result_always_contained(self):
        # Whatever path is taken, the returned text must contain the result.
        # This preserves the invariant the prior superset check guaranteed: a
        # stray pre-tool block is never emitted IN PLACE of the real answer.
        for final_result, blocks in [
            ("TAIL", [DELIVERABLE, "TAIL"]),
            ("ONLY", ["ONLY"]),
            ("X", []),
            ("end", ["note one.", "note two.", "end"]),
        ]:
            out = _compose_full_reply(final_result, blocks)
            self.assertIn(final_result, out)
            self.assertTrue(out.endswith(final_result))

    def test_threshold_boundary(self):
        final_result = "done"
        just_under = "a" * (_DELIVERABLE_MIN_CHARS - 1)
        just_over = "b" * _DELIVERABLE_MIN_CHARS
        self.assertEqual(
            _compose_full_reply(final_result, [just_under, final_result]), final_result
        )
        out = _compose_full_reply(final_result, [just_over, final_result])
        self.assertIn(just_over, out)
        self.assertTrue(out.endswith(final_result))


if __name__ == "__main__":
    unittest.main()
