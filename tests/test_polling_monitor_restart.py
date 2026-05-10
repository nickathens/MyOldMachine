"""Unit tests for PollingHealthMonitor._restart_local_api macOS path.

The Linux path was already covered implicitly by previous deploys; the macOS
path was missing entirely. These tests verify:

  - macOS branch tries `launchctl kickstart -k system/com.telegram-bot-api`
    before falling back to sudo.
  - The sudo fallback fires when the no-sudo attempt fails.
  - Linux path is unchanged.
  - Unsupported systems return False without crashing.

All subprocess calls are mocked. No real launchctl/systemctl ever runs.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import health  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _fast_sleep(_seconds):
    """Replacement for asyncio.sleep so the 5s post-restart wait doesn't
    inflate test runtime to 15+ seconds."""
    return None


class _FakeProc:
    """Mimics asyncio.subprocess.Process for our purposes."""

    def __init__(self, returncode: int):
        self.returncode = returncode

    async def communicate(self, input=None):  # noqa: A002
        return (b"", b"")


def _make_monitor() -> health.PollingHealthMonitor:
    return health.PollingHealthMonitor(local_api_base="http://localhost:8081")


class RestartLocalApiMacosTests(unittest.TestCase):

    def test_macos_kickstart_no_sudo_success(self):
        mon = _make_monitor()
        seen = []

        async def fake_exec(*args, **kwargs):
            seen.append(args)
            return _FakeProc(0)

        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("asyncio.sleep", side_effect=_fast_sleep):
            ok = _run(mon._restart_local_api("test"))

        self.assertTrue(ok)
        self.assertEqual(len(seen), 1)
        self.assertIn("launchctl", seen[0][0])
        self.assertIn("kickstart", seen[0])
        self.assertIn("system/com.telegram-bot-api", seen[0])

    def test_macos_kickstart_sudo_fallback_with_password(self):
        mon = _make_monitor()
        seen = []
        rc_sequence = iter([1, 0])  # first attempt fails, sudo path succeeds

        async def fake_exec(*args, **kwargs):
            seen.append(args)
            return _FakeProc(next(rc_sequence))

        sudo_pass_path = Path("/tmp/__mom_test_sudo")
        sudo_pass_path.write_text("hunter2\n", encoding="utf-8")
        try:
            with patch.object(health.platform, "system", return_value="Darwin"), \
                 patch.object(health.Path, "home", return_value=sudo_pass_path.parent), \
                 patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
                 patch("asyncio.sleep", side_effect=_fast_sleep):
                # Path.home() returns /tmp; ~/.sudo_pass resolves to /tmp/.sudo_pass
                # but the test wrote to /tmp/__mom_test_sudo. Move it inline.
                target = sudo_pass_path.parent / ".sudo_pass"
                target.write_text("hunter2\n", encoding="utf-8")
                try:
                    ok = _run(mon._restart_local_api("test"))
                finally:
                    target.unlink(missing_ok=True)

            self.assertTrue(ok)
            self.assertEqual(len(seen), 2)
            # First attempt: no-sudo
            self.assertNotIn("sudo", seen[0][0])
            # Second attempt: sudo -S, since password is present
            self.assertEqual(seen[1][0], "sudo")
            self.assertEqual(seen[1][1], "-S")
        finally:
            sudo_pass_path.unlink(missing_ok=True)

    def test_macos_no_sudo_pass_uses_sudo_n(self):
        mon = _make_monitor()
        seen = []

        async def fake_exec(*args, **kwargs):
            seen.append(args)
            return _FakeProc(1)  # both attempts fail

        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.Path, "home", return_value=Path("/nonexistent_home")), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            ok = _run(mon._restart_local_api("test"))

        self.assertFalse(ok)
        self.assertEqual(len(seen), 2)
        # When no password file, falls through to `sudo -n` (passwordless)
        self.assertEqual(seen[1][0], "sudo")
        self.assertEqual(seen[1][1], "-n")

    def test_macos_both_attempts_fail_returns_false(self):
        mon = _make_monitor()

        async def fake_exec(*args, **kwargs):
            return _FakeProc(1)

        with patch.object(health.platform, "system", return_value="Darwin"), \
             patch.object(health.Path, "home", return_value=Path("/nonexistent_home")), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            ok = _run(mon._restart_local_api("test"))

        self.assertFalse(ok)

    def test_linux_path_still_works(self):
        mon = _make_monitor()
        seen = []

        async def fake_exec(*args, **kwargs):
            seen.append(args)
            return _FakeProc(0)

        with patch.object(health.platform, "system", return_value="Linux"), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
             patch("asyncio.sleep", side_effect=_fast_sleep):
            ok = _run(mon._restart_local_api("test"))

        self.assertTrue(ok)
        self.assertEqual(seen[0][0], "systemctl")

    def test_unsupported_system_returns_false(self):
        mon = _make_monitor()

        async def fake_exec(*args, **kwargs):
            return _FakeProc(0)

        with patch.object(health.platform, "system", return_value="FreeBSD"), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            ok = _run(mon._restart_local_api("test"))

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
