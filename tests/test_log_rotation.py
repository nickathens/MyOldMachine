"""Service log rotation and service-template log routing.

Two related defects are covered here:

1. The install templates pointed launchd/systemd stdout+stderr at
   {{LOG_DIR}}/bot.log, the same path bot.py owns via a RotatingFileHandler.
   Two independent writers on one file means Python's rollover renames the
   inode out from under the supervisor, which then appends to bot.log.1
   forever until backupCount unlinks it mid-write.

2. The supervisor-owned logs had no rotation of their own, leaving
   cleanup_logs' 50 MB truncate-to-last-1 MB as the only bound. That keeps
   disk in check but throws away the crash output you would actually want.
"""

import gzip
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import cleanup  # noqa: E402

REPO = Path(__file__).parent.parent
TEMPLATES = REPO / "install" / "templates"


class ServiceTemplateLogRoutingTests(unittest.TestCase):
    """Templates must never route supervisor output at an app-owned log."""

    # Paths bot.py / the miniapp own via RotatingFileHandler.
    APP_OWNED = ("bot.log", "miniapp.log")

    def _log_targets(self, name: str) -> list[str]:
        """Every {{LOG_DIR}}/<file> the template sends stdout/stderr to.

        Comments are stripped first: both templates document the bot.log path
        they must avoid, and that prose is not a log target.
        """
        text = (TEMPLATES / name).read_text()
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)  # plist
        text = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)  # systemd
        return re.findall(r"\{\{LOG_DIR\}\}/(\S+?\.log)", text)

    def test_templates_do_not_write_to_app_owned_logs(self):
        for name in (
            "com.myoldmachine.bot.plist",
            "com.myoldmachine.miniapp.plist",
            "myoldmachine.service",
            "myoldmachine-miniapp.service",
        ):
            targets = self._log_targets(name)
            self.assertTrue(targets, f"{name}: no log target found")
            for target in targets:
                self.assertNotIn(
                    target,
                    self.APP_OWNED,
                    f"{name} routes supervisor output to {target}, which the "
                    f"app rotates itself. Use a .launchd.log/.systemd.log path.",
                )

    def test_template_targets_are_rotatable(self):
        """Targets must match SERVICE_LOG_PATTERNS or nothing rotates them."""
        for name in (
            "com.myoldmachine.bot.plist",
            "com.myoldmachine.miniapp.plist",
            "myoldmachine.service",
            "myoldmachine-miniapp.service",
        ):
            for target in self._log_targets(name):
                self.assertTrue(
                    cleanup._is_service_log(Path(target)),
                    f"{name}: {target} matches no SERVICE_LOG_PATTERNS entry, "
                    f"so rotate_service_logs would never bound it.",
                )


class RotateServiceLogsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.log_dir = Path(self._tmp.name)
        patcher = mock.patch.object(cleanup, "LOG_DIR", self.log_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name: str, mb: float) -> Path:
        p = self.log_dir / name
        p.write_bytes(b"x" * int(mb * 1024 * 1024))
        return p

    def test_oversized_log_is_archived_and_truncated(self):
        log = self._write("bot.launchd.log", 2)
        self.assertEqual(cleanup.rotate_service_logs(max_size_mb=1), 1)

        self.assertEqual(log.stat().st_size, 0, "log should be emptied in place")
        archives = list(self.log_dir.glob("bot.launchd.until-*.log.gz"))
        self.assertEqual(len(archives), 1)

    def test_archive_preserves_original_content(self):
        log = self.log_dir / "bot.launchd.log"
        payload = b"crash traceback\n" * 200_000
        log.write_bytes(payload)

        cleanup.rotate_service_logs(max_size_mb=1)

        archive = next(self.log_dir.glob("bot.launchd.until-*.log.gz"))
        with gzip.open(archive, "rb") as fh:
            self.assertEqual(fh.read(), payload)

    def test_rotation_keeps_supervisors_open_descriptor_alive(self):
        """The property the whole copy-truncate approach exists for.

        launchd/systemd open the log once at spawn and never reopen it. If
        rotation replaced the inode, every subsequent line would vanish into
        an unlinked file. Writes through a descriptor opened *before* rotation
        must still land in the live log afterwards.
        """
        log = self._write("bot.launchd.log", 2)
        inode_before = log.stat().st_ino

        fd = os.open(log, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, b"before rotation\n")
            cleanup.rotate_service_logs(max_size_mb=1)
            os.write(fd, b"after rotation\n")
        finally:
            os.close(fd)

        self.assertEqual(log.stat().st_ino, inode_before, "inode was replaced")
        self.assertEqual(
            log.read_bytes(),
            b"after rotation\n",
            "post-rotation write did not reach the live log",
        )

    def test_small_log_is_left_alone(self):
        log = self._write("bot.launchd.log", 0.5)
        self.assertEqual(cleanup.rotate_service_logs(max_size_mb=1), 0)
        self.assertEqual(log.stat().st_size, int(0.5 * 1024 * 1024))
        self.assertEqual(list(self.log_dir.glob("*.gz")), [])

    def test_systemd_logs_rotate_too(self):
        self._write("bot.systemd.log", 2)
        self.assertEqual(cleanup.rotate_service_logs(max_size_mb=1), 1)
        self.assertEqual(len(list(self.log_dir.glob("bot.systemd.until-*.log.gz"))), 1)

    def test_same_day_rotations_do_not_collide(self):
        for _ in range(3):
            self._write("bot.launchd.log", 2)
            cleanup.rotate_service_logs(max_size_mb=1, keep=10)
        self.assertEqual(len(list(self.log_dir.glob("bot.launchd.until-*.log.gz"))), 3)

    def test_old_archives_are_pruned_to_keep(self):
        for i in range(5):
            self._write("bot.launchd.log", 2)
            cleanup.rotate_service_logs(max_size_mb=1, keep=10)
            # Force distinct mtimes so pruning order is deterministic.
            for j, a in enumerate(sorted(self.log_dir.glob("*.gz"))):
                os.utime(a, (1_700_000_000 + j, 1_700_000_000 + j))

        self._write("bot.launchd.log", 2)
        cleanup.rotate_service_logs(max_size_mb=1, keep=2)
        self.assertEqual(len(list(self.log_dir.glob("bot.launchd.until-*.log.gz"))), 2)

    def test_failed_archive_does_not_destroy_the_log(self):
        """Compression happens before truncation, so a failure costs nothing."""
        log = self._write("bot.launchd.log", 2)
        with mock.patch.object(cleanup.shutil, "copyfileobj", side_effect=OSError("disk full")):
            self.assertEqual(cleanup.rotate_service_logs(max_size_mb=1), 0)
        self.assertEqual(log.stat().st_size, 2 * 1024 * 1024, "log lost on failure")

    def test_missing_log_dir_is_not_an_error(self):
        with mock.patch.object(cleanup, "LOG_DIR", self.log_dir / "gone"):
            self.assertEqual(cleanup.rotate_service_logs(), 0)


class CleanupLogsExcludesServiceLogsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.log_dir = Path(self._tmp.name)
        patcher = mock.patch.object(cleanup, "LOG_DIR", self.log_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_service_logs_are_not_blunt_truncated(self):
        """Otherwise the 50 MB backstop would race rotation and win sometimes,
        discarding history rotate_service_logs was about to archive."""
        svc = self.log_dir / "bot.launchd.log"
        svc.write_bytes(b"x" * (3 * 1024 * 1024))

        self.assertEqual(cleanup.cleanup_logs(max_size_mb=1), 0)
        self.assertEqual(svc.stat().st_size, 3 * 1024 * 1024)

    def test_app_logs_are_still_truncated(self):
        app = self.log_dir / "bot.log"
        app.write_bytes(b"x" * (3 * 1024 * 1024))

        self.assertEqual(cleanup.cleanup_logs(max_size_mb=1), 1)
        self.assertLess(app.stat().st_size, 3 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
