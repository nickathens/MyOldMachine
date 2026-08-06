"""Tests for the Claude CLI call budget in reflect.py.

The nightly reflection died on 2026-08-06 with four consecutive "Claude CLI
timed out (120s)" failures. The calls were not hanging: measured against the
real Phase 2 prompt, claude-opus-5 returned complete output in 120.1s, 123.6s
and 128.2s. The 120s ceiling sat inside that spread, so the job killed its own
work a moment before it landed, and a timeout is indistinguishable from a 0-byte
return so the retry ladder just repeated the near-miss.

These tests pin the three things that fix has to keep true.
"""
import itertools
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make MOM utils importable without spinning up the bot
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ["MOM_TEST"] = "1"  # keep synthetic output out of the real reflection.log

from core.scheduler import DEFAULT_COMMAND_TIMEOUT
from utils import reflect


# Slowest complete Phase 2 run measured on this machine (claude-opus-5,
# 32k-char prompt, 28k-char response). The budget must clear it with room.
MEASURED_WORST_CASE_SEC = 128

# Longest outcome script the ladder can consume. Every attempt spends from one
# of the two budgets, so the loop cannot make more attempts than their sum, and
# a script this long explores every reachable path.
LADDER_SCRIPT_LEN = reflect.PHASE2_MAX_EMPTY_RETRIES + reflect.PHASE2_MAX_FORMAT_RETRIES


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


class RetryLadderBoundsTests(unittest.TestCase):
    """What the ladder costs, measured by running it rather than by a formula.

    Two corrections to the first draft of this guard, both found by driving the
    real loop:

    1. The formula assumed PHASE2_MAX_EMPTY_RETRIES attempts. The two budgets
       are independent, so a run that alternates spends from both: the loop can
       reach empty_cap - 1 empties AND format_cap - 1 format failures and still
       take one more attempt. With 4 and 2 that is 5 attempts, not 4, and the
       formula understated the worst case by a whole call plus a backoff.

    2. It measured against the 03:30 cleanup job, which only deletes
       attachments, rotates logs and prunes the outbox. Colliding with it costs
       nothing. What actually kills a long reflection is the scheduler's own
       ceiling: bot.py registers the nightly job with no timeout_seconds
       override, so core.scheduler SIGTERMs its whole process group at
       DEFAULT_COMMAND_TIMEOUT. That happens to be the same 1800s the first
       draft hardcoded, so this is not a change of number, it is a change of
       source: the bound now tracks the thing that actually does the killing.

    The bound below is the single-user, CLI-only shape: one CLI budget per
    attempt. Two things make a real run cost more, both reported on the PR
    rather than fixed here, and both leave this test on the safe side of the
    line: _call_llm_for_phase2 falls through to _call_api when the CLI returns
    nothing, which adds that provider's httpx timeout to an attempt on an
    install where the fallback is configured; and main() reflects users in
    sequence inside the one command, so N users multiply this figure.
    """

    def _drive(self, outcomes, cost_per_attempt_sec):
        """Run the real ladder against a scripted sequence of call outcomes.

        'E' returns 0 bytes, 'F' returns text the parser rejects. Every attempt
        is charged the full budget, which is what a timed-out call costs, and
        every backoff is charged its sleep. Nothing actually sleeps and nothing
        reaches the log file.

        Returns (attempts_made, simulated_seconds).
        """
        clock = [0.0]
        attempts = [0]
        script = iter(outcomes)

        def fake_call(prompt):
            attempts[0] += 1
            clock[0] += cost_per_attempt_sec
            return "" if next(script, "E") == "E" else "text the parser rejects"

        def fake_sleep(seconds):
            clock[0] += seconds

        mm = MagicMock()
        mm.parse_reflection_output.return_value = ("", "")

        with patch("utils.reflect._call_llm_for_phase2", side_effect=fake_call), \
             patch("utils.reflect.time.sleep", side_effect=fake_sleep), \
             patch("utils.reflect.log"):
            reflect._run_phase2_with_retry("prompt", mm)

        return attempts[0], clock[0]

    def _worst_path(self, cost_per_attempt_sec):
        """Exhaustively search every failure sequence for the costliest path."""
        worst = (0, 0.0, ())
        for outcomes in itertools.product("EF", repeat=LADDER_SCRIPT_LEN):
            attempts, seconds = self._drive(outcomes, cost_per_attempt_sec)
            if seconds > worst[1]:
                worst = (attempts, seconds, outcomes[:attempts])
        return worst

    def test_retry_ladder_stays_inside_the_scheduler_kill(self):
        """Raising CLAUDE_CLI_TIMEOUT_SEC must stay cheap enough to survive the job."""
        attempts, seconds, path = self._worst_path(reflect.CLAUDE_CLI_TIMEOUT_SEC)

        self.assertLess(
            seconds, DEFAULT_COMMAND_TIMEOUT,
            f"worst path is {''.join(path)}: {attempts} attempts, "
            f"{seconds / 60:.1f} min. The scheduler SIGTERMs a command job's "
            f"process group at {DEFAULT_COMMAND_TIMEOUT / 60:.0f} min, so the "
            "reflection would be killed mid-run")

    def test_driver_charges_every_call_and_every_backoff(self):
        """The clock is only worth trusting if it bills what the loop spends.

        A cross-check, not a second bound. The attempt count still comes from
        driving the real loop; this pins what each of those attempts costs.
        Without it, a driver that quietly stopped charging calls or backoffs
        would leave the bound above passing on a number far below the truth,
        which is the same shape of false pass as the formula it replaced.
        """
        attempts, seconds, _ = self._worst_path(reflect.CLAUDE_CLI_TIMEOUT_SEC)

        expected = (attempts * reflect.CLAUDE_CLI_TIMEOUT_SEC
                    + (attempts - 1) * reflect.PHASE2_RETRY_BACKOFF_SEC)
        self.assertEqual(
            seconds, expected,
            "on the worst path every attempt burns the full budget and every "
            "attempt but the last is followed by a backoff")

    def test_worst_path_spends_from_both_budgets(self):
        """The costliest path is longer than either budget alone allows.

        This is the arithmetic the first draft's formula got wrong. It is
        asserted here so the bound above cannot quietly go back to counting
        one budget's worth of attempts.
        """
        attempts, _, path = self._worst_path(reflect.CLAUDE_CLI_TIMEOUT_SEC)

        self.assertEqual(
            attempts,
            reflect.PHASE2_MAX_EMPTY_RETRIES + reflect.PHASE2_MAX_FORMAT_RETRIES - 1,
            f"worst path {''.join(path)} made {attempts} attempts; the loop "
            "exits only once one budget is exhausted, so the worst case is "
            "(empty_cap - 1) + (format_cap - 1) + 1")


if __name__ == "__main__":
    unittest.main()
