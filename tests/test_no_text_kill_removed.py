"""Regression: the 3-minute no-text kill stays removed from both CLI providers.

Run: python3 -m unittest tests.test_no_text_kill_removed  (from repo root)
  or: python3 tests/test_no_text_kill_removed.py

The no-text timeout killed any tool run that produced no user-facing text
for 180s. On modest hardware, stem separation, transcription, builds, and
large downloads routinely run silent for longer than that while healthy,
so the timeout killed legitimate work rather than stalls. It was removed
from the Claude provider when tool detection was fixed (it had been inert
there), and from the Codex provider, where detection worked and the kill
was live. The surviving safety nets are the idle timeout, the absolute
cap, and the tool-named progress reports.

These tests pin that decision: reintroducing a NO_TEXT_TIMEOUT must be
deliberate, not accidental.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make the project root importable when tests run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import ClaudeCLIProvider, CodexCLIProvider  # noqa: E402


class TestNoTextKillRemoved(unittest.TestCase):
    def test_codex_has_no_no_text_timeout(self):
        self.assertFalse(
            hasattr(CodexCLIProvider, "NO_TEXT_TIMEOUT"),
            "NO_TEXT_TIMEOUT was deliberately removed; see module docstring",
        )

    def test_claude_has_no_no_text_timeout(self):
        self.assertFalse(
            hasattr(ClaudeCLIProvider, "NO_TEXT_TIMEOUT"),
            "NO_TEXT_TIMEOUT was deliberately removed; see module docstring",
        )

    def test_codex_surviving_safety_nets(self):
        self.assertEqual(CodexCLIProvider.IDLE_TIMEOUT, 1800)
        self.assertEqual(CodexCLIProvider.ABSOLUTE_TIMEOUT, 7200)
        # Flat 10-minute progress cadence (no front-loaded burst); the user found
        # early/frequent heartbeats more annoying than a quiet gap.
        self.assertEqual(CodexCLIProvider.PROGRESS_INTERVAL, 600)

    def test_claude_surviving_safety_nets(self):
        self.assertEqual(ClaudeCLIProvider.IDLE_TIMEOUT, 1800)
        self.assertEqual(ClaudeCLIProvider.ABSOLUTE_TIMEOUT, 7200)
        # Flat 10-minute progress cadence (no front-loaded burst); the user found
        # early/frequent heartbeats more annoying than a quiet gap.
        self.assertEqual(ClaudeCLIProvider.PROGRESS_INTERVAL, 600)


if __name__ == "__main__":
    unittest.main()
