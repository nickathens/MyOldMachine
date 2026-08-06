"""Tests for Claude CLI cap detection in reflect.py.

A capped Claude CLI prints its usage/spend-limit notice to stdout and exits 0.
Before this fix, reflect.py returned that notice as if it were reflection output,
which poisoned the person model and burned the retry budget re-calling a CLI that
could not answer (it never failed over to the API provider). These tests cover:

  - _is_cap_message recognises the real cap formats and rejects narrative text
  - _call_claude_cli returns "" (not the cap text) when the CLI is capped
  - the phase-2 provider chain fails over to the API when the CLI is capped
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make MOM utils importable without spinning up the bot
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ["MOM_TEST"] = "1"  # keep synthetic output out of the real reflection.log

from utils import reflect

# The exact final-result formats the Claude CLI emits when capped (U+00B7 middot).
CAP_MESSAGES = [
    "You've hit your limit · resets 4:30pm (Europe/Athens)",
    "You've hit your weekly limit · resets 9am (Europe/Athens)",
    "You've hit your session limit · resets 10:10pm (Europe/Athens)",
    "You've hit your fast limit · resets in 2h 30m",
    "You've hit your monthly spend limit · raise it at claude.ai/settings/usage",
]


class IsCapMessageTests(unittest.TestCase):

    def test_real_cap_formats_detected(self):
        for msg in CAP_MESSAGES:
            self.assertTrue(reflect._is_cap_message(msg), f"should detect: {msg!r}")

    def test_narrative_mentioning_limit_is_not_a_cap(self):
        # A genuine reflection response can discuss limits without being a cap.
        narrative = (
            "Nick has hit a limit on how much he trusts unverified claims; when the "
            "machine hit your typical failure mode he wanted proof. There is no reset "
            "here, just a behavioural pattern. " * 5
        )
        self.assertFalse(reflect._is_cap_message(narrative))

    def test_anchor_without_corroborating_clause_is_not_a_cap(self):
        # Anchor present but no reset time / duration / spend marker.
        self.assertFalse(reflect._is_cap_message("you've hit your limit somehow today"))

    def test_over_length_cap_text_is_rejected(self):
        # Real cap lines are short; a huge blob that happens to contain the anchor
        # and a reset clause is narrative, not a cap notice.
        blob = "x" * 500 + CAP_MESSAGES[0]
        self.assertFalse(reflect._is_cap_message(blob))

    def test_empty_and_non_string_inputs(self):
        self.assertFalse(reflect._is_cap_message(""))
        self.assertFalse(reflect._is_cap_message(None))


def _completed(returncode=0, stdout="", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class CallClaudeCliCapTests(unittest.TestCase):
    """The CLI wrapper must not pass a cap notice off as reflection output."""

    def test_capped_cli_returns_empty(self):
        for msg in CAP_MESSAGES:
            with patch("utils.reflect.which", return_value="/usr/bin/claude"), \
                 patch("utils.reflect.get_llm_model", return_value="claude-sonnet-4-6"), \
                 patch("utils.reflect.subprocess.run", return_value=_completed(0, msg)):
                self.assertEqual(reflect._call_claude_cli("prompt"), "",
                                 f"cap text leaked as output: {msg!r}")

    def test_normal_output_passes_through(self):
        with patch("utils.reflect.which", return_value="/usr/bin/claude"), \
             patch("utils.reflect.get_llm_model", return_value="claude-sonnet-4-6"), \
             patch("utils.reflect.subprocess.run",
                   return_value=_completed(0, "## MODEL\nreal reflection output")):
            self.assertEqual(reflect._call_claude_cli("prompt"),
                             "## MODEL\nreal reflection output")


class Phase2FailoverTests(unittest.TestCase):
    """End-to-end: a capped CLI must let the phase-2 chain reach the API."""

    def test_capped_cli_fails_over_to_api(self):
        with patch("utils.reflect.which", return_value="/usr/bin/claude"), \
             patch("utils.reflect.get_llm_model", return_value="claude-sonnet-4-6"), \
             patch("utils.reflect.subprocess.run",
                   return_value=_completed(0, CAP_MESSAGES[4])), \
             patch("utils.reflect._call_api", return_value="api_output") as mock_api:
            result = reflect._call_llm_for_phase2("prompt")
        self.assertEqual(result, "api_output")
        mock_api.assert_called_once_with("prompt")


if __name__ == "__main__":
    unittest.main()
