#!/usr/bin/env python3
"""
LLM Provider Abstraction Layer for MyOldMachine.

PRIMARY: Claude Code CLI — runs as subprocess with full tool-use (bash, file
read/write, etc.). This is how the bot actually controls the machine.

API PROVIDERS: OpenAI, Google Gemini, Ollama, OpenRouter — these use httpx
for API calls with function-calling / tool-use support. The LLM sends
structured tool calls, we execute them locally, and return results.
"""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from core.tools import (
    get_tools_openai,
    get_tools_gemini,
    MAX_TOOL_ITERATIONS,
    execute_tool,
)

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
        return True  # All providers now support tool use

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
    """Anthropic Claude API — text-only, no tool execution layer."""

    API_URL = "https://api.anthropic.com/v1/messages"

    @property
    def provider_name(self) -> str:
        return "claude-api"

    @property
    def supports_tool_use(self) -> bool:
        return False  # Claude API provider doesn't use our tool layer

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


# --- OpenAI-Compatible Tool-Use Loop ---
# Used by OpenAI, OpenRouter, and Ollama (all share the same format)

async def _openai_tool_loop(
    url: str,
    headers: dict,
    body: dict,
    model: str,
    provider_name: str,
    timeout: float = 300.0,
) -> LLMResponse:
    """
    Shared tool-use loop for OpenAI-compatible APIs.

    Sends the request with tool definitions. If the model responds with
    tool_calls, executes them locally, appends results, and re-sends.
    Repeats until the model responds with text or hits the iteration limit.
    """
    # Add tools to the request body
    body["tools"] = get_tools_openai()
    body["tool_choice"] = "auto"

    messages = body["messages"]
    total_input = 0
    total_output = 0
    used_tools = False

    async with httpx.AsyncClient(timeout=timeout) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                resp = await client.post(url, headers=headers, json=body)
                data = resp.json()
            except Exception as e:
                return LLMResponse(
                    text="", model=model, provider=provider_name,
                    error=f"{provider_name} request failed: {e}",
                )

            if resp.status_code != 200:
                error_obj = data.get("error", {})
                if isinstance(error_obj, dict):
                    error_msg = error_obj.get("message", str(data))
                else:
                    error_msg = str(error_obj)
                return LLMResponse(
                    text="", model=model, provider=provider_name,
                    error=f"{provider_name} error ({model}): {error_msg}",
                )

            # Track token usage
            usage = data.get("usage", {})
            total_input += usage.get("prompt_tokens", 0)
            total_output += usage.get("completion_tokens", 0)

            choices = data.get("choices", [])
            if not choices:
                return LLMResponse(
                    text="", model=model, provider=provider_name,
                    error=f"{provider_name} returned no choices",
                )

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            # Check if the model wants to call tools
            # finish_reason varies by provider: "tool_calls" (OpenAI), "stop" (some OpenRouter models),
            # empty string, or None. Check tool_calls presence regardless of finish_reason.
            tool_calls = message.get("tool_calls")
            if tool_calls:
                used_tools = True

                # Append the assistant message with tool_calls to conversation
                assistant_msg = {"role": "assistant", "content": message.get("content") or ""}
                assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)

                # Execute each tool call and append results
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    func = tc.get("function", {})
                    func_name = func.get("name", "")
                    try:
                        func_args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        func_args = {}

                    logger.info(f"[{provider_name}] Tool call: {func_name}({json.dumps(func_args)[:200]})")
                    result = await execute_tool(func_name, func_args)
                    logger.info(f"[{provider_name}] Tool result: {result[:200]}")

                    # Truncate to avoid context overflow on smaller models
                    if len(result) > 30000:
                        result = result[:30000] + "\n\n[Truncated — full output was " + str(len(result)) + " chars]"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result,
                    })

                # Update body with extended messages and loop
                body["messages"] = messages
                continue

            # No tool calls — this is the final text response
            text = message.get("content", "") or ""
            return LLMResponse(
                text=text, model=model, provider=provider_name,
                input_tokens=total_input, output_tokens=total_output,
                tool_use=used_tools,
            )

    # Hit iteration limit
    return LLMResponse(
        text="I reached the maximum number of tool-use steps. Here's what I accomplished so far. Please send a follow-up message to continue.",
        model=model, provider=provider_name,
        tool_use=True,
    )


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible API with tool-use support."""

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
        return await _openai_tool_loop(
            url=f"{self.base_url}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
        )


class OpenRouterProvider(LLMProvider):
    """OpenRouter API — routes to many models via a single API key. Tool-use enabled."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def supports_vision(self) -> bool:
        return True

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
        return await _openai_tool_loop(
            url=f"{self.BASE_URL}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
        )


class GeminiProvider(LLMProvider):
    """Google Gemini API with function-calling / tool-use support."""

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def supports_vision(self) -> bool:
        return True

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        url = f"{self.API_URL}/{self.model}:generateContent?key={self.api_key}"

        # Build Gemini conversation format
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
            "tools": get_tools_gemini(),
        }

        total_input = 0
        total_output = 0
        used_tools = False

        async with httpx.AsyncClient(timeout=300.0) as client:
            for iteration in range(MAX_TOOL_ITERATIONS):
                try:
                    resp = await client.post(url, json=body)
                    data = resp.json()
                except Exception as e:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Gemini request failed: {e}",
                    )

                if resp.status_code != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Gemini API error: {error_msg}",
                    )

                # Track usage
                usage = data.get("usageMetadata", {})
                total_input += usage.get("promptTokenCount", 0)
                total_output += usage.get("candidatesTokenCount", 0)

                candidates = data.get("candidates", [])
                if not candidates:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error="Gemini returned no candidates",
                    )

                parts = candidates[0].get("content", {}).get("parts", [])

                # Check for function calls in the response parts
                function_calls = [p for p in parts if "functionCall" in p]
                text_parts = [p.get("text", "") for p in parts if "text" in p]

                if function_calls:
                    used_tools = True

                    # Append the model's response (with function calls) to contents
                    contents.append({
                        "role": "model",
                        "parts": parts,
                    })

                    # Execute each function call and build response parts
                    response_parts = []
                    for fc_part in function_calls:
                        fc = fc_part["functionCall"]
                        func_name = fc.get("name", "")
                        func_args = fc.get("args") or {}

                        logger.info(f"[gemini] Tool call: {func_name}({json.dumps(func_args)[:200]})")
                        result = await execute_tool(func_name, func_args)
                        logger.info(f"[gemini] Tool result: {result[:200]}")

                        # Truncate result for Gemini's request size limits
                        if len(result) > 30000:
                            result = result[:30000] + "\n\n[Truncated — full output was " + str(len(result)) + " chars]"

                        response_parts.append({
                            "functionResponse": {
                                "name": func_name,
                                "response": {"result": result},
                            }
                        })

                    # Append function responses as a user turn
                    contents.append({
                        "role": "user",
                        "parts": response_parts,
                    })

                    # Update body and loop
                    body["contents"] = contents
                    continue

                # No function calls — this is the final text response
                text = "".join(text_parts)
                return LLMResponse(
                    text=text, model=self.model, provider=self.provider_name,
                    input_tokens=total_input, output_tokens=total_output,
                    tool_use=used_tools,
                )

        # Hit iteration limit
        return LLMResponse(
            text="I reached the maximum number of tool-use steps. Please send a follow-up message to continue.",
            model=self.model, provider=self.provider_name,
            tool_use=True,
        )


class GrokProvider(LLMProvider):
    """xAI Grok API — OpenAI-compatible with tool-use support.

    Uses api.x.ai/v1 endpoint. $25 free credits on signup,
    plus $150/month free if you opt into data sharing.
    """

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "grok"

    @property
    def supports_vision(self) -> bool:
        # Grok 2+ models support vision, as do explicit vision variants
        return "vision" in self.model or "grok-2" in self.model or "grok-3" in self.model

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
        return await _openai_tool_loop(
            url=f"{self.BASE_URL}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
        )


class OllamaProvider(LLMProvider):
    """Ollama local models with tool-use support."""

    def __init__(self, model: str, api_key: str = "", base_url: str = "http://localhost:11434"):
        super().__init__(model, api_key)
        self.base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        # Ollama supports OpenAI-compatible /v1/chat/completions endpoint
        # which includes tool-use support
        headers = {"Content-Type": "application/json"}

        # OpenAI-compat endpoint uses max_tokens at root level, not options
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ],
        }

        # Check if Ollama is reachable before trying
        try:
            async with httpx.AsyncClient(timeout=10.0) as probe:
                probe_resp = await probe.get(f"{self.base_url}/api/tags")
                if probe_resp.status_code != 200:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error="Cannot connect to Ollama. Is it running? (ollama serve)"
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error="Cannot connect to Ollama. Is it running? (ollama serve)"
            )
        except Exception:
            pass  # Proceed anyway — the main request will fail with a better error

        # Try OpenAI-compatible endpoint (supports tool-use)
        result = await _openai_tool_loop(
            url=f"{self.base_url}/v1/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
            timeout=600.0,
        )

        # If the OpenAI-compat endpoint returned an error, fall back to native API
        if result.error and ("404" in result.error or "not found" in result.error.lower()):
            logger.info(f"Ollama OpenAI-compat endpoint unavailable, falling back to native API")
            url = f"{self.base_url}/api/chat"
            native_body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *[{"role": m.role, "content": m.content} for m in messages],
                ],
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            }
            try:
                async with httpx.AsyncClient(timeout=600.0) as client:
                    resp = await client.post(url, json=native_body)
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
            except Exception as e:
                return LLMResponse(
                    text="", model=self.model, provider=self.provider_name,
                    error=f"Ollama request failed: {e}"
                )

        return result


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
        "grok": lambda: GrokProvider(model, api_key),
        "xai": lambda: GrokProvider(model, api_key),
    }
    factory = providers.get(provider.lower())
    if not factory:
        supported = ", ".join(sorted(providers.keys()))
        raise ValueError(f"Unknown LLM provider: {provider}. Supported: {supported}")
    return factory()
