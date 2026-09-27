"""bot.log redaction covers tracebacks, not only the message line.

Linux bot review 2026-09-27, S002: _TokenRedactFilter rewrote record.msg only, so a
logged exception whose text holds a Bot API URL (the token sits in it) went
to bot.log unredacted.
"""

import logging
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MOM_TEST"] = "1"

import bot  # noqa: E402

TOKEN = "1234567890:AAH" + "x" * 32


class Redaction(unittest.TestCase):
    def _format(self, record):
        bot._TokenRedactFilter().filter(record)
        return logging.Formatter("%(message)s").format(record)

    def test_traceback_is_redacted(self):
        try:
            raise RuntimeError(f"404 for url 'http://localhost:8081/bot{TOKEN}/getMe'")
        except RuntimeError:
            record = logging.LogRecord("t", logging.ERROR, __file__, 1, "send failed", None, sys.exc_info())
        text = self._format(record)
        self.assertNotIn(TOKEN, text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("RuntimeError", text)

    def test_message_and_args_still_redacted(self):
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "url %s", (f"/bot{TOKEN}/x",), None)
        self.assertNotIn(TOKEN, self._format(record))


if __name__ == "__main__":
    unittest.main()
