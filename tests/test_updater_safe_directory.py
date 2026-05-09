"""Tests for the git safe.directory auto-recovery in core/updater.py.

The updater wraps every git invocation in `_run_git`, which detects the
"dubious ownership" rejection (Git >= 2.35.2 / CVE-2022-24765) and silently
recovers by adding the working directory to `safe.directory`, then retrying.
"""
from __future__ import annotations

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
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class IsDubiousOwnershipTests(unittest.TestCase):
    def test_matches_canonical_message(self) -> None:
        msg = "fatal: detected dubious ownership in repository at '/Users/x/repo'\n"
        self.assertTrue(updater._is_dubious_ownership(msg))

    def test_case_insensitive(self) -> None:
        self.assertTrue(updater._is_dubious_ownership("DUBIOUS OWNERSHIP"))

    def test_unrelated_error_returns_false(self) -> None:
        self.assertFalse(updater._is_dubious_ownership("fatal: not a git repository"))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(updater._is_dubious_ownership(""))

    def test_none_returns_false(self) -> None:
        self.assertFalse(updater._is_dubious_ownership(None))


class AddSafeDirectoryTests(unittest.TestCase):
    def test_skips_when_already_present(self) -> None:
        path = "/Users/x/repo"
        check = _proc(0, stdout=f"/some/other/path\n{path}\n")
        with patch.object(updater, "_run", return_value=check) as run_mock:
            self.assertTrue(updater._add_safe_directory(path))
        run_mock.assert_called_once()
        args, _ = run_mock.call_args
        self.assertIn("--get-all", args[0])

    def test_adds_when_missing_and_returns_true_on_success(self) -> None:
        path = "/Users/x/repo"
        responses = iter([
            _proc(0, stdout="/some/other/path\n"),
            _proc(0),
        ])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)) as run_mock:
            self.assertTrue(updater._add_safe_directory(path))
        self.assertEqual(run_mock.call_count, 2)
        add_call_args = run_mock.call_args_list[1].args
        self.assertIn("--add", add_call_args[0])
        self.assertIn("safe.directory", add_call_args[0])
        self.assertIn(path, add_call_args[0])

    def test_returns_false_when_add_fails(self) -> None:
        responses = iter([
            _proc(0, stdout=""),
            _proc(1, stderr="git config error"),
        ])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)):
            self.assertFalse(updater._add_safe_directory("/a"))

    def test_handles_failed_listing(self) -> None:
        # If --get-all itself fails, we should still attempt --add.
        responses = iter([
            _proc(128, stderr="boom"),
            _proc(0),
        ])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)) as run_mock:
            self.assertTrue(updater._add_safe_directory("/p"))
        self.assertEqual(run_mock.call_count, 2)

    def test_quotes_paths_with_spaces(self) -> None:
        path = "/Users/Old User/MyOldMachine"
        responses = iter([_proc(0, stdout=""), _proc(0)])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)) as run_mock:
            self.assertTrue(updater._add_safe_directory(path))
        add_cmd = run_mock.call_args_list[1].args[0]
        # shlex.quote wraps a path containing a space in single quotes.
        self.assertIn(f"'{path}'", add_cmd)


class RunGitRecoveryTests(unittest.TestCase):
    def test_passthrough_on_success(self) -> None:
        with patch.object(updater, "_run", return_value=_proc(0, stdout="ok")):
            result = updater._run_git("git status", cwd="/p")
        self.assertEqual(result.returncode, 0)

    def test_passthrough_on_unrelated_error(self) -> None:
        # Unrelated git failures should NOT be retried — we don't want to silently
        # mask real problems with safe.directory writes.
        err = _proc(1, stderr="fatal: not a git repository")
        with patch.object(updater, "_run", return_value=err) as run_mock:
            result = updater._run_git("git status", cwd="/p")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(run_mock.call_count, 1)

    def test_recovers_on_dubious_ownership(self) -> None:
        first = _proc(128, stderr="fatal: detected dubious ownership in repository at '/p'")
        second = _proc(0, stdout="recovered")
        run_calls = iter([first, second])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(run_calls)), \
             patch.object(updater, "_add_safe_directory", return_value=True) as add_mock:
            result = updater._run_git("git status", cwd="/p")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "recovered")
        add_mock.assert_called_once_with("/p")

    def test_no_retry_when_safe_directory_add_fails(self) -> None:
        # If we can't write to git config, don't loop forever.
        first = _proc(128, stderr="dubious ownership")
        with patch.object(updater, "_run", return_value=first) as run_mock, \
             patch.object(updater, "_add_safe_directory", return_value=False):
            result = updater._run_git("git status", cwd="/p")
        self.assertEqual(result.returncode, 128)
        self.assertEqual(run_mock.call_count, 1)

    def test_only_retries_once(self) -> None:
        # If safe.directory was added but the second call still says dubious
        # (e.g. unwritable git config), we should NOT retry forever.
        responses = iter([
            _proc(128, stderr="dubious ownership"),
            _proc(128, stderr="dubious ownership"),
        ])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)) as run_mock, \
             patch.object(updater, "_add_safe_directory", return_value=True):
            result = updater._run_git("git status", cwd="/p")
        self.assertEqual(result.returncode, 128)
        self.assertEqual(run_mock.call_count, 2)


class CheckForUpdatesIntegrationTests(unittest.TestCase):
    """Verify the public surface routes through _run_git so the recovery applies."""

    def test_check_for_updates_recovers_dubious_ownership(self) -> None:
        # Simulate: fetch fails with dubious, then succeeds; then git log returns commits.
        fetch_fail = _proc(128, stderr="fatal: detected dubious ownership in repository at '/p'")
        fetch_ok = _proc(0)
        log_result = _proc(0, stdout="abc1234 fix bug\n")
        branch_result = _proc(0, stdout="main\n")

        responses = iter([fetch_fail, fetch_ok, branch_result, log_result])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)), \
             patch.object(updater, "_add_safe_directory", return_value=True) as add_mock:
            has, msg = updater.check_for_updates(Path("/p"))
        self.assertTrue(has)
        self.assertIn("abc1234", msg)
        add_mock.assert_called_once_with("/p")

    def test_check_for_updates_surfaces_other_errors(self) -> None:
        # A non-ownership failure should surface to the user.
        fetch_fail = _proc(1, stderr="fatal: unable to access remote")
        with patch.object(updater, "_run", return_value=fetch_fail), \
             patch.object(updater, "_add_safe_directory") as add_mock:
            has, msg = updater.check_for_updates(Path("/p"))
        self.assertFalse(has)
        self.assertIn("Failed to check", msg)
        add_mock.assert_not_called()

    def test_pull_updates_recovers_dubious_ownership(self) -> None:
        # rev-parse for "current" version: fail then succeed via recovery.
        rev_fail = _proc(128, stderr="dubious ownership")
        rev_ok_old = _proc(0, stdout="old1234\n")
        pull_ok = _proc(0)
        rev_ok_new = _proc(0, stdout="new5678\n")

        # Order depends on internal call sequence:
        #   get_current_version -> _run_git rev-parse (recover): rev_fail, rev_ok_old
        #   pull_updates direct -> _run_git pull: pull_ok
        #   get_current_version after -> _run_git rev-parse: rev_ok_new
        responses = iter([rev_fail, rev_ok_old, pull_ok, rev_ok_new])
        with patch.object(updater, "_run", side_effect=lambda *a, **kw: next(responses)), \
             patch.object(updater, "_add_safe_directory", return_value=True), \
             patch.object(updater.Path, "exists", return_value=False):
            success, msg = updater.pull_updates(Path("/p"))
        self.assertTrue(success)
        self.assertIn("old1234", msg)
        self.assertIn("new5678", msg)


if __name__ == "__main__":
    unittest.main()
