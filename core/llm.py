#!/usr/bin/env python3
"""
LLM Provider Abstraction Layer for MyOldMachine.

PRIMARY: Claude Code CLI — runs as subprocess with full tool-use (bash, file
read/write, etc.). This is how the bot actually controls the machine.

FALLBACK API PROVIDERS: OpenAI, Google Gemini, Ollama, OpenRouter — these
use httpx for API calls and return plain text (no tool-use capability).
Useful for low-cost chat or when Claude Code CLI is unavailable.
"""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
    tool_use: bool = False  # Whether the response involved tool use


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
    def supports_tool_use(self) -> bool:
        """Whether this provider supports tool use (running commands, etc.)."""
        return False

    @property
    def supports_vision(self) -> bool:
        """Whether this provider/model supports image inputs."""
        return False


class ClaudeCLIProvider(LLMProvider):
    """
    Claude Code CLI provider — the primary provider for MyOldMachine.

    Runs `claude` CLI as a subprocess with --dangerously-skip-permissions,
    giving the LLM full tool-use capability: bash commands, file read/write,
    web fetch, etc. This is how the bot controls the machine.

    Output is parsed from stream-json format to track progress and extract
    the final result.
    """

    IDLE_TIMEOUT = 3600  # 1 hour of no output = stuck
    PROGRESS_INTERVAL = 300  # Send progress message every 5 min

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str = ""):
        super().__init__(model, api_key)
        self._bot_dir = Path(__file__).parent.parent
        self._active_processes: set = set()
        # Callbacks set by bot.py
        self.on_progress_save = None  # (user_id, message, partial, status, tool) -> None
        self.on_progress_clear = None  # (user_id) -> None

    @property
    def provider_name(self) -> str:
        return "claude-cli"

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return True

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7,
                       chat=None, user_id: int = None, original_message: str = "") -> LLMResponse:
        """
        Call Claude Code CLI with full tool-use.

        Unlike API providers, this ignores max_tokens/temperature and uses
        the CLI's own defaults. The system_prompt + messages are combined
        into a single prompt passed via stdin.

        Args:
            chat: Telegram chat object for typing indicators / progress messages
            user_id: For progress tracking
            original_message: The user's original message (for progress recovery)
        """
        # Build the full prompt from system prompt + conversation history + new message
        prompt = system_prompt + "\n\n"
        for msg in messages:
            prompt += f"<{msg.role}>{msg.content}</{msg.role}>\n"
        prompt += "\nContinue the conversation naturally, responding to the latest message."

        cmd = [
            "claude",
            "-p",
            "--model", self.model,
            "--dangerously-skip-permissions",
            "--disallowedTools", "Task,EnterPlanMode",
            "--output-format", "stream-json",
            "--verbose",
            "-",  # Read from stdin
        ]

        typing_task = None
        process = None
        last_activity = asyncio.get_event_loop().time()
        last_progress_message = asyncio.get_event_loop().time()
        last_progress_save = asyncio.get_event_loop().time()
        final_result = None
        partial_text = ""
        current_status = "thinking"
        tool_in_progress = None

        async def send_typing_periodically():
            while True:
                try:
                    if chat:
                        await chat.send_action("typing")
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(3)

        async def read_line_with_timeout(stream, timeout: float):
            try:
                return await asyncio.wait_for(stream.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                return None

        try:
            if chat:
                typing_task = asyncio.create_task(send_typing_periodically())

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._bot_dir),
                env={**os.environ, "HOME": str(Path.home())},
                limit=10 * 1024 * 1024,  # 10MB buffer
            )
            self._active_processes.add(process)

            # Write prompt to stdin
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

            # Read output line by line with activity-based timeout
            while True:
                current_time = asyncio.get_event_loop().time()
                time_since_activity = current_time - last_activity

                if time_since_activity > self.IDLE_TIMEOUT:
                    logger.warning(f"Claude idle timeout for user {user_id} after {self.IDLE_TIMEOUT}s")
                    if self.on_progress_save and user_id:
                        self.on_progress_save(user_id, original_message, partial_text,
                                              f"timeout after {self.IDLE_TIMEOUT}s", tool_in_progress)
                    process.kill()
                    await process.wait()
                    if final_result:
                        if self.on_progress_clear and user_id:
                            self.on_progress_clear(user_id)
                        return LLMResponse(
                            text=final_result + "\n\n[Task incomplete - Claude stopped responding after 1 hour]",
                            model=self.model, provider=self.provider_name, tool_use=True,
                        )
                    timeout_msg = "Claude stopped responding after 1 hour of inactivity."
                    if tool_in_progress:
                        timeout_msg += f" Was running: {tool_in_progress}"
                    if partial_text:
                        timeout_msg += "\n\nPartial progress was saved. Use /recover to see it."
                    return LLMResponse(text=timeout_msg, model=self.model, provider=self.provider_name)

                # Send progress message periodically
                time_since_progress = current_time - last_progress_message
                if time_since_progress >= self.PROGRESS_INTERVAL and chat:
                    try:
                        if tool_in_progress:
                            await chat.send_message(f"Still working... (running {tool_in_progress})")
                        else:
                            await chat.send_message(f"Still working... ({current_status})")
                        last_progress_message = current_time
                    except Exception:
                        pass

                read_timeout = min(30, self.IDLE_TIMEOUT - time_since_activity)
                line = await read_line_with_timeout(process.stdout, timeout=read_timeout)

                if line:
                    last_activity = asyncio.get_event_loop().time()
                    line_str = line.decode().strip()
                    if line_str:
                        try:
                            data = json.loads(line_str)
                            msg_type = data.get("type")

                            if msg_type == "assistant":
                                current_status = "generating response"
                                tool_in_progress = None
                                msg_data = data.get("message", {})
                                for block in msg_data.get("content", []):
                                    if block.get("type") == "text":
                                        text = block.get("text", "")
                                        if text and text not in partial_text:
                                            partial_text += text + "\n"
                            elif msg_type == "tool_use":
                                tool_name = data.get("name", "tool")
                                tool_in_progress = tool_name
                                current_status = f"using {tool_name}"
                            elif msg_type == "tool_result":
                                tool_in_progress = None
                                current_status = "processing result"

                            if msg_type == "result" and "result" in data:
                                final_result = data["result"]

                            # Save progress periodically
                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_progress_save >= 30:
                                if self.on_progress_save and user_id:
                                    self.on_progress_save(user_id, original_message,
                                                          partial_text, current_status,
                                                          tool_in_progress)
                                last_progress_save = current_time
                        except json.JSONDecodeError:
                            pass
                elif line == b'':
                    break  # EOF

                if process.returncode is not None:
                    break

            await process.wait()
            stderr_bytes = await process.stderr.read()
            stderr_text = stderr_bytes.decode(errors="replace").strip()

            if process.returncode != 0 and not final_result:
                logger.error(f"Claude error for user {user_id} (exit {process.returncode}): {stderr_text}")
                if partial_text.strip():
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    return LLMResponse(
                        text=partial_text.strip(), model=self.model,
                        provider=self.provider_name, tool_use=True,
                    )
                if self.on_progress_save and user_id:
                    self.on_progress_save(user_id, original_message, partial_text,
                                          f"error: {stderr_text[:100]}", tool_in_progress)
                if "out of memory" in stderr_text.lower() or "killed" in stderr_text.lower():
                    return LLMResponse(
                        text="Claude process was killed (likely out of memory). Try a simpler request or use /clear.",
                        model=self.model, provider=self.provider_name,
                        error="OOM killed",
                    )
                return LLMResponse(
                    text=f"Error (exit code {process.returncode}): {stderr_text[:500]}",
                    model=self.model, provider=self.provider_name,
                    error=stderr_text[:200],
                )

            # Success
            if self.on_progress_clear and user_id:
                self.on_progress_clear(user_id)

            if final_result:
                return LLMResponse(
                    text=final_result, model=self.model,
                    provider=self.provider_name, tool_use=True,
                )
            elif partial_text.strip():
                return LLMResponse(
                    text=partial_text.strip(), model=self.model,
                    provider=self.provider_name, tool_use=True,
                )
            else:
                if stderr_text:
                    logger.warning(f"No response from Claude for user {user_id}. Stderr: {stderr_text[:500]}")
                return LLMResponse(
                    text="Claude produced no response. Try /clear to reset, or send a shorter message.",
                    model=self.model, provider=self.provider_name,
                    error="No output",
                )

        except Exception as e:
            logger.exception(f"Failed to call Claude for user {user_id}")
            return LLMResponse(
                text=f"Error: {str(e)}", model=self.model,
                provider=self.provider_name, error=str(e),
            )
        finally:
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            if process:
                self._active_processes.discard(process)
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    async def graceful_shutdown(self):
        """Wait for active Claude processes to complete."""
        if self._active_processes:
            logger.info(f"Waiting for {len(self._active_processes)} active Claude processes...")
            for _ in range(300):
                if not self._active_processes:
                    break
                await asyncio.sleep(1)
            if self._active_processes:
                logger.warning(f"Timeout: {len(self._active_processes)} processes still running")


class ClaudeAPIProvider(LLMProvider):
    """Anthropic Claude API (no tool-use, text-only fallback)."""

    API_URL = "https://api.anthropic.com/v1/messages"

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def supports_vision(self) -> bool:
        return True

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
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
    """OpenAI-compatible API."""

    def __init__(self, model: str, api_key: str = "", base_url: str = "https://api.openai.com/v1"):
        super().__init__(model, api_key)
        self.base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def supports_vision(self) -> bool:
        return "gpt-4" in self.model or "vision" in self.model

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
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
                resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
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


class OpenRouterProvider(LLMProvider):
    """OpenRouter API — routes to many models via a single API key."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def supports_vision(self) -> bool:
        return True  # Most OpenRouter models support vision

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nickathens/MyOldMachine",
            "X-Title": "MyOldMachine",
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
                resp = await client.post(f"{self.BASE_URL}/chat/completions", headers=headers, json=body)
                data = resp.json()
                if resp.status_code != 200:
                    error_obj = data.get("error", {})
                    error_msg = error_obj.get("message", str(data)) if isinstance(error_obj, dict) else str(error_obj)
                    # Include model info for debugging
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"OpenRouter error ({self.model}): {error_msg}"
                    )
                choices = data.get("choices", [])
                if not choices:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"OpenRouter returned no choices for model {self.model}"
                    )
                text = choices[0].get("message", {}).get("content", "")
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text, model=self.model, provider=self.provider_name,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )
        except Exception as e:
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error=f"OpenRouter request failed: {e}"
            )


class GeminiProvider(LLMProvider):
    """Google Gemini API."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def supports_vision(self) -> bool:
        return True

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
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

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
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
    """Factory function to create the right LLM provider.

    When provider is 'claude' and no API key is set, uses the Claude CLI
    provider (with full tool-use). If an API key is provided, falls back
    to the API-only provider.
    """
    providers = {
        "claude": lambda: ClaudeCLIProvider(model, api_key) if not api_key else ClaudeAPIProvider(model, api_key),
        "claude-cli": lambda: ClaudeCLIProvider(model, api_key),
        "claude-api": lambda: ClaudeAPIProvider(model, api_key),
        "anthropic": lambda: ClaudeAPIProvider(model, api_key),
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
