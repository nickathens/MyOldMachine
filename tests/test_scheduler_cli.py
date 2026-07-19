"""Tests for utils/scheduler_cli.py add.

Covers the two audit fixes: --type agent (a timed ACTION was previously
impossible to schedule from the CLI — 'reminder' was hardcoded, so agent
tasks fired as text messages) and the past-time guard (a typo'd year used
to be stored silently and fire ~65s later via the sync loop).
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import scheduler_cli  # noqa: E402


def _future() -> str:
    return (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")


def _past() -> str:
    return (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d %H:%M")


def _parse(argv):
    return scheduler_cli.build_parser().parse_args(argv)


class AddTypeTests(unittest.TestCase):
    def test_default_is_reminder_with_notify(self):
        args = _parse(["add", "--user", "1", "--at", _future(), "--message", "hi"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 0)
        kw = save.call_args.kwargs
        self.assertEqual(kw["job_type"], "reminder")
        self.assertTrue(kw["notify"])

    def test_agent_type_reaches_meta(self):
        args = _parse(["add", "--user", "1", "--at", _future(),
                       "--type", "agent", "--message", "do the thing"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 0)
        self.assertEqual(save.call_args.kwargs["job_type"], "agent")

    def test_no_notify_maps_to_notify_false(self):
        args = _parse(["add", "--user", "1", "--at", _future(),
                       "--type", "agent", "--no-notify", "--message", "quietly"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 0)
        self.assertFalse(save.call_args.kwargs["notify"])

    def test_command_type_rejected_by_argparse(self):
        # command jobs stay CLI-uncreatable on purpose (remove already
        # requires --force for them); only reminder|agent are offered.
        with self.assertRaises(SystemExit):
            _parse(["add", "--user", "1", "--at", _future(),
                    "--type", "command", "--message", "rm -rf"])


class PastTimeGuardTests(unittest.TestCase):
    def test_past_oneshot_rejected(self):
        args = _parse(["add", "--user", "1", "--at", _past(), "--message", "typo year"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 1)
        save.assert_not_called()

    def test_past_with_allow_past_accepted(self):
        args = _parse(["add", "--user", "1", "--at", _past(),
                       "--allow-past", "--message", "fire now"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 0)
        save.assert_called_once()

    def test_past_recurring_allowed(self):
        # For recurring jobs run_at is only the time-of-day anchor.
        args = _parse(["add", "--user", "1", "--at", _past(),
                       "--repeat", "daily", "--message", "anchor"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 0)
        save.assert_called_once()

    def test_future_oneshot_accepted(self):
        args = _parse(["add", "--user", "1", "--at", _future(), "--message", "ok"])
        with patch.object(scheduler_cli, "_save_meta") as save:
            rc = scheduler_cli.cmd_add(args)
        self.assertEqual(rc, 0)
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
