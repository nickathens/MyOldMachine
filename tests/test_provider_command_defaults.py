#!/usr/bin/env python3
"""Unit tests for the /provider default-model map.

Run: python3 -m unittest tests.test_provider_command_defaults  (from repo root)

`/provider` used to carry its own hand-copied copy of the setup catalog, under
a "keep in sync with install/wizard.py DEFAULT_MODELS" comment. It drifted, and
the drift was invisible at runtime:

  1. Stale models. The copy still handed out claude-sonnet-4-6 and gpt-5.5
     after the July 2026 refresh retired both, so `/provider claude` silently
     configured a superseded model that the catalog tests assert is gone.
  2. A missing provider. The copy never listed ``codex``, and the same dict is
     the validation allowlist (`valid_providers = set(default_models.keys())`),
     so `/provider codex` answered "Unknown provider" even though the Codex CLI
     provider is registered in core.llm.create_provider and offered by the
     wizard.

bot.provider_default_models() now derives the map from the wizard, so both
classes of drift are structurally impossible. These tests lock that.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot as botmod  # noqa: E402
from install import wizard  # noqa: E402


class ProviderDefaultsDerivedTests(unittest.TestCase):
    def test_matches_wizard_for_every_shared_key(self):
        models = botmod.provider_default_models()
        for provider, default in wizard.DEFAULT_MODELS.items():
            with self.subTest(provider=provider):
                self.assertEqual(models.get(provider), default)

    def test_every_offered_provider_is_switchable(self):
        # The map doubles as /provider's validation allowlist, so a provider
        # the wizard offers but the map omits is unreachable from Telegram.
        models = botmod.provider_default_models()
        for provider, _desc in wizard._ALL_LLM_PROVIDERS:
            with self.subTest(provider=provider):
                self.assertIn(provider, models)

    def test_codex_is_switchable(self):
        # The concrete regression: codex was missing for a month of releases.
        models = botmod.provider_default_models()
        self.assertIn("codex", models)
        self.assertTrue(models["codex"])

    def test_cli_aliases_mirror_canonical_entry(self):
        models = botmod.provider_default_models()
        self.assertEqual(models["claude-cli"], models["claude"])
        self.assertEqual(models["codex-cli"], models["codex"])

    def test_no_retired_model_offered(self):
        # Names the exact ids the July 2026 refreshes retired; the catalog
        # suite proves they left PROVIDER_MODELS, this proves /provider too.
        values = set(botmod.provider_default_models().values())
        for retired in ("claude-sonnet-4-6", "claude-opus-4-8", "gpt-5"):
            with self.subTest(model=retired):
                self.assertNotIn(retired, values)

    def test_returns_a_fresh_copy(self):
        # Callers mutate nothing shared: the wizard catalog is global state.
        first = botmod.provider_default_models()
        first["claude"] = "tampered"
        self.assertNotEqual(botmod.provider_default_models()["claude"], "tampered")
        self.assertNotEqual(wizard.DEFAULT_MODELS["claude"], "tampered")


class HiddenProviderIdsTests(unittest.TestCase):
    def test_hidden_ids_exist_in_the_map(self):
        # The display filter and the "Valid:" error message both subtract this
        # tuple; an id that is not a real key would silently hide nothing.
        models = botmod.provider_default_models()
        for provider in botmod.HIDDEN_PROVIDER_IDS:
            with self.subTest(provider=provider):
                self.assertIn(provider, models)

    def test_canonical_providers_stay_visible(self):
        # Aliases are hidden, the providers they alias are not.
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                self.assertNotIn(provider, botmod.HIDDEN_PROVIDER_IDS)


if __name__ == "__main__":
    unittest.main()
