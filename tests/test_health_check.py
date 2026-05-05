"""Unit tests for LLMProvider.health_check() implementations.

The bot calls health_check() at startup and after /provider, /model,
/apikey switches. A failed result short-circuits call_llm() so users
see an actionable error instead of a stack trace.

We mock httpx and subprocess to keep tests offline. Each provider class
gets at least one healthy and one unhealthy case.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import llm  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _mock_response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _mock_http_client(get_response=None, get_side_effect=None):
    """Build a mock context manager that returns a client with .get patched."""
    client = MagicMock()
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    else:
        client.get = AsyncMock(return_value=get_response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class HttpGetHealthHelperTests(unittest.TestCase):
    """Cover _http_get_health response handling — the shared helper."""

    def test_200_is_healthy(self):
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertTrue(ok)
        self.assertIn("ok", reason)

    def test_204_is_healthy(self):
        cm = _mock_http_client(get_response=_mock_response(204))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertTrue(ok)

    def test_401_is_unhealthy(self):
        cm = _mock_http_client(get_response=_mock_response(401))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertFalse(ok)
        self.assertIn("invalid API key", reason)

    def test_403_is_unhealthy(self):
        cm = _mock_http_client(get_response=_mock_response(403))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertFalse(ok)

    def test_404_is_lenient(self):
        # /models endpoint can legitimately 404 on some providers (MiniMax),
        # so we treat that as "could not pre-verify, proceed".
        cm = _mock_http_client(get_response=_mock_response(404))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertTrue(ok)
        self.assertIn("404", reason)

    def test_500_is_unhealthy(self):
        cm = _mock_http_client(get_response=_mock_response(500, "internal error"))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertFalse(ok)
        self.assertIn("500", reason)

    def test_connect_error_is_unhealthy(self):
        cm = _mock_http_client(get_side_effect=llm.httpx.ConnectError("boom"))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertFalse(ok)
        self.assertIn("cannot reach", reason)

    def test_read_timeout_is_unhealthy(self):
        cm = _mock_http_client(get_side_effect=llm.httpx.ReadTimeout("timed out"))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(llm._http_get_health("http://x", {}, "test"))
        self.assertFalse(ok)
        self.assertIn("timed out", reason)


class LooksLikeBinaryLoadFailureTests(unittest.TestCase):
    """Marker detection for dyld / GLIBC failures in CLI stderr."""

    def test_dyld_symbol_not_found(self):
        msg = "dyld: Symbol not found: _ubrk_clone\n  Referenced from: /usr/local/bin/claude"
        self.assertTrue(llm._looks_like_binary_load_failure(msg))

    def test_dyld_bracket_form(self):
        msg = "dyld[1234]: missing symbol called"
        self.assertTrue(llm._looks_like_binary_load_failure(msg))

    def test_glibc_not_found(self):
        msg = "/lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.31' not found (required by claude)"
        self.assertTrue(llm._looks_like_binary_load_failure(msg))

    def test_random_error_is_not_a_binary_failure(self):
        msg = "Error: not enough arguments\nUsage: claude [options]"
        self.assertFalse(llm._looks_like_binary_load_failure(msg))


class ClaudeAPIHealthCheckTests(unittest.TestCase):
    def test_no_key_returns_unhealthy(self):
        provider = llm.ClaudeAPIProvider("claude-sonnet-4-6", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("ANTHROPIC_API_KEY", reason)

    def test_200_returns_healthy(self):
        provider = llm.ClaudeAPIProvider("claude-sonnet-4-6", api_key="sk-ant-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)

    def test_bad_key_returns_unhealthy(self):
        provider = llm.ClaudeAPIProvider("claude-sonnet-4-6", api_key="bogus")
        cm = _mock_http_client(get_response=_mock_response(401))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("invalid API key", reason)


class OpenAIHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.OpenAIProvider("gpt-4o", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("OPENAI_API_KEY", reason)

    def test_200(self):
        provider = llm.OpenAIProvider("gpt-4o", api_key="sk-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)


class OpenRouterHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.OpenRouterProvider("openai/gpt-4o", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("OPENROUTER_API_KEY", reason)

    def test_200(self):
        provider = llm.OpenRouterProvider("openai/gpt-4o", api_key="sk-or-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)


class GeminiHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.GeminiProvider("gemini-3-flash", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("GEMINI_API_KEY", reason)

    def test_200(self):
        provider = llm.GeminiProvider("gemini-3-flash", api_key="AIza-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)


class DeepSeekHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.DeepSeekProvider("deepseek-chat", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("DEEPSEEK_API_KEY", reason)

    def test_200(self):
        provider = llm.DeepSeekProvider("deepseek-chat", api_key="sk-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)


class GrokHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.GrokProvider("grok-4", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("XAI_API_KEY", reason)

    def test_200(self):
        provider = llm.GrokProvider("grok-4", api_key="xai-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)


class KimiHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.KimiProvider("kimi-k2", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("MOONSHOT_API_KEY", reason)

    def test_200(self):
        provider = llm.KimiProvider("kimi-k2", api_key="sk-test")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)


class MiniMaxHealthCheckTests(unittest.TestCase):
    def test_no_key(self):
        provider = llm.MiniMaxProvider("minimax-m2.7", api_key="")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("MINIMAX_API_KEY", reason)

    def test_404_is_lenient(self):
        # MiniMax /models endpoint shape uncertain — 404 should not block.
        provider = llm.MiniMaxProvider("minimax-m2.7", api_key="sk-test")
        cm = _mock_http_client(get_response=_mock_response(404))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)

    def test_401_is_unhealthy(self):
        provider = llm.MiniMaxProvider("minimax-m2.7", api_key="bad")
        cm = _mock_http_client(get_response=_mock_response(401))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertFalse(ok)


class OllamaHealthCheckTests(unittest.TestCase):
    def test_local_no_key_ok_when_reachable(self):
        provider = llm.OllamaProvider("llama3", api_key="", base_url="http://localhost:11434")
        cm = _mock_http_client(get_response=_mock_response(200))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)

    def test_local_unreachable(self):
        provider = llm.OllamaProvider("llama3", api_key="", base_url="http://localhost:11434")
        cm = _mock_http_client(get_side_effect=llm.httpx.ConnectError("refused"))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("ollama serve", reason)

    def test_cloud_requires_key(self):
        provider = llm.OllamaProvider("llama3", api_key="", base_url="https://ollama.com")
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("OLLAMA_API_KEY", reason)
        self.assertIn("cloud", reason)

    def test_cloud_invalid_key(self):
        provider = llm.OllamaProvider("llama3", api_key="bad", base_url="https://ollama.com")
        cm = _mock_http_client(get_response=_mock_response(401))
        with patch.object(llm.httpx, "AsyncClient", return_value=cm):
            ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("invalid API key", reason)


class ClaudeCLIHealthCheckTests(unittest.TestCase):
    """ClaudeCLIProvider.health_check spawns `claude --version`. We patch the
    asyncio subprocess machinery to feed in fake stdout/stderr/returncode
    triples covering: success, dyld failure, missing binary, timeout.
    """

    def _make_proc(self, stdout: bytes, stderr: bytes, returncode: int):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        proc.kill = MagicMock()
        return proc

    def test_healthy_version_output(self):
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        # Point at a real existing path so the early not-found check passes.
        provider._cli_binary = "/usr/bin/env"
        proc = self._make_proc(b"claude 1.2.3\n", b"", 0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, reason = _run(provider.health_check())
        self.assertTrue(ok)
        self.assertIn("1.2.3", reason)

    def test_dyld_symbol_failure(self):
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        provider._cli_binary = "/usr/bin/env"
        proc = self._make_proc(
            b"",
            b"dyld: Symbol not found: _ubrk_clone\nReferenced from: /usr/local/bin/claude\n",
            134,
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("Symbol not found", reason)

    def test_missing_binary(self):
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6", api_key="")
        # Force the not-found early-return path: empty cli_binary triggers
        # "Claude CLI binary not found in PATH" without spawning a subprocess.
        provider._cli_binary = ""
        ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("not found", reason.lower())


class CodexCLIHealthCheckTests(unittest.TestCase):
    def _make_proc(self, stdout: bytes, stderr: bytes, returncode: int):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
        proc.returncode = returncode
        proc.kill = MagicMock()
        return proc

    def test_healthy(self):
        provider = llm.CodexCLIProvider("gpt-5-codex", api_key="")
        provider._cli_binary = "/usr/bin/env"
        proc = self._make_proc(b"codex 0.5.1\n", b"", 0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, _ = _run(provider.health_check())
        self.assertTrue(ok)

    def test_dyld_failure(self):
        provider = llm.CodexCLIProvider("gpt-5-codex", api_key="")
        provider._cli_binary = "/usr/bin/env"
        proc = self._make_proc(b"", b"dyld[3]: Symbol not found: _foo\n", 1)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            ok, reason = _run(provider.health_check())
        self.assertFalse(ok)
        self.assertIn("Symbol not found", reason)


class BaseProviderDefaultHealthCheckTests(unittest.TestCase):
    """The default LLMProvider.health_check returns (True, ...). Subclasses
    that don't override (none today, but a defensive contract) should not
    block startup.
    """

    def test_default_is_optimistic(self):
        # Build a minimal concrete subclass with no health_check override.
        class _Toy(llm.LLMProvider):
            @property
            def provider_name(self):
                return "toy"

            async def complete(self, *a, **kw):  # pragma: no cover
                raise NotImplementedError

        ok, reason = _run(_Toy("m").health_check())
        self.assertTrue(ok)
        self.assertIn("no health-check", reason)


if __name__ == "__main__":
    unittest.main()
