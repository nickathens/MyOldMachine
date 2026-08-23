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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIBE = ROOT / "skills" / "voice" / "scripts" / "transcribe.py"

spec = importlib.util.spec_from_file_location("transcribe_mod", TRANSCRIBE)
tmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tmod)  # cheap: whisper import is deferred into _run_whisper


class ParseArgsTests(unittest.TestCase):
    def test_defaults(self):
        audio, lang, model, srt = tmod._parse_args(["transcribe.py", "a.ogg"])
        self.assertEqual(audio, "a.ogg")
        self.assertIsNone(lang)
        self.assertEqual(model, tmod.DEFAULT_MODEL)
        self.assertFalse(srt)

    def test_language_and_model_flags(self):
        audio, lang, model, srt = tmod._parse_args(
            ["transcribe.py", "a.ogg", "--language", "el", "--model", "small"]
        )
        self.assertEqual(audio, "a.ogg")
        self.assertEqual(lang, "el")
        self.assertEqual(model, "small")
        self.assertFalse(srt)

    def test_srt_flag_is_parsed_anywhere_in_argv(self):
        _, _, _, srt = tmod._parse_args(["transcribe.py", "a.ogg", "--srt"])
        self.assertTrue(srt)
        _, lang, _, srt = tmod._parse_args(
            ["transcribe.py", "a.ogg", "--srt", "--language", "el"]
        )
        self.assertTrue(srt)
        self.assertEqual(lang, "el")


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


class SrtFormatTests(unittest.TestCase):
    """--srt exists so a transcript can drive a subtitle track (caption and
    whiteboard workflows want timings, not one text blob). The formatter is
    pure, so it is checked here without loading whisper."""

    def test_timestamp_format(self):
        self.assertEqual(tmod._srt_timestamp(0), "00:00:00,000")
        self.assertEqual(tmod._srt_timestamp(1.5), "00:00:01,500")
        self.assertEqual(tmod._srt_timestamp(61.25), "00:01:01,250")
        self.assertEqual(tmod._srt_timestamp(3725.007), "01:02:05,007")
        # 0.9996s is where rounding and truncation disagree (999.6 ms). Without
        # this line a truncating implementation passes every other case here.
        self.assertEqual(tmod._srt_timestamp(0.9996), "00:00:01,000")

    def test_negative_offset_clamps_to_zero(self):
        self.assertEqual(tmod._srt_timestamp(-0.4), "00:00:00,000")

    def test_document_shape(self):
        out = tmod._format_srt([
            {"start": 0.0, "end": 2.5, "text": " Hello there "},
            {"start": 2.5, "end": 4.0, "text": "second cue"},
        ])
        self.assertEqual(
            out,
            "1\n00:00:00,000 --> 00:00:02,500\nHello there\n\n"
            "2\n00:00:02,500 --> 00:00:04,000\nsecond cue\n",
        )

    def test_blank_segments_dropped_and_indices_stay_contiguous(self):
        out = tmod._format_srt([
            {"start": 0.0, "end": 1.0, "text": "one"},
            {"start": 1.0, "end": 2.0, "text": "   "},
            {"start": 2.0, "end": 3.0, "text": "two"},
        ])
        self.assertEqual([ln for ln in out.split("\n") if ln.isdigit()], ["1", "2"])
        self.assertNotIn("00:00:01,000 -->", out)

    def test_empty_and_missing_segments(self):
        self.assertEqual(tmod._format_srt([]), "")
        self.assertEqual(tmod._format_srt(None), "")

    def test_missing_timings_default_to_zero(self):
        out = tmod._format_srt([{"text": "no timings"}])
        self.assertEqual(out, "1\n00:00:00,000 --> 00:00:00,000\nno timings\n")


class SrtSkipsTheWarmEngineTests(unittest.TestCase):
    """The warm listening engine returns text with no segment boundaries.

    This is the port-specific hazard: the bot has no warm path, MOM does, and
    `hear.py` prints a finished transcript and nothing else. Routing an SRT
    request through it would print plain text under a flag that promised
    timings, which is worse than being slow. `hear.py` lives under `data/`,
    which is gitignored, so this can only be pinned at the branch, not by
    reading the daemon.
    """

    def _run_main(self, argv):
        """Drive main() with isolation and whisper both stubbed out."""
        calls = {}
        with mock.patch.dict(tmod.os.environ, {"WHISPER_ISOLATED": "1"}, clear=False), \
             mock.patch.object(tmod, "_try_warm_engine",
                               side_effect=lambda *a, **k: calls.setdefault("warm", True) or "warm text"), \
             mock.patch.object(tmod, "_run_whisper",
                               side_effect=lambda *a, **k: calls.setdefault("whisper", (a, k))):
            tmod.main(argv)
        return calls

    def test_plain_run_uses_the_warm_engine(self):
        # The control. Without it, a bypass that fires for every run would pass.
        calls = self._run_main(["transcribe.py", "a.ogg"])
        self.assertTrue(calls.get("warm"), "the warm engine is no longer the default path")
        self.assertNotIn("whisper", calls)

    def test_srt_run_bypasses_it_and_asks_whisper_for_segments(self):
        calls = self._run_main(["transcribe.py", "a.ogg", "--srt"])
        self.assertNotIn("warm", calls, "--srt was routed through the segment-less warm engine")
        self.assertIn("whisper", calls, "--srt did not reach the legacy whisper path")
        args, _ = calls["whisper"]
        self.assertIs(args[3], True, f"_run_whisper was not told to emit SRT: {args!r}")


if __name__ == "__main__":
    unittest.main()
