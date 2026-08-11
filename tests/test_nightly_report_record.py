"""The nightly report keeps a copy of what it sent (ported 2026-08-11).

Production-bot audit finding B4: the morning digest's content was recorded
nowhere after sending, so "what did it say" and "did it even arrive" had no
answer on disk. Same class here: send() shelled out and kept nothing.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from utils import nightly_report


def _proc(rc: int):
    return SimpleNamespace(returncode=rc, stdout="", stderr="boom" if rc else "")


class NightlyReportRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "logs" / "nightly_report.jsonl"
        # The real log must not gain lines from a test run: snapshot it.
        self._real_log = nightly_report.REPORT_LOG
        self._real_size = (self._real_log.stat().st_size
                           if self._real_log.exists() else -1)
        patcher = patch.object(nightly_report, "REPORT_LOG", self.log)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        real_size = (self._real_log.stat().st_size
                     if self._real_log.exists() else -1)
        self.assertEqual(real_size, self._real_size,
                         "test wrote into the real data/logs tree")

    def _send(self, rc: int, text="Backup: OK\nDisk: fine"):
        with patch.object(nightly_report, "get_primary_admin_id", return_value=42), \
             patch.object(nightly_report.subprocess, "run", return_value=_proc(rc)):
            return nightly_report.send(text)

    def test_delivered_report_is_recorded_with_its_text(self):
        self.assertEqual(self._send(0), 0)
        rec = json.loads(self.log.read_text().splitlines()[0])
        self.assertTrue(rec["delivered"])
        self.assertEqual(rec["text"], "Backup: OK\nDisk: fine")
        self.assertEqual(rec["chars"], len(rec["text"]))
        self.assertTrue(rec["ts"])

    def test_failed_delivery_is_recorded_as_undelivered(self):
        self.assertEqual(self._send(7), 7)
        rec = json.loads(self.log.read_text().splitlines()[0])
        self.assertFalse(rec["delivered"])

    def test_records_append_across_nights(self):
        self._send(0, "night one")
        self._send(0, "night two")
        lines = self.log.read_text().splitlines()
        self.assertEqual([json.loads(x)["text"] for x in lines],
                         ["night one", "night two"])

    def test_unwritable_log_cannot_break_the_send(self):
        # Parent path runs through a FILE, so mkdir raises. Send must still
        # return the transport's own exit code.
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("x")
        with patch.object(nightly_report, "REPORT_LOG",
                          blocker / "sub" / "log.jsonl"):
            self.assertEqual(self._send(0), 0)

    def test_no_admin_configured_sends_and_records_nothing(self):
        with patch.object(nightly_report, "get_primary_admin_id", return_value=None):
            self.assertEqual(nightly_report.send("hello"), 1)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
