#!/usr/bin/env python3
"""Bounding the retired-reply folder (utils.cleanup.cleanup_expired_outbox).

Run: python3 -m unittest tests.test_outbox_cleanup  (from repo root)

utils/outbox.py never deletes a reply. An entry it gives up redelivering is
MOVED to outbox/expired/ and logged at ERROR, because destroying a finished
answer is the bug that module exists to prevent. Nothing then emptied that
folder, so it grew for the life of the install.

The contract these tests pin:
  1. A retired entry past the window is pruned, and the prune is logged.
  2. A retired entry inside the window is left alone.
  3. A LIVE queued reply in outbox/ is never touched, however old it is. That
     is the dangerous case: those belong to bot.drain_outbox, and ageing one
     out from here would race a redelivery in flight and destroy exactly the
     answer the outbox exists to save.
  4. The prune window is far longer than the redelivery window, so nothing is
     ever pruned while it is still deliverable.
  5. cleanup.py's copies of the outbox directory names match outbox.py's own.
     They are duplicated on purpose (the nightly job runs cleanup.py as a
     script, where `utils` is not importable), which makes drift possible.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import core.users as users_mod  # noqa: E402
from utils import cleanup, outbox  # noqa: E402

DAY = 86400


class _IsolatedUsersDir(unittest.TestCase):
    """Base: point every user-dir root at a temp tree for the whole test.

    Two roots, not one. cleanup.USERS_DIR is what the sweep walks, and these
    tests DELETE what it finds, so that one is not optional. But
    core.users.USERS_DATA_DIR is redirected too: it is the source of truth any
    later test here would resolve a user dir through, and the guard in
    tests/test_outbox.py holds every queueing module to it after
    tests.test_send_retry was caught leaving fake replies in data/users/1.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.users_dir = Path(self._tmp.name) / "users"
        self.users_dir.mkdir()
        patcher = mock.patch.object(cleanup, "USERS_DIR", self.users_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

        orig_users_data_dir = users_mod.USERS_DATA_DIR
        users_mod.USERS_DATA_DIR = self.users_dir
        self.addCleanup(
            lambda: setattr(users_mod, "USERS_DATA_DIR", orig_users_data_dir)
        )

        # Belt and braces: a patch that silently failed would run this suite
        # against the live tree.
        self.assertEqual(cleanup.USERS_DIR, self.users_dir)
        self.assertNotIn("data/users", str(cleanup.USERS_DIR))

    def _entry(self, user: str, name: str, *, retired: bool, age_days: float) -> Path:
        """Write a queued (or retired) reply and backdate it."""
        directory = self.users_dir / user / cleanup.OUTBOX_DIRNAME
        if retired:
            directory = directory / cleanup.OUTBOX_EXPIRED_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text('{"chunks": ["a finished answer"]}', encoding="utf-8")
        stamp = time.time() - age_days * DAY
        os.utime(path, (stamp, stamp))
        return path


class PrunesRetiredEntriesTests(_IsolatedUsersDir):
    def test_entry_past_the_window_is_removed(self):
        old = self._entry("111", "old.json", retired=True, age_days=31)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 1)
        self.assertFalse(old.exists())

    def test_entry_inside_the_window_is_kept(self):
        recent = self._entry("111", "recent.json", retired=True, age_days=29)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(recent.exists())

    def test_window_is_configurable(self):
        entry = self._entry("111", "old.json", retired=True, age_days=8)
        self.assertEqual(cleanup.cleanup_expired_outbox(max_age_days=30), 0)
        self.assertEqual(cleanup.cleanup_expired_outbox(max_age_days=7), 1)
        self.assertFalse(entry.exists())

    def test_every_user_is_swept(self):
        for user in ("111", "222", "333"):
            self._entry(user, "old.json", retired=True, age_days=40)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 3)

    def test_prune_is_logged(self):
        """Deleting a finished answer must leave a trace, even 30 days on."""
        self._entry("111", "gone.json", retired=True, age_days=31)
        with self.assertLogs(cleanup.logger, level="INFO") as captured:
            cleanup.cleanup_expired_outbox()
        self.assertTrue(
            any("gone.json" in line for line in captured.output),
            f"prune was not logged: {captured.output}",
        )


class NeverTouchesLiveQueueTests(_IsolatedUsersDir):
    """The property that makes this safe to run nightly."""

    def test_live_reply_is_kept_however_old(self):
        live = self._entry("111", "queued.json", retired=False, age_days=400)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(
            live.exists(),
            "a queued reply was deleted; drain_outbox owns those, and the "
            "user is still owed the answer",
        )

    def test_live_reply_survives_alongside_a_retired_one(self):
        live = self._entry("111", "queued.json", retired=False, age_days=400)
        retired = self._entry("111", "old.json", retired=True, age_days=400)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 1)
        self.assertTrue(live.exists())
        self.assertFalse(retired.exists())

    def test_prune_window_outlasts_the_redelivery_window(self):
        """Otherwise this could bin a reply the drain would still have sent."""
        self.assertGreater(
            cleanup.OUTBOX_EXPIRED_MAX_AGE_DAYS * DAY,
            outbox.MAX_AGE_SECONDS,
        )


class StaleWriteLeftoverTests(_IsolatedUsersDir):
    """save_json writes <name>.json.tmp then renames. A process killed in
    between leaves the temp file, which no reader globs and nothing removed."""

    def _tmp_file(self, user: str, name: str, *, retired: bool, age_days: float) -> Path:
        directory = self.users_dir / user / cleanup.OUTBOX_DIRNAME
        if retired:
            directory = directory / cleanup.OUTBOX_EXPIRED_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text('{"partial":', encoding="utf-8")
        stamp = time.time() - age_days * DAY
        os.utime(path, (stamp, stamp))
        return path

    def test_stale_leftover_in_the_queue_is_removed(self):
        stale = self._tmp_file("111", "half.json.tmp", retired=False, age_days=31)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 1)
        self.assertFalse(stale.exists())

    def test_stale_leftover_in_expired_is_removed(self):
        stale = self._tmp_file("111", "half.json.tmp", retired=True, age_days=31)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 1)
        self.assertFalse(stale.exists())

    def test_fresh_leftover_is_left_alone(self):
        """A .tmp seconds old may be a write in progress right now."""
        fresh = self._tmp_file("111", "half.json.tmp", retired=False, age_days=0)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(fresh.exists())


class LeavesEverythingElseAloneTests(_IsolatedUsersDir):
    def test_other_user_files_are_untouched(self):
        user_dir = self.users_dir / "111"
        (user_dir / "attachments").mkdir(parents=True)
        strangers = [
            user_dir / "conversation_2026.json",
            user_dir / "attachments" / "photo.jpg",
            user_dir / "memory.json",
        ]
        for path in strangers:
            path.write_text("keep me", encoding="utf-8")
            stamp = time.time() - 400 * DAY
            os.utime(path, (stamp, stamp))
        self._entry("111", "old.json", retired=True, age_days=400)

        self.assertEqual(cleanup.cleanup_expired_outbox(), 1)
        for path in strangers:
            self.assertTrue(path.exists(), f"{path.name} should be untouched")

    def test_directories_inside_expired_are_not_removed(self):
        nested = (self.users_dir / "111" / cleanup.OUTBOX_DIRNAME
                  / cleanup.OUTBOX_EXPIRED_DIRNAME / "looks_like.json")
        nested.mkdir(parents=True)
        stamp = time.time() - 400 * DAY
        os.utime(nested, (stamp, stamp))
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(nested.is_dir())

    def test_stray_file_in_the_users_root_is_ignored(self):
        stray = self.users_dir / "notes.json"
        stray.write_text("{}", encoding="utf-8")
        stamp = time.time() - 400 * DAY
        os.utime(stray, (stamp, stamp))
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(stray.exists())


class MissingPathsTests(_IsolatedUsersDir):
    def test_no_users_dir_is_not_an_error(self):
        with mock.patch.object(cleanup, "USERS_DIR", self.users_dir / "gone"):
            self.assertEqual(cleanup.cleanup_expired_outbox(), 0)

    def test_user_without_an_outbox_is_skipped(self):
        (self.users_dir / "111").mkdir()
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)

    def test_outbox_without_an_expired_dir_is_skipped(self):
        (self.users_dir / "111" / cleanup.OUTBOX_DIRNAME).mkdir(parents=True)
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)


class FailureIsolationTests(_IsolatedUsersDir):
    def test_one_undeletable_file_does_not_stop_the_sweep(self):
        first = self._entry("111", "a_locked.json", retired=True, age_days=40)
        second = self._entry("111", "b_fine.json", retired=True, age_days=40)

        real_unlink = Path.unlink

        def flaky(self, *args, **kwargs):
            if self.name == "a_locked.json":
                raise OSError("read-only filesystem")
            return real_unlink(self, *args, **kwargs)

        with mock.patch.object(Path, "unlink", flaky), \
             self.assertLogs(cleanup.logger, level="WARNING"):
            removed = cleanup.cleanup_expired_outbox()

        self.assertEqual(removed, 1)
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())


class AgreesWithTheOutboxModuleTests(_IsolatedUsersDir):
    """cleanup.py hardcodes the directory names, so pin them to the source."""

    def test_directory_names_match(self):
        self.assertEqual(cleanup.OUTBOX_DIRNAME, outbox.OUTBOX_DIRNAME)
        self.assertEqual(cleanup.OUTBOX_EXPIRED_DIRNAME, outbox.EXPIRED_DIRNAME)

    def test_prunes_a_genuinely_retired_entry(self):
        """End to end through outbox.record + outbox.retire, so the two
        modules must agree on the real path shape, not just the constants."""
        user_dir = self.users_dir / "111"
        user_dir.mkdir()
        with self.assertLogs(outbox.logger, level="WARNING"):
            path = outbox.record(user_dir, 111, ["a finished answer"], sent=0)
        self.assertIsNotNone(path)

        with self.assertLogs(outbox.logger, level="ERROR"):
            retired = outbox.retire(path, "test: exhausted attempts")
        self.assertIsNotNone(retired)
        self.assertTrue(retired.exists())

        # Still inside the window: nothing goes yet.
        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(retired.exists())

        stamp = time.time() - 31 * DAY
        os.utime(retired, (stamp, stamp))
        self.assertEqual(cleanup.cleanup_expired_outbox(), 1)
        self.assertFalse(retired.exists())

    def test_a_live_recorded_entry_is_never_pruned(self):
        user_dir = self.users_dir / "111"
        user_dir.mkdir()
        with self.assertLogs(outbox.logger, level="WARNING"):
            path = outbox.record(user_dir, 111, ["a finished answer"], sent=0)
        stamp = time.time() - 400 * DAY
        os.utime(path, (stamp, stamp))

        self.assertEqual(cleanup.cleanup_expired_outbox(), 0)
        self.assertTrue(path.exists())
        # And it is still a valid, redeliverable entry.
        self.assertIsNotNone(outbox.load(path))


class RunCleanupReportTests(_IsolatedUsersDir):
    """The nightly job runs run_cleanup(); the sweep has to be wired into it."""

    def setUp(self):
        super().setUp()
        # Keep run_cleanup off the real machine: its own log dir, and no
        # sweep of the shared /tmp.
        log_dir = Path(self._tmp.name) / "logs"
        log_dir.mkdir()
        for patcher in (
            mock.patch.object(cleanup, "LOG_DIR", log_dir),
            mock.patch.object(cleanup, "cleanup_temp", return_value=0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_report_counts_pruned_replies(self):
        self._entry("111", "old.json", retired=True, age_days=40)
        self._entry("222", "old.json", retired=True, age_days=40)
        with self.assertLogs(cleanup.logger, level="INFO"):
            report = cleanup.run_cleanup()
        self.assertIn("Retired undelivered replies removed: 2", report)

    def test_dry_run_removes_nothing(self):
        entry = self._entry("111", "old.json", retired=True, age_days=40)
        report = cleanup.run_cleanup(dry_run=True)
        self.assertIn("DRY RUN", report)
        self.assertTrue(entry.exists())


if __name__ == "__main__":
    unittest.main()
