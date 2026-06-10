"""Unit tests for core.llm._extract_tool_activity.

Run: python3 -m unittest tests.test_tool_activity  (from repo root)
  or: python3 tests/test_tool_activity.py

Regression coverage for the inert tool-progress tracking in the Claude CLI
provider: the stream parser waited for top-level "tool_use" / "tool_result"
event types that the claude CLI never emits, so tool_in_progress stayed None
forever. Progress reports never named the running tool and timeout messages
never said what was running. Tool calls actually arrive as tool_use content
blocks INSIDE "assistant" events, and tool results as tool_result blocks
INSIDE "user" events.

Verified live (2026-06-10) against claude CLI v2.1.169 with the provider's
exact spawn flags. Top-level event types observed in the stream: system,
assistant, user, result, rate_limit_event. The fixtures below encode the
captured shapes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import _extract_tool_activity  # noqa: E402


def _assistant_event(blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def _user_event(content):
    return {"type": "user", "message": {"role": "user", "content": content}}


class ExtractToolActivityTests(unittest.TestCase):
    def test_tool_use_block_inside_assistant_event(self):
        # The shape captured live: thinking block, then the tool_use block
        event = _assistant_event([
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "id": "toolu_01", "name": "Bash",
             "input": {"command": "date"}},
        ])
        names, finished = _extract_tool_activity(event)
        self.assertEqual(names, ["Bash"])
        self.assertFalse(finished)

    def test_multiple_parallel_tool_calls(self):
        event = _assistant_event([
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}},
        ])
        names, _ = _extract_tool_activity(event)
        self.assertEqual(names, ["Read", "Bash"])

    def test_text_only_assistant_event(self):
        names, finished = _extract_tool_activity(
            _assistant_event([{"type": "text", "text": "hi"}])
        )
        self.assertEqual(names, [])
        self.assertFalse(finished)

    def test_tool_result_inside_user_event(self):
        event = _user_event(
            [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]
        )
        names, finished = _extract_tool_activity(event)
        self.assertEqual(names, [])
        self.assertTrue(finished)

    def test_plain_string_user_content(self):
        names, finished = _extract_tool_activity(_user_event("hello"))
        self.assertEqual(names, [])
        self.assertFalse(finished)

    def test_top_level_tool_use_type_never_matches(self):
        # The OLD parser waited for this shape; the CLI never emits it.
        # The helper must treat it as no activity rather than blow up.
        names, finished = _extract_tool_activity({"type": "tool_use", "name": "Bash"})
        self.assertEqual(names, [])
        self.assertFalse(finished)

    def test_unnamed_tool_use_block_gets_placeholder(self):
        names, _ = _extract_tool_activity(_assistant_event([{"type": "tool_use"}]))
        self.assertEqual(names, ["tool"])

    def test_non_message_event_types(self):
        for etype in ("result", "system", "rate_limit_event"):
            names, finished = _extract_tool_activity({"type": etype})
            self.assertEqual(names, [], etype)
            self.assertFalse(finished, etype)

    def test_malformed_message_shapes(self):
        for event in (
            {"type": "assistant"},                                   # no message
            {"type": "assistant", "message": {"content": None}},     # content None
            {"type": "assistant", "message": {"content": [None, "x", 7]}},  # junk
            {"type": "user", "message": {}},                         # no content
        ):
            names, finished = _extract_tool_activity(event)
            self.assertEqual(names, [])
            self.assertFalse(finished)


if __name__ == "__main__":
    unittest.main()
