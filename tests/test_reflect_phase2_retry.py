"""Tests for Phase 2 dual-budget retry logic in reflect.py.

Covers the 0-byte transient burp scenario that previously caused parse_error
on the very first failure. The retry loop distinguishes:
  - 0-byte returns from the provider chain (empty budget, 4 retries)
  - Non-empty but unparseable returns (format budget, 2 retries)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make MOM utils importable without spinning up the bot
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import reflect


def _mock_mm(parse_results):
    """Build a MemoryManager mock whose parse_reflection_output yields the given results.

    parse_results is a list of (model_content, summary) tuples consumed in order.
    """
    mm = MagicMock()
    mm.parse_reflection_output.side_effect = parse_results
    return mm


class Phase2RetryTests(unittest.TestCase):

    def setUp(self):
        # Patch time.sleep to keep tests fast (no real 30s waits)
        patcher_sleep = patch("utils.reflect.time.sleep")
        self.mock_sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

        # Patch the underlying provider chain so we control return values precisely
        patcher_call = patch("utils.reflect._call_llm_for_phase2")
        self.mock_call = patcher_call.start()
        self.addCleanup(patcher_call.stop)

    # ─── Happy path ─────────────────────────────────────────────

    def test_immediate_success(self):
        self.mock_call.side_effect = ["MODEL_OUTPUT"]
        mm = _mock_mm([("model body", "summary body")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "model body")
        self.assertEqual(summary, "summary body")
        self.assertIsNone(failure)
        self.assertEqual(self.mock_call.call_count, 1)
        self.mock_sleep.assert_not_called()

    # ─── Empty-budget recovery ──────────────────────────────────

    def test_recovers_from_single_empty(self):
        self.mock_call.side_effect = ["", "MODEL_OUTPUT"]
        mm = _mock_mm([("model body", "summary body")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "model body")
        self.assertEqual(summary, "summary body")
        self.assertIsNone(failure)
        self.assertEqual(self.mock_call.call_count, 2)
        # Backoff fired once between the empty and the recovery
        self.assertEqual(self.mock_sleep.call_count, 1)
        self.mock_sleep.assert_called_with(reflect.PHASE2_RETRY_BACKOFF_SEC)

    def test_recovers_from_three_empties_within_budget(self):
        self.mock_call.side_effect = ["", "", "", "MODEL_OUTPUT"]
        mm = _mock_mm([("model body", "summary body")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "model body")
        self.assertIsNone(failure)
        self.assertEqual(self.mock_call.call_count, 4)
        # Three empties → three backoffs between them; success on attempt 4 → no final backoff
        self.assertEqual(self.mock_sleep.call_count, 3)

    def test_gives_up_after_max_empties(self):
        # 4 empties in a row exhausts PHASE2_MAX_EMPTY_RETRIES (=4)
        self.mock_call.side_effect = ["", "", "", ""]
        mm = _mock_mm([])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "")
        self.assertEqual(summary, "")
        self.assertEqual(failure, "no_llm")
        self.assertEqual(self.mock_call.call_count, reflect.PHASE2_MAX_EMPTY_RETRIES)
        # Backoff between the first three failures, then loop exits without sleeping
        self.assertEqual(self.mock_sleep.call_count, reflect.PHASE2_MAX_EMPTY_RETRIES - 1)
        mm.parse_reflection_output.assert_not_called()

    # ─── Format-budget recovery ─────────────────────────────────

    def test_recovers_from_single_format_failure(self):
        self.mock_call.side_effect = ["UNPARSEABLE", "MODEL_OUTPUT"]
        mm = _mock_mm([("", ""), ("model body", "summary body")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "model body")
        self.assertEqual(summary, "summary body")
        self.assertIsNone(failure)
        self.assertEqual(self.mock_call.call_count, 2)
        self.assertEqual(mm.parse_reflection_output.call_count, 2)
        self.assertEqual(self.mock_sleep.call_count, 1)

    def test_gives_up_after_max_format_failures(self):
        # 2 unparseable returns exhausts PHASE2_MAX_FORMAT_RETRIES (=2)
        self.mock_call.side_effect = ["UNPARSEABLE1", "UNPARSEABLE2"]
        mm = _mock_mm([("", ""), ("", "")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "")
        self.assertEqual(failure, "parse_error")
        self.assertEqual(self.mock_call.call_count, reflect.PHASE2_MAX_FORMAT_RETRIES)
        # Backoff once between the two format failures
        self.assertEqual(self.mock_sleep.call_count, reflect.PHASE2_MAX_FORMAT_RETRIES - 1)

    # ─── Mixed scenarios ────────────────────────────────────────

    def test_empty_then_format_then_success(self):
        self.mock_call.side_effect = ["", "UNPARSEABLE", "MODEL_OUTPUT"]
        mm = _mock_mm([("", ""), ("model body", "summary body")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "model body")
        self.assertIsNone(failure)
        self.assertEqual(self.mock_call.call_count, 3)
        # parse_reflection_output skipped on the empty turn, called twice afterward
        self.assertEqual(mm.parse_reflection_output.call_count, 2)
        # Two backoffs (after empty, after format), success on attempt 3 → no final backoff
        self.assertEqual(self.mock_sleep.call_count, 2)

    def test_empty_then_format_then_format_exhausts_budget(self):
        # 1 empty, 2 format failures → format budget hits 2, gives up as parse_error
        self.mock_call.side_effect = ["", "UNPARSEABLE1", "UNPARSEABLE2"]
        mm = _mock_mm([("", ""), ("", "")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "")
        self.assertEqual(failure, "parse_error")
        self.assertEqual(self.mock_call.call_count, 3)

    def test_format_then_empty_then_success(self):
        self.mock_call.side_effect = ["UNPARSEABLE", "", "MODEL_OUTPUT"]
        mm = _mock_mm([("", ""), ("model body", "summary body")])

        model, summary, failure = reflect._run_phase2_with_retry("prompt", mm)

        self.assertEqual(model, "model body")
        self.assertIsNone(failure)
        self.assertEqual(self.mock_call.call_count, 3)


class Phase2HelperTests(unittest.TestCase):
    """Smoke test for _call_llm_for_phase2 — verifies CLI → API fallback chain."""

    def test_returns_cli_output_when_present(self):
        with patch("utils.reflect._call_claude_cli", return_value="cli_output"), \
             patch("utils.reflect._call_api") as mock_api:
            result = reflect._call_llm_for_phase2("prompt")
        self.assertEqual(result, "cli_output")
        mock_api.assert_not_called()

    def test_falls_back_to_api_when_cli_empty(self):
        with patch("utils.reflect._call_claude_cli", return_value=""), \
             patch("utils.reflect._call_api", return_value="api_output") as mock_api:
            result = reflect._call_llm_for_phase2("prompt")
        self.assertEqual(result, "api_output")
        mock_api.assert_called_once_with("prompt")

    def test_returns_empty_when_both_fail(self):
        with patch("utils.reflect._call_claude_cli", return_value=""), \
             patch("utils.reflect._call_api", return_value=""):
            result = reflect._call_llm_for_phase2("prompt")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
