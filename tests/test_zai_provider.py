"""Tests for the Z.ai (GLM) provider and its catalog registration.

Covers the ZaiProvider class (factory aliases, provider_name, BASE_URL,
vision detection, empty-key health check, request body + temperature clamp)
and the registration invariants across install.wizard and miniapp.server so
a future refactor cannot silently drop the provider. Also guards the
"also update" wave: Kimi K2.7-Code default + GLM-5.2 on Ollama Cloud.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import Message, ZaiProvider, create_provider  # noqa: E402
from install import wizard  # noqa: E402


class ZaiFactoryTests(unittest.TestCase):
    def test_aliases_create_zai_provider(self):
        for alias in ("zai", "glm", "zhipu", "ZAI", "GLM"):
            with self.subTest(alias=alias):
                self.assertIsInstance(
                    create_provider(alias, "glm-5.2", "k"), ZaiProvider
                )

    def test_provider_name_and_base_url(self):
        p = create_provider("zai", "glm-5.2", "k")
        self.assertEqual(p.provider_name, "zai")
        self.assertEqual(p.BASE_URL, "https://api.z.ai/api/paas/v4")
        self.assertEqual(p.model, "glm-5.2")


class ZaiVisionTests(unittest.TestCase):
    def test_glm_5_2_is_text_only(self):
        self.assertFalse(ZaiProvider("glm-5.2").supports_vision)

    def test_vision_variants_detected(self):
        for m in ("glm-5v-turbo", "glm-4.6v", "glm-4.5v", "GLM-5V-Turbo"):
            with self.subTest(model=m):
                self.assertTrue(ZaiProvider(m).supports_vision)


class ZaiHealthCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_key_message(self):
        ok, detail = await ZaiProvider("glm-5.2", "").health_check()
        self.assertFalse(ok)
        self.assertIn("ZAI_API_KEY", detail)
        self.assertIn("LLM_API_KEY", detail)


class ZaiCompleteTests(unittest.IsolatedAsyncioTestCase):
    async def _capture(self, temperature):
        mock = AsyncMock(return_value="sentinel")
        with patch("core.llm._openai_tool_loop", mock):
            result = await ZaiProvider("glm-5.2", "k").complete(
                "sys", [Message(role="user", content="hi")],
                max_tokens=1234, temperature=temperature,
            )
        return result, mock.await_args.kwargs

    async def test_request_shape(self):
        result, kw = await self._capture(0.7)
        self.assertEqual(result, "sentinel")
        self.assertEqual(kw["url"], "https://api.z.ai/api/paas/v4/chat/completions")
        self.assertEqual(kw["model"], "glm-5.2")
        self.assertEqual(kw["provider_name"], "zai")
        self.assertEqual(kw["headers"]["Authorization"], "Bearer k")
        body = kw["body"]
        self.assertEqual(body["model"], "glm-5.2")
        self.assertEqual(body["max_tokens"], 1234)
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})

    async def test_temperature_clamped_high(self):
        _, kw = await self._capture(1.7)
        self.assertEqual(kw["body"]["temperature"], 1.0)

    async def test_temperature_clamped_low(self):
        _, kw = await self._capture(-0.5)
        self.assertEqual(kw["body"]["temperature"], 0.0)


class WizardRegistrationTests(unittest.TestCase):
    def test_in_all_llm_providers(self):
        self.assertIn("zai", [p[0] for p in wizard._ALL_LLM_PROVIDERS])

    def test_default_model(self):
        self.assertEqual(wizard.DEFAULT_MODELS["zai"], "glm-5.2")

    def test_provider_models_list(self):
        entries = wizard.PROVIDER_MODELS["zai"]
        self.assertTrue(entries)
        self.assertEqual(entries[0][0], "glm-5.2")

    def test_in_api_key_providers(self):
        self.assertIn("zai", wizard.API_KEY_PROVIDERS)

    def test_api_key_guide_complete(self):
        guide = wizard.API_KEY_GUIDES["zai"]
        for key in ("name", "url", "steps"):
            self.assertIn(key, guide)
        self.assertTrue(guide["steps"])
        self.assertIn("z.ai", guide["url"])

    def test_default_model_has_catalog_entry(self):
        ids = [mid for mid, _ in wizard.PROVIDER_MODELS["zai"]]
        self.assertIn(wizard.DEFAULT_MODELS["zai"], ids)


class WaveUpdateTests(unittest.TestCase):
    """The 'also update' wave: Kimi K2.7-Code default + GLM-5.2 on Ollama Cloud."""

    def test_kimi_default_bumped_to_k27(self):
        self.assertEqual(wizard.DEFAULT_MODELS["kimi"], "kimi-k2.7-code")

    def test_kimi_k27_in_catalog_and_default_present(self):
        ids = [mid for mid, _ in wizard.PROVIDER_MODELS["kimi"]]
        self.assertIn("kimi-k2.7-code", ids)
        self.assertIn(wizard.DEFAULT_MODELS["kimi"], ids)

    def test_glm_5_2_on_ollama_cloud(self):
        ids = [mid for mid, _ in wizard.PROVIDER_MODELS["ollama-cloud"]]
        self.assertIn("glm-5.2:cloud", ids)


class MiniAppRegistrationTests(unittest.TestCase):
    def test_zai_in_available_providers(self):
        import miniapp.server as srv
        self.assertIn("zai", [p["id"] for p in srv._available_providers()])

    def test_zai_models_surface(self):
        import miniapp.server as srv
        self.assertIn("glm-5.2", [m["id"] for m in srv._available_models("zai")])


if __name__ == "__main__":
    unittest.main()
