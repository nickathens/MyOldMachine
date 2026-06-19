"""Tests for references.py bounded discovery (resolve_projects_root + find_work).

The point of find_work is to give the agent a SAFE way to locate past work so it
never improvises an unbounded `find ~`. These tests pin the three properties that
make it safe: it stays inside the root, it is depth-bounded, and it stops on a
time/result budget instead of running forever.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import references  # noqa: E402


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


class ResolveProjectsRootTests(unittest.TestCase):
    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"MOM_PROJECTS_DIR": "/tmp/custom-root"}):
            self.assertEqual(references.resolve_projects_root(), Path("/tmp/custom-root"))

    def test_env_override_expands_user(self):
        with mock.patch.dict(os.environ, {"MOM_PROJECTS_DIR": "~/decks"}):
            self.assertEqual(
                references.resolve_projects_root(), Path("~/decks").expanduser()
            )

    def test_default_is_install_data_memory_projects(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            root = references.resolve_projects_root()
        self.assertEqual(root.parts[-3:], ("data", "memory", "projects"))


class FindWorkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # depth 0..3 artifacts + a non-artifact that must be ignored
        _touch(self.root / "a.html")
        _touch(self.root / "d1" / "b.pdf")
        _touch(self.root / "d1" / "d2" / "c.html")
        _touch(self.root / "d1" / "d2" / "d3" / "deep.html")
        _touch(self.root / "notes.txt")

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_artifacts_and_ignores_non_artifacts(self):
        res = references.find_work(root=self.root)
        self.assertEqual(res["root"], str(self.root))
        self.assertFalse(res["truncated"])
        self.assertIn("a.html", res["matches"])
        self.assertIn(str(Path("d1") / "b.pdf"), res["matches"])
        self.assertNotIn("notes.txt", res["matches"])

    def test_results_are_sorted(self):
        res = references.find_work(root=self.root)
        self.assertEqual(res["matches"], sorted(res["matches"]))

    def test_query_filters_case_insensitively(self):
        _touch(self.root / "Treatment.html")
        res = references.find_work("treatment", root=self.root)
        self.assertEqual(res["matches"], ["Treatment.html"])

    def test_max_depth_prunes_deeper_dirs(self):
        res = references.find_work(root=self.root, max_depth=2)
        joined = "|".join(res["matches"])
        self.assertIn("a.html", res["matches"])  # depth 0
        self.assertIn(str(Path("d1") / "d2" / "c.html"), res["matches"])  # depth 2
        self.assertNotIn("deep.html", joined)  # depth 3, pruned

    def test_missing_root_returns_empty_no_fallback(self):
        ghost = self.root / "does-not-exist"
        res = references.find_work(root=ghost)
        self.assertEqual(res["matches"], [])
        self.assertEqual(res["root"], str(ghost))  # reported root, not a home scan
        self.assertEqual(res["reason"], "projects root does not exist")

    def test_result_cap_truncates(self):
        res = references.find_work(root=self.root, result_cap=1)
        self.assertEqual(len(res["matches"]), 1)
        self.assertTrue(res["truncated"])
        self.assertEqual(res["reason"], "result cap reached")

    def test_time_budget_truncates(self):
        # pre-loop monotonic() sets the deadline; the loop-top read jumps past it.
        with mock.patch.object(references.time, "monotonic", side_effect=[100.0, 100.9]):
            res = references.find_work(root=self.root, time_budget=0.5)
        self.assertTrue(res["truncated"])
        self.assertEqual(res["reason"], "time budget exceeded")

    def test_symlink_out_of_root_is_not_followed(self):
        outside = Path(self._tmp.name).parent / f"outside-{os.getpid()}"
        try:
            _touch(outside / "secret.html")
            link = self.root / "ext"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            res = references.find_work(root=self.root, max_depth=4)
            self.assertNotIn("secret.html", "|".join(res["matches"]))
        finally:
            for p in sorted(outside.rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            if outside.exists():
                outside.rmdir()

    def test_skips_heavy_and_hidden_dirs(self):
        _touch(self.root / "node_modules" / "pkg.html")
        _touch(self.root / ".git" / "hook.html")
        res = references.find_work(root=self.root)
        joined = "|".join(res["matches"])
        self.assertNotIn("node_modules", joined)
        self.assertNotIn(".git", joined)


if __name__ == "__main__":
    unittest.main()
