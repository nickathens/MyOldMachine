"""Unit tests for utils.project_manager.

Covers the per-user ownership model: projects are private by default,
share/unshare move between visibility states, and the CLI refuses cross-user
reads when callers do not have admin privileges.
"""
from __future__ import annotations

import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(tmp_memory: Path):
    """Import project_manager with PROJECTS_DIR pointed at a temp dir."""
    sys.modules.pop("utils.project_manager", None)
    pm = importlib.import_module("utils.project_manager")
    pm.MEMORY_DIR = tmp_memory
    pm.PROJECTS_DIR = tmp_memory / "projects"
    pm.DECISIONS_DIR = tmp_memory / "decisions"
    pm.TOPICS_DIR = tmp_memory / "topics"
    pm.ensure_dirs()
    return pm


class OwnerSemanticsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pm = _load(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def _read(self, slug):
        return json.loads(
            (self.pm.PROJECTS_DIR / slug / "state.json").read_text()
        )

    def test_create_with_user_is_private_to_user(self):
        self.pm.create_project(
            "Foo", "summary", str(self.tmp / "loc"), user_id=111
        )
        self.assertEqual(self._read("foo")["owner"], "111")

    def test_create_with_shared_flag_is_shared(self):
        self.pm.create_project(
            "Foo", "summary", str(self.tmp / "loc"),
            user_id=111, shared=True,
        )
        self.assertEqual(self._read("foo")["owner"], "shared")

    def test_create_without_user_defaults_to_shared(self):
        self.pm.create_project("Foo", "summary", str(self.tmp / "loc"))
        self.assertEqual(self._read("foo")["owner"], "shared")

    def test_legacy_project_with_no_owner_is_visible_to_everyone(self):
        # Simulate a project written by the pre-ownership version.
        slug = "legacy"
        (self.pm.PROJECTS_DIR / slug).mkdir(parents=True)
        (self.pm.PROJECTS_DIR / slug / "state.json").write_text(json.dumps({
            "name": "Legacy", "slug": slug, "status": "in_progress",
            "summary": "old", "location": str(self.tmp / "old"),
        }))
        state = self._read(slug)
        self.assertNotIn("owner", state)
        self.assertTrue(self.pm._can_see(state, user_id=222))
        self.assertTrue(self.pm._can_see(state, user_id=None))


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pm = _load(self.tmp)
        # 111 owns "alpha", 222 owns "beta", "gamma" is shared
        self.pm.create_project("Alpha", "s", str(self.tmp / "a"), user_id=111)
        self.pm.create_project("Beta", "s", str(self.tmp / "b"), user_id=222)
        self.pm.create_project("Gamma", "s", str(self.tmp / "g"),
                               user_id=111, shared=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _list_output(self, user_id=None, show_all=False):
        from io import StringIO
        buf = StringIO()
        with patch("sys.stdout", buf):
            with patch.object(self.pm, "_is_admin", return_value=show_all):
                self.pm.list_projects(user_id=user_id, show_all=show_all)
        return buf.getvalue()

    def test_user_sees_own_and_shared(self):
        out = self._list_output(user_id=111)
        self.assertIn("Alpha", out)
        self.assertNotIn("Beta", out)
        self.assertIn("Gamma", out)

    def test_other_user_does_not_see_private(self):
        out = self._list_output(user_id=222)
        self.assertIn("Beta", out)
        self.assertNotIn("Alpha", out)
        self.assertIn("Gamma", out)

    def test_status_refuses_when_not_owner(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.pm.get_project_status("alpha", user_id=222)
        self.assertEqual(cm.exception.code, 2)

    def test_admin_can_see_other_users_private(self):
        with patch.object(self.pm, "_is_admin", return_value=True):
            # No exit means it printed normally
            self.pm.get_project_status("alpha", user_id=999)

    def test_list_all_requires_admin(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            with self.assertRaises(SystemExit):
                self.pm.list_projects(user_id=222, show_all=True)


class ShareUnshareTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pm = _load(self.tmp)
        self.pm.create_project("Foo", "s", str(self.tmp / "f"), user_id=111)

    def tearDown(self):
        self._tmp.cleanup()

    def _owner(self, slug="foo"):
        return json.loads(
            (self.pm.PROJECTS_DIR / slug / "state.json").read_text()
        )["owner"]

    def test_owner_can_share(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            self.pm.share_project("foo", user_id=111)
        self.assertEqual(self._owner(), "shared")

    def test_non_owner_cannot_share(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                self.pm.share_project("foo", user_id=222)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self._owner(), "111")

    def test_admin_can_share_anyones_project(self):
        with patch.object(self.pm, "_is_admin", return_value=True):
            self.pm.share_project("foo", user_id=999)
        self.assertEqual(self._owner(), "shared")

    def test_unshare_claims_to_caller(self):
        # First make it shared
        with patch.object(self.pm, "_is_admin", return_value=False):
            self.pm.share_project("foo", user_id=111)
        # 222 claims it
        with patch.object(self.pm, "_is_admin", return_value=False):
            self.pm.unshare_project("foo", user_id=222)
        self.assertEqual(self._owner(), "222")

    def test_unshare_for_other_user_requires_admin(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            self.pm.share_project("foo", user_id=111)
        with patch.object(self.pm, "_is_admin", return_value=False):
            with self.assertRaises(SystemExit):
                self.pm.unshare_project("foo", user_id=222, claim_for=333)
        with patch.object(self.pm, "_is_admin", return_value=True):
            self.pm.unshare_project("foo", user_id=222, claim_for=333)
        self.assertEqual(self._owner(), "333")

    def test_unshare_private_project_refused(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            with self.assertRaises(SystemExit):
                self.pm.unshare_project("foo", user_id=222)


class UpdatePermissionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pm = _load(self.tmp)
        self.pm.create_project("Foo", "s", str(self.tmp / "f"), user_id=111)

    def tearDown(self):
        self._tmp.cleanup()

    def test_owner_can_update(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            self.pm.update_project("foo", user_id=111, status="active")
        state = json.loads(
            (self.pm.PROJECTS_DIR / "foo" / "state.json").read_text()
        )
        self.assertEqual(state["status"], "active")

    def test_non_owner_cannot_update(self):
        with patch.object(self.pm, "_is_admin", return_value=False):
            with self.assertRaises(SystemExit):
                self.pm.update_project("foo", user_id=222, status="done")


if __name__ == "__main__":
    unittest.main()
