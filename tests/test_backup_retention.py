"""Tests for tarball backup retention.

Covers:
- _prune_old_tarballs keeps exactly the N newest and removes the rest
- retention of 1 is honoured (never prunes the only archive away)
- unreadable/undeletable archives do not abort the prune
- an interrupted run leaves nothing the prune or the listing can count
- DEFAULT_RETENTION is the single source of truth: no module carries its
  own numeric fallback for backup_retention

The last one is the guard that matters. This default was duplicated across
utils/backup.py, utils/maintenance.py, bot.py and install/wizard.py, so
changing it in one place left the others silently disagreeing. It is a source
scan, so it can only see a literal next to the token; install/wizard.py holds
its default in a prompt argument instead, which that scan cannot match at all.
WizardRetentionDefaultTests drives the wizard step to cover that blind spot.

The interrupted-run case is what makes a retention of 2 mean 2. The prune
selects by filename and never opens a candidate, so before PARTIAL_SUFFIX a
half-written archive counted as a backup and evicted a good one: measured over
one killed night followed by a good one, retention 7 kept 7 archives of which
6 restored, and retention 2 kept 2 of which 1 restored.

Most fixtures are empty files with controlled mtimes, which is all the prune
logic reads. The partial tests drive the real _create_tarball_backup over a
small temp tree, so the suffix and the rename are exercised rather than
asserted about.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import stat
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

os.environ["MOM_TEST"] = "1"  # keep test backup runs out of the production backup.log

import utils.backup as backup  # noqa: E402
from utils.backup import (  # noqa: E402
    DEFAULT_RETENTION,
    PARTIAL_SUFFIX,
    STALE_PARTIAL_AGE_SEC,
    _prune_old_tarballs,
    _sweep_stale_partials,
)

PRUNE_GLOB = "myoldmachine_*.tar.gz"


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


@contextlib.contextmanager
def _fake_bot_dir(retention: int = 2):
    """Point the real _create_tarball_backup at a throwaway tree.

    Yields (source_dir, target_dir). BACKUP_SOURCES is narrowed to one small
    file so the run is fast, and the maintenance config is stubbed so the
    machine's own maintenance.json cannot change the retention under the test.
    """
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
        source = Path(src)
        (source / "payload").mkdir()
        (source / "payload" / "note.txt").write_text("backed up", encoding="utf-8")
        with mock.patch.object(backup, "BOT_DIR", source), \
                mock.patch.object(backup, "BACKUP_SOURCES", ["payload"]), \
                mock.patch.object(backup, "get_maintenance_config",
                                  lambda: {"backup_retention": retention}):
            yield source, Path(dst)


class PartialArchiveTests(unittest.TestCase):
    """An interrupted run must leave nothing that counts as a backup."""

    def test_completed_run_publishes_the_final_name_and_leaves_no_partial(self):
        with _fake_bot_dir() as (_src, target):
            summary = backup._create_tarball_backup(str(target))

            self.assertIn("Backup complete", summary)
            archives = list(target.glob(PRUNE_GLOB))
            self.assertEqual(len(archives), 1, "the finished archive should be published")
            self.assertEqual(list(target.glob(f"*{PARTIAL_SUFFIX}")), [])
            with tarfile.open(str(archives[0]), "r:gz") as tar:
                self.assertIn("payload/note.txt", tar.getnames())

    def test_the_archive_is_written_under_the_partial_suffix(self):
        """Positive test on the mechanism: the bytes never land at the final
        name, so there is no window in which a kill can leave one behind."""
        written_to = []
        real_open = tarfile.open

        def recording_open(name, *a, **kw):
            written_to.append(str(name))
            return real_open(name, *a, **kw)

        with _fake_bot_dir() as (_src, target):
            with mock.patch.object(tarfile, "open", recording_open):
                backup._create_tarball_backup(str(target))

            self.assertEqual(len(written_to), 1)
            self.assertTrue(written_to[0].endswith(PARTIAL_SUFFIX), written_to[0])
            # The name it was written under must be invisible to the prune.
            self.assertNotIn(Path(written_to[0]).name,
                             [p.name for p in target.glob(PRUNE_GLOB)])

    def test_interrupted_write_leaves_nothing_the_prune_can_count(self):
        """The failure path must take the partial with it."""
        real_open = tarfile.open

        def exploding_open(name, *a, **kw):
            tar = real_open(name, *a, **kw)
            tar.add = mock.Mock(side_effect=RuntimeError("target went away mid-write"))
            return tar

        with _fake_bot_dir() as (_src, target):
            with mock.patch.object(tarfile, "open", exploding_open):
                summary = backup._create_tarball_backup(str(target))

            self.assertIn("Backup failed", summary)
            self.assertEqual(list(target.glob(PRUNE_GLOB)), [])
            self.assertEqual(list(target.glob(f"*{PARTIAL_SUFFIX}")), [])

    def test_a_leftover_partial_never_spends_a_retention_slot(self):
        """The eviction this suffix exists to prevent: a killed night followed
        by a good one used to leave 2 archives of which only 1 restored."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            made = _make_archives(target, 2)
            partial = target / f"myoldmachine_2026-08-09_0200.tar.gz{PARTIAL_SUFFIX}"
            partial.write_bytes(b"\x1f\x8b truncated gzip, not restorable")
            # Newest thing in the directory, so a prune that could see it would
            # keep it and drop a good archive instead.
            os.utime(partial, None)

            removed = _prune_old_tarballs(target, 2)

            self.assertEqual(removed, 0)
            survivors = sorted(p.name for p in target.glob(PRUNE_GLOB))
            self.assertEqual(survivors, sorted(p.name for p in made))

    def test_listing_ignores_partials(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            _make_archives(target, 1)
            (target / f"myoldmachine_2026-08-09_0200.tar.gz{PARTIAL_SUFFIX}").write_bytes(b"x")

            listing = backup._list_tarball_backups(str(target))

            self.assertNotIn(PARTIAL_SUFFIX, listing)
            self.assertIn("1 backup(s)", listing)

    def test_sweep_discards_an_abandoned_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            dead = target / f"myoldmachine_2026-08-01_0200.tar.gz{PARTIAL_SUFFIX}"
            dead.write_bytes(b"half an archive")
            stale = time.time() - STALE_PARTIAL_AGE_SEC - 60
            os.utime(dead, (stale, stale))

            self.assertEqual(_sweep_stale_partials(target), 1)
            self.assertFalse(dead.exists())

    def test_sweep_leaves_a_live_partial_alone(self):
        """The nightly job and /maintenance run backup can overlap, and there
        is no lock. Deleting a partial that is still being written would break
        a working backup instead of cleaning up after a dead one."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            live = target / f"myoldmachine_2026-08-10_0200.tar.gz{PARTIAL_SUFFIX}"
            live.write_bytes(b"still being written")
            recent = time.time() - (STALE_PARTIAL_AGE_SEC // 2)
            os.utime(live, (recent, recent))

            self.assertEqual(_sweep_stale_partials(target), 0)
            self.assertTrue(live.exists())

    def test_sweep_never_touches_a_finished_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            made = _make_archives(target, 3)  # mtimes are years in the past

            self.assertEqual(_sweep_stale_partials(target), 0)
            self.assertEqual(len(list(target.glob(PRUNE_GLOB))), len(made))

    def test_a_run_sweeps_before_it_writes(self):
        with _fake_bot_dir() as (_src, target):
            dead = target / f"myoldmachine_2026-08-01_0200.tar.gz{PARTIAL_SUFFIX}"
            dead.write_bytes(b"half an archive")
            stale = time.time() - STALE_PARTIAL_AGE_SEC - 60
            os.utime(dead, (stale, stale))

            backup._create_tarball_backup(str(target))

            self.assertFalse(dead.exists())
            self.assertEqual(len(list(target.glob(PRUNE_GLOB))), 1)

    def test_published_archive_is_still_0600(self):
        """The archive bundles .env in cleartext. The rename must not widen it."""
        with _fake_bot_dir() as (_src, target):
            backup._create_tarball_backup(str(target))
            archive = next(iter(target.glob(PRUNE_GLOB)))

            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)


class WizardRetentionDefaultTests(unittest.TestCase):
    """The source scan above cannot see install/wizard.py's default, which
    lives in a prompt argument rather than next to the config key. Drive the
    step instead, so the number a human is shown at install time is pinned."""

    def _run_step(self, retention_answer: str) -> tuple[dict, dict]:
        from install import wizard

        saved: dict = {}
        prompts: dict = {}

        with tempfile.TemporaryDirectory() as tmp:
            def fake_ask(prompt, default=None, **kw):
                prompts[prompt] = default
                if "target directory" in prompt:
                    return tmp
                if "backups to keep" in prompt:
                    return retention_answer
                return default or ""

            with mock.patch.object(wizard, "ask", fake_ask), \
                    mock.patch.object(wizard, "ask_choice", lambda *a, **kw: "tarball"), \
                    mock.patch("utils.maintenance.update_config",
                               lambda **kw: saved.update(kw)), \
                    contextlib.redirect_stdout(io.StringIO()):
                wizard._run_backup_setup_step({})

        return prompts, saved

    def test_prompt_offers_the_constant_as_its_default(self):
        prompts, _ = self._run_step("")
        prompt = next(p for p in prompts if "backups to keep" in p)
        self.assertEqual(prompts[prompt], str(DEFAULT_RETENTION))

    def test_accepting_the_default_saves_the_constant(self):
        _, saved = self._run_step(str(DEFAULT_RETENTION))
        self.assertEqual(saved["backup_retention"], DEFAULT_RETENTION)

    def test_unparseable_answer_falls_back_to_the_constant(self):
        _, saved = self._run_step("not a number")
        self.assertEqual(saved["backup_retention"], DEFAULT_RETENTION)

    def test_an_explicit_answer_is_honoured(self):
        """Guards the fallback tests above: they must not pass because the
        step ignores the answer and always writes the default."""
        _, saved = self._run_step("5")
        self.assertEqual(saved["backup_retention"], 5)


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
