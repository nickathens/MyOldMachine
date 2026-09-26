#!/usr/bin/env python3
"""The archive sweep's dependency search stays on the file's own drive.

Run: python3 -m unittest tests.test_archive_search  (from repo root)

Proven 2026-09-25 with py-spy on MOM's suite: sweep() looked for other ledgers
naming a condemned file by walking its folder and two parents. From a fixture
at /tmp/tmpX/master.mov the parents were /tmp and "/", so one sweep walked every
mount on the machine, the external backup drive included, and opened every file
whose name began with "sha" (shape_partial.py, shadow passes). The same climb
from /Volumes/Film/master.mov walks every mounted volume on a Mac.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "postproduction" / "scripts"


def _load():
    """archive.py imports its siblings (_common, prove) by bare name and puts
    its own folder on sys.path. Other skills have a _common of their own, and
    test discovery imports every test file before any test runs, so the path
    and the sibling modules are put back once archive is loaded: left behind,
    they handed the greek-law tests the wrong _common."""
    saved_path = list(sys.path)
    saved = {n: sys.modules.get(n) for n in ("_common", "prove")}
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("pp_archive_search", SCRIPTS / "archive.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved_path
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return mod


arc = _load()


class SearchRootsTests(unittest.TestCase):
    def test_never_climbs_to_the_filesystem_root(self):
        with mock.patch.object(arc.os.path, "ismount", return_value=False):
            roots = arc._search_roots(["/tmp/tmpabc/master.mov"])
        self.assertEqual(roots, ["/tmp"])

    def test_stops_at_the_drive_the_file_sits_on(self):
        with mock.patch.object(arc.os.path, "ismount",
                               side_effect=lambda d: d == "/Volumes/Film"):
            # Without the stop both of these would climb to /Volumes.
            self.assertEqual(arc._search_roots(["/Volumes/Film/master.mov"]),
                             ["/Volumes/Film"])
            self.assertEqual(arc._search_roots(["/Volumes/Film/v2/master.mov"]),
                             ["/Volumes/Film"])

    def test_three_levels_on_an_ordinary_path(self):
        with mock.patch.object(arc.os.path, "ismount", return_value=False):
            roots = arc._search_roots(["/home/u/film/deliveries/v2/master.mov"])
        # v2, deliveries and film; film holds the other two.
        self.assertEqual(roots, ["/home/u/film"])

    def test_a_folder_inside_another_root_is_not_walked_twice(self):
        with mock.patch.object(arc.os.path, "ismount", return_value=False):
            roots = arc._search_roots(["/p/a/b/one.mov", "/p/a/two.mov", "/q/r/s/t/three.mov"])
        self.assertEqual(roots, ["/p", "/q/r"])


class WalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name) / "film"
        self.v2 = base / "deliveries" / "v2"
        self.v3 = base / "deliveries" / "v3"
        self.v2.mkdir(parents=True)
        self.v3.mkdir(parents=True)
        self.master = self.v2 / "OLD_MASTER.mov"
        self.master.write_text("old")

    def _ledger(self, folder: Path, name: str):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps({"entries": [{"path": str(self.master)}]}))
        return folder / name

    def test_a_newer_versions_ledger_is_still_found(self):
        led = self._ledger(self.v3, "SHA256.json")
        hits = arc._referenced_elsewhere([str(self.master)])
        self.assertEqual([h["named_in"] for h in hits], [str(led)])

    def test_every_ledger_name_the_skill_writes_is_read(self):
        names = ["SHA256.json", "DEPS_SHA256.json", "sha256sums.txt", "shasums",
                 "sha_list.json", "reel.sha256", "reel.sha256.txt"]
        for n in names:
            self._ledger(self.v3 / n.replace(".", "_") , n)
        found = sorted(Path(h["named_in"]).name for h in arc._referenced_elsewhere([str(self.master)]))
        self.assertEqual(found, sorted(names))

    def test_media_named_like_sha_is_not_opened(self):
        for n in ("shadow_v003.exr", "shape_matte.mov", "sharpen_pass.dpx"):
            self._ledger(self.v3, n)
        opened = []
        real_open = open

        def spy(path, *a, **k):
            opened.append(os.path.basename(str(path)))
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", spy):
            hits = arc._referenced_elsewhere([str(self.master)])
        self.assertEqual(hits, [])
        self.assertEqual(opened, [])

    def test_an_oversized_ledger_is_skipped(self):
        self._ledger(self.v3, "SHA256.json")
        with mock.patch.object(arc, "LEDGER_MAX_BYTES", 10):
            self.assertEqual(arc._referenced_elsewhere([str(self.master)]), [])

    def test_a_disk_mounted_inside_the_tree_is_not_entered(self):
        other = self.v3.parent / "mounted_backup"
        self._ledger(other, "SHA256.json")
        real_lstat = os.lstat
        other_s = str(other)

        def lstat(path, *a, **k):
            st = real_lstat(path, *a, **k)
            if str(path) == other_s:
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1) + tuple(st)[3:])
            return st
        with mock.patch.object(arc.os, "lstat", lstat):
            self.assertEqual(arc._referenced_elsewhere([str(self.master)]), [])
        # Control: on the same drive the same ledger is found.
        self.assertEqual(len(arc._referenced_elsewhere([str(self.master)])), 1)


if __name__ == "__main__":
    unittest.main()
