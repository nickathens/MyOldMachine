"""Regression tests for the July 2026 model-catalog refresh.

Locks install.wizard after every LLM provider's model list was re-verified
against live data (retired and superseded models removed, current flagships
added). Guards two invariants that must survive future edits: the
recommended-first ordering (DEFAULT_MODELS[p] == PROVIDER_MODELS[p][0]) and
the absence of models that have been retired upstream.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import GrokProvider, _claude_accepts_temperature  # noqa: E402
from install import wizard  # noqa: E402


def _ids(provider: str) -> list[str]:
    return [mid for mid, _ in wizard.PROVIDER_MODELS[provider]]


class CatalogIntegrityTests(unittest.TestCase):
    def test_default_is_first_entry(self):
        # Header comment promises the first entry in each list is the default.
        for provider, entries in wizard.PROVIDER_MODELS.items():
            with self.subTest(provider=provider):
                self.assertTrue(entries, f"{provider} catalog is empty")
                self.assertEqual(wizard.DEFAULT_MODELS[provider], entries[0][0])

    def test_openrouter_default_is_first_entry(self):
        self.assertEqual(
            wizard.DEFAULT_MODELS["openrouter"],
            wizard.OPENROUTER_FREE_MODELS[0][0],
        )

    def test_no_duplicate_ids(self):
        for provider, entries in wizard.PROVIDER_MODELS.items():
            ids = [mid for mid, _ in entries]
            with self.subTest(provider=provider):
                self.assertEqual(len(ids), len(set(ids)), f"{provider} has dupes")
        or_ids = [mid for mid, _ in wizard.OPENROUTER_FREE_MODELS]
        self.assertEqual(len(or_ids), len(set(or_ids)), "openrouter has dupes")


class CurrentFlagshipsPresentTests(unittest.TestCase):
    def test_sonnet_5_present(self):
        # Sonnet 5 (claude-sonnet-5) GA June 30, 2026 — current Sonnet flagship.
        self.assertIn("claude-sonnet-5", _ids("claude"))
        self.assertIn("claude-sonnet-5", _ids("claude-api"))

    def test_gpt_5_6_present(self):
        self.assertIn("gpt-5.6", _ids("openai"))
        self.assertIn("gpt-5.6", _ids("codex"))

    def test_grok_4_5_present(self):
        self.assertIn("grok-4.5", _ids("grok"))

    def test_kimi_k3_present(self):
        self.assertIn("kimi-k3", _ids("kimi"))

    def test_openrouter_refreshed(self):
        or_ids = {mid for mid, _ in wizard.OPENROUTER_FREE_MODELS}
        self.assertIn("cohere/north-mini-code:free", or_ids)
        self.assertIn("tencent/hy3:free", or_ids)
        self.assertIn("poolside/laguna-xs-2.1:free", or_ids)


class RetiredModelsAbsentTests(unittest.TestCase):
    def test_deepseek_aliases_removed(self):
        # deepseek-chat / deepseek-reasoner retire 2026-07-24.
        ids = _ids("deepseek")
        self.assertNotIn("deepseek-chat", ids)
        self.assertNotIn("deepseek-reasoner", ids)

    def test_obsolete_gpt5_removed(self):
        self.assertNotIn("gpt-5", _ids("openai"))

    def test_superseded_opus_removed(self):
        # Opus 4.6/4.7 are superseded by 4.8 at identical pricing.
        for provider in ("claude", "claude-api"):
            ids = _ids(provider)
            self.assertNotIn("claude-opus-4-7", ids)
            self.assertNotIn("claude-opus-4-6", ids)
        self.assertNotIn("claude-sonnet-4-5-20250929", _ids("claude-api"))

    def test_superseded_sonnet_removed(self):
        # Sonnet 4.6 is superseded by Sonnet 5 at identical standard pricing.
        for provider in ("claude", "claude-api"):
            self.assertNotIn("claude-sonnet-4-6", _ids(provider))

    def test_dead_ollama_cloud_tags_removed(self):
        ids = _ids("ollama-cloud")
        self.assertNotIn("glm-5:cloud", ids)
        self.assertNotIn("qwen3-coder-next:cloud", ids)

    def test_dead_openrouter_free_removed(self):
        or_ids = {mid for mid, _ in wizard.OPENROUTER_FREE_MODELS}
        for dead in (
            "openrouter/owl-alpha",
            "nex-agi/nex-n2-pro:free",
            "openai/gpt-oss-120b:free",
            "moonshotai/kimi-k2.6:free",
            "poolside/laguna-xs.2:free",
        ):
            self.assertNotIn(dead, or_ids)

    def test_codex_spark_id_corrected(self):
        ids = _ids("codex")
        self.assertNotIn("gpt-5.3-codex-spark", ids)
        self.assertIn("gpt-5.3-codex", ids)


class GrokVisionGateTests(unittest.TestCase):
    def test_grok_4_5_supports_vision(self):
        self.assertTrue(GrokProvider("grok-4.5").supports_vision)


class ClaudeSamplingGateTests(unittest.TestCase):
    """Sonnet 5, Fable 5, and Opus 4.7+ 400 on non-default temperature; the
    API provider must omit it for them and keep sending it to legacy models."""

    def test_new_models_omit_temperature(self):
        for model in ("claude-sonnet-5", "claude-fable-5",
                      "claude-opus-4-8", "claude-opus-4-7"):
            with self.subTest(model=model):
                self.assertFalse(_claude_accepts_temperature(model))

    def test_legacy_models_keep_temperature(self):
        for model in ("claude-sonnet-4-6", "claude-sonnet-4-5-20250929",
                      "claude-haiku-4-5"):
            with self.subTest(model=model):
                self.assertTrue(_claude_accepts_temperature(model))


if __name__ == "__main__":
    unittest.main()
