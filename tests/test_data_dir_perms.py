#!/usr/bin/env python3
"""Unit tests for core.config data-directory permissions.

Run: python3 -m unittest tests.test_data_dir_perms  (from repo root)

Conversations, memory, identities, and logs live under data/. The
import-time loop in core.config must create these directories private to
the bot's OS account (0700) and re-tighten them on the next import if
something loosened them.
"""
from __future__ import annotations

import importlib
import os
import stat
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config as config  # noqa: E402

ALL_DATA_DIRS = [
    config.DATA_DIR,
    config.USERS_DIR,
    config.MEMORY_DIR,
    config.SCHEDULER_DIR,
    config.IDENTITY_DIR,
    config.LOG_DIR,
]


def _mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


class DataDirPermsTests(unittest.TestCase):
    def test_data_dirs_created_private(self):
        for d in ALL_DATA_DIRS:
            self.assertTrue(d.is_dir(), f"{d} missing")
            self.assertEqual(
                _mode(d), 0o700, f"{d} is {oct(_mode(d))}, want 0o700"
            )

    def test_loosened_dir_heals_on_reimport(self):
        os.chmod(config.DATA_DIR, 0o755)
        self.assertEqual(_mode(config.DATA_DIR), 0o755)
        try:
            importlib.reload(config)
            self.assertEqual(_mode(config.DATA_DIR), 0o700)
        finally:
            # Re-tighten even if the assertion fails, so a broken run does
            # not leave the tree loosened for later tests.
            os.chmod(config.DATA_DIR, 0o700)


if __name__ == "__main__":
    unittest.main()
