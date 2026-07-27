"""Test runs must never write into the production bot.log.

bot.py installs a RotatingFileHandler on `data/logs/bot.log` at import time.
Logging is configured once per process, so a single test module that imports
bot without setting MOM_TEST first routes every LATER module's synthetic
failures into the real log, where the digest health report counts them as
production errors. That is not theoretical: on the Linux side five ERROR lines
dated 2026-07-27 in a live bot.log turned out to be a test's fake tracebacks
("provider blew up", "still down"), and probing two of MOM's own test modules
wrote 3,484 bytes of the same fiction into MOM's log.

Two guards here:
  - the source scan catches a module that imports bot unguarded (the defect);
  - the handler check proves the guard in bot.py is live, and would catch its
    removal, without writing to the real log to find out.
"""
from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot  # noqa: E402,F401


class LogIsolationTests(unittest.TestCase):
    def test_every_bot_importing_test_sets_mom_test_first(self):
        """A test module must set MOM_TEST before its first bot import."""
        offenders = []
        for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            guard = None
            for i, line in enumerate(lines):
                if "MOM_TEST" in line and "environ" in line:
                    guard = i
                    break
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(("import bot", "from bot import")):
                    if guard is None or guard > i:
                        offenders.append(f"{path.name}:{i + 1}")
                    break
        self.assertEqual(offenders, [], f"unguarded bot imports: {offenders}")

    def test_no_production_log_handler_under_mom_test(self):
        """With MOM_TEST set, bot must not attach the bot.log file handler."""
        attached = [
            h for h in logging.getLogger().handlers
            if str(getattr(h, "baseFilename", "")).endswith("bot.log")
        ]
        self.assertEqual(
            attached, [],
            "bot.log handler attached during a test run: bot.py's MOM_TEST guard "
            "is missing or was bypassed",
        )


if __name__ == "__main__":
    unittest.main()
