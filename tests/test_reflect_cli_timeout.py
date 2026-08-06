"""Tests for the Claude CLI call budget in reflect.py.

The nightly reflection died on 2026-08-06 with four consecutive "Claude CLI
timed out (120s)" failures. The calls were not hanging: measured against the
real Phase 2 prompt, claude-opus-5 returned complete output in 120.1s, 123.6s
and 128.2s. The 120s ceiling sat inside that spread, so the job killed its own
work a moment before it landed, and a timeout is indistinguishable from a 0-byte
return so the retry ladder just repeated the near-miss.

These tests pin the three things that fix has to keep true.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make MOM utils importable without spinning up the bot
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import reflect


# Slowest complete Phase 2 run measured on this machine (claude-opus-5,
# 32k-char prompt, 28k-char response). The budget must clear it with room.
MEASURED_WORST_CASE_SEC = 128


def _cli_result(stdout="output", returncode=0, stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class CliTimeoutBudgetTests(unittest.TestCase):

    def setUp(self):
        patcher_which = patch("utils.reflect.which", return_value="/usr/bin/claude")
        patcher_which.start()
        self.addCleanup(patcher_which.stop)

        patcher_model = patch("utils.reflect.get_llm_model", return_value="claude-opus-5")
        patcher_model.start()
        self.addCleanup(patcher_model.stop)

    def test_budget_clears_the_measured_worst_case(self):
        """A budget at or below the measured spread cancels work that would land."""
        self.assertGreater(reflect.CLAUDE_CLI_TIMEOUT_SEC, MEASURED_WORST_CASE_SEC * 2,
                           "CLI budget must keep real headroom over the slowest "
                           "measured Phase 2 run, not sit inside its spread")

    def test_call_uses_the_configured_budget(self):
        with patch("utils.reflect.subprocess.run",
                   return_value=_cli_result()) as mock_run:
            reflect._call_claude_cli("prompt")

        # .get, not [], so a dropped kwarg fails with a readable assertion
        # instead of a KeyError that reads like a broken test.
        self.assertEqual(mock_run.call_args.kwargs.get("timeout"),
                         reflect.CLAUDE_CLI_TIMEOUT_SEC)

    def test_call_closes_stdin(self):
        """Scheduled runs inherit a stdin the CLI waits 3s on before proceeding."""
        with patch("utils.reflect.subprocess.run",
                   return_value=_cli_result()) as mock_run:
            reflect._call_claude_cli("prompt")

        self.assertEqual(mock_run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_timeout_message_reports_the_real_budget(self):
        """The admin alert quotes this string; a stale literal misreports the cause."""
        with patch("utils.reflect.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1)), \
             patch("utils.reflect.log"):
            output = reflect._call_claude_cli("prompt")

        self.assertEqual(output, "")
        self.assertIn(f"({reflect.CLAUDE_CLI_TIMEOUT_SEC}s)",
                      reflect._recorded_provider_error())

    def test_retry_ladder_stays_bounded(self):
        """Worst case must not run long enough to collide with the 03:30 cleanup job."""
        worst_case_sec = (
            reflect.CLAUDE_CLI_TIMEOUT_SEC * reflect.PHASE2_MAX_EMPTY_RETRIES
            + reflect.PHASE2_RETRY_BACKOFF_SEC * (reflect.PHASE2_MAX_EMPTY_RETRIES - 1)
        )
        self.assertLess(worst_case_sec, 30 * 60,
                        "Phase 2 retry ladder must finish inside the 30-minute "
                        "window between the 03:00 reflection and 03:30 cleanup")


if __name__ == "__main__":
    unittest.main()
