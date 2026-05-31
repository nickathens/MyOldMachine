"""Tests for the agent-to-agent peer channel.

Covers:
  - core.users.register_peer (idempotent upsert + non-numeric-ID fencing)
  - bot._about_section (the system-prompt bio injection)
  - utils/peer_channel.py CLI (register flow + send-path validation)

The provider/LLM send path itself is an integration concern (it spawns the
configured CLI/API provider) and is exercised manually, not here.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.config as config  # noqa: E402
import core.users as users  # noqa: E402


class _TempUsersStore(unittest.TestCase):
    """Base class: redirect users.json + users/ to a private tempdir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mom_peer_test_"))
        self.users_json = self.tmp / "users.json"
        self.users_dir = self.tmp / "users"
        self.users_dir.mkdir()
        self._patches = [
            patch.object(users, "USERS_JSON", self.users_json),
            patch.object(users, "USERS_DATA_DIR", self.users_dir),
            patch.object(config, "USERS_PROFILES_FILE", self.users_json),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class RegisterPeerTests(_TempUsersStore):
    def test_registers_fenced_peer_profile(self):
        ok, msg = users.register_peer("alice", "Alice", "I am a peer agent.")
        self.assertTrue(ok, msg)
        prof = users.get_user("alice")
        self.assertIsNotNone(prof)
        self.assertEqual(prof["name"], "Alice")
        self.assertEqual(prof["display_name"], "Alice")
        self.assertEqual(prof["role"], "user")
        self.assertTrue(prof["is_peer"])
        self.assertEqual(prof["about"], "I am a peer agent.")
        self.assertFalse(prof["can_install"])
        self.assertFalse(prof["can_restart"])

    def test_numeric_peer_id_rejected(self):
        # Anything int() accepts would leak into the Telegram allow-list.
        for bad in ("123456", "007", "-77"):
            ok, msg = users.register_peer(bad, "Bad")
            self.assertFalse(ok, bad)
            self.assertIn("numeric", msg.lower(), bad)

    def test_unsafe_peer_id_rejected(self):
        # Path-traversal / separators / specials must not become directory names.
        for bad in ("../etc", "a/b", "a b", "+5", "alice!", ""):
            ok, _ = users.register_peer(bad, "Bad")
            self.assertFalse(ok, repr(bad))

    def test_empty_name_and_bad_role_rejected(self):
        ok, _ = users.register_peer("alice", "")
        self.assertFalse(ok)
        ok, _ = users.register_peer("alice", "Alice", role="superuser")
        self.assertFalse(ok)

    def test_peer_excluded_from_allow_and_admin_lists(self):
        # A real numeric admin, plus a peer registered even as admin: the peer
        # must never leak into the Telegram allow-list or the admin list.
        users.add_user(900900900, "RealAdmin", role="admin")
        ok, _ = users.register_peer("alice", "Alice", role="admin")
        self.assertTrue(ok)

        allowed = config.get_allowed_users()
        self.assertIn(900900900, allowed)
        self.assertNotIn("alice", [str(x) for x in allowed])

        admins = users.get_admin_telegram_ids()
        self.assertIn(900900900, admins)
        self.assertNotIn("alice", [str(x) for x in admins])

    def test_reregister_is_idempotent_update(self):
        ok, msg = users.register_peer("alice", "Alice", "first")
        self.assertTrue(ok)
        self.assertIn("Registered", msg)
        added = users.get_user("alice")["added"]

        ok, msg = users.register_peer("alice", "Alice 2", "second")
        self.assertTrue(ok)
        self.assertIn("Updated", msg)
        prof = users.get_user("alice")
        self.assertEqual(prof["name"], "Alice 2")
        self.assertEqual(prof["about"], "second")
        self.assertEqual(prof["added"], added)  # history-preserving


class AboutSectionTests(unittest.TestCase):
    def test_present(self):
        import bot

        out = bot._about_section({"about": "ZZ_MARKER"})
        self.assertEqual(
            out, ["### About the person you are talking to:", "ZZ_MARKER", ""]
        )

    def test_absent_or_blank(self):
        import bot

        self.assertEqual(bot._about_section({}), [])
        self.assertEqual(bot._about_section({"about": ""}), [])
        self.assertEqual(bot._about_section({"about": "   "}), [])
        self.assertEqual(bot._about_section({"about": None}), [])


class PeerChannelCliTests(_TempUsersStore):
    def setUp(self):
        super().setUp()
        import utils.peer_channel as pc

        self.pc = pc

    def test_register_via_cli_writes_peer(self):
        about_file = self.tmp / "persona.txt"
        about_file.write_text("CLI_PEER_BIO", encoding="utf-8")
        rc = self.pc.main(
            ["register", "--peer", "alice", "--name", "Alice",
             "--about-file", str(about_file)]
        )
        self.assertEqual(rc, 0)
        prof = users.get_user("alice")
        self.assertEqual(prof["about"], "CLI_PEER_BIO")
        self.assertTrue(prof["is_peer"])

    def test_register_missing_about_file_errs(self):
        rc = self.pc.main(
            ["register", "--peer", "alice", "--name", "Alice",
             "--about-file", str(self.tmp / "nope.txt")]
        )
        self.assertEqual(rc, 1)

    def test_send_unknown_peer_returns_2(self):
        rc = self.pc.main(["send", "--peer", "ghost"])
        self.assertEqual(rc, 2)

    def test_send_non_peer_identity_returns_2(self):
        users.add_user(900900901, "Human", role="user")
        rc = self.pc.main(["send", "--peer", "900900901"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
