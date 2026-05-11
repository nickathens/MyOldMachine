"""Unit tests for core.credentials.

The Keychain backend itself is mocked — these tests verify our argument
shaping, dump parser, validation, and error mapping without touching the
real login Keychain.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import credentials  # noqa: E402


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


@patch("core.credentials.platform.system", return_value="Darwin")
class NormalizeServiceTests(unittest.TestCase):
    def test_accepts_bare_name(self, _sys):
        ref = credentials.CredentialRef(
            service=credentials._normalize_service("surge"), account="a@b.c"
        )
        self.assertEqual(ref.keychain_service, "jarvis-surge")

    def test_strips_existing_prefix(self, _sys):
        self.assertEqual(credentials._normalize_service("jarvis-github"), "github")

    def test_lowercases(self, _sys):
        self.assertEqual(credentials._normalize_service("GitHub"), "github")

    def test_rejects_bad_chars(self, _sys):
        for bad in ("with space", "weird!", "-leading-hyphen", ""):
            with self.assertRaises(ValueError):
                credentials._normalize_service(bad)


@patch("core.credentials.platform.system", return_value="Darwin")
class SaveTests(unittest.TestCase):
    def test_save_invokes_add_generic_password_with_update_flag(self, _sys):
        with patch("core.credentials.subprocess.run", return_value=_proc()) as run:
            ref = credentials.save("surge", "user@example.com", "secret")
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["security", "add-generic-password"])
        self.assertIn("-U", args)
        self.assertIn("jarvis-surge", args)
        self.assertIn("user@example.com", args)
        self.assertIn("secret", args)
        self.assertEqual(ref.keychain_service, "jarvis-surge")

    def test_save_requires_account(self, _sys):
        with self.assertRaises(ValueError):
            credentials.save("surge", "", "secret")

    def test_save_requires_password(self, _sys):
        with self.assertRaises(ValueError):
            credentials.save("surge", "a@b.c", "")

    def test_save_propagates_security_failure(self, _sys):
        with patch(
            "core.credentials.subprocess.run",
            return_value=_proc(returncode=1, stderr="boom"),
        ):
            with self.assertRaises(credentials.CredentialError):
                credentials.save("surge", "a@b.c", "secret")


@patch("core.credentials.platform.system", return_value="Darwin")
class GetTests(unittest.TestCase):
    def test_get_returns_password_without_trailing_newline(self, _sys):
        with patch(
            "core.credentials.subprocess.run",
            return_value=_proc(stdout="hunter2\n"),
        ):
            self.assertEqual(credentials.get("surge"), "hunter2")

    def test_get_passes_account_when_provided(self, _sys):
        with patch(
            "core.credentials.subprocess.run", return_value=_proc(stdout="x\n")
        ) as run:
            credentials.get("surge", "user@example.com")
        args = run.call_args.args[0]
        self.assertEqual(args.count("-a"), 1)
        a_idx = args.index("-a")
        self.assertEqual(args[a_idx + 1], "user@example.com")

    def test_get_raises_not_found_on_44(self, _sys):
        with patch(
            "core.credentials.subprocess.run",
            return_value=_proc(returncode=44, stderr="could not be found"),
        ):
            with self.assertRaises(credentials.CredentialNotFound):
                credentials.get("missing")

    def test_get_raises_generic_error_on_other_failure(self, _sys):
        with patch(
            "core.credentials.subprocess.run",
            return_value=_proc(returncode=1, stderr="some other failure"),
        ):
            with self.assertRaises(credentials.CredentialError):
                credentials.get("surge")


@patch("core.credentials.platform.system", return_value="Darwin")
class DeleteTests(unittest.TestCase):
    def test_delete_returns_true_on_success(self, _sys):
        with patch("core.credentials.subprocess.run", return_value=_proc()):
            self.assertTrue(credentials.delete("surge"))

    def test_delete_returns_false_when_missing(self, _sys):
        with patch(
            "core.credentials.subprocess.run",
            return_value=_proc(returncode=44, stderr="could not be found"),
        ):
            self.assertFalse(credentials.delete("surge"))


@patch("core.credentials.platform.system", return_value="Darwin")
class ListAllTests(unittest.TestCase):
    DUMP = """\
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    0x00000007 <blob>="jarvis-surge"
    "acct"<blob>="user@example.com"
    "svce"<blob>="jarvis-surge"
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="bot@example.com"
    "svce"<blob>="jarvis-github"
keychain: "/Users/x/Library/Keychains/login.keychain-db"
class: "genp"
attributes:
    "acct"<blob>="not-ours"
    "svce"<blob>="iCloud"
"""

    def test_list_returns_only_jarvis_entries_sorted(self, _sys):
        with patch(
            "core.credentials.subprocess.run", return_value=_proc(stdout=self.DUMP)
        ):
            refs = credentials.list_all()
        self.assertEqual(
            [(r.service, r.account) for r in refs],
            [("github", "bot@example.com"), ("surge", "user@example.com")],
        )

    def test_list_deduplicates(self, _sys):
        dump = self.DUMP + self.DUMP  # duplicated section
        with patch(
            "core.credentials.subprocess.run", return_value=_proc(stdout=dump)
        ):
            refs = credentials.list_all()
        self.assertEqual(len(refs), 2)


class PlatformGuardTests(unittest.TestCase):
    def test_non_darwin_raises_unsupported(self):
        with patch("core.credentials.platform.system", return_value="Linux"):
            with self.assertRaises(credentials.UnsupportedPlatform):
                credentials.save("surge", "a@b.c", "x")
            with self.assertRaises(credentials.UnsupportedPlatform):
                credentials.get("surge")
            with self.assertRaises(credentials.UnsupportedPlatform):
                credentials.delete("surge")
            with self.assertRaises(credentials.UnsupportedPlatform):
                credentials.list_all()


if __name__ == "__main__":
    unittest.main()
