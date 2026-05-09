"""Unit tests for utils.system_update.

Focused on:
  - _run_cmd's new force_sudo_on_darwin override
  - _count_softwareupdate_available parser
  - _run_macos_softwareupdate orchestration
  - _maybe_run_macos_softwareupdate config gating
  - run_system_update end-to-end paths (Linux apt, macOS brew, macOS no-brew, errors)

All shell commands are mocked via subprocess.run; no real system calls.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import system_update as su  # noqa: E402


def _make_completed(rc: int, stdout: str = "", stderr: str = ""):
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class RunCmdSudoOverrideTests(unittest.TestCase):
    """The force_sudo_on_darwin flag must override the Darwin no-sudo default."""

    @patch("utils.system_update._get_sudo_password", return_value="hunter2")
    @patch("utils.system_update.subprocess.run")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_darwin_sudo_suppressed_by_default(self, _plat, run, _pw):
        run.return_value = _make_completed(0, "ok")
        rc, out = su._run_cmd("brew update", use_sudo=True)
        self.assertEqual(rc, 0)
        called_cmd = run.call_args.args[0]
        self.assertNotIn("sudo", called_cmd)
        # stdin should not carry password
        self.assertIsNone(run.call_args.kwargs.get("input"))

    @patch("utils.system_update._get_sudo_password", return_value="hunter2")
    @patch("utils.system_update.subprocess.run")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_darwin_force_sudo_wraps_in_sudo(self, _plat, run, _pw):
        run.return_value = _make_completed(0, "ok")
        rc, out = su._run_cmd(
            "softwareupdate -i -a", use_sudo=True, force_sudo_on_darwin=True
        )
        self.assertEqual(rc, 0)
        called_cmd = run.call_args.args[0]
        self.assertTrue(called_cmd.startswith("sudo -S "))
        self.assertEqual(run.call_args.kwargs["input"], "hunter2\n")

    @patch("utils.system_update._get_sudo_password", return_value="")
    @patch("utils.system_update.subprocess.run")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_darwin_force_sudo_no_password(self, _plat, run, _pw):
        run.return_value = _make_completed(0, "ok")
        rc, _ = su._run_cmd(
            "softwareupdate -i -a", use_sudo=True, force_sudo_on_darwin=True
        )
        self.assertEqual(rc, 0)
        called_cmd = run.call_args.args[0]
        self.assertTrue(called_cmd.startswith("sudo "))
        # No -S because no password to pipe
        self.assertNotIn("sudo -S", called_cmd)
        self.assertIsNone(run.call_args.kwargs.get("input"))

    @patch("utils.system_update._get_sudo_password", return_value="hunter2")
    @patch("utils.system_update.subprocess.run")
    @patch("utils.system_update.platform.system", return_value="Linux")
    def test_linux_force_sudo_irrelevant(self, _plat, run, _pw):
        # On Linux, sudo wraps regardless of force_sudo_on_darwin.
        run.return_value = _make_completed(0, "ok")
        rc, _ = su._run_cmd("apt-get update", use_sudo=True, force_sudo_on_darwin=False)
        self.assertEqual(rc, 0)
        self.assertTrue(run.call_args.args[0].startswith("sudo -S "))


class CountSoftwareupdateAvailableTests(unittest.TestCase):
    @patch("utils.system_update.platform.system", return_value="Linux")
    def test_non_darwin_short_circuits_to_zero(self, _plat):
        self.assertEqual(su._count_softwareupdate_available(), 0)

    @patch("utils.system_update._run_cmd", return_value=(0, "No new software available."))
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_no_updates_message(self, _plat, _run):
        self.assertEqual(su._count_softwareupdate_available(), 0)

    @patch("utils.system_update._run_cmd")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_modern_format_with_three_updates(self, _plat, run):
        run.return_value = (0, (
            "Software Update Tool\n"
            "Finding available software\n"
            "Software Update found the following new or updated software:\n"
            "* Label: Safari17.5MontereyAuto-17.5\n"
            "\tTitle: Safari, Version: 17.5, Size: 130000K, Recommended: YES\n"
            "* Label: macOSUpd14.5-14.5\n"
            "\tTitle: macOS 14.5, Version: 14.5, Size: 6500000K, Recommended: YES, Action: restart\n"
            "* Label: CommandLineTools-15.4\n"
            "\tTitle: Command Line Tools, Version: 15.4, Size: 700000K\n"
        ))
        self.assertEqual(su._count_softwareupdate_available(), 3)

    @patch("utils.system_update._run_cmd")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_legacy_indented_format(self, _plat, run):
        # Older macOS variants prefixed lines with whitespace + asterisk.
        run.return_value = (0, (
            "   * iTunesX-12.10\n"
            "      iTunes (12.10)\n"
            "   * SafariUpdate-13.1\n"
            "      Safari (13.1)\n"
        ))
        self.assertEqual(su._count_softwareupdate_available(), 2)

    @patch("utils.system_update._run_cmd", return_value=(0, "garbage output\nno asterisks"))
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_unparseable_output_returns_zero(self, _plat, _run):
        self.assertEqual(su._count_softwareupdate_available(), 0)


class RunMacosSoftwareupdateTests(unittest.TestCase):
    def setUp(self):
        self.log_calls = []
        self.notify_calls = []
        self.log_fn = self.log_calls.append
        self.notify_fn = self.notify_calls.append

    @patch("utils.system_update.platform.system", return_value="Linux")
    def test_skipped_on_non_darwin(self, _plat):
        self.assertEqual(
            su._run_macos_softwareupdate(False, self.log_fn, self.notify_fn), ""
        )
        self.assertEqual(self.log_calls, [])

    @patch("utils.system_update._count_softwareupdate_available", return_value=0)
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_no_updates_returns_empty(self, _plat, _count):
        self.assertEqual(
            su._run_macos_softwareupdate(False, self.log_fn, self.notify_fn), ""
        )
        self.assertIn("no Apple updates available", self.log_calls[0])

    @patch("utils.system_update._run_cmd")
    @patch("utils.system_update._count_softwareupdate_available")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_install_success_no_restart(self, _plat, count, run):
        count.side_effect = [3, 0]  # before, after
        run.return_value = (0, "Done.")
        result = su._run_macos_softwareupdate(False, self.log_fn, self.notify_fn)
        self.assertIn("3 Apple update(s) installed", result)
        self.assertNotIn("Restart triggered", result)
        # Did NOT pass --restart
        self.assertNotIn("--restart", run.call_args.args[0])
        # Did pass force_sudo_on_darwin
        self.assertTrue(run.call_args.kwargs.get("force_sudo_on_darwin"))

    @patch("utils.system_update._run_cmd")
    @patch("utils.system_update._count_softwareupdate_available")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_install_with_restart(self, _plat, count, run):
        count.side_effect = [2, 0]
        run.return_value = (0, "Some updates require a restart to complete.")
        result = su._run_macos_softwareupdate(True, self.log_fn, self.notify_fn)
        self.assertIn("2 Apple update(s) installed", result)
        self.assertIn("auto-restart enabled", result)
        self.assertIn("--restart", run.call_args.args[0])

    @patch("utils.system_update._run_cmd")
    @patch("utils.system_update._count_softwareupdate_available")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_install_no_restart_omits_suffix(self, _plat, count, run):
        # Even if mock output happens to contain "restart" (e.g. from a
        # phrase like "no restart required"), the no-restart path must
        # never claim auto-restart.
        count.side_effect = [1, 0]
        run.return_value = (0, "Done. No restart required.")
        result = su._run_macos_softwareupdate(False, self.log_fn, self.notify_fn)
        self.assertNotIn("auto-restart", result)
        self.assertNotIn("Restart triggered", result)

    @patch("utils.system_update._run_cmd", return_value=(1, "permission denied"))
    @patch("utils.system_update._count_softwareupdate_available", return_value=2)
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_install_failure_notifies(self, _plat, _count, _run):
        result = su._run_macos_softwareupdate(False, self.log_fn, self.notify_fn)
        self.assertIn("install failed", result)
        self.assertEqual(self.notify_calls, [result])

    @patch("utils.system_update._run_cmd")
    @patch("utils.system_update._count_softwareupdate_available")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_partial_install_remaining_pending(self, _plat, count, run):
        count.side_effect = [4, 1]
        run.return_value = (0, "Some completed.")
        result = su._run_macos_softwareupdate(False, self.log_fn, self.notify_fn)
        self.assertIn("3 Apple update(s) installed", result)
        self.assertIn("1 still pending", result)


class MaybeRunSoftwareupdateTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.notify = []

    @patch("utils.system_update.platform.system", return_value="Linux")
    def test_non_darwin_returns_empty(self, _plat):
        self.assertEqual(
            su._maybe_run_macos_softwareupdate(self.log.append, self.notify.append), ""
        )

    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_flag_off_returns_empty(self, _plat):
        with patch.dict(
            "sys.modules",
            {
                "utils.maintenance": MagicMock(load_config=lambda: {"macos_system_updates": False})
            },
        ):
            self.assertEqual(
                su._maybe_run_macos_softwareupdate(self.log.append, self.notify.append), ""
            )

    @patch("utils.system_update._run_macos_softwareupdate")
    @patch("utils.system_update.platform.system", return_value="Darwin")
    def test_flag_on_invokes_runner(self, _plat, run):
        run.return_value = "softwareupdate: 2 Apple update(s) installed."
        with patch.dict(
            "sys.modules",
            {
                "utils.maintenance": MagicMock(load_config=lambda: {
                    "macos_system_updates": True,
                    "macos_system_updates_restart": True,
                })
            },
        ):
            out = su._maybe_run_macos_softwareupdate(self.log.append, self.notify.append)
        self.assertIn("installed", out)
        run.assert_called_once()
        self.assertTrue(run.call_args.kwargs.get("auto_restart"))


class RunSystemUpdateOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.notified = []
        self.notify = self.notified.append

    @patch("utils.system_update._maybe_run_macos_softwareupdate", return_value="")
    @patch("utils.system_update._run_pkg_manager_update")
    def test_pkg_error_short_circuits_softwareupdate(self, pkg, sw):
        pkg.return_value = ("System update: upgrade failed (apt)", True)
        result = su.run_system_update(notify_fn=self.notify)
        self.assertIn("upgrade failed", result)
        # softwareupdate must NOT have been called after pkg error
        sw.assert_not_called()

    @patch("utils.system_update._maybe_run_macos_softwareupdate", return_value="")
    @patch("utils.system_update._run_pkg_manager_update", return_value=("", False))
    def test_no_updates_no_notify(self, _pkg, _sw):
        result = su.run_system_update(notify_fn=self.notify)
        self.assertEqual(result, "No updates available.")
        self.assertEqual(self.notified, [])

    @patch("utils.system_update._maybe_run_macos_softwareupdate", return_value="")
    @patch("utils.system_update._run_pkg_manager_update")
    def test_pkg_upgrade_notifies(self, pkg, _sw):
        pkg.return_value = ("Nightly update: 5 package(s) upgraded.", False)
        result = su.run_system_update(notify_fn=self.notify)
        self.assertIn("5 package(s) upgraded", result)
        self.assertEqual(self.notified, [result])

    @patch("utils.system_update._maybe_run_macos_softwareupdate")
    @patch("utils.system_update._run_pkg_manager_update", return_value=("", False))
    def test_softwareupdate_only_no_brew(self, _pkg, sw):
        sw.return_value = "softwareupdate: 1 Apple update(s) installed."
        result = su.run_system_update(notify_fn=self.notify)
        self.assertEqual(result, "softwareupdate: 1 Apple update(s) installed.")
        self.assertEqual(self.notified, [result])

    @patch("utils.system_update._maybe_run_macos_softwareupdate")
    @patch("utils.system_update._run_pkg_manager_update")
    def test_brew_and_softwareupdate_combined(self, pkg, sw):
        pkg.return_value = ("Nightly update: 2 package(s) upgraded.", False)
        sw.return_value = "softwareupdate: 1 Apple update(s) installed."
        result = su.run_system_update(notify_fn=self.notify)
        self.assertIn("2 package(s) upgraded", result)
        self.assertIn("1 Apple update(s) installed", result)
        self.assertEqual(len(self.notified), 1)

    @patch("utils.system_update._maybe_run_macos_softwareupdate", return_value="")
    @patch("utils.system_update._run_pkg_manager_update")
    def test_held_back_does_not_notify(self, pkg, _sw):
        pkg.return_value = ("All 3 updates held back (phased rollout). Nothing changed.", False)
        result = su.run_system_update(notify_fn=self.notify)
        self.assertIn("held back", result)
        # Held-back is informational only; no notification should fire.
        self.assertEqual(self.notified, [])


if __name__ == "__main__":
    unittest.main()
