#!/usr/bin/env python3
"""Pin the OCR throttle: pdftoppm and tesseract must run under nice 19,
single OCR thread, and 150 DPI.

Run: python3 -m unittest tests.test_pdf_ocr_throttle  (from repo root)

The unthrottled burst (pdftoppm then tesseract with OpenMP threads on every
core, from idle) is a power transient that can hard-reset old machines with
marginal PSUs. These tests fail if anyone restores the original unthrottled
commands.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class TestOcrThrottle(unittest.TestCase):
    def _run_ocr(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return FakeCompleted(returncode=0, stdout="some ocr text")

        def fake_glob(pattern):
            return [pattern.replace("*", "001")]

        with patch.object(bot.subprocess, "run", side_effect=fake_run):
            with patch.object(bot._glob_mod, "glob", side_effect=fake_glob):
                with patch.object(bot.shutil, "which", return_value="/usr/bin/x"):
                    bot._ocr_pdf("/tmp/fake.pdf", num_pages=3,
                                 max_pages=5, max_chars=1000)
        return calls

    def test_pdftoppm_is_throttled(self):
        calls = self._run_ocr()
        pdftoppm = next(c for c, _ in calls if "pdftoppm" in c)
        self.assertEqual(pdftoppm[:3], ["nice", "-n", "19"])
        dpi = pdftoppm[pdftoppm.index("-r") + 1]
        self.assertEqual(dpi, "150")

    def test_tesseract_is_throttled_and_single_thread(self):
        calls = self._run_ocr()
        tess_cmd, tess_kwargs = next((c, k) for c, k in calls if "tesseract" in c)
        self.assertEqual(tess_cmd[:3], ["nice", "-n", "19"])
        self.assertEqual(tess_kwargs.get("env", {}).get("OMP_THREAD_LIMIT"), "1")

    def test_ocr_still_returns_text(self):
        def fake_run(cmd, **kwargs):
            return FakeCompleted(returncode=0, stdout="OCR PAGE TEXT")

        def fake_glob(pattern):
            return [pattern.replace("*", "001")]

        with patch.object(bot.subprocess, "run", side_effect=fake_run):
            with patch.object(bot._glob_mod, "glob", side_effect=fake_glob):
                with patch.object(bot.shutil, "which", return_value="/usr/bin/x"):
                    out = bot._ocr_pdf("/tmp/fake.pdf", 1, 5, 1000)
        self.assertIn("OCR PAGE TEXT", out)


if __name__ == "__main__":
    unittest.main()
