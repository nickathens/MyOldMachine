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


if __name__ == "__main__":
    unittest.main()
