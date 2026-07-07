#!/usr/bin/env python3
"""Unit tests for the watch skill whisper.py memory-isolation funnel.

Run: python3 -m unittest tests.test_watch_whisper_isolation  (from repo root)

The local whisper CLI in the watch skill is wrapped in a memory-capped systemd
user scope so a heavy model cannot exhaust system RAM and OOM the machine. These
tests cover the pure decision logic: scope command shape, the safe-model gate,
and the isolate-or-refuse funnel. The real isolated run shares the voice skill's
mechanism and is exercised manually.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHISPER = ROOT / "skills" / "watch" / "scripts" / "whisper.py"

spec = importlib.util.spec_from_file_location("watch_whisper_mod", WHISPER)
wmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wmod)  # stdlib-only module, no torch/whisper import


class ScopePrefixTests(unittest.TestCase):
    def test_prefix_carries_memory_cap_and_terminator(self):
        prefix = wmod._scope_prefix(wmod.WHISPER_MEM_MAX)
        if prefix is None:
            self.skipTest("systemd-run not installed on this host")
        self.assertTrue(prefix[0].endswith("systemd-run"))
        self.assertIn("--scope", prefix)
        self.assertIn("--user", prefix)
        self.assertIn(f"MemoryMax={wmod.WHISPER_MEM_MAX}", prefix)
        self.assertIn("MemorySwapMax=0", prefix)
        # Must end with the argv terminator so the payload appends cleanly.
        self.assertEqual(prefix[-1], "--")

    def test_no_systemd_run_returns_none(self):
        # If systemd-run is absent, the prefix builder must signal "no scope"
        # so the caller can fall back to the safe-model gate.
        orig_which = wmod.shutil.which
        wmod.shutil.which = lambda name: None
        try:
            self.assertIsNone(wmod._scope_prefix("6G"))
        finally:
            wmod.shutil.which = orig_which

    def test_default_cap_sized_for_medium(self):
        # 'medium' on CPU (fp32) peaks ~4.8GB; the cap must clear that with
        # headroom while staying low enough to bound a runaway. 5-8G is valid.
        self.assertTrue(wmod.WHISPER_MEM_MAX.endswith("G"))
        gb = int(wmod.WHISPER_MEM_MAX[:-1])
        self.assertGreaterEqual(gb, 5)
        self.assertLessEqual(gb, 8)


class SafeModelGateTests(unittest.TestCase):
    def test_default_model_is_safe(self):
        self.assertIn(wmod.LOCAL_DEFAULT_MODEL, wmod.SAFE_MODELS)

    def test_heavy_models_are_not_safe(self):
        for m in ("large", "large-v2", "large-v3", "turbo"):
            self.assertNotIn(m, wmod.SAFE_MODELS)


class IsolateOrRefuseTests(unittest.TestCase):
    BASE_CMD = ["whisper", "a.mp3", "--model", "base"]
    HEAVY_CMD = ["whisper", "a.mp3", "--model", "large-v3"]

    def _patch(self, **attrs):
        """Swap module-level names, returning a restore callable."""
        saved = {k: getattr(wmod, k) for k in attrs}
        for k, v in attrs.items():
            setattr(wmod, k, v)
        return lambda: [setattr(wmod, k, v) for k, v in saved.items()]

    def test_prepends_scope_when_isolation_available(self):
        fake_prefix = ["/usr/bin/systemd-run", "--user", "--scope", "--"]
        restore = self._patch(
            _scope_prefix=lambda mem: fake_prefix,
            _isolation_works=lambda prefix: True,
        )
        try:
            out = wmod._isolate_or_refuse(list(self.BASE_CMD), "base", "cpu")
        finally:
            restore()
        self.assertEqual(out[: len(fake_prefix)], fake_prefix)
        self.assertEqual(out[len(fake_prefix):], self.BASE_CMD)

    def test_refuses_heavy_model_without_isolation(self):
        restore = self._patch(_scope_prefix=lambda mem: None)  # systemd-run absent
        try:
            with self.assertRaises(SystemExit):
                wmod._isolate_or_refuse(list(self.HEAVY_CMD), "large-v3", "cpu")
        finally:
            restore()

    def test_refuses_heavy_model_when_scope_creation_fails(self):
        # systemd-run exists but the user manager can't make a scope.
        restore = self._patch(
            _scope_prefix=lambda mem: ["systemd-run", "--"],
            _isolation_works=lambda prefix: False,
        )
        try:
            with self.assertRaises(SystemExit):
                wmod._isolate_or_refuse(list(self.HEAVY_CMD), "large-v3", "cpu")
        finally:
            restore()

    def test_allows_safe_model_without_isolation(self):
        restore = self._patch(_scope_prefix=lambda mem: None)
        try:
            out = wmod._isolate_or_refuse(list(self.BASE_CMD), "base", "cpu")
        finally:
            restore()
        self.assertEqual(out, self.BASE_CMD)  # unchanged, runs unisolated

    def test_gpu_is_exempt_even_for_heavy_model(self):
        # GPU weights live in VRAM, not system RAM; never wrapped and never
        # refused, regardless of model size.
        out = wmod._isolate_or_refuse(list(self.HEAVY_CMD), "large-v3", "cuda")
        self.assertEqual(out, self.HEAVY_CMD)


if __name__ == "__main__":
    unittest.main()
