"""Tests for the multi-target restart_service in core/updater.py.

restart_service grew a `target` parameter ("bot" or "miniapp") so the Mini
App's restart button can reuse the same launchd/systemd plumbing. These
tests pin the target dispatch table and the Linux systemctl path; macOS
launchctl + the detached shell script are covered by integration only.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["systemctl"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class RestartServiceTargetTableTests(unittest.TestCase):
    def test_bot_and_miniapp_targets_registered(self) -> None:
        self.assertIn("bot", updater._SERVICE_TARGETS)
        self.assertIn("miniapp", updater._SERVICE_TARGETS)

    def test_each_target_has_linux_and_macos_keys(self) -> None:
        for name, spec in updater._SERVICE_TARGETS.items():
            self.assertIn("linux_service", spec, f"{name}: linux_service")
            self.assertIn("macos_plist_name", spec, f"{name}: macos_plist_name")

    def test_unknown_target_returns_error(self) -> None:
        ok, msg = updater.restart_service("notathing")
        self.assertFalse(ok)
        self.assertIn("notathing", msg)


class RestartServiceLinuxDispatchTests(unittest.TestCase):
    """Linux path: systemctl restart <unit>. We mock subprocess.run + the sudo
    password helper so the test never actually shells out."""

    def setUp(self) -> None:
        self._password_patch = patch.object(updater, "get_sudo_password", return_value="")
        self._password_patch.start()
        self._platform_patch = patch.object(updater.platform, "system", return_value="Linux")
        self._platform_patch.start()

    def tearDown(self) -> None:
        self._platform_patch.stop()
        self._password_patch.stop()

    def test_bot_target_uses_myoldmachine_unit(self) -> None:
        with patch.object(updater.subprocess, "run", return_value=_proc(0)) as run_mock:
            ok, _ = updater.restart_service("bot")
        self.assertTrue(ok)
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[-1], "myoldmachine")

    def test_miniapp_target_uses_miniapp_unit(self) -> None:
        with patch.object(updater.subprocess, "run", return_value=_proc(0)) as run_mock:
            ok, _ = updater.restart_service("miniapp")
        self.assertTrue(ok)
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[-1], "myoldmachine-miniapp")

    def test_miniapp_env_override_honored(self) -> None:
        with patch.dict(os.environ, {"MOM_MINIAPP_SERVICE": "custom-miniapp"}, clear=False):
            with patch.object(updater.subprocess, "run", return_value=_proc(0)) as run_mock:
                ok, _ = updater.restart_service("miniapp")
        self.assertTrue(ok)
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[-1], "custom-miniapp")

    def test_invalid_service_name_rejected(self) -> None:
        # Should refuse anything with shell-meta or whitespace before invoking
        # subprocess. We use an env override to inject a bad name.
        with patch.dict(os.environ, {"MOM_MINIAPP_SERVICE": "bad name; rm -rf /"}, clear=False):
            with patch.object(updater.subprocess, "run") as run_mock:
                ok, msg = updater.restart_service("miniapp")
        self.assertFalse(ok)
        self.assertIn("Invalid", msg)
        run_mock.assert_not_called()

    def test_systemctl_failure_propagates(self) -> None:
        with patch.object(updater.subprocess, "run",
                          return_value=_proc(1, stderr="unit not found")):
            ok, msg = updater.restart_service("bot")
        self.assertFalse(ok)
        self.assertIn("unit not found", msg)


if __name__ == "__main__":
    unittest.main()
