"""What each provider sends for the models added on 2026-10-06.

A catalog row is only half of adding a model: the request has to be one the
model's API accepts, and the image switch has to know the model sees. Every
rule here comes from the model's own documentation, read that day, and each
one closes a request that would otherwise fail or quietly lose something:

* GPT-6 on the OpenAI API takes max_completion_tokens and no temperature, like
  GPT-5, and a prefix check on "gpt-5" sent it neither. gpt-6-sol and
  gpt-6-luna take tools on Chat Completions only with reasoning_effort "none".
* Kimi fixes the sampling of every model it still serves: "passing any other
  value returns an error". The provider sent 0.7 on every turn.
* Gemini 3 deprecated temperature and asks for the default; the provider sent
  0.7. A function call that carries an id is answered with the same id.
* DeepSeek's thinking mode answers 400 unless reasoning_content comes back on
  the next tool step, and its Flash model now sees images.
* The image switches: every Grok 4, GPT-6, current Kimi, MiniMax M3 and the
  GLM-5.3 Flash pair take images, and each switch said no to some of them.
* The reflection and email triage fallbacks build these requests by hand, and
  sent temperature to every Claude model and max_tokens to GPT-6.
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import llm  # noqa: E402
from core import model_efforts as me  # noqa: E402
from core.llm import (  # noqa: E402
    DeepSeekProvider,
    GeminiProvider,
    KimiProvider,
    Message,
    MiniMaxProvider,
    OpenAIProvider,
    ZaiProvider,
)
from install import wizard  # noqa: E402


def _ids(provider: str) -> list[str]:
    return [mid for mid, _ in wizard.PROVIDER_MODELS[provider]]


class _Reply:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _AsyncClient:
    """Stands in for httpx.AsyncClient: scripted replies, recorded bodies."""

    def __init__(self, replies, sent):
        self._replies = list(replies)
        self._sent = sent

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self._sent.append(copy.deepcopy(json))
        return _Reply(self._replies.pop(0))


class _Client(_AsyncClient):
    """The same for the synchronous httpx.Client the nightly helpers use."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        self._sent.append(copy.deepcopy(json))
        return _Reply(self._replies.pop(0))


def _image_message(text="what is this?"):
    tmp = Path(tempfile.mkdtemp(prefix="mom-req-rules-"))
    img = tmp / "pixel.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    return Message(role="user", content=text, images=[str(img)])


class SharedRulesTests(unittest.TestCase):
    def test_versions_are_read_by_number(self):
        self.assertEqual(me.model_version("gpt-6.1-sol", "gpt-"), (6, 1))
        self.assertEqual(me.model_version("gpt-6-luna", "gpt-"), (6, 0))
        self.assertEqual(me.model_version("grok-4-1-fast-reasoning", "grok-"), (4, 0))
        self.assertEqual(me.model_version("grok-4.20-0309-reasoning", "grok-"), (4, 20))
        self.assertEqual(me.model_version("kimi-k2.7-code", "kimi-k"), (2, 7))
        self.assertIsNone(me.model_version("grok-build-0.1", "grok-"))
        self.assertIsNone(me.model_version("gemma4:31b", "gemini-"))

    def test_gpt_5_and_later_are_reasoning_models(self):
        for model in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-6-sol",
                      "gpt-5.6", "gpt-5.4-mini", "o3"):
            with self.subTest(model=model):
                self.assertTrue(me.openai_is_reasoning(model))
        for model in ("gpt-4.1", "gpt-4.1-mini", "gpt-4o"):
            with self.subTest(model=model):
                self.assertFalse(me.openai_is_reasoning(model))

    def test_gemini_3_and_later_get_no_temperature(self):
        for model in _ids("gemini"):
            with self.subTest(model=model):
                self.assertFalse(me.gemini_accepts_temperature(model))
        self.assertTrue(me.gemini_accepts_temperature("gemini-2.5-flash"))

    def test_sonnet_5_5_gets_no_temperature(self):
        self.assertFalse(me.claude_accepts_temperature("claude-sonnet-5-5"))
        self.assertTrue(me.claude_accepts_temperature("claude-haiku-4-5"))

    def test_the_provider_reads_the_shared_claude_rule(self):
        # One copy: core.llm re-exports it rather than keeping its own list.
        self.assertIs(llm._claude_accepts_temperature, me.claude_accepts_temperature)
        self.assertFalse(hasattr(llm, "_CLAUDE_SAMPLING_OK"))

    def test_kimi_takes_no_temperature(self):
        self.assertFalse(me.KIMI_ACCEPTS_TEMPERATURE)


class _LoopCapture:
    async def body_for(self, provider, messages=None):
        loop = AsyncMock(return_value="sentinel")
        with patch("core.llm._openai_tool_loop", loop):
            await provider.complete(
                "sys", messages or [Message(role="user", content="hi")],
                max_tokens=1234, temperature=0.7)
        return loop.await_args.kwargs["body"]


class OpenAIRequestTests(_LoopCapture, unittest.IsolatedAsyncioTestCase):
    async def test_gpt_6_gets_completion_tokens_and_no_temperature(self):
        for model in ("gpt-6-astra", "gpt-6-luna", "gpt-6-sol"):
            with self.subTest(model=model):
                body = await self.body_for(OpenAIProvider(model, "k"))
                self.assertEqual(body["max_completion_tokens"], 1234)
                self.assertNotIn("max_tokens", body)
                self.assertNotIn("temperature", body)

    async def test_sol_and_luna_run_tools_with_reasoning_off(self):
        # "Chat Completions supports function calling only with
        # reasoning_effort set to none" on both model pages.
        for model in ("gpt-6-luna", "gpt-6-sol"):
            with self.subTest(model=model):
                body = await self.body_for(OpenAIProvider(model, "k"))
                self.assertEqual(body["reasoning_effort"], "none")

    async def test_other_models_keep_their_reasoning(self):
        for model in ("gpt-6-astra", "gpt-5.6", "gpt-5.5", "gpt-4.1"):
            with self.subTest(model=model):
                body = await self.body_for(OpenAIProvider(model, "k"))
                self.assertNotIn("reasoning_effort", body)

    async def test_gpt_4_1_keeps_temperature_and_max_tokens(self):
        body = await self.body_for(OpenAIProvider("gpt-4.1", "k"))
        self.assertEqual(body["max_tokens"], 1234)
        self.assertEqual(body["temperature"], 0.7)

    async def test_every_offered_model_sees_images(self):
        for model in _ids("openai"):
            with self.subTest(model=model):
                self.assertTrue(OpenAIProvider(model, "k").supports_vision)
        body = await self.body_for(OpenAIProvider("gpt-6-astra", "k"),
                                   [_image_message()])
        content = body["messages"][-1]["content"]
        self.assertIsInstance(content, list)
        self.assertIn("image_url", {part.get("type") for part in content})

    def test_a_pre_vision_gpt_still_does_not(self):
        self.assertFalse(OpenAIProvider("gpt-3.5-turbo", "k").supports_vision)


class KimiRequestTests(_LoopCapture, unittest.IsolatedAsyncioTestCase):
    async def test_no_temperature_reaches_any_offered_model(self):
        for model in _ids("kimi"):
            with self.subTest(model=model):
                body = await self.body_for(KimiProvider(model, "k"))
                self.assertNotIn("temperature", body)

    def test_every_offered_model_sees_images(self):
        for model in _ids("kimi"):
            with self.subTest(model=model):
                self.assertTrue(KimiProvider(model, "k").supports_vision)
        self.assertFalse(KimiProvider("kimi-k2-0905-preview", "k").supports_vision)


class DeepSeekRequestTests(_LoopCapture, unittest.IsolatedAsyncioTestCase):
    def test_flash_sees_and_pro_does_not(self):
        self.assertTrue(DeepSeekProvider("deepseek-flash", "k").supports_vision)
        self.assertFalse(DeepSeekProvider("deepseek-v4-pro", "k").supports_vision)

    async def test_an_image_reaches_flash_as_an_image_part(self):
        body = await self.body_for(DeepSeekProvider("deepseek-flash", "k"),
                                   [_image_message()])
        content = body["messages"][-1]["content"]
        self.assertIn("image_url", {part.get("type") for part in content})

    async def test_text_only_turns_are_unchanged(self):
        body = await self.body_for(DeepSeekProvider("deepseek-flash", "k"))
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "hi"})
        self.assertEqual(body["max_tokens"], 1234)


class VisionSwitchTests(unittest.TestCase):
    def test_minimax_m3_family_sees(self):
        self.assertTrue(MiniMaxProvider("MiniMax-M3", "k").supports_vision)
        self.assertTrue(MiniMaxProvider("MiniMax-M3.1-Flash-Preview", "k").supports_vision)
        self.assertFalse(MiniMaxProvider("MiniMax-M2.7", "k").supports_vision)

    def test_glm_5_3_flash_pair_sees_and_glm_5_3_does_not(self):
        self.assertTrue(ZaiProvider("glm-5.3-flash", "k").supports_vision)
        self.assertTrue(ZaiProvider("glm-5.3-flashx", "k").supports_vision)
        self.assertFalse(ZaiProvider("glm-5.3", "k").supports_vision)


class GeminiRequestTests(unittest.IsolatedAsyncioTestCase):
    FINAL = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}

    async def _run(self, model, replies):
        sent: list = []
        with (patch("core.llm.httpx.AsyncClient", _AsyncClient(replies, sent)),
              patch("core.llm.execute_tool", AsyncMock(return_value="ok"))):
            result = await GeminiProvider(model, "k").complete(
                "sys", [Message(role="user", content="hi")], temperature=0.7)
        return result, sent

    async def test_gemini_3_gets_no_temperature(self):
        _result, sent = await self._run("gemini-3.8-flash", [self.FINAL])
        self.assertNotIn("temperature", sent[0]["generationConfig"])
        self.assertIn("maxOutputTokens", sent[0]["generationConfig"])

    async def test_an_older_gemini_still_gets_it(self):
        _result, sent = await self._run("gemini-2.5-flash", [self.FINAL])
        self.assertEqual(sent[0]["generationConfig"]["temperature"], 0.7)

    async def test_a_function_call_id_comes_back_with_its_response(self):
        call = {"candidates": [{"content": {"parts": [{"functionCall": {
            "id": "call-1", "name": "run_command", "args": {"command": "ls"}}}]}}]}
        result, sent = await self._run("gemini-3.8-flash", [call, self.FINAL])
        self.assertEqual(result.text, "done")
        response = sent[1]["contents"][-1]["parts"][0]["functionResponse"]
        self.assertEqual(response["id"], "call-1")
        self.assertEqual(response["name"], "run_command")

    async def test_a_call_without_an_id_is_answered_without_one(self):
        call = {"candidates": [{"content": {"parts": [{"functionCall": {
            "name": "run_command", "args": {"command": "ls"}}}]}}]}
        _result, sent = await self._run("gemini-3.8-flash", [call, self.FINAL])
        self.assertNotIn("id", sent[1]["contents"][-1]["parts"][0]["functionResponse"])


class ToolLoopReasoningTests(unittest.IsolatedAsyncioTestCase):
    FINAL = {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    async def _run(self, first):
        sent: list = []
        with (patch("core.llm.httpx.AsyncClient", _AsyncClient([first, self.FINAL], sent)),
              patch("core.llm.execute_tool", AsyncMock(return_value="ok"))):
            result = await llm._openai_tool_loop(
                url="https://example.invalid/v1/chat/completions", headers={},
                body={"model": "deepseek-flash",
                      "messages": [{"role": "user", "content": "hi"}]},
                model="deepseek-flash", provider_name="deepseek")
        self.assertEqual(result.text, "done")
        return [m for m in sent[1]["messages"] if m["role"] == "assistant"][0]

    async def test_reasoning_goes_back_on_the_next_tool_step(self):
        first = {"choices": [{"message": {
            "role": "assistant", "content": "", "reasoning_content": "thought",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "run_command", "arguments": '{"command": "ls"}'}}]}}]}
        assistant = await self._run(first)
        self.assertEqual(assistant["reasoning_content"], "thought")
        self.assertEqual(assistant["tool_calls"][0]["id"], "c1")

    async def test_a_model_that_sends_none_gets_none_back(self):
        first = {"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "run_command", "arguments": '{"command": "ls"}'}}]}}]}
        assistant = await self._run(first)
        self.assertNotIn("reasoning_content", assistant)

    async def test_the_text_fallback_hands_it_back_too(self):
        first = {"choices": [{"message": {
            "role": "assistant", "reasoning_content": "thought",
            "content": '{"name": "run_command", "arguments": {"command": "ls"}}'}}]}
        assistant = await self._run(first)
        self.assertEqual(assistant["reasoning_content"], "thought")


class NightlyHelperRequestTests(unittest.TestCase):
    """The reflection and email triage fallbacks, which build these by hand."""

    OPENAI_REPLY = {"choices": [{"message": {"content": "ok"}}]}
    CLAUDE_REPLY = {"content": [{"type": "text", "text": "ok"}]}
    GEMINI_REPLY = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    def _triage(self, provider, model, reply):
        from utils import email_triage
        sent: list = []
        with (patch("httpx.Client", _Client([reply], sent)),
              patch.object(email_triage, "get_llm_provider", return_value=provider),
              patch.object(email_triage, "get_llm_api_key", return_value="k")):
            self.assertEqual(email_triage._call_api("p", model, 30), "ok")
        return sent[0]

    def _reflect(self, provider, model, reply):
        from utils import reflect
        sent: list = []
        with (patch("httpx.Client", _Client([reply], sent)),
              patch.object(reflect, "get_llm_provider", return_value=provider),
              patch.object(reflect, "get_llm_model", return_value=model),
              patch.object(reflect, "get_llm_api_key", return_value="k")):
            self.assertEqual(reflect._call_api("p"), "ok")
        return sent[0]

    def test_sonnet_5_5_gets_no_temperature(self):
        for call in (self._triage, self._reflect):
            with self.subTest(helper=call.__name__):
                body = call("claude-api", "claude-sonnet-5-5", self.CLAUDE_REPLY)
                self.assertNotIn("temperature", body)
                self.assertEqual(body["model"], "claude-sonnet-5-5")

    def test_haiku_keeps_its_temperature(self):
        for call in (self._triage, self._reflect):
            with self.subTest(helper=call.__name__):
                body = call("claude-api", "claude-haiku-4-5", self.CLAUDE_REPLY)
                self.assertIn("temperature", body)

    def test_gpt_6_gets_completion_tokens_and_no_temperature(self):
        for call in (self._triage, self._reflect):
            with self.subTest(helper=call.__name__):
                body = call("openai", "gpt-6-astra", self.OPENAI_REPLY)
                self.assertIn("max_completion_tokens", body)
                self.assertNotIn("max_tokens", body)
                self.assertNotIn("temperature", body)

    def test_kimi_gets_no_temperature(self):
        for call in (self._triage, self._reflect):
            with self.subTest(helper=call.__name__):
                body = call("kimi", "kimi-k2.7-code", self.OPENAI_REPLY)
                self.assertNotIn("temperature", body)

    def test_gemini_3_gets_no_temperature(self):
        for call in (self._triage, self._reflect):
            with self.subTest(helper=call.__name__):
                body = call("gemini", "gemini-3.8-flash", self.GEMINI_REPLY)
                self.assertNotIn("temperature", body["generationConfig"])


if __name__ == "__main__":
    unittest.main()
