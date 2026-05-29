"""Tests for the multi-target restart_service in core/updater.py.

restart_service grew a `target` parameter ("bot" or "miniapp") so the Mini
App's restart button can reuse the same launchd/systemd plumbing. These tests
pin the target dispatch table and the Linux detached-restart path; the macOS
launchctl variant is covered by integration only.

The Linux path can't block on `systemctl restart` of its own unit without
being SIGTERM'd mid-call, so it detaches the restart into a background script.
These tests mock subprocess.Popen and inspect the generated script rather than
watching for a (now-absent) blocking subprocess.run.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import updater  # noqa: E402


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
    """Linux detaches the restart into a background script (mirrors macOS) so it
    survives the caller being SIGTERM'd by `systemctl restart` of its own unit.
    We mock Popen (the spawn) and run (any blocking call), then inspect the
    generated script."""

    def setUp(self) -> None:
        self._platform_patch = patch.object(updater.platform, "system", return_value="Linux")
        self._platform_patch.start()
        self.addCleanup(self._platform_patch.stop)
        self._temp_scripts: list[str] = []

    def tearDown(self) -> None:
        # The real script rm's itself when it runs; Popen is mocked here, so the
        # script never runs — clean up the leftover temp files ourselves.
        for path in self._temp_scripts:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _restart(self, target: str):
        """Invoke restart_service(target) with the spawn + any blocking run
        mocked out. Returns (ok, msg, popen_mock, run_mock, script_text)."""
        with patch.object(updater.subprocess, "Popen") as popen_mock, \
                patch.object(updater.subprocess, "run") as run_mock:
            ok, msg = updater.restart_service(target)
        script_text = ""
        if popen_mock.called:
            script_path = popen_mock.call_args[0][0][0]
            self._temp_scripts.append(script_path)
            script_text = Path(script_path).read_text()
        return ok, msg, popen_mock, run_mock, script_text

    def test_bot_target_uses_myoldmachine_unit(self) -> None:
        ok, _, _, run_mock, script = self._restart("bot")
        self.assertTrue(ok)
        self.assertIn("systemctl restart myoldmachine\n", script)
        run_mock.assert_not_called()

    def test_miniapp_target_uses_miniapp_unit(self) -> None:
        ok, _, _, run_mock, script = self._restart("miniapp")
        self.assertTrue(ok)
        self.assertIn("systemctl restart myoldmachine-miniapp\n", script)
        run_mock.assert_not_called()

    def test_miniapp_env_override_honored(self) -> None:
        with patch.dict(os.environ, {"MOM_MINIAPP_SERVICE": "custom-miniapp"}, clear=False):
            ok, _, _, _, script = self._restart("miniapp")
        self.assertTrue(ok)
        self.assertIn("systemctl restart custom-miniapp\n", script)

    def test_invalid_service_name_rejected(self) -> None:
        # Validation is synchronous and runs BEFORE any spawn, so a name with
        # shell-meta/whitespace never reaches the script or Popen.
        with patch.dict(os.environ, {"MOM_MINIAPP_SERVICE": "bad name; rm -rf /"}, clear=False):
            ok, msg, popen_mock, run_mock, _ = self._restart("miniapp")
        self.assertFalse(ok)
        self.assertIn("Invalid", msg)
        popen_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_linux_returns_immediately_without_blocking(self) -> None:
        # The whole point of the detach: return a scheduled-restart message
        # right away, spawn a detached session, and never block on the unit.
        ok, msg, popen_mock, run_mock, _ = self._restart("bot")
        self.assertTrue(ok)
        self.assertEqual(msg, "Service restarting...")
        popen_mock.assert_called_once()
        self.assertTrue(popen_mock.call_args.kwargs.get("start_new_session"))
        run_mock.assert_not_called()

    def test_sudo_password_not_embedded_in_script(self) -> None:
        # Credentials must be read from ~/.sudo_pass at runtime via stdin, never
        # written into the script body (it lives in a temp dir) or onto argv.
        import tempfile as _tf
        with _tf.TemporaryDirectory() as home:
            secret = "s3cr3t-should-never-appear"
            (Path(home) / ".sudo_pass").write_text(secret + "\n")
            with patch.object(updater.Path, "home", return_value=Path(home)):
                ok, _, _, _, script = self._restart("bot")
        self.assertTrue(ok)
        self.assertNotIn(secret, script)
        self.assertIn("sudo -S", script)
        self.assertIn(".sudo_pass", script)


if __name__ == "__main__":
    unittest.main()
