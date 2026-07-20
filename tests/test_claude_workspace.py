"""Tests for per-user Claude CLI workspaces (core/claude_workspace.py).

Covers the three seams that keep one Telegram user's context out of
another's: the per-user CLAUDE_CONFIG_DIR handed to the CLI subprocess,
the idempotent workspace bootstrap (symlinked machine-wide settings,
seeded onboarding state), and the one-time provenance split of the legacy
shared memory pool. Everything runs inside a tmpdir; no real config dirs,
no real users.json.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import claude_workspace as cw  # noqa: E402
from core import llm  # noqa: E402

# The fake bot dir used across migration tests; encodes to "-x-y".
FAKE_BOT_DIR = Path("/x/y")
ENC = "-x-y"


class EncodeProjectDirTests(unittest.TestCase):
    def test_real_bot_dir_encoding(self):
        self.assertEqual(
            cw._encode_project_dir(Path("/Users/coocooai/MyOldMachine")),
            "-Users-coocooai-MyOldMachine",
        )

    def test_dots_and_underscores_also_encode(self):
        self.assertEqual(cw._encode_project_dir(Path("/a/b.c_d")), "-a-b-c-d")


class WorkspaceTestBase(unittest.TestCase):
    """Shared tmpdir scaffolding with resolve_user_dir patched into it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.users_root = self.root / "users"
        self.legacy = self.root / "legacy_claude"
        self.legacy.mkdir(parents=True)
        self._resolve = patch(
            "core.users.resolve_user_dir",
            side_effect=lambda uid: self.users_root / str(uid),
        )
        self._resolve.start()

    def tearDown(self) -> None:
        self._resolve.stop()
        self._tmp.cleanup()

    # -- legacy-pool builders -------------------------------------------

    def legacy_memory(self) -> Path:
        mem = self.legacy / "projects" / ENC / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        return mem

    def add_memory_file(self, name: str, origin: str | None) -> Path:
        mem = self.legacy_memory()
        origin_line = f"  originSessionId: {origin}\n" if origin else ""
        body = (
            "---\n"
            f"name: {name.removesuffix('.md')}\n"
            "description: test note\n"
            "metadata: \n"
            "  node_type: memory\n"
            "  type: project\n"
            f"{origin_line}"
            "---\n\n"
            f"Body of {name}\n"
        )
        path = mem / name
        path.write_text(body, encoding="utf-8")
        return path

    def add_transcript(self, session_id: str, telegram_id: int) -> Path:
        proj = self.legacy / "projects" / ENC
        proj.mkdir(parents=True, exist_ok=True)
        path = proj / f"{session_id}.jsonl"
        line = json.dumps(
            {
                "type": "user",
                "message": "The user's name is T. "
                f"User's Telegram ID: {telegram_id}\nrest of prompt",
            }
        )
        path.write_text(line + "\n", encoding="utf-8")
        return path

    def user_memory(self, uid: int) -> Path:
        return self.users_root / str(uid) / "claude" / "projects" / ENC / "memory"


class EnsureUserClaudeConfigTests(WorkspaceTestBase):
    def setUp(self) -> None:
        super().setUp()
        # Every ensure() needs reachable CLI auth; give the legacy root a
        # credential file like a logged-in install has.
        (self.legacy / ".credentials.json").write_text('{"tok": "t"}')

    def test_creates_dir_and_seeds_onboarding_state(self):
        (self.legacy / "settings.json").write_text("{}")
        (self.legacy / "plugins").mkdir()
        (self.legacy / ".claude.json").write_text(
            json.dumps({"oauthAccount": {"email": "x@y"}, "userID": "u1", "junk": 1})
        )
        cfg = cw.ensure_user_claude_config(
            9, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )
        self.assertEqual(cfg, self.users_root / "9" / "claude")
        self.assertTrue(cfg.is_dir())
        self.assertTrue((cfg / "settings.json").is_symlink())
        self.assertEqual(
            (cfg / "settings.json").resolve(),
            (self.legacy / "settings.json").resolve(),
        )
        self.assertTrue((cfg / "plugins").is_symlink())
        self.assertTrue((cfg / ".credentials.json").is_symlink())
        state = json.loads((cfg / ".claude.json").read_text())
        self.assertTrue(state["hasCompletedOnboarding"])
        self.assertEqual(state["oauthAccount"], {"email": "x@y"})
        self.assertEqual(state["userID"], "u1")
        self.assertNotIn("junk", state)

    def test_idempotent_and_distinct_per_user(self):
        (self.legacy / "settings.json").write_text("{}")
        a1 = cw.ensure_user_claude_config(
            1, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )
        a2 = cw.ensure_user_claude_config(
            1, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )
        b = cw.ensure_user_claude_config(
            2, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)

    def test_missing_legacy_entries_are_skipped(self):
        cfg = cw.ensure_user_claude_config(
            3, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )
        self.assertFalse((cfg / "settings.json").exists())
        self.assertTrue((cfg / ".credentials.json").is_symlink())
        self.assertTrue((cfg / ".claude.json").exists())

    def test_refuses_workspace_without_any_auth(self):
        (self.legacy / ".credentials.json").unlink()
        blank = {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}
        with patch.dict("os.environ", blank), patch.object(
            cw, "_export_keychain_credentials", return_value=False
        ):
            with self.assertRaises(RuntimeError):
                cw.ensure_user_claude_config(
                    4, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
                )

    def test_env_key_alone_is_enough(self):
        (self.legacy / ".credentials.json").unlink()
        with patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "sk-test"}
        ), patch.object(cw, "_export_keychain_credentials", return_value=False):
            cfg = cw.ensure_user_claude_config(
                4, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
            )
        self.assertTrue(cfg.is_dir())
        self.assertFalse((cfg / ".credentials.json").exists())


class MigrateLegacyMemoryTests(WorkspaceTestBase):
    def test_splits_by_provenance_with_admin_fallback(self):
        self.add_memory_file("a.md", "sid-aaaa-1111")
        self.add_memory_file("b.md", "sid-bbbb-2222")
        self.add_memory_file("c.md", None)  # no origin -> admin
        self.add_transcript("sid-aaaa-1111", 111)
        self.add_transcript("sid-bbbb-2222", 222)
        self.legacy_memory().joinpath("MEMORY.md").write_text(
            "- [A](a.md) — hook a\n"
            "- [B](b.md) — hook b\n"
            "- [C](c.md) — hook c\n",
            encoding="utf-8",
        )

        with patch("core.config.get_primary_admin_id", return_value=111):
            assignment = cw.migrate_legacy_memory(self.legacy, FAKE_BOT_DIR)

        self.assertEqual(assignment, {"a.md": 111, "b.md": 222, "c.md": 111})
        self.assertTrue((self.user_memory(111) / "a.md").exists())
        self.assertTrue((self.user_memory(111) / "c.md").exists())
        self.assertTrue((self.user_memory(222) / "b.md").exists())
        self.assertFalse((self.user_memory(222) / "a.md").exists())

        idx_111 = (self.user_memory(111) / "MEMORY.md").read_text()
        idx_222 = (self.user_memory(222) / "MEMORY.md").read_text()
        self.assertIn("(a.md)", idx_111)
        self.assertIn("(c.md)", idx_111)
        self.assertNotIn("(b.md)", idx_111)
        self.assertEqual(idx_222.strip(), "- [B](b.md) — hook b")

        # Legacy pool intact plus marker.
        self.assertTrue((self.legacy_memory() / "a.md").exists())
        marker = self.legacy_memory() / cw._MIGRATION_MARKER
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(marker.read_text())["split"]["b.md"], 222)

    def test_marker_makes_split_single_shot(self):
        self.add_memory_file("a.md", "sid-aaaa-1111")
        self.add_transcript("sid-aaaa-1111", 111)
        with patch("core.config.get_primary_admin_id", return_value=111):
            first = cw.migrate_legacy_memory(self.legacy, FAKE_BOT_DIR)
            (self.user_memory(111) / "a.md").unlink()
            second = cw.migrate_legacy_memory(self.legacy, FAKE_BOT_DIR)
        self.assertEqual(first, {"a.md": 111})
        self.assertEqual(second, {})
        # A completed split never re-copies.
        self.assertFalse((self.user_memory(111) / "a.md").exists())

    def test_no_admin_leaves_split_retryable(self):
        self.add_memory_file("d.md", None)
        with patch("core.config.get_primary_admin_id", return_value=None):
            assignment = cw.migrate_legacy_memory(self.legacy, FAKE_BOT_DIR)
        self.assertEqual(assignment, {})
        self.assertFalse((self.legacy_memory() / cw._MIGRATION_MARKER).exists())

    def test_no_legacy_pool_is_a_noop(self):
        empty = self.root / "nothing_here"
        self.assertEqual(cw.migrate_legacy_memory(empty, FAKE_BOT_DIR), {})


class ClaudeWorkspaceEnvTests(WorkspaceTestBase):
    def test_env_includes_per_user_config_dir(self):
        with patch(
            "core.claude_workspace.ensure_user_claude_config",
            return_value=Path("/w/5/claude"),
        ) as ensure:
            env = llm._claude_workspace_env(5)
        ensure.assert_called_once_with(5)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/w/5/claude")
        self.assertEqual(env["JARVIS_USER_ID"], "5")
        self.assertTrue(env["JARVIS_USER_DIR"].endswith("/users/5"))

    def test_none_user_gets_no_workspace(self):
        with patch(
            "core.claude_workspace.ensure_user_claude_config"
        ) as ensure:
            env = llm._claude_workspace_env(None)
        ensure.assert_not_called()
        self.assertEqual(env, {})

    def test_fails_open_when_workspace_breaks(self):
        with patch(
            "core.claude_workspace.ensure_user_claude_config",
            side_effect=RuntimeError("disk full"),
        ):
            env = llm._claude_workspace_env(7)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(env["JARVIS_USER_ID"], "7")


class ReconcileSharedCredentialsTests(WorkspaceTestBase):
    """The refresh-heal that keeps the shared OAuth chain intact.

    The CLI rewrites .credentials.json by renaming a temp file onto it when
    the token refreshes, which detaches a workspace's symlink into a private
    copy. reconcile_shared_credentials folds that refresh back into the
    shared file and restores the symlink so no workspace goes stale.
    """

    def setUp(self) -> None:
        super().setUp()
        (self.legacy / ".credentials.json").write_text('{"tok": "t"}')

    def make_workspace(self, uid: int) -> Path:
        return cw.ensure_user_claude_config(
            uid, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )

    def simulate_refresh(self, cfg: Path, token: bytes) -> None:
        """Mimic the CLI's refresh write: a temp file renamed onto the
        credential path, which detaches the symlink into a regular file."""
        cred = cfg / ".credentials.json"
        tmp = cfg / ".credentials.json.tmp.deadbeef"
        tmp.write_bytes(token)
        os.replace(tmp, cred)

    def test_fresh_workspace_credential_is_a_symlink(self):
        cfg = self.make_workspace(1)
        self.assertTrue((cfg / ".credentials.json").is_symlink())

    def test_detached_credential_propagates_and_relinks(self):
        cfg = self.make_workspace(1)
        self.simulate_refresh(cfg, b'{"tok": "REFRESHED"}')
        self.assertFalse((cfg / ".credentials.json").is_symlink())  # detached

        result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)

        self.assertTrue(result)
        self.assertEqual(
            (self.legacy / ".credentials.json").read_bytes(), b'{"tok": "REFRESHED"}'
        )
        self.assertTrue((cfg / ".credentials.json").is_symlink())
        self.assertEqual(
            (cfg / ".credentials.json").read_bytes(), b'{"tok": "REFRESHED"}'
        )

    def test_symlinked_credential_is_a_noop(self):
        cfg = self.make_workspace(1)
        result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)
        self.assertFalse(result)
        self.assertTrue((cfg / ".credentials.json").is_symlink())
        self.assertEqual(
            (self.legacy / ".credentials.json").read_bytes(), b'{"tok": "t"}'
        )

    def test_absent_credential_is_a_noop(self):
        # user 2 has no workspace at all; nothing to reconcile.
        result = cw.reconcile_shared_credentials(2, legacy_root=self.legacy)
        self.assertFalse(result)

    def test_identical_detached_credential_relinks_without_rewrite(self):
        cfg = self.make_workspace(1)
        shared = self.legacy / ".credentials.json"
        ino_before = shared.stat().st_ino
        # The CLI rewrote byte-identical content (token not really refreshed).
        self.simulate_refresh(cfg, b'{"tok": "t"}')

        result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)

        self.assertFalse(result)  # nothing new to propagate
        self.assertTrue((cfg / ".credentials.json").is_symlink())  # still healed
        self.assertEqual(shared.stat().st_ino, ino_before)  # shared untouched

    def test_refreshed_token_reaches_the_other_workspace(self):
        # The convergence proof: one user's refresh must reach the others.
        a = self.make_workspace(1)
        b = self.make_workspace(2)
        self.assertEqual((a / ".credentials.json").read_bytes(), b'{"tok": "t"}')
        self.assertEqual((b / ".credentials.json").read_bytes(), b'{"tok": "t"}')

        self.simulate_refresh(a, b'{"tok": "REFRESHED"}')
        self.assertTrue(cw.reconcile_shared_credentials(1, legacy_root=self.legacy))

        # user 2, untouched and still symlinked, now reads the refreshed token.
        self.assertTrue((b / ".credentials.json").is_symlink())
        self.assertEqual(
            (b / ".credentials.json").read_bytes(), b'{"tok": "REFRESHED"}'
        )
        self.assertTrue((a / ".credentials.json").is_symlink())

    def test_ensure_reconverges_a_detached_credential(self):
        # Crash-recovery net: a refresh left un-propagated by a restart is
        # healed by the pre-turn reconcile inside ensure_user_claude_config.
        cfg = self.make_workspace(1)
        self.simulate_refresh(cfg, b'{"tok": "REFRESHED"}')

        cfg2 = self.make_workspace(1)

        self.assertEqual(cfg, cfg2)
        self.assertTrue((cfg / ".credentials.json").is_symlink())
        self.assertEqual(
            (self.legacy / ".credentials.json").read_bytes(), b'{"tok": "REFRESHED"}'
        )

    def test_write_failure_does_not_raise(self):
        cfg = self.make_workspace(1)
        self.simulate_refresh(cfg, b'{"tok": "NEW"}')
        with patch(
            "core.claude_workspace.os.replace", side_effect=OSError("boom")
        ):
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)
        self.assertFalse(result)  # propagation failed, but no exception escaped

    def test_repeated_refresh_cycles_stay_converged(self):
        a = self.make_workspace(1)
        b = self.make_workspace(2)
        for token in (b'{"tok": "r1"}', b'{"tok": "r2"}', b'{"tok": "r3"}'):
            self.simulate_refresh(a, token)
            cw.reconcile_shared_credentials(1, legacy_root=self.legacy)
            self.assertTrue((a / ".credentials.json").is_symlink())
            self.assertTrue((b / ".credentials.json").is_symlink())
            self.assertEqual((b / ".credentials.json").read_bytes(), token)


class CredentialUsabilityTests(unittest.TestCase):
    """_credential_is_usable: the difference between "a file is there" and
    "the CLI can authenticate with it"."""

    def test_well_formed_oauth_is_usable(self):
        raw = json.dumps({"claudeAiOauth": {
            "accessToken": "a", "refreshToken": "r"}})
        self.assertTrue(cw._credential_is_usable(raw))

    def test_expired_access_with_live_refresh_is_usable(self):
        # The normal recoverable state. Rejecting it would break the very
        # refresh chain this module maintains.
        raw = json.dumps({"claudeAiOauth": {
            "accessToken": "a", "refreshToken": "r", "expiresAt": 1}})
        self.assertTrue(cw._credential_is_usable(raw))

    def test_hollow_tokens_are_not_usable(self):
        for oauth in ({"accessToken": "", "refreshToken": "r"},
                      {"accessToken": "a", "refreshToken": ""},
                      {"accessToken": "", "refreshToken": ""},
                      {}):
            with self.subTest(oauth=oauth):
                raw = json.dumps({"claudeAiOauth": oauth})
                self.assertFalse(cw._credential_is_usable(raw))

    def test_malformed_input_is_not_usable(self):
        for raw in ("", "{", "[]", "null", b"\xff\xfe", None, b""):
            with self.subTest(raw=raw):
                self.assertFalse(cw._credential_is_usable(raw))

    def test_unknown_shapes_pass_through_unjudged(self):
        # An API-key-form credential is not the OAuth shape; this helper
        # must not veto a credential it cannot read.
        self.assertTrue(cw._credential_is_usable('{"apiKey": "sk-x"}'))

    def test_bytes_are_accepted(self):
        raw = json.dumps({"claudeAiOauth": {
            "accessToken": "a", "refreshToken": "r"}}).encode()
        self.assertTrue(cw._credential_is_usable(raw))


class KeychainNamespaceTests(unittest.TestCase):
    """Locks in the CLI's undocumented per-config-dir keychain naming.

    If a CLI release moves this scheme, these fail loudly in CI rather
    than letting the bot silently read a credential nobody is maintaining.
    Both expectations were observed live on the affected machine.
    """

    def test_matches_values_observed_live(self):
        self.assertEqual(
            cw._namespaced_keychain_service(
                Path("/Users/coocooai/MyOldMachine/data/users/8044898180/claude")),
            "Claude Code-credentials-8ca26d87",
        )
        self.assertEqual(
            cw._namespaced_keychain_service(
                Path("/Users/coocooai/MyOldMachine/data/users/8488788773/claude")),
            "Claude Code-credentials-a5656211",
        )

    def test_distinct_config_dirs_get_distinct_services(self):
        self.assertNotEqual(
            cw._namespaced_keychain_service(Path("/a")),
            cw._namespaced_keychain_service(Path("/b")),
        )

    def test_never_resolves_to_the_real_login_item(self):
        # Safety invariant: reconcile DELETES the service this returns, and
        # the unsuffixed "Claude Code-credentials" is the machine's actual
        # login. A derivation that ever collapsed to it would log the human
        # out of Claude Code entirely.
        for path in (Path("/"), Path(""), Path("/Users/x/.claude"),
                     Path(cw._KEYCHAIN_SERVICE)):
            with self.subTest(path=path):
                self.assertNotEqual(
                    cw._namespaced_keychain_service(path), cw._KEYCHAIN_SERVICE)
                self.assertTrue(
                    cw._namespaced_keychain_service(path).startswith(
                        cw._KEYCHAIN_SERVICE + "-"))


class EnsureSharedCredentialsTests(WorkspaceTestBase):
    """A hollow credential file must not count as success forever."""

    def test_usable_file_is_accepted_without_keychain_export(self):
        cred = self.legacy / ".credentials.json"
        cred.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "a", "refreshToken": "r"}}))
        with patch.object(cw, "_export_keychain_credentials") as export:
            self.assertTrue(cw._ensure_shared_credentials(self.legacy))
        export.assert_not_called()

    def test_hollow_file_triggers_keychain_reexport(self):
        cred = self.legacy / ".credentials.json"
        cred.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "", "refreshToken": ""}}))
        with patch.object(cw, "_export_keychain_credentials",
                          return_value=True) as export:
            self.assertTrue(cw._ensure_shared_credentials(self.legacy))
        export.assert_called_once()

    def test_hollow_file_and_dead_keychain_reports_failure(self):
        (self.legacy / ".credentials.json").write_text("{}")
        with patch.object(cw, "_export_keychain_credentials", return_value=False):
            self.assertFalse(cw._ensure_shared_credentials(self.legacy))


class KeychainShadowingTests(WorkspaceTestBase):
    """The 2026-07-20 outage: on macOS a per-config-dir keychain item
    shadows .credentials.json entirely, so every file-based repair in this
    module runs, logs success, and changes nothing the CLI actually reads.
    """

    GOOD = json.dumps({"claudeAiOauth": {
        "accessToken": "fresh", "refreshToken": "fresh-r"}})
    HOLLOW = json.dumps({"claudeAiOauth": {
        "accessToken": "", "refreshToken": ""}})

    def setUp(self) -> None:
        super().setUp()
        (self.legacy / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {
                "accessToken": "old", "refreshToken": "old-r"}})
        )
        self._darwin = patch.object(cw.sys, "platform", "darwin")
        self._darwin.start()

    def tearDown(self) -> None:
        self._darwin.stop()
        super().tearDown()

    def make_workspace(self, uid: int) -> Path:
        return cw.ensure_user_claude_config(
            uid, legacy_root=self.legacy, bot_dir=FAKE_BOT_DIR
        )

    def test_refreshed_item_is_folded_into_shared_file_and_removed(self):
        self.make_workspace(1)
        with patch.object(cw, "_read_keychain_item", return_value=self.GOOD), \
             patch.object(cw, "_delete_keychain_item", return_value=True) as delete:
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)

        self.assertTrue(result)
        self.assertEqual(
            (self.legacy / ".credentials.json").read_text(), self.GOOD)
        # Removed so the workspace rejoins the shared file on its next turn.
        delete.assert_called_once()

    def test_shadowing_is_checked_even_when_the_symlink_is_intact(self):
        # The regression that made this bug invisible: an untouched symlink
        # says nothing about whether a keychain item exists, so returning
        # early on "still a symlink" skipped the check that mattered.
        cfg = self.make_workspace(1)
        self.assertTrue((cfg / ".credentials.json").is_symlink())
        with patch.object(cw, "_read_keychain_item",
                          return_value=self.GOOD) as read, \
             patch.object(cw, "_delete_keychain_item", return_value=True):
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)
        read.assert_called_once()
        self.assertTrue(result)

    def test_unusable_item_is_removed_without_overwriting_shared_file(self):
        # Exactly the outage state, and exactly what the manual recovery
        # did: drop the dead item so the healthy shared file takes over.
        self.make_workspace(1)
        before = (self.legacy / ".credentials.json").read_text()
        with patch.object(cw, "_read_keychain_item", return_value=self.HOLLOW), \
             patch.object(cw, "_delete_keychain_item", return_value=True) as delete:
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)

        self.assertFalse(result)
        self.assertEqual((self.legacy / ".credentials.json").read_text(), before)
        delete.assert_called_once()

    def test_item_is_preserved_when_publishing_fails(self):
        # Never delete the only fresh copy on the strength of a write that
        # did not land.
        self.make_workspace(1)
        with patch.object(cw, "_read_keychain_item", return_value=self.GOOD), \
             patch.object(cw, "_write_shared_credential", return_value=False), \
             patch.object(cw, "_delete_keychain_item") as delete:
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)

        self.assertFalse(result)
        delete.assert_not_called()

    def test_absent_item_is_a_noop(self):
        self.make_workspace(1)
        before = (self.legacy / ".credentials.json").read_text()
        with patch.object(cw, "_read_keychain_item", return_value=None), \
             patch.object(cw, "_delete_keychain_item") as delete:
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)
        self.assertFalse(result)
        self.assertEqual((self.legacy / ".credentials.json").read_text(), before)
        delete.assert_not_called()

    def test_keychain_is_not_consulted_off_darwin(self):
        self.make_workspace(1)
        with patch.object(cw.sys, "platform", "linux"), \
             patch.object(cw, "_read_keychain_item") as read:
            cw.reconcile_shared_credentials(1, legacy_root=self.legacy)
        read.assert_not_called()

    def test_hollow_detached_file_does_not_clobber_good_shared_file(self):
        cfg = self.make_workspace(1)
        before = (self.legacy / ".credentials.json").read_text()
        tmp = cfg / ".credentials.json.tmp"
        tmp.write_text(self.HOLLOW)
        os.replace(tmp, cfg / ".credentials.json")

        with patch.object(cw, "_read_keychain_item", return_value=None):
            result = cw.reconcile_shared_credentials(1, legacy_root=self.legacy)

        self.assertFalse(result)
        self.assertEqual((self.legacy / ".credentials.json").read_text(), before)
        # Still re-linked, so the next turn reads the healthy shared file.
        self.assertTrue((cfg / ".credentials.json").is_symlink())


if __name__ == "__main__":
    unittest.main()
