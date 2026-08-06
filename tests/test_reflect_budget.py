"""The nightly reflection must bound its own wall clock.

Per-call timeouts do not bound the run. The Phase 2 empty and format budgets are
independent, so a mixed failure sequence spends both, and Phase 1 is charged on
top with no retry of its own. Driven rather than derived, the ladder reaches 5
attempts, which puts every provider shape over the scheduler's 1800s command
timeout -- single-user installs included. The scheduler's kill is a SIGTERM to
the whole process group, so it lands wherever it lands, including part-way
through MemoryManager.set_model().

The bound here is measured by running the real loop, never by re-deriving the
formula the loop is supposed to implement: a formula in a test is just the same
assumption twice. The cost the driver bills is itself cross-checked against the
attempt and sleep counts, because a driver that silently under-bills passes a
bound it never actually tested.
"""
from __future__ import annotations

import itertools
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep synthetic output out of the real reflection.log

from core.scheduler import DEFAULT_COMMAND_TIMEOUT  # noqa: E402
from utils import reflect  # noqa: E402


class _MM:
    """Minimal MemoryManager stand-in: only the success marker parses."""

    def parse_reflection_output(self, output):
        return ("MODEL", "SUMMARY") if output == "OK" else ("", "")


class _Ladder:
    """Runs the real ladder against a scripted failure sequence, on a fake clock.

    Every attempt is charged its worst-case wire cost and every backoff its real
    sleep, so `spent` is what the loop would take against providers that hold the
    line open until their timeout.
    """

    def __init__(self, seq, cli_timeout, api_timeout, budget=None):
        self.seq = iter(seq)
        self.cli_timeout = cli_timeout
        self.api_timeout = api_timeout
        self.budget = budget
        self.spent = 0.0
        self.calls = 0
        self.sleeps = 0

    def _now(self):
        return self.spent

    def _call(self, prompt):
        self.calls += 1
        kind = next(self.seq, "E")  # past the script, keep returning 0 bytes
        # A refused call costs nothing: mirror the real clamp, which declines to
        # start a call it cannot finish.
        if reflect.budget_remaining() < reflect.MIN_USEFUL_SLICE_SEC:
            return ""
        if kind == "E":
            # CLI burns its budget, then the API fallback burns its own on top.
            self.spent += min(self.cli_timeout, max(0.0, reflect.budget_remaining()))
            self.spent += min(self.api_timeout, max(0.0, reflect.budget_remaining()))
            return ""
        self.spent += min(self.cli_timeout, max(0.0, reflect.budget_remaining()))
        return "OK" if kind == "S" else "UNPARSEABLE"

    def _sleep(self, sec):
        self.sleeps += 1
        self.spent += sec

    def run(self):
        with mock.patch.object(reflect.time, "monotonic", self._now), \
             mock.patch.object(reflect.time, "sleep", self._sleep), \
             mock.patch.object(reflect, "_call_llm_for_phase2", self._call):
            if self.budget is not None:
                reflect.arm_budget(self.budget)
                reflect.arm_user_slice(1)
            else:
                reflect.arm_budget(0)
            return reflect._run_phase2_with_retry("prompt", _MM())

    def cross_check(self, test):
        """The driver must have billed what its own call and sleep counts imply.

        Without this a driver that forgets to charge a call reports a tiny
        number and the bound passes having measured nothing.
        """
        floor = self.sleeps * reflect.PHASE2_RETRY_BACKOFF_SEC
        ceiling = (self.calls * (self.cli_timeout + self.api_timeout)
                   + self.sleeps * reflect.PHASE2_RETRY_BACKOFF_SEC)
        test.assertGreaterEqual(self.spent, floor)
        test.assertLessEqual(self.spent, ceiling)
        if self.calls:
            test.assertGreater(self.spent, 0.0, "driver billed nothing for its calls")


def _worst(cli_timeout, api_timeout, budget=None, maxlen=8):
    """Search every failure sequence for the most expensive path through the loop."""
    worst = None
    for n in range(1, maxlen + 1):
        for combo in itertools.product("EF", repeat=n):
            ladder = _Ladder("".join(combo), cli_timeout, api_timeout, budget)
            ladder.run()
            if worst is None or ladder.spent > worst.spent:
                worst = ladder
    return worst


class BudgetSizingTests(unittest.TestCase):
    def tearDown(self):
        reflect.arm_budget(0)

    def test_budget_stays_under_the_scheduler_ceiling(self):
        """The run budget must leave the scheduler's kill a tail to write in."""
        self.assertLess(
            reflect.REFLECT_BUDGET_SEC, DEFAULT_COMMAND_TIMEOUT,
            "REFLECT_BUDGET_SEC must stay under core.scheduler."
            "DEFAULT_COMMAND_TIMEOUT or the scheduler SIGTERMs mid-write")
        tail = DEFAULT_COMMAND_TIMEOUT - reflect.REFLECT_BUDGET_SEC
        self.assertGreaterEqual(
            tail, 60,
            f"only {tail}s left for the writes and the 30s admin alert that "
            f"follow the last call")

    def test_floor_covers_the_measured_phase2_runtime(self):
        """Starting a call that cannot finish just guarantees a wasted tail."""
        self.assertGreaterEqual(reflect.MIN_USEFUL_SLICE_SEC, 130,
                                "below Phase 2's worst observed clean run (128.2s)")
        self.assertLess(reflect.MIN_USEFUL_SLICE_SEC, reflect.CLAUDE_CLI_TIMEOUT_SEC)


class UnboundedLadderTests(unittest.TestCase):
    """What the ladder costs with no run budget: the shape being fixed."""

    def tearDown(self):
        reflect.arm_budget(0)

    def test_ladder_reaches_five_attempts_not_four(self):
        worst = _worst(reflect.CLAUDE_CLI_TIMEOUT_SEC, 0)
        worst.cross_check(self)
        self.assertEqual(worst.calls, 5,
                         "the empty and format budgets are independent; a mixed "
                         "sequence spends one of each cap's attempts")

    def test_every_provider_shape_overruns_the_scheduler_unbounded(self):
        """Recorded so a future budget change cannot quietly reopen this."""
        for label, api in (("cli-only", 0), ("cli+api-120", 120), ("cli+ollama-300", 300)):
            with self.subTest(shape=label):
                worst = _worst(reflect.CLAUDE_CLI_TIMEOUT_SEC, api)
                worst.cross_check(self)
                phase1 = reflect.CLAUDE_CLI_TIMEOUT_SEC + api  # one call, no retry
                self.assertGreater(worst.spent + phase1, DEFAULT_COMMAND_TIMEOUT)


class BoundedLadderTests(unittest.TestCase):
    """With the budget armed, no failure sequence can outlive it."""

    def tearDown(self):
        reflect.arm_budget(0)

    def test_no_sequence_outlives_the_budget(self):
        for label, api in (("cli-only", 0), ("cli+api-120", 120), ("cli+ollama-300", 300)):
            with self.subTest(shape=label):
                worst = _worst(reflect.CLAUDE_CLI_TIMEOUT_SEC, api,
                               budget=reflect.REFLECT_BUDGET_SEC)
                worst.cross_check(self)
                self.assertLessEqual(worst.spent, reflect.REFLECT_BUDGET_SEC)

    def test_worst_run_plus_every_user_fits_under_the_kill(self):
        """Four slots, one command, one ceiling: the whole queue must fit."""
        worst = _worst(reflect.CLAUDE_CLI_TIMEOUT_SEC, 300,
                       budget=reflect.REFLECT_BUDGET_SEC)
        self.assertLess(worst.spent, DEFAULT_COMMAND_TIMEOUT)

    def test_exhausted_budget_reports_no_llm_not_parse_error(self):
        """Nothing failed to parse, so parse_error would name the wrong cause."""
        ladder = _Ladder("F", reflect.CLAUDE_CLI_TIMEOUT_SEC, 0,
                         budget=reflect.CLAUDE_CLI_TIMEOUT_SEC + 60)
        _content, _summary, failure = ladder.run()
        self.assertEqual(failure, "no_llm")

    def test_format_exhaustion_still_reports_parse_error(self):
        """The pre-budget cap exits are unchanged."""
        ladder = _Ladder("FF", reflect.CLAUDE_CLI_TIMEOUT_SEC, 0)
        _content, _summary, failure = ladder.run()
        self.assertEqual(failure, "parse_error")

    def test_success_still_returns_the_model(self):
        ladder = _Ladder("ES", reflect.CLAUDE_CLI_TIMEOUT_SEC, 0,
                         budget=reflect.REFLECT_BUDGET_SEC)
        content, summary, failure = ladder.run()
        self.assertEqual((content, summary, failure), ("MODEL", "SUMMARY", None))


class ClampTests(unittest.TestCase):
    def tearDown(self):
        reflect.arm_budget(0)

    def test_disarmed_budget_leaves_every_call_at_its_full_timeout(self):
        reflect.arm_budget(0)
        self.assertEqual(reflect.budget_remaining(), float("inf"))
        self.assertEqual(reflect.process_remaining(), float("inf"))
        self.assertEqual(reflect._call_timeout(300.0), 300.0)
        self.assertEqual(reflect._call_timeout(120.0), 120.0)

    def test_a_fresh_import_is_disarmed(self):
        """bot.py imports this module for the intro reflection on a live turn.

        Run in a real subprocess rather than read off the already-imported
        module, whose globals every other test in this file has moved.
        """
        import subprocess
        out = subprocess.run(
            [sys.executable, "-c",
             "import os; os.environ['MOM_TEST']='1';"
             "import sys; sys.path.insert(0, %r);"
             "from utils import reflect;"
             "print(reflect._DEADLINE, reflect._PROCESS_DEADLINE,"
             " reflect.budget_remaining(), reflect._call_timeout(300.0))" % str(ROOT)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, out.stderr[-600:])
        self.assertEqual(out.stdout.strip(), "None None inf 300.0")

    def test_call_timeout_never_exceeds_what_is_left(self):
        reflect.arm_budget(200)
        self.assertLessEqual(reflect._call_timeout(300.0), 200.0)
        self.assertGreater(reflect._call_timeout(300.0), 150.0)

    def test_cli_refuses_to_start_a_call_it_cannot_finish(self):
        reflect.arm_budget(10)
        with mock.patch.object(reflect, "which", return_value="/usr/bin/claude"), \
             mock.patch.object(reflect.subprocess, "run") as run:
            out = reflect._call_claude_cli("prompt")
        self.assertEqual(out, "")
        run.assert_not_called()
        self.assertIn("run budget", reflect._recorded_provider_error())

    def test_cli_passes_the_clamped_timeout_to_subprocess(self):
        reflect.arm_budget(200)
        fake = mock.Mock(returncode=0, stdout="OK", stderr="")
        with mock.patch.object(reflect, "which", return_value="/usr/bin/claude"), \
             mock.patch.object(reflect.subprocess, "run", return_value=fake) as run:
            reflect._call_claude_cli("prompt")
        passed = run.call_args.kwargs["timeout"]
        self.assertLessEqual(passed, 200.0)
        self.assertLess(passed, reflect.CLAUDE_CLI_TIMEOUT_SEC)

    def test_cli_uses_its_full_timeout_when_no_budget_is_armed(self):
        reflect.arm_budget(0)
        fake = mock.Mock(returncode=0, stdout="OK", stderr="")
        with mock.patch.object(reflect, "which", return_value="/usr/bin/claude"), \
             mock.patch.object(reflect.subprocess, "run", return_value=fake) as run:
            reflect._call_claude_cli("prompt")
        self.assertEqual(run.call_args.kwargs["timeout"], reflect.CLAUDE_CLI_TIMEOUT_SEC)

    def test_api_refuses_when_the_budget_cannot_afford_it(self):
        """The API runs after the CLI on the same attempt: unclamped it doubles it."""
        reflect.arm_budget(10)
        with mock.patch.object(reflect, "get_llm_provider", return_value="openai"), \
             mock.patch.object(reflect, "get_llm_model", return_value="gpt-5"), \
             mock.patch.object(reflect, "get_llm_api_key", return_value="k"), \
             mock.patch("httpx.Client") as client:
            out = reflect._call_api("prompt")
        self.assertEqual(out, "")
        client.assert_not_called()

    def test_every_api_timeout_is_clamped_in_source(self):
        """A new provider branch must not reintroduce a hardcoded timeout."""
        src = (ROOT / "utils" / "reflect.py").read_text(encoding="utf-8")
        bare = [ln.strip() for ln in src.splitlines()
                if "httpx.Client(timeout=" in ln and "_call_timeout(" not in ln]
        self.assertEqual(bare, [], f"unclamped httpx timeouts: {bare}")

    def test_backoff_refuses_when_the_retry_could_not_run(self):
        reflect.arm_budget(reflect.MIN_USEFUL_SLICE_SEC + 10)
        self.assertFalse(reflect._budget_backoff(reflect.PHASE2_RETRY_BACKOFF_SEC))

    def test_backoff_sleeps_when_there_is_room(self):
        reflect.arm_budget(reflect.REFLECT_BUDGET_SEC)
        with mock.patch.object(reflect.time, "sleep") as sleep:
            self.assertTrue(reflect._budget_backoff(reflect.PHASE2_RETRY_BACKOFF_SEC))
        sleep.assert_called_once_with(reflect.PHASE2_RETRY_BACKOFF_SEC)

    def test_backoff_always_sleeps_when_no_budget_is_armed(self):
        reflect.arm_budget(0)
        with mock.patch.object(reflect.time, "sleep") as sleep:
            self.assertTrue(reflect._budget_backoff(reflect.PHASE2_RETRY_BACKOFF_SEC))
        sleep.assert_called_once()


class UserSliceTests(unittest.TestCase):
    """One user's worst night must not eat the whole queue's budget."""

    def tearDown(self):
        reflect.arm_budget(0)

    def test_slice_divides_what_is_left_among_the_users_left(self):
        reflect.arm_budget(1000)
        reflect.arm_user_slice(4)
        self.assertAlmostEqual(reflect.budget_remaining(), 250, delta=5)
        self.assertAlmostEqual(reflect.process_remaining(), 1000, delta=5)

    def test_slice_never_outlives_the_process_budget(self):
        reflect.arm_budget(1000)
        for users_left in (1, 2, 3, 4, 10):
            with self.subTest(users_left=users_left):
                reflect.arm_user_slice(users_left)
                self.assertLessEqual(reflect.budget_remaining(),
                                     reflect.process_remaining() + 1)

    def test_a_single_user_gets_the_whole_budget(self):
        reflect.arm_budget(1000)
        reflect.arm_user_slice(1)
        self.assertAlmostEqual(reflect.budget_remaining(), 1000, delta=5)

    def test_slice_is_a_noop_when_no_budget_is_armed(self):
        reflect.arm_budget(0)
        reflect.arm_user_slice(4)
        self.assertEqual(reflect.budget_remaining(), float("inf"))

    def test_zero_users_left_does_not_divide_by_zero(self):
        reflect.arm_budget(1000)
        reflect.arm_user_slice(0)
        self.assertAlmostEqual(reflect.budget_remaining(), 1000, delta=5)


class MainLoopTests(unittest.TestCase):
    """main() must skip a user it cannot serve rather than be killed serving them."""

    def tearDown(self):
        reflect.arm_budget(0)

    def _run_main(self, argv, users, reflect_side_effect):
        mm = mock.Mock()
        mm.get_all_users.return_value = users
        seen = []

        def _reflect(_mm, user_id, *a, **k):
            seen.append(user_id)
            reflect_side_effect(user_id)
            return {"user_id": user_id, "status": "updated", "summary": ""}

        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(reflect, "MemoryManager", return_value=mm), \
             mock.patch.object(reflect, "_warn_if_login_expiring"), \
             mock.patch.object(reflect, "_send_alert") as alert, \
             mock.patch.object(reflect, "run_reflection", side_effect=_reflect):
            reflect.main()
        return seen, alert

    def test_budget_is_armed_before_any_user_is_reflected_on(self):
        armed = []
        seen, _alert = self._run_main(
            ["reflect.py"], [1],
            lambda uid: armed.append(reflect.process_remaining()))
        self.assertEqual(seen, [1])
        self.assertLessEqual(armed[0], reflect.REFLECT_BUDGET_SEC)
        self.assertGreater(armed[0], reflect.REFLECT_BUDGET_SEC - 60)

    def test_users_past_the_budget_are_skipped_and_alerted_not_killed(self):
        def burn(uid):
            if uid == 1:
                reflect.arm_budget(0.0001)  # user 1's bad night eats the run

        seen, alert = self._run_main(["reflect.py"], [1, 2, 3], burn)
        self.assertEqual(seen, [1], "users 2 and 3 must be skipped, not attempted")
        alert.assert_called_once()
        self.assertIn("2 user(s)", alert.call_args.args[0])

    def test_each_user_is_sliced_against_the_users_still_queued(self):
        real = reflect.arm_user_slice
        asked = []

        def spy(users_left):
            asked.append(users_left)
            return real(users_left)

        with mock.patch.object(reflect, "arm_user_slice", spy):
            seen, _alert = self._run_main(["reflect.py"], [1, 2, 3], lambda uid: None)
        self.assertEqual(seen, [1, 2, 3])
        self.assertEqual(asked, [3, 2, 1])

    def test_the_first_user_is_not_handed_the_whole_queue_budget(self):
        """The behavioural half: one bad night must not starve the queue."""
        shares = []
        seen, _alert = self._run_main(
            ["reflect.py"], [1, 2, 3],
            lambda uid: shares.append(reflect.budget_remaining()))
        self.assertEqual(seen, [1, 2, 3])
        self.assertLess(shares[0], reflect.REFLECT_BUDGET_SEC / 2,
                        "user 1 was given the whole run budget to burn")
        self.assertGreater(shares[0], reflect.MIN_USEFUL_SLICE_SEC)

    def test_budget_zero_disarms_for_an_operator_run_by_hand(self):
        seen, _alert = self._run_main(["reflect.py", "--budget", "0"], [1, 2], lambda uid: None)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(reflect.process_remaining(), float("inf"))

    def test_default_budget_matches_the_constant(self):
        parser_default = None
        real_parse = reflect.argparse.ArgumentParser.parse_args

        def capture(self_parser, *a, **k):
            nonlocal parser_default
            ns = real_parse(self_parser, *a, **k)
            parser_default = ns.budget
            return ns

        with mock.patch.object(reflect.argparse.ArgumentParser, "parse_args", capture):
            self._run_main(["reflect.py"], [], lambda uid: None)
        self.assertEqual(parser_default, reflect.REFLECT_BUDGET_SEC)


if __name__ == "__main__":
    unittest.main()
