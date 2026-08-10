"""Tests for tarball backup retention.

Covers:
- _prune_old_tarballs keeps exactly the N newest and removes the rest
- retention of 1 is honoured (never prunes the only archive away)
- unreadable/undeletable archives do not abort the prune
- DEFAULT_RETENTION is the single source of truth: no module carries its
  own numeric fallback for backup_retention

The last one is the guard that matters. This default was duplicated across
utils/backup.py, utils/maintenance.py, bot.py and install/wizard.py, so
changing it in one place left the others silently disagreeing.

No real archives are written: the fixtures are empty files with controlled
mtimes, which is all the prune logic reads.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from utils.backup import DEFAULT_RETENTION, _prune_old_tarballs  # noqa: E402


def _make_archives(target: Path, count: int) -> list[Path]:
    """Create `count` fake archives, oldest first, one day apart."""
    made = []
    for i in range(count):
        f = target / f"myoldmachine_2026-08-{i + 1:02d}_0200.tar.gz"
        f.write_bytes(b"")
        # Ascending mtime so index 0 is oldest, index -1 is newest.
        stamp = 1_700_000_000 + i * 86_400
        os.utime(f, (stamp, stamp))
        made.append(f)
    return made


class PruneOldTarballsTests(unittest.TestCase):

    def test_keeps_exactly_the_n_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            made = _make_archives(target, 7)

            removed = _prune_old_tarballs(target, 2)

            self.assertEqual(removed, 5)
            survivors = sorted(p.name for p in target.glob("*.tar.gz"))
            self.assertEqual(survivors, sorted(p.name for p in made[-2:]))

    def test_no_op_when_under_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            _make_archives(target, 2)

            self.assertEqual(_prune_old_tarballs(target, 2), 0)
            self.assertEqual(len(list(target.glob("*.tar.gz"))), 2)

    def test_retention_of_one_keeps_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            made = _make_archives(target, 3)

            self.assertEqual(_prune_old_tarballs(target, 1), 2)
            survivors = [p.name for p in target.glob("*.tar.gz")]
            self.assertEqual(survivors, [made[-1].name])

    def test_ignores_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            _make_archives(target, 4)
            (target / "notes.txt").write_text("keep me")
            (target / "other_2026-08-01.tar.gz").write_bytes(b"")

            _prune_old_tarballs(target, 1)

            self.assertTrue((target / "notes.txt").exists())
            self.assertTrue((target / "other_2026-08-01.tar.gz").exists())

    def test_undeletable_archive_does_not_abort_the_prune(self):
        """One failure must not leave the rest of the backlog unpruned."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            _make_archives(target, 5)

            real_unlink = Path.unlink
            calls = {"n": 0}

            def flaky_unlink(self, *a, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("permission denied")
                return real_unlink(self, *a, **kw)

            with mock.patch.object(Path, "unlink", flaky_unlink):
                removed = _prune_old_tarballs(target, 1)

            # 4 candidates, first raised, remaining 3 still deleted.
            self.assertEqual(removed, 3)
            self.assertEqual(len(list(target.glob("*.tar.gz"))), 2)


class SingleSourceOfTruthTests(unittest.TestCase):

    def test_default_is_a_sane_small_count(self):
        self.assertIsInstance(DEFAULT_RETENTION, int)
        self.assertGreaterEqual(DEFAULT_RETENTION, 1)

    def test_maintenance_default_config_uses_the_constant(self):
        from utils.maintenance import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG["backup_retention"], DEFAULT_RETENTION)

    def test_no_module_carries_its_own_numeric_fallback(self):
        """`config.get("backup_retention", 7)` in any module re-introduces the
        drift this constant exists to prevent."""
        pattern = re.compile(
            r"""backup_retention["']\s*,\s*(\d+)"""
        )
        sources = [
            REPO_DIR / "bot.py",
            REPO_DIR / "utils" / "backup.py",
            REPO_DIR / "utils" / "maintenance.py",
            REPO_DIR / "install" / "wizard.py",
        ]
        offenders = []
        for src in sources:
            if not src.exists():
                continue
            for lineno, line in enumerate(
                src.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(f"{src.name}:{lineno}: {line.strip()}")

        self.assertEqual(
            offenders, [],
            "numeric backup_retention fallback found; use "
            "utils.backup.DEFAULT_RETENTION instead:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
