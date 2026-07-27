"""Runtime-gating registration invariants.

A code review caught that a new provider can be wired into the *setup*
surfaces (install.wizard + miniapp.server + core.llm catalog) while being
omitted from the *runtime* gating lists the live bot and background jobs
actually read, so CI stays green while the feature is half-delivered.

This guards the eight runtime constructs directly: any first-class API provider
must appear in every one of them. Adding a provider to setup but forgetting a
runtime list now fails here instead of silently shipping.

The lists are function-local (not importable), so we scan source text with a
balanced-delimiter extractor that correctly skips inner f-string braces like
{api_key} and inner tuple parens.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

# Providers that are first-class hosted chat APIs: each must be wired into
# every runtime gating list. (openrouter/gemini/ollama are intentionally
# special-cased in some lists, so they are not part of this invariant.)
FIRST_CLASS_API_PROVIDERS = ("openai", "deepseek", "grok", "kimi", "minimax", "zai")

# (relative path, anchor that ends with the opening '{' or '(' of the literal)
#
# `/provider`'s default-model map used to be a hand-copied literal here too
# ("bot.py", "default_models = {"). It is now derived from the wizard catalog
# by bot.provider_default_models(), so there is no literal left to scan and the
# invariant is structural rather than textual. It is asserted against the
# derived mapping in test_first_class_providers_in_the_default_model_map below,
# which is the stronger check: the text scan only proved the name appeared.
GATING_CONSTRUCTS = (
    ("bot.py", "full_mode = provider in ("),
    ("bot.py", "needs_key = new_provider in ("),
    ("bot.py", "capable_providers = ("),
    ("utils/reflect.py", "api_configs = {"),
    ("utils/reflect.py", "_CAPABLE_PROVIDERS = {"),
    ("utils/email_triage.py", "api_urls = {"),
    ("utils/email_triage.py", "DRAFT_CAPABLE_API = {"),
)


def _balanced_block(src: str, anchor: str) -> str:
    """Return the bracketed literal that begins at `anchor`.

    `anchor` must end with its opening delimiter ('{' or '('). Scans forward
    counting only that delimiter pair, so balanced inner braces ({api_key}) and
    inner parens (tuple values) do not terminate the block early.
    """
    start = src.index(anchor) + len(anchor) - 1  # index of the opening delimiter
    open_ch = src[start]
    close_ch = {"{": "}", "(": ")"}[open_ch]
    depth = 0
    for j in range(start, len(src)):
        c = src[j]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced block for anchor: {anchor!r}")


class RuntimeGatingRegistrationTests(unittest.TestCase):
    def test_first_class_providers_in_every_gating_list(self):
        for relpath, anchor in GATING_CONSTRUCTS:
            block = _balanced_block((ROOT / relpath).read_text(encoding="utf-8"), anchor)
            for prov in FIRST_CLASS_API_PROVIDERS:
                with self.subTest(file=relpath, anchor=anchor, provider=prov):
                    self.assertIn(
                        f'"{prov}"', block,
                        f"{prov!r} missing from `{anchor}` in {relpath}",
                    )

    def test_first_class_providers_in_the_default_model_map(self):
        # Same invariant the "default_models = {" source scan used to carry:
        # a first-class provider missing from /provider's map is unswitchable,
        # because the map doubles as the validation allowlist. Asserted on the
        # derived mapping now, so it also proves the value is non-empty.
        import bot as botmod

        models = botmod.provider_default_models()
        for prov in FIRST_CLASS_API_PROVIDERS:
            with self.subTest(provider=prov):
                self.assertIn(prov, models, f"{prov!r} missing from /provider's default-model map")
                self.assertTrue(models[prov], f"{prov!r} has no default model")

    def test_provider_alias_canonicalization(self):
        # /provider glm and /provider zhipu must resolve to the canonical 'zai'.
        bot_src = (ROOT / "bot.py").read_text(encoding="utf-8")
        self.assertIn('"glm": "zai"', bot_src)
        self.assertIn('"zhipu": "zai"', bot_src)

    def test_kimi_runtime_default_synced_to_wizard(self):
        # The runtime default must not lag install/wizard.py DEFAULT_MODELS.
        # This was a source-text assertion against bot.py's hand-copied literal
        # (the exact drift that stranded kimi on k2.6). The literal is gone and
        # the map is derived, so the check moves onto the live value: it now
        # fails if the wizard itself regresses, which the text scan could not
        # see, and it holds for every provider, not just the one that broke.
        import bot as botmod
        from install import wizard

        models = botmod.provider_default_models()
        self.assertEqual(wizard.DEFAULT_MODELS["kimi"], "kimi-k2.7-code")
        self.assertEqual(models["kimi"], "kimi-k2.7-code")
        self.assertEqual(models, {**wizard.DEFAULT_MODELS, **{
            "claude-cli": wizard.DEFAULT_MODELS["claude"],
            "codex-cli": wizard.DEFAULT_MODELS["codex"],
        }})

    def test_zai_runtime_endpoint_matches_provider_base_url(self):
        # The URL baked into the reflect/email-triage maps must match the
        # provider's BASE_URL so they can never drift out of sync.
        from core.llm import ZaiProvider

        expected = f"{ZaiProvider.BASE_URL}/chat/completions"
        for relpath in ("utils/reflect.py", "utils/email_triage.py"):
            src = (ROOT / relpath).read_text(encoding="utf-8")
            with self.subTest(file=relpath):
                self.assertIn(expected, src)


if __name__ == "__main__":
    unittest.main()
