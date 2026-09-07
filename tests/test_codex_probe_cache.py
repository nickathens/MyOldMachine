"""A codex capability probe that could not run must be asked again.

Two probes stand between the Codex provider and a broken turn:

* `_codex_feature_names` — an unknown `--disable` name is a hard abort with
  no events, on every request, so the names are checked against the build.
* `_codex_accepts_hook_trust_bypass` — the flag that lets this repo's own
  hooks (the resource gate, the usage log, the orphan cleanup) run at all on
  a Codex turn. It was the whole point of the hook-parity port in #156.

Both were memoised with `lru_cache`, which also memoised their FAILURES. One
`OSError`, or one 15-second timeout under boot load, and the empty answer
stood for the life of the process. The consequence is not a stale value, it
is a feature switching itself off with nothing on screen: hooks stop running
on every later Codex turn until somebody restarts the bot.

So a probe that RAN is cached for good, and a probe that could not run is
retried, but no more often than `_CODEX_PROBE_RETRY_AFTER`, because a missing
binary must not put a subprocess spawn on the front of every turn.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import llm  # noqa: E402

BIN = "/usr/bin/codex"
FEATURES = "multi_agent  stable  true\nmulti_agent_v2  stable  false\n"
HELP_WITH_FLAG = "  --dangerously-bypass-hook-trust  Trust hooks\n"


def _done(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([BIN], returncode, stdout, stderr)


class _Runs:
    """A scripted `subprocess.run`, counting how many times it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, cmd, *a, **k):
        self.calls += 1
        answer = (self.answers.pop(0) if self.answers
                  else _done(FEATURES + HELP_WITH_FLAG))
        if isinstance(answer, Exception):
            raise answer
        return answer


class ProbeCacheTests(unittest.TestCase):
    def setUp(self):
        llm._codex_probe_cache_clear()
        self.addCleanup(llm._codex_probe_cache_clear)

    # ── the failures that used to stick ──────────────────────────────

    def test_a_features_probe_that_could_not_run_is_retried(self):
        runs = _Runs(OSError("no such binary"), _done(FEATURES))
        with patch.object(llm.subprocess, "run", runs):
            first = llm._codex_feature_names(BIN)
            self.assertEqual(first, frozenset())
            llm._codex_probe_cache_expire_now()
            second = llm._codex_feature_names(BIN)
        self.assertIn("multi_agent", second)
        self.assertEqual(runs.calls, 2)

    def test_a_timed_out_features_probe_is_retried(self):
        runs = _Runs(subprocess.TimeoutExpired(BIN, 15), _done(FEATURES))
        with patch.object(llm.subprocess, "run", runs):
            self.assertEqual(llm._codex_feature_names(BIN), frozenset())
            llm._codex_probe_cache_expire_now()
            self.assertIn("multi_agent", llm._codex_feature_names(BIN))

    def test_a_hook_trust_probe_that_could_not_run_is_retried(self):
        # The one that matters most: a False here means no hooks run.
        runs = _Runs(OSError("boom"), _done(HELP_WITH_FLAG))
        with patch.object(llm.subprocess, "run", runs):
            self.assertFalse(llm._codex_accepts_hook_trust_bypass(BIN))
            llm._codex_probe_cache_expire_now()
            self.assertTrue(llm._codex_accepts_hook_trust_bypass(BIN))
        self.assertEqual(runs.calls, 2)

    def test_a_nonzero_features_exit_is_retried(self):
        runs = _Runs(_done("error: unrecognized subcommand", returncode=2),
                     _done(FEATURES))
        with patch.object(llm.subprocess, "run", runs):
            self.assertEqual(llm._codex_feature_names(BIN), frozenset())
            llm._codex_probe_cache_expire_now()
            self.assertIn("multi_agent", llm._codex_feature_names(BIN))

    # ── the answers that must still stick ────────────────────────────

    def test_a_real_answer_is_asked_once(self):
        runs = _Runs(_done(FEATURES))
        with patch.object(llm.subprocess, "run", runs):
            for _ in range(5):
                self.assertIn("multi_agent", llm._codex_feature_names(BIN))
        self.assertEqual(runs.calls, 1)

    def test_a_real_answer_survives_the_retry_window(self):
        # Expiry must reach the retryable entries only. A build that really
        # has no such flag is a fact, not a failed ask.
        runs = _Runs(_done("  --other-flag\n"))
        with patch.object(llm.subprocess, "run", runs):
            self.assertFalse(llm._codex_accepts_hook_trust_bypass(BIN))
            llm._codex_probe_cache_expire_now()
            self.assertFalse(llm._codex_accepts_hook_trust_bypass(BIN))
        self.assertEqual(runs.calls, 1, "a real False was asked twice")

    def test_an_empty_but_successful_features_table_is_kept(self):
        # returncode 0 with no rows is an answer, not a failure.
        runs = _Runs(_done(""))
        with patch.object(llm.subprocess, "run", runs):
            self.assertEqual(llm._codex_feature_names(BIN), frozenset())
            llm._codex_probe_cache_expire_now()
            self.assertEqual(llm._codex_feature_names(BIN), frozenset())
        self.assertEqual(runs.calls, 1)

    def test_the_two_probes_do_not_share_a_cache_entry(self):
        runs = _Runs(_done(FEATURES), _done(HELP_WITH_FLAG))
        with patch.object(llm.subprocess, "run", runs):
            self.assertIn("multi_agent", llm._codex_feature_names(BIN))
            self.assertTrue(llm._codex_accepts_hook_trust_bypass(BIN))
        self.assertEqual(runs.calls, 2)

    def test_each_binary_is_probed_separately(self):
        runs = _Runs(_done(FEATURES), _done(""))
        with patch.object(llm.subprocess, "run", runs):
            self.assertIn("multi_agent", llm._codex_feature_names(BIN))
            self.assertEqual(llm._codex_feature_names("/opt/codex"),
                             frozenset())
        self.assertEqual(runs.calls, 2)

    # ── the cache's own shape ────────────────────────────────────────

    def test_a_real_answer_is_stored_as_never_expiring(self):
        with patch.object(llm.subprocess, "run", _Runs(_done(FEATURES))):
            llm._codex_feature_names(BIN)
        expires, _ = llm._codex_probe_cache[("features", BIN)]
        self.assertEqual(expires, math.inf)

    def test_a_failed_answer_is_stored_with_a_finite_window(self):
        with patch.object(llm.subprocess, "run", _Runs(OSError("x"))):
            llm._codex_feature_names(BIN)
        expires, _ = llm._codex_probe_cache[("features", BIN)]
        self.assertTrue(math.isfinite(expires))
        self.assertGreater(llm._CODEX_PROBE_RETRY_AFTER, 0)

    def test_a_failed_probe_is_not_re_asked_inside_the_window(self):
        # The other half of the contract: a missing binary must not put a
        # subprocess spawn on the front of every single turn.
        runs = _Runs(OSError("x"), OSError("x"), OSError("x"))
        with patch.object(llm.subprocess, "run", runs):
            for _ in range(3):
                llm._codex_feature_names(BIN)
        self.assertEqual(runs.calls, 1)

    def test_clear_forgets_real_answers_too(self):
        runs = _Runs(_done(FEATURES), _done(FEATURES))
        with patch.object(llm.subprocess, "run", runs):
            llm._codex_feature_names(BIN)
            llm._codex_probe_cache_clear()
            llm._codex_feature_names(BIN)
        self.assertEqual(runs.calls, 2)


if __name__ == "__main__":
    unittest.main()
