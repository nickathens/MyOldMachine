"""Regression tests for the model-catalog refreshes (July and October 2026).

Locks install.wizard after every LLM provider's model list was re-verified
against live data (retired and superseded models removed, current flagships
added). Guards two invariants that must survive future edits: the
recommended-first ordering (DEFAULT_MODELS[p] == PROVIDER_MODELS[p][0]) and
the absence of models that have been retired upstream, or that the endpoint a
provider uses cannot serve. The request-shape rules the October models needed
are pinned in tests/test_model_request_rules.py.
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
    def test_opus_5_5_present(self):
        # Opus 5.5 (claude-opus-5-5) launched September 22, 2026 as the
        # current Opus flagship: $4/$20 per MTok (down from Opus 5's $5/$25),
        # 1M ctx, 128K output, replacing Opus 5 the way Opus 5 replaced 4.8.
        self.assertIn("claude-opus-5-5", _ids("claude"))
        self.assertIn("claude-opus-5-5", _ids("claude-api"))

    def test_opus_5_5_carries_its_cli_floor_in_the_offer_text(self):
        # The CLI list only: the claude-api row never touches Claude Code.
        from core.model_efforts import MODEL_MIN_CLI
        floor = ".".join(str(p) for p in MODEL_MIN_CLI["claude-opus-5-5"])
        self.assertIn(floor, dict(wizard.PROVIDER_MODELS["claude"])["claude-opus-5-5"])
        self.assertNotIn(floor, dict(wizard.PROVIDER_MODELS["claude-api"])["claude-opus-5-5"])

    def test_opus_5_does_not_hijack_default(self):
        # Opus is offered, never recommended: the default stays on Sonnet so a
        # fresh install does not silently pick the pricier model. The Sonnet is
        # 5.5 since 2026-09-28, FCC included, since it fronts the same CLI.
        for provider in ("claude", "claude-api", "fcc"):
            self.assertEqual(wizard.DEFAULT_MODELS[provider], "claude-sonnet-5-5")

    def test_fable_5_1_present(self):
        # Fable 5.1 (claude-fable-5-1) succeeds Fable 5 in the same tier at the
        # same $10/$50 per MTok. Offered, never recommended — see
        # test_opus_5_does_not_hijack_default for why the default stays Sonnet.
        self.assertIn("claude-fable-5-1", _ids("claude"))
        self.assertIn("claude-fable-5-1", _ids("claude-api"))

    def test_sonnet_5_5_present(self):
        # Sonnet 5.5 (claude-sonnet-5-5) launched September 28, 2026 at $2/$10
        # per MTok, the current Sonnet; Sonnet 5 moved to the Legacy table.
        self.assertIn("claude-sonnet-5-5", _ids("claude"))
        self.assertIn("claude-sonnet-5-5", _ids("claude-api"))

    def test_sonnet_5_5_claims_no_cli_floor(self):
        # Measured: Claude Code 2.1.278 and 2.1.283 both ran it, so the CLI row
        # must not tell anyone to update, and there is no floor to enforce.
        from core.model_efforts import MODEL_MIN_CLI
        self.assertNotIn("claude-sonnet-5-5", MODEL_MIN_CLI)
        self.assertNotIn("needs Claude Code", dict(wizard.PROVIDER_MODELS["claude"])["claude-sonnet-5-5"])

    def test_codex_gpt_6_rows_present(self):
        # Every id run end to end on codex-cli 0.160.1 on 2026-10-06.
        ids = _ids("codex")
        self.assertEqual(wizard.DEFAULT_MODELS["codex"], "gpt-6.1-sol")
        for model in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-6-sol"):
            with self.subTest(model=model):
                self.assertIn(model, ids)

    def test_codex_default_carries_its_cli_floor_in_the_offer_text(self):
        from core.model_efforts import MODEL_MIN_CLI
        floor = ".".join(str(p) for p in MODEL_MIN_CLI["gpt-6.1-sol"])
        self.assertEqual(floor, "0.159.0")
        self.assertIn(floor, dict(wizard.PROVIDER_MODELS["codex"])["gpt-6.1-sol"])

    def test_openai_gpt_6_rows_present(self):
        ids = _ids("openai")
        for model in ("gpt-6-astra", "gpt-6-luna", "gpt-6-sol",
                      "gpt-5.6-terra", "gpt-5.6-luna"):
            with self.subTest(model=model):
                self.assertIn(model, ids)
        # The 5.6 alias stays the default: GPT-6.1 Sol, the tier that would
        # replace it, takes no tools on Chat Completions (see below).
        self.assertEqual(wizard.DEFAULT_MODELS["openai"], "gpt-5.6")

    def test_grok_4_7_and_4_6_present(self):
        for model in ("grok-4.7", "grok-4.6"):
            with self.subTest(model=model):
                self.assertIn(model, _ids("grok"))

    def test_gemini_flash_line_present_and_3_8_is_default(self):
        ids = _ids("gemini")
        for model in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                      "gemini-3.5-flash", "gemini-3.5-flash-lite"):
            with self.subTest(model=model):
                self.assertIn(model, ids)
        self.assertEqual(wizard.DEFAULT_MODELS["gemini"], "gemini-3.8-flash")

    def test_deepseek_flash_is_the_default(self):
        # V4.1 Flash's own id since 2026-09-10.
        self.assertEqual(wizard.DEFAULT_MODELS["deepseek"], "deepseek-flash")
        self.assertIn("deepseek-v4-pro", _ids("deepseek"))

    def test_kimi_highspeed_present(self):
        self.assertIn("kimi-k2.7-code-highspeed", _ids("kimi"))

    def test_minimax_m3_1_says_it_needs_an_m_plan_key(self):
        # Pay-as-you-go keys cannot reach it yet; the row has to say so.
        desc = dict(wizard.PROVIDER_MODELS["minimax"])["MiniMax-M3.1-Flash-Preview"]
        self.assertIn("M Plan", desc)

    def test_zai_glm_5_3_family_present(self):
        for model in ("glm-5.3", "glm-5.3-flash", "glm-5.3-flashx"):
            with self.subTest(model=model):
                self.assertIn(model, _ids("zai"))

    def test_ollama_cloud_ids_are_the_api_names(self):
        # ollama.com/api/tags names, which API requests to ollama.com take.
        # The Ollama app spells the same models "<name>:cloud"; this provider
        # is the API, so none of its ids may carry that suffix.
        ids = _ids("ollama-cloud")
        self.assertEqual(wizard.DEFAULT_MODELS["ollama-cloud"], "glm-5.3-flash")
        for model in ids:
            with self.subTest(model=model):
                self.assertFalse(model.endswith(":cloud"))
                self.assertFalse(model.endswith("-cloud"))
        for model in ("glm-5.3", "glm-5.3-flash", "kimi-k3", "deepseek-v4.1-flash",
                      "deepseek-v4-pro:0813", "gemma4:31b"):
            with self.subTest(model=model):
                self.assertIn(model, ids)

    def test_gpt_5_6_present(self):
        # On the API the bare alias is real and routes to Sol. Codex is a
        # different catalog and rejects it, so the codex list carries the
        # three tier ids instead. See test_codex_ids_are_codex_ids below.
        self.assertIn("gpt-5.6", _ids("openai"))
        for tier in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            self.assertIn(tier, _ids("codex"))

    def test_grok_4_5_present(self):
        self.assertIn("grok-4.5", _ids("grok"))

    def test_kimi_k3_present(self):
        self.assertIn("kimi-k3", _ids("kimi"))

    def test_openrouter_refreshed(self):
        # Free and tool-capable on openrouter.ai/api/v1/models, 2026-10-06.
        or_ids = {mid for mid, _ in wizard.OPENROUTER_FREE_MODELS}
        for model in ("cohere/north-mini-code:free", "thinkingmachines/inkling:free",
                      "thinkingmachines/inkling-small:free",
                      "nvidia/nemotron-3.5-lightning:free",
                      "dots-studio/dots-3-note-preview:free",
                      "apodex/apodex-1.1-mini:free"):
            with self.subTest(model=model):
                self.assertIn(model, or_ids)


class RetiredModelsAbsentTests(unittest.TestCase):
    def test_deepseek_aliases_removed(self):
        # deepseek-chat / deepseek-reasoner retire 2026-07-24.
        ids = _ids("deepseek")
        self.assertNotIn("deepseek-chat", ids)
        self.assertNotIn("deepseek-reasoner", ids)

    def test_obsolete_gpt5_removed(self):
        self.assertNotIn("gpt-5", _ids("openai"))

    def test_superseded_opus_removed(self):
        # Each Opus is retired by its successor: 4.6/4.7 by 4.8, 4.8 by Opus 5
        # (July 24, 2026), then Opus 5 by Opus 5.5 (September 22, 2026, at a
        # LOWER price), each the day the docs moved the predecessor into the
        # Legacy models table.
        for provider in ("claude", "claude-api"):
            ids = _ids(provider)
            self.assertNotIn("claude-opus-5", ids)
            self.assertNotIn("claude-opus-4-8", ids)
            self.assertNotIn("claude-opus-4-7", ids)
            self.assertNotIn("claude-opus-4-6", ids)
        self.assertNotIn("claude-sonnet-4-5-20250929", _ids("claude-api"))

    def test_superseded_fable_removed(self):
        # Fable 5 is superseded by Fable 5.1 at identical pricing, the same way
        # each Opus retires its predecessor. 5.1 is not a drop-in: forced
        # tool_choice ("any"/"tool") now 400s and thinking blocks are bound to
        # the model that produced them, so leaving both on offer would let a
        # picked model silently change request semantics.
        for provider in ("claude", "claude-api"):
            self.assertNotIn("claude-fable-5", _ids(provider), msg=provider)

    def test_superseded_sonnet_removed(self):
        # Sonnet 4.6 is superseded by Sonnet 5 at identical standard pricing,
        # and Sonnet 5 by Sonnet 5.5 (2026-09-28), which the docs now list
        # first while Sonnet 5 sits in the Legacy table.
        for provider in ("claude", "claude-api"):
            self.assertNotIn("claude-sonnet-4-6", _ids(provider))
            self.assertNotIn("claude-sonnet-5", _ids(provider))

    def test_rows_chat_completions_cannot_serve_are_absent(self):
        # OpenAIProvider always sends tools on Chat Completions. gpt-5.5-pro
        # is Responses-only, gpt-6.1-sol takes Chat Completions "without tool
        # calling", and gpt-5.4-nano is deprecated (shutdown 2027-04-01).
        for model in ("gpt-5.5-pro", "gpt-6.1-sol", "gpt-5.4-nano"):
            with self.subTest(model=model):
                self.assertNotIn(model, _ids("openai"))

    def test_retired_grok_fast_removed(self):
        # Retired 2026-05-15: the slugs are served by grok-4.3 at its rate.
        for model in ("grok-4-1-fast-non-reasoning", "grok-4-1-fast-reasoning"):
            with self.subTest(model=model):
                self.assertNotIn(model, _ids("grok"))

    def test_gemini_rows_new_projects_cannot_use_are_absent(self):
        # 2.5: closed to new projects 2026-09-18. 3.1 Flash-Lite: deprecated,
        # shutdown 2027-05-07. 3 Flash Preview: "our legacy Flash model".
        for model in ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite",
                      "gemini-3.1-flash-lite", "gemini-3-flash-preview"):
            with self.subTest(model=model):
                self.assertNotIn(model, _ids("gemini"))

    def test_retired_deepseek_flash_name_removed(self):
        # V4 Flash was retired 2026-09-10; its id now answers with V4.1 Flash.
        self.assertNotIn("deepseek-v4-flash", _ids("deepseek"))

    def test_dead_ollama_cloud_tags_removed(self):
        ids = _ids("ollama-cloud")
        self.assertNotIn("glm-5:cloud", ids)
        self.assertNotIn("qwen3-coder-next:cloud", ids)
        # Not served by ollama.com/api/tags on 2026-10-06, by any name.
        for gone in ("qwen3.5", "glm-5.1", "deepseek-v4-flash"):
            with self.subTest(model=gone):
                self.assertFalse(any(m == gone or m.startswith(gone + ":") for m in ids))

    def test_dead_openrouter_free_removed(self):
        or_ids = {mid for mid, _ in wizard.OPENROUTER_FREE_MODELS}
        for dead in (
            "openrouter/owl-alpha",
            "nex-agi/nex-n2-pro:free",
            "openai/gpt-oss-120b:free",
            "moonshotai/kimi-k2.6:free",
            "poolside/laguna-xs.2:free",
            # No longer free, or gone, on 2026-10-06:
            "qwen/qwen3-coder:free",
            "poolside/laguna-m.1:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "nvidia/nemotron-3-nano-30b-a3b:free",
            "tencent/hy3:free",
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-nano-12b-v2-vl:free",
            "nvidia/nemotron-nano-9b-v2:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            # Free, but only until 2026-10-31 (expiration_date on the listing):
            "poolside/laguna-xs-2.1:free",
            "poolside/laguna-s-2.1:free",
            # Priced 0 without a ":free" id: a launch promotion on a paid model.
            "inclusionai/ling-3.1-flash",
        ):
            self.assertNotIn(dead, or_ids)

    def test_codex_ids_are_codex_ids_not_api_ids(self):
        # This assertion used to run the other way: the July 2026 catalog
        # refresh "corrected" gpt-5.3-codex-spark to gpt-5.3-codex, and added
        # gpt-5.6 and gpt-5.4 to the codex list, all verified "against current
        # provider docs" — the OpenAI API's docs. Codex on a ChatGPT account
        # has its own, smaller catalog, and answers HTTP 400 for anything
        # outside it. Measured 2026-09-07 on codex-cli 0.153.4, one live
        # `codex exec` per id: the three below failed, and all seven ids the
        # picker offered then completed.
        ids = _ids("codex")
        for api_only in ("gpt-5.6", "gpt-5.4", "gpt-5.3-codex"):
            self.assertNotIn(api_only, ids)
        # gpt-5.3-codex-spark was the Codex spelling pinned here until Codex
        # dropped it (HTTP 400 on 2026-09-29, codex-cli 0.158.0). The 5.6
        # tiers are the same case: the API's bare gpt-5.6 is refused, and
        # gpt-5.6-sol is what Codex runs.
        self.assertIn("gpt-5.6-sol", ids)


class GrokVisionGateTests(unittest.TestCase):
    def test_grok_4_5_supports_vision(self):
        self.assertTrue(GrokProvider("grok-4.5").supports_vision)

    def test_every_offered_grok_supports_vision(self):
        # xAI lists TEXT and IMAGE input for all of them. The old named list
        # answered False for the default, grok-4.3, and for 4.6 and 4.7.
        for model in _ids("grok"):
            with self.subTest(model=model):
                self.assertTrue(GrokProvider(model).supports_vision)


class ClaudeSamplingGateTests(unittest.TestCase):
    """Sonnet 5, Fable 5, and Opus 4.7+ 400 on non-default temperature; the
    API provider must omit it for them and keep sending it to legacy models."""

    def test_new_models_omit_temperature(self):
        # Opus 5 and 5.5 have no entry in CLAUDE_SAMPLING_OK and must not
        # gain one: omitting temperature is accepted by every model, sending
        # it 400s on everything from Opus 4.7 forward.
        for model in ("claude-sonnet-5-5", "claude-sonnet-5", "claude-fable-5-1",
                      "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
                      "claude-opus-4-7"):
            with self.subTest(model=model):
                self.assertFalse(_claude_accepts_temperature(model))

    def test_legacy_models_keep_temperature(self):
        for model in ("claude-sonnet-4-6", "claude-sonnet-4-5-20250929",
                      "claude-haiku-4-5"):
            with self.subTest(model=model):
                self.assertTrue(_claude_accepts_temperature(model))


class LocalOllamaCatalogTests(unittest.TestCase):
    """install/ollama_setup.MODEL_CATALOG, the local rungs.

    recommend_model() takes the LAST row that fits, so the order is the whole
    selection rule, and nothing checked it while the October refresh rewrote
    the top two rows. Out of order, a machine is handed a smaller model than
    it can hold, or one too big for it.
    """

    def test_rows_ascend_by_need_and_quality(self):
        from install.ollama_setup import MODEL_CATALOG
        for earlier, later in zip(MODEL_CATALOG, MODEL_CATALOG[1:]):
            with self.subTest(row=later[0]):
                self.assertLessEqual(earlier[1], later[1])  # disk
                self.assertLessEqual(earlier[2], later[2])  # RAM
                self.assertLess(earlier[3], later[3])  # quality

    def test_each_machine_gets_the_best_model_it_can_hold(self):
        from install.ollama_setup import MODEL_CATALOG, recommend_model
        for ram_gb in (4, 8, 10, 16, 24, 30, 64):
            fits = [row for row in MODEL_CATALOG if row[2] <= ram_gb - 2.0]
            best = max(fits, key=lambda row: row[3])[0]
            with self.subTest(ram_gb=ram_gb):
                tag, _why = recommend_model(
                    {"ram_gb": ram_gb, "disk_free_gb": 500, "gpu": {"type": "none"}})
                self.assertEqual(tag, best)


if __name__ == "__main__":
    unittest.main()
