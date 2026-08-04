#!/usr/bin/env python3
"""The nightly job commands must survive an install path that contains a space.

The scheduler runs a command job through a shell (asyncio.create_subprocess_shell
in core.scheduler._execute_command), so every path baked into the command string
has to be shell-quoted. Three of the eight nightly jobs (probe, machine-check,
report) already shlex.quote their paths; the other five (reflection, cleanup,
system-update, backup, reboot) interpolated raw. On an install under a path like
"/Users/John Smith/MyOldMachine" those five silently failed every night, while
their quoted siblings worked -- an inconsistency, and exactly the kind of quiet
maintenance failure the nightly report exists to surface.

These tests build every nightly job under a spaced BOT_DIR and assert each
command re-parses (shlex.split) with the interpreter path intact as one token.
The same check guards the Claude Code hook commands, which Claude runs through a
shell too.
"""
from __future__ import annotations

import os
import shlex
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot as botmod  # noqa: E402

SPACED_DIR = Path("/tmp/mom test dir/MyOldMachine")
EXPECTED_PY = str(SPACED_DIR / ".venv" / "bin" / "python")

MAINT_CONFIG = {
    "cleanup": True,
    "system_updates": True,
    "backup_enabled": True,
    "backup_path": "/tmp/mom test dir/backups",
    "nightly_reboot": True,
    "nightly_reboot_hour": 5,
    "nightly_reboot_minute": 0,
}


class _FakeScheduler:
    """Captures every add_job(command=...) the setup helpers emit."""

    def __init__(self):
        self.jobs: list[dict] = []

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)
        return SimpleNamespace(job_id="fake")


def _collect_maintenance_jobs() -> list[dict]:
    sched = _FakeScheduler()
    with patch.object(botmod, "BOT_DIR", SPACED_DIR), \
         patch.object(botmod, "DATA_DIR", SPACED_DIR / "data"), \
         patch.object(botmod, "get_primary_admin_id", lambda: 4242), \
         patch("core.scheduler._get_all_meta", lambda *a, **k: []), \
         patch("utils.maintenance.load_config", lambda: dict(MAINT_CONFIG)):
        botmod._setup_maintenance_jobs(sched)
    return sched.jobs


def _collect_reflection_job() -> list[dict]:
    sched = _FakeScheduler()
    with patch.object(botmod, "BOT_DIR", SPACED_DIR), \
         patch.object(botmod, "get_primary_admin_id", lambda: 4242), \
         patch.object(botmod, "get_llm_provider", lambda: "claude"), \
         patch.object(botmod, "get_llm_model", lambda: "claude-sonnet-5"), \
         patch("core.scheduler._get_all_meta", lambda *a, **k: []):
        botmod._setup_reflection_job(sched)
    return sched.jobs


class NightlyJobQuotingTests(unittest.TestCase):
    def _assert_shell_safe(self, command: str, job_name: str):
        # shlex.split models exactly how the scheduler's shell will tokenize
        # the command. The interpreter path must come back as ONE token equal
        # to the full spaced path -- if the quoting were missing, the space
        # would split it into "/tmp/mom" and "test" and the job would die.
        tokens = shlex.split(command)
        self.assertTrue(tokens, f"{job_name}: empty command")
        self.assertEqual(
            tokens[0], EXPECTED_PY,
            f"{job_name}: interpreter path was split by the space: {tokens[:3]}",
        )

    def test_all_maintenance_jobs_are_shell_safe(self):
        jobs = _collect_maintenance_jobs()
        names = {j["name"] for j in jobs}
        # The five that used to interpolate raw must all be present here.
        for required in ("nightly-cleanup", "nightly-system-update",
                         "nightly-backup", "nightly-reboot"):
            self.assertIn(required, names, f"{required} not scheduled")
        for job in jobs:
            with self.subTest(job=job["name"]):
                self._assert_shell_safe(job["command"], job["name"])

    def test_reflection_job_is_shell_safe(self):
        jobs = _collect_reflection_job()
        self.assertEqual(len(jobs), 1, "reflection job not scheduled")
        job = jobs[0]
        self.assertEqual(job["name"], "nightly-reflection")
        self._assert_shell_safe(job["command"], job["name"])
        # The reflect.py path must survive as a single token too.
        tokens = shlex.split(job["command"])
        self.assertEqual(tokens[1], str(SPACED_DIR / "utils" / "reflect.py"))

    def test_backup_and_update_keep_their_notify_args(self):
        # Quoting the paths must not drop the --notify/--user-id tail.
        jobs = {j["name"]: j for j in _collect_maintenance_jobs()}
        for name in ("nightly-system-update", "nightly-backup"):
            tokens = shlex.split(jobs[name]["command"])
            self.assertIn("--notify", tokens, f"{name} lost --notify")
            self.assertIn("--user-id", tokens, f"{name} lost --user-id")
            self.assertIn("4242", tokens, f"{name} lost the admin id")


class ClaudeHookQuotingTests(unittest.TestCase):
    """_configure_claude_hooks writes hook 'command' strings that Claude Code
    runs through a shell, so the script paths must be quoted the same way."""

    def test_hook_commands_survive_spaced_bot_dir(self):
        import json

        with TemporaryDirectory(prefix="mom home ") as home:
            # _configure_claude_hooks reads Path.home(), which honors $HOME on
            # POSIX. Point it at a directory whose name contains a space.
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = home
            try:
                with patch.object(botmod, "BOT_DIR", SPACED_DIR):
                    botmod._configure_claude_hooks()
                settings = json.loads(
                    (Path(home) / ".claude" / "settings.json").read_text()
                )
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

        hook_cmds = [
            hook["command"]
            for entries in settings.get("hooks", {}).values()
            for entry in entries
            for hook in entry.get("hooks", [])
        ]
        self.assertTrue(hook_cmds, "no hook commands were written")
        for cmd in hook_cmds:
            with self.subTest(cmd=cmd):
                tokens = shlex.split(cmd)  # must not raise, must not split paths
                joined = " ".join(tokens)
                self.assertIn(str(SPACED_DIR / "utils"), joined)
                # The script path must appear as a single intact token.
                script_tokens = [t for t in tokens if t.endswith((".sh", ".py"))]
                self.assertEqual(
                    len(script_tokens), 1,
                    f"expected exactly one script token, got {tokens}",
                )
                self.assertTrue(script_tokens[0].startswith(str(SPACED_DIR)))


if __name__ == "__main__":
    unittest.main()
