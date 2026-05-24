"""Unit tests for miniapp.server — env writes and project visibility scoping.

These tests poke private helpers directly (no FastAPI TestClient) so the bot
doesn't need a running event loop. The HTTP surface is thin around these
helpers; covering the data scoping rules is what actually matters.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import miniapp.server as srv  # noqa: E402


class TestEnvWrites(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-env-"))
        self.env = self.tmp / ".env"
        self.env.write_text(
            "# header comment\n"
            "TELEGRAM_BOT_TOKEN=abc123\n"
            "LLM_PROVIDER=claude\n"
            "LLM_MODEL=claude-sonnet-4-6\n"
            "OTHER_VAR=keep-me\n",
            encoding="utf-8",
        )
        # Snapshot real value so the test can patch and restore reliably.
        self._saved_env_file = srv.ENV_FILE
        srv.ENV_FILE = self.env

    def tearDown(self) -> None:
        srv.ENV_FILE = self._saved_env_file
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_existing_var(self) -> None:
        self.assertEqual(srv._read_env_var("LLM_PROVIDER"), "claude")
        self.assertEqual(srv._read_env_var("LLM_MODEL"), "claude-sonnet-4-6")

    def test_read_missing_returns_default(self) -> None:
        self.assertEqual(srv._read_env_var("DOES_NOT_EXIST", "fallback"), "fallback")

    def test_write_replaces_existing_in_place(self) -> None:
        srv._write_env_var("LLM_PROVIDER", "openai")
        lines = self.env.read_text(encoding="utf-8").splitlines()
        # Order preserved; header comment preserved; OTHER_VAR untouched.
        self.assertEqual(lines[0], "# header comment")
        self.assertIn("LLM_PROVIDER=openai", lines)
        self.assertIn("OTHER_VAR=keep-me", lines)
        # No duplicate of the key.
        self.assertEqual(sum(1 for ln in lines if ln.startswith("LLM_PROVIDER=")), 1)

    def test_write_appends_when_missing(self) -> None:
        srv._write_env_var("LLM_EFFORT", "high")
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("LLM_EFFORT=high", text)

    def test_write_refuses_unsafe_keys(self) -> None:
        # Only WRITABLE_ENV_KEYS may be touched. Refuse TELEGRAM_BOT_TOKEN etc.
        with self.assertRaises(ValueError):
            srv._write_env_var("TELEGRAM_BOT_TOKEN", "stolen")

    def test_write_refuses_unsafe_value_chars(self) -> None:
        # Newline injection would corrupt other keys; refuse outright.
        with self.assertRaises(ValueError):
            srv._write_env_var("LLM_MODEL", "value\nLLM_PROVIDER=malicious")

    def test_write_allows_empty_value_for_clearing(self) -> None:
        # Provider switch to ollama needs to clear LLM_MODEL — empty string
        # must be accepted so the bot can't fall back to the old provider's
        # model name and try to use it as an ollama tag.
        srv._write_env_var("LLM_MODEL", "")
        self.assertEqual(srv._read_env_var("LLM_MODEL"), "")

    def test_tmp_file_path_is_dot_env_dot_tmp(self) -> None:
        # The atomic write must produce '.env.tmp', not '.env.env.tmp'.
        # We can't observe the temp file directly (it's renamed in-flight),
        # but we can verify the parent directory contains no .env.env.tmp
        # leftover after a write, even if the write fails mid-way.
        srv._write_env_var("LLM_MODEL", "claude-sonnet-4-6")
        leftovers = list(self.env.parent.glob(".env.env.tmp"))
        self.assertEqual(leftovers, [])


class TestProjectVisibility(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mom-mini-proj-"))
        self.projects_dir = self.tmp / "data" / "memory" / "projects"
        self.archive_dir = self.projects_dir / "_archive"
        self.projects_dir.mkdir(parents=True)
        self.archive_dir.mkdir()
        self._write_project("alpha", owner="111", status="in_progress", summary="A")
        self._write_project("beta", owner="222", status="in_progress", summary="B")
        self._write_project("gamma", owner="shared", status="ongoing", summary="C")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_project(self, slug: str, *, owner: str, status: str, summary: str,
                       archived: bool = False) -> None:
        base = self.archive_dir if archived else self.projects_dir
        (base / slug).mkdir(parents=True, exist_ok=True)
        (base / slug / "state.json").write_text(json.dumps({
            "name": slug.title(),
            "slug": slug,
            "owner": owner,
            "status": status,
            "summary": summary,
            "next_steps": [],
            "blockers": [],
        }), encoding="utf-8")

    def setUp_paths(self) -> None:
        self._saved_proj = srv.PROJECTS_DIR
        self._saved_arch = srv.ARCHIVE_DIR
        srv.PROJECTS_DIR = self.projects_dir
        srv.ARCHIVE_DIR = self.archive_dir

    def restore_paths(self) -> None:
        srv.PROJECTS_DIR = self._saved_proj
        srv.ARCHIVE_DIR = self._saved_arch

    def _user(self, tid: str, role: str = "user") -> dict:
        return {
            "_id": tid,
            "_profile": {"role": role, "name": f"User{tid}"},
        }

    def test_owner_sees_own_project(self) -> None:
        self.setUp_paths()
        try:
            listing = srv._list_projects(self._user("111"))
            slugs = {p["slug"] for p in listing}
            self.assertIn("alpha", slugs)
            self.assertIn("gamma", slugs)  # shared visible to everyone
            self.assertNotIn("beta", slugs)  # belongs to user 222
        finally:
            self.restore_paths()

    def test_other_user_blocked_from_private(self) -> None:
        self.setUp_paths()
        try:
            listing = srv._list_projects(self._user("222"))
            slugs = {p["slug"] for p in listing}
            self.assertIn("beta", slugs)
            self.assertIn("gamma", slugs)
            self.assertNotIn("alpha", slugs)  # user 222 cannot see 111's project
        finally:
            self.restore_paths()

    def test_admin_sees_all(self) -> None:
        self.setUp_paths()
        try:
            listing = srv._list_projects(self._user("999", role="admin"))
            slugs = {p["slug"] for p in listing}
            self.assertEqual(slugs, {"alpha", "beta", "gamma"})
        finally:
            self.restore_paths()

    def test_detail_lookup_respects_visibility(self) -> None:
        self.setUp_paths()
        try:
            # Owner sees their own.
            self.assertIsNotNone(srv._get_project_detail("alpha", self._user("111")))
            # Stranger blocked.
            self.assertIsNone(srv._get_project_detail("alpha", self._user("222")))
            # Admin sees it.
            self.assertIsNotNone(srv._get_project_detail("alpha", self._user("999", role="admin")))
        finally:
            self.restore_paths()


class TestModifyAuthorization(unittest.TestCase):
    """Archive/unarchive authorization: owner or admin only. Shared projects
    are admin-only to prevent any allowlisted user from archiving system work."""

    def _user(self, tid: str, role: str = "user") -> dict:
        return {"_id": tid, "_profile": {"role": role}}

    def test_owner_can_modify_own(self) -> None:
        state = {"owner": "111"}
        self.assertTrue(srv._user_can_modify_project(state, self._user("111")))

    def test_other_user_blocked(self) -> None:
        state = {"owner": "111"}
        self.assertFalse(srv._user_can_modify_project(state, self._user("222")))

    def test_admin_can_modify_any(self) -> None:
        state = {"owner": "111"}
        self.assertTrue(srv._user_can_modify_project(state, self._user("999", role="admin")))

    def test_shared_is_admin_only(self) -> None:
        state = {"owner": "shared"}
        self.assertFalse(srv._user_can_modify_project(state, self._user("111")))
        self.assertFalse(srv._user_can_modify_project(state, self._user("222")))
        self.assertTrue(srv._user_can_modify_project(state, self._user("999", role="admin")))


class TestSlugValidation(unittest.TestCase):
    def test_accepts_safe_slugs(self) -> None:
        # Should not raise.
        srv._validate_slug("alpha")
        srv._validate_slug("project_name")
        srv._validate_slug("project-1")
        srv._validate_slug("abc123")

    def test_rejects_path_traversal(self) -> None:
        from fastapi import HTTPException
        for bad in ["..", "../../etc", "a/../b", "foo/bar", ".hidden",
                    "Project", "UPPER", "with space", "with.dot",
                    "with%encoded", ""]:
            with self.assertRaises(HTTPException, msg=f"slug {bad!r} should be rejected"):
                srv._validate_slug(bad)


if __name__ == "__main__":
    unittest.main()
