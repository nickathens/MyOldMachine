"""Usage accounting: the per-person ledger and the two subscription meters.

The traps this file exists to hold shut:

1. **A dollar figure that is not a bill.** Claude Code's ``total_cost_usd`` is
   list-price accounting (the CLI tags the same number ``costBasis: "list"``).
   It is stored and displayed as ``list_cost_usd`` so it can never be read as
   a charge against a subscription nobody is billed per turn for.

2. **A meter that reads zero when it means "no idea".** Both meters report
   unavailable WITH a reason rather than an empty bar.

3. **Closing stdin kills the Codex read.** ``subprocess.communicate`` closes
   the pipe as soon as it has written, and the app-server then exits cleanly
   with rc 0 and no output, which looks exactly like an account with no
   limits. Measured against the real 0.154.0 binary; the fake server below
   reproduces it.

4. **A utilization is a fraction.** Claude reports 0.05 for 5%; the CLI itself
   renders it with ``Math.round(utilization * 100)``. Storing it as a percent
   would understate every window by a factor of a hundred.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import usage, users  # noqa: E402


class _TempTree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-usage-"))
        self._saved_users = users.USERS_DATA_DIR
        self._saved_limits = usage.CLAUDE_LIMITS_FILE
        self._saved_usage_dir = usage.USAGE_DIR
        users.USERS_DATA_DIR = self.tmp / "users"
        usage.USAGE_DIR = self.tmp / "usage"
        usage.CLAUDE_LIMITS_FILE = usage.USAGE_DIR / "claude_rate_limits.json"
        usage.codex_cache_clear()

    def tearDown(self):
        users.USERS_DATA_DIR = self._saved_users
        usage.USAGE_DIR = self._saved_usage_dir
        usage.CLAUDE_LIMITS_FILE = self._saved_limits
        usage.codex_cache_clear()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class LedgerTests(_TempTree):
    def test_a_turn_is_recorded_and_summed(self):
        usage.record_turn(7, provider="claude-cli", model="claude-opus-5",
                          effort="max", engine="opus", input_tokens=10,
                          output_tokens=39, cache_read_tokens=13607,
                          cache_creation_tokens=7368, list_cost_usd=0.0172)
        summary = usage.summarise(7, 7)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["input_tokens"], 10)
        self.assertEqual(summary["output_tokens"], 39)
        self.assertEqual(summary["cache_read_tokens"], 13607)
        self.assertEqual(summary["list_cost_usd"], 0.0172)
        self.assertEqual(summary["by_model"]["claude-opus-5"]["turns"], 1)

    def test_a_failed_turn_still_counts_because_it_still_spent(self):
        usage.record_turn(7, provider="codex", model="gpt-6-astra", ok=False,
                          input_tokens=500)
        summary = usage.summarise(7, 7)
        self.assertEqual(summary["turns"], 1)
        self.assertEqual(summary["failed_turns"], 1)

    def test_rows_outside_the_window_are_left_out(self):
        usage.record_turn(7, provider="claude-cli", model="claude-opus-5",
                          input_tokens=1)
        path = usage.ledger_path(7)
        old = json.loads(path.read_text().strip())
        old["ts"] = int(time.time()) - 40 * 86400
        old["input_tokens"] = 999
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(old) + "\n")
        self.assertEqual(usage.summarise(7, 7)["input_tokens"], 1)
        self.assertEqual(usage.summarise(7, 90)["input_tokens"], 1000)
        self.assertEqual(usage.summarise(7, 0)["input_tokens"], 1000)

    def test_one_persons_ledger_is_not_anothers(self):
        usage.record_turn(7, provider="claude-cli", model="claude-opus-5")
        self.assertEqual(usage.summarise(8, 7)["turns"], 0)

    def test_a_torn_line_is_skipped_not_fatal(self):
        usage.record_turn(7, provider="claude-cli", model="claude-opus-5")
        with open(usage.ledger_path(7), "a", encoding="utf-8") as f:
            f.write("{ half a line\n")
        self.assertEqual(usage.summarise(7, 7)["turns"], 1)

    def test_an_unwritable_ledger_costs_the_line_and_nothing_else(self):
        with patch("core.usage.open", side_effect=OSError("read-only")):
            self.assertFalse(usage.record_turn(7, provider="x", model="y"))

    def test_trimming_drops_old_rows_and_keeps_recent_ones(self):
        path = usage.ledger_path(7)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        with open(path, "w", encoding="utf-8") as f:
            for age_days in (200, 120, 1):
                f.write(json.dumps({"ts": now - age_days * 86400, "turns": 1,
                                    "model": "m", "input_tokens": 1}) + "\n")
        usage._trim(path)
        rows = usage._read_rows(path)
        self.assertEqual(len(rows), 1)

    def test_the_everyone_view_covers_only_real_ledgers(self):
        usage.record_turn(7, provider="claude-cli", model="claude-opus-5")
        usage.record_turn(8, provider="codex", model="gpt-6-astra")
        (users.USERS_DATA_DIR / "not-a-user-id").mkdir(parents=True, exist_ok=True)
        (users.USERS_DATA_DIR / "9").mkdir(parents=True, exist_ok=True)
        everyone = usage.summarise_everyone(7)
        self.assertEqual(sorted(everyone), ["7", "8"])


class ClaudeMeterTests(_TempTree):
    LIVE_EVENT = {
        "status": "allowed",
        "resetsAt": 1789296000,
        "rateLimitType": "five_hour",
        "unifiedWindows": {
            "five_hour": {"utilization": 0.05, "resetsAt": 1789296000},
            "seven_day": {"utilization": 0.09, "resetsAt": 1789570800},
        },
    }

    def test_no_reading_yet_is_unavailable_with_a_reason(self):
        self.assertIsNone(usage.claude_meter())
        reading = usage.meters(codex_timeout=0.1)["claude"]
        self.assertTrue(reading["unavailable"])
        self.assertTrue(reading["reason"])

    def test_a_stored_event_becomes_percentages(self):
        usage.save_claude_rate_limits(self.LIVE_EVENT)
        meter = usage.claude_meter()
        windows = {w["id"]: w for w in meter["windows"]}
        self.assertEqual(windows["five_hour"]["used_percent"], 5.0)
        self.assertEqual(windows["seven_day"]["used_percent"], 9.0)
        self.assertEqual(windows["five_hour"]["label"], "5 hours")
        self.assertEqual(windows["seven_day"]["resets_at"], 1789570800)
        self.assertFalse(meter["live"])
        self.assertLessEqual(abs(meter["captured_at"] - time.time()), 5)

    def test_a_full_window_reads_as_a_hundred_percent(self):
        usage.save_claude_rate_limits(
            {"unifiedWindows": {"five_hour": {"utilization": 1.0,
                                              "resetsAt": 1}}})
        self.assertEqual(usage.claude_meter()["windows"][0]["used_percent"], 100.0)

    def test_a_payload_with_no_windows_keeps_its_status(self):
        usage.save_claude_rate_limits({"status": "allowed"})
        meter = usage.claude_meter()
        self.assertEqual(meter["status"], "allowed")
        self.assertEqual(meter["windows"], [])


    def test_junk_is_refused_rather_than_stored(self):
        self.assertFalse(usage.save_claude_rate_limits("not a dict"))
        self.assertIsNone(usage.claude_meter())


class CodexMeterTests(_TempTree):
    RESULT = {
        "ordinaryUsageAllowed": True,
        "rateLimits": {
            "planType": "pro",
            "primary": {"usedPercent": 16, "windowDurationMins": 10080,
                        "resetsAt": 1789805456},
            "secondary": {"usedPercent": 3, "windowDurationMins": 300,
                          "resetsAt": 1789296590},
        },
    }

    def test_a_reading_becomes_labelled_windows(self):
        with patch("core.usage._codex_rpc_rate_limits", return_value=self.RESULT):
            meter = usage.codex_meter(use_cache=False)
        self.assertEqual(meter["plan"], "pro")
        self.assertTrue(meter["live"])
        labels = [(w["label"], w["used_percent"]) for w in meter["windows"]]
        self.assertEqual(labels, [("codex: 7 days", 16.0), ("codex: 5 hours", 3.0)])

    def test_a_missing_secondary_window_is_simply_absent(self):
        result = {"rateLimits": {"primary": {"usedPercent": 16,
                                             "windowDurationMins": 10080},
                                 "secondary": None}}
        with patch("core.usage._codex_rpc_rate_limits", return_value=result):
            meter = usage.codex_meter(use_cache=False)
        self.assertEqual(len(meter["windows"]), 1)

    def test_no_answer_is_unavailable_with_a_reason(self):
        with patch("core.usage._codex_rpc_rate_limits", return_value=None):
            self.assertIsNone(usage.codex_meter(use_cache=False))
            reading = usage.meters()["codex"]
        self.assertTrue(reading["unavailable"])
        self.assertIn("signed in", reading["reason"])

    def test_a_result_without_percentages_is_not_a_reading(self):
        with patch("core.usage._codex_rpc_rate_limits",
                   return_value={"rateLimits": {"primary": {}}}):
            self.assertIsNone(usage.codex_meter(use_cache=False))

    def test_the_reading_is_cached_between_presses(self):
        with patch("core.usage._codex_rpc_rate_limits",
                   return_value=self.RESULT) as rpc:
            usage.codex_meter()
            usage.codex_meter()
        self.assertEqual(rpc.call_count, 1)

    def test_window_labels_come_from_the_duration(self):
        self.assertEqual(usage._codex_window_label(10080, "x"), "7 days")
        self.assertEqual(usage._codex_window_label(20160, "x"), "14 days")
        self.assertEqual(usage._codex_window_label(1440, "x"), "24 hours")
        self.assertEqual(usage._codex_window_label(300, "x"), "5 hours")
        self.assertEqual(usage._codex_window_label(60, "x"), "1 hour")
        self.assertEqual(usage._codex_window_label(45, "x"), "45 minutes")
        self.assertEqual(usage._codex_window_label(None, "primary"), "primary")


class CodexTransportTests(_TempTree):
    """The RPC itself, against a fake server with the real one's manners."""

    def _fake_server(self, *, die_on_eof: bool) -> str:
        """A stand-in app-server. Answers 0.3s after the request arrives.

        ``die_on_eof`` mirrors the real binary: when its input closes it
        shuts down, answer or no answer. A caller that closes stdin after
        writing therefore gets rc 0 and an empty stream.
        """
        script = self.tmp / f"fake_server_{die_on_eof}.py"
        script.write_text(textwrap.dedent(f'''
            import json, sys, threading, time
            answered = threading.Event()
            initialized = False

            def answer():
                time.sleep(0.3)
                sys.stdout.write(json.dumps({{
                    "id": 2,
                    "result": {{"rateLimits": {{"planType": "pro", "primary":
                        {{"usedPercent": 42, "windowDurationMins": 10080}}}}}},
                }}) + "\\n")
                sys.stdout.flush()
                answered.set()

            for line in sys.stdin:
                msg = json.loads(line)
                if msg.get("id") == 1:
                    print(json.dumps({{"id": 1, "result": {{}}}}), flush=True)
                elif msg.get("method") == "initialized":
                    initialized = True
                elif msg.get("id") == 2:
                    assert initialized
                    threading.Thread(target=answer, daemon=True).start()
            # stdin closed: the server shuts down.
            if not {die_on_eof!r}:
                answered.wait(5)
            sys.exit(0)
        '''), encoding="utf-8")
        wrapper = self.tmp / f"fake_codex_{die_on_eof}"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        return str(wrapper)

    def test_stdin_stays_open_long_enough_for_the_answer(self):
        # The regression: closing stdin after writing (what communicate does)
        # made the real server exit with rc 0 and no output at all.
        result = usage._codex_rpc_rate_limits(
            self._fake_server(die_on_eof=True), timeout=10)
        self.assertIsNotNone(result)
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 42)

    def test_a_binary_that_is_not_there_is_no_reading(self):
        self.assertIsNone(usage._codex_rpc_rate_limits(
            str(self.tmp / "no-such-binary"), timeout=5))

    def test_a_server_that_never_answers_times_out_instead_of_hanging(self):
        script = self.tmp / "mute.py"
        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        wrapper = self.tmp / "mute"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        started = time.monotonic()
        self.assertIsNone(usage._codex_rpc_rate_limits(str(wrapper), timeout=1.0))
        self.assertLess(time.monotonic() - started, 15)

    def test_an_error_reply_is_not_a_reading(self):
        script = self.tmp / "refuse.py"
        script.write_text(textwrap.dedent('''
            import json, sys
            for line in sys.stdin:
                msg = json.loads(line)
                if msg.get("id") == 1:
                    print(json.dumps({"id": 1, "result": {}}), flush=True)
                if msg.get("id") == 2:
                    sys.stdout.write(json.dumps(
                        {"id": 2, "error": {"message": "not signed in"}}) + "\\n")
                    sys.stdout.flush()
        '''), encoding="utf-8")
        wrapper = self.tmp / "refuse"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        self.assertIsNone(usage._codex_rpc_rate_limits(str(wrapper), timeout=10))


if __name__ == "__main__":
    unittest.main()
