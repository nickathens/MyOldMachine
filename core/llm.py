"""
LLM Provider Abstraction Layer.

Supports: Claude (Anthropic), OpenAI, Google Gemini, Ollama, OpenRouter.
All providers expose the same interface: send messages, get text back.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Message:
    role: str  # "user" or "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None


class LLMProvider(ABC):
    """Base class for LLM providers."""

    def __init__(self, model: str, api_key: str = ""):
        self.model = model
        self.api_key = api_key

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        messages: list[Message],
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send messages and get a completion."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    def supports_vision(self) -> bool:
        """Whether this provider/model supports image inputs."""
        return False


class ClaudeProvider(LLMProvider):
    """Anthropic Claude API."""

    API_URL = "https://api.anthropic.com/v1/messages"

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def supports_vision(self) -> bool:
        return True

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7):
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(self.API_URL, headers=headers, json=body)
                data = resp.json()
                if resp.status_code != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Claude API error: {error_msg}"
                    )
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text += block["text"]
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text, model=self.model, provider=self.provider_name,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
        except Exception as e:
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error=f"Claude request failed: {e}"
            )


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible API (works with OpenAI, OpenRouter, and compatible endpoints)."""

    def __init__(self, model: str, api_key: str = "", base_url: str = "https://api.openai.com/v1"):
        super().__init__(model, api_key)
        self.base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def supports_vision(self) -> bool:
        return "gpt-4" in self.model or "vision" in self.model

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=body
                )
                data = resp.json()
                if resp.status_code != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"OpenAI API error: {error_msg}"
                    )
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text, model=self.model, provider=self.provider_name,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )
        except Exception as e:
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error=f"OpenAI request failed: {e}"
            )


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter API (OpenAI-compatible with model routing)."""

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key, base_url="https://openrouter.ai/api/v1")

    @property
    def provider_name(self) -> str:
        return "openrouter"


class GeminiProvider(LLMProvider):
    """Google Gemini API."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def supports_vision(self) -> bool:
        return True

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7):
        url = f"{self.API_URL}/{self.model}:generateContent?key={self.api_key}"
        contents = []
        for m in messages:
            role = "user" if m.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.content}]})

        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, json=body)
                data = resp.json()
                if resp.status_code != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Gemini API error: {error_msg}"
                    )
                candidates = data.get("candidates", [])
                if not candidates:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error="Gemini returned no candidates"
                    )
                parts = candidates[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                usage = data.get("usageMetadata", {})
                return LLMResponse(
                    text=text, model=self.model, provider=self.provider_name,
                    input_tokens=usage.get("promptTokenCount", 0),
                    output_tokens=usage.get("candidatesTokenCount", 0),
                )
        except Exception as e:
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error=f"Gemini request failed: {e}"
            )


class OllamaProvider(LLMProvider):
    """Ollama local models."""

    def __init__(self, model: str, api_key: str = "", base_url: str = "http://localhost:11434"):
        super().__init__(model, api_key)
        self.base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7):
        url = f"{self.base_url}/api/chat"
        ollama_messages = [
            {"role": "system", "content": system_prompt},
            *[{"role": m.role, "content": m.content} for m in messages],
        ]
        body = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(url, json=body)
                data = resp.json()
                if resp.status_code != 200:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Ollama error: {data}"
                    )
                text = data.get("message", {}).get("content", "")
                return LLMResponse(
                    text=text, model=self.model, provider=self.provider_name,
                    input_tokens=data.get("prompt_eval_count", 0),
                    output_tokens=data.get("eval_count", 0),
                )
        except httpx.ConnectError:
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error="Cannot connect to Ollama. Is it running? (ollama serve)"
            )
        except Exception as e:
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error=f"Ollama request failed: {e}"
            )


def create_provider(
    provider: str, model: str, api_key: str = "", **kwargs
) -> LLMProvider:
    """Factory function to create the right LLM provider."""
    providers = {
        "claude": lambda: ClaudeProvider(model, api_key),
        "anthropic": lambda: ClaudeProvider(model, api_key),
        "openai": lambda: OpenAIProvider(model, api_key),
        "gemini": lambda: GeminiProvider(model, api_key),
        "google": lambda: GeminiProvider(model, api_key),
        "ollama": lambda: OllamaProvider(
            model, api_key, kwargs.get("base_url", "http://localhost:11434")
        ),
        "openrouter": lambda: OpenRouterProvider(model, api_key),
    }
    factory = providers.get(provider.lower())
    if not factory:
        supported = ", ".join(sorted(providers.keys()))
        raise ValueError(f"Unknown LLM provider: {provider}. Supported: {supported}")
    return factory()
