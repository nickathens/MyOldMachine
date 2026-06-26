#!/usr/bin/env python3
"""Unit tests for the voice skill transcribe.py memory-isolation wrapper.

Run: python3 -m unittest tests.test_transcribe_isolation  (from repo root)

transcribe.py re-execs the whisper run inside a memory-capped systemd user
scope so a heavy model cannot exhaust system RAM and OOM the machine (the bot
service has no memory cap of its own, so an unbounded model would otherwise
pressure the whole box). These tests cover the pure plumbing (arg parse, scope
command shape, safe-model gate). The real end-to-end isolated run needs whisper
installed plus a host with systemd-run and is exercised manually.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIBE = ROOT / "skills" / "voice" / "scripts" / "transcribe.py"

spec = importlib.util.spec_from_file_location("transcribe_mod", TRANSCRIBE)
tmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tmod)  # cheap: whisper import is deferred into _run_whisper


class ParseArgsTests(unittest.TestCase):
    def test_defaults(self):
        audio, lang, model = tmod._parse_args(["transcribe.py", "a.ogg"])
        self.assertEqual(audio, "a.ogg")
        self.assertIsNone(lang)
        self.assertEqual(model, tmod.DEFAULT_MODEL)

    def test_language_and_model_flags(self):
        audio, lang, model = tmod._parse_args(
            ["transcribe.py", "a.ogg", "--language", "el", "--model", "small"]
        )
        self.assertEqual(audio, "a.ogg")
        self.assertEqual(lang, "el")
        self.assertEqual(model, "small")


class ScopePrefixTests(unittest.TestCase):
    def test_prefix_carries_memory_cap_and_terminator(self):
        prefix = tmod._scope_prefix()
        if prefix is None:
            self.skipTest("systemd-run not installed on this host")
        self.assertTrue(prefix[0].endswith("systemd-run"))
        self.assertIn("--scope", prefix)
        self.assertIn("--user", prefix)
        self.assertIn(f"MemoryMax={tmod.MEM_MAX}", prefix)
        self.assertIn("MemorySwapMax=0", prefix)
        # Must end with the argv terminator so the payload appends cleanly.
        self.assertEqual(prefix[-1], "--")

    def test_no_systemd_run_returns_none(self):
        orig_which = tmod.shutil.which
        tmod.shutil.which = lambda name: None
        try:
            self.assertIsNone(tmod._scope_prefix())
        finally:
            tmod.shutil.which = orig_which

    def test_default_cap_sized_for_medium(self):
        # Whisper 'medium' on CPU (fp32) peaks ~4.8GB. The default cap must clear
        # that with headroom while staying low enough to bound a runaway. 5-8G is
        # the valid band.
        self.assertTrue(tmod.MEM_MAX.endswith("G"))
        gb = int(tmod.MEM_MAX[:-1])
        self.assertGreaterEqual(gb, 5)
        self.assertLessEqual(gb, 8)


class SafeModelGateTests(unittest.TestCase):
    def test_default_model_is_safe(self):
        self.assertIn(tmod.DEFAULT_MODEL, tmod.SAFE_MODELS)

    def test_heavy_models_are_not_safe(self):
        for m in ("large", "large-v2", "large-v3", "turbo"):
            self.assertNotIn(m, tmod.SAFE_MODELS)


if __name__ == "__main__":
    unittest.main()
