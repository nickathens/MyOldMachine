"""The global PTB error handler (ported from the production bot, 2026-08-11).

Before it existed, python-telegram-bot logged "No error handlers are
registered, logging exception" at ERROR for every polling transport hiccup,
so expected network noise (blips, the opt-in nightly reboot, a restarting
Telegram API) inflated real error counts. The handler grades: transport
errors are WARNING, everything else stays ERROR with the full traceback.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

from telegram.error import BadRequest, NetworkError, TimedOut

import bot


class PTBErrorHandlerTests(unittest.TestCase):
    def test_transport_error_logs_warning_not_error(self):
        ctx = SimpleNamespace(error=NetworkError("Bad Gateway"))
        with self.assertLogs(bot.logger, level="WARNING") as cm:
            asyncio.run(bot.on_ptb_error(None, ctx))
        self.assertTrue(any(r.levelname == "WARNING" for r in cm.records))
        self.assertFalse(any(r.levelname == "ERROR" for r in cm.records))

    def test_timeout_is_transport_noise(self):
        ctx = SimpleNamespace(error=TimedOut())
        with self.assertLogs(bot.logger, level="WARNING") as cm:
            asyncio.run(bot.on_ptb_error(None, ctx))
        self.assertFalse(any(r.levelname == "ERROR" for r in cm.records))

    def test_bad_request_is_a_real_bug_despite_subclassing_networkerror(self):
        # PTB hierarchy: BadRequest < NetworkError. A bare isinstance check
        # would demote real API-usage bugs (bad markup, oversized message)
        # to WARNING — this pin is what caught that in the production port.
        ctx = SimpleNamespace(error=BadRequest("can't parse entities"))
        with self.assertLogs(bot.logger, level="ERROR") as cm:
            asyncio.run(bot.on_ptb_error(None, ctx))
        self.assertTrue(any(r.levelname == "ERROR" for r in cm.records))

    def test_real_failure_stays_error_with_traceback(self):
        ctx = SimpleNamespace(error=RuntimeError("handler blew up"))
        with self.assertLogs(bot.logger, level="ERROR") as cm:
            asyncio.run(bot.on_ptb_error(None, ctx))
        record = cm.records[0]
        self.assertEqual(record.levelname, "ERROR")
        self.assertIsNotNone(record.exc_info)
        self.assertIn("handler blew up", str(record.exc_info[1]))

    def test_handler_is_registered_in_main(self):
        # Registration happens inside main(), which builds the full PTB app
        # and cannot run under test, so this is a deliberate source-text
        # guard: deleting the add_error_handler line must break this test.
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("app.add_error_handler(on_ptb_error)", source)


if __name__ == "__main__":
    unittest.main()
