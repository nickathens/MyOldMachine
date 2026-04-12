#!/usr/bin/env python3
"""
LLM Provider Abstraction Layer for MyOldMachine.

PRIMARY: Claude Code CLI — runs as subprocess with full tool-use (bash, file
read/write, etc.). This is how the bot actually controls the machine.

API PROVIDERS: OpenAI, Google Gemini, Kimi, MiniMax, Ollama, OpenRouter — these use
httpx for API calls with function-calling / tool-use support. The LLM sends
structured tool calls, we execute them locally, and return results.
"""

import asyncio
import base64
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
    MAX_FALLBACK_ATTEMPTS,
    execute_tool,
    extract_tool_calls_from_text,
)

logger = logging.getLogger(__name__)


@dataclass
class Message:
    role: str  # "user" or "assistant"
    content: str
    images: list = None  # File paths for multimodal vision messages


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


# --- Multimodal image helpers ---

def _encode_image_base64(path: str) -> tuple[str, str]:
    """Encode an image file to base64 and determine MIME type."""
    path_lower = path.lower()
    if path_lower.endswith(".png"):
        mime = "image/png"
    elif path_lower.endswith(".gif"):
        mime = "image/gif"
    elif path_lower.endswith(".webp"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return data, mime


def _has_images(messages: list[Message]) -> bool:
    """Check if any message in the list has image attachments."""
    return any(m.images for m in messages)


def _build_openai_messages(system_prompt: str, messages: list[Message]) -> list[dict]:
    """Build OpenAI-format messages, embedding images as multimodal content."""
    result = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.images:
            content_parts = [{"type": "text", "text": m.content}]
            for img_path in m.images:
                try:
                    data, mime = _encode_image_base64(img_path)
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"}
                    })
                except Exception as e:
                    logger.warning(f"Failed to encode image {img_path}: {e}")
            result.append({"role": m.role, "content": content_parts})
        else:
            result.append({"role": m.role, "content": m.content})
    return result


def _build_gemini_contents(messages: list[Message]) -> list[dict]:
    """Build Gemini-format contents, embedding images as inline_data."""
    contents = []
    for m in messages:
        role = "user" if m.role == "user" else "model"
        parts = [{"text": m.content}]
        if m.images:
            for img_path in m.images:
                try:
                    data, mime = _encode_image_base64(img_path)
                    parts.append({"inline_data": {"mime_type": mime, "data": data}})
                except Exception as e:
                    logger.warning(f"Failed to encode image {img_path}: {e}")
        contents.append({"role": role, "parts": parts})
    return contents


def _build_claude_messages(messages: list[Message]) -> list[dict]:
    """Build Claude API-format messages, embedding images as base64 source."""
    result = []
    for m in messages:
        if m.images:
            content = [{"type": "text", "text": m.content}]
            for img_path in m.images:
                try:
                    data, mime = _encode_image_base64(img_path)
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": data}
                    })
                except Exception as e:
                    logger.warning(f"Failed to encode image {img_path}: {e}")
            result.append({"role": m.role, "content": content})
        else:
            result.append({"role": m.role, "content": m.content})
    return result


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
    NO_TEXT_TIMEOUT = 600  # 10 min of tool activity with zero user-facing text = stuck
    PROGRESS_INTERVAL = 300  # Send progress message every 5 min

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str = ""):
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
        start_time = asyncio.get_running_loop().time()
        last_activity = start_time
        last_text_output = start_time
        last_progress_message = start_time
        last_progress_save = start_time
        final_result = None
        partial_text = ""
        last_turn_text_blocks = []
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
            except (asyncio.LimitOverrunError, ValueError) as e:
                logger.warning(f"Oversized output line ({e}), draining buffer")
                try:
                    chunk = await asyncio.wait_for(
                        stream.read(len(stream._buffer) if stream._buffer else 1024),
                        timeout=5
                    )
                    logger.warning(f"Drained {len(chunk)} bytes from oversized line")
                except Exception as drain_err:
                    logger.warning(f"Buffer drain failed: {drain_err}")
                return b'\n'

        try:
            if chat:
                typing_task = asyncio.create_task(send_typing_periodically())

            # Claude CLI needs a mostly-complete environment but we still strip
            # dangerous env vars (other provider API keys, bot tokens, DB passwords, etc.)
            cli_env = {k: v for k, v in os.environ.items()
                       if k not in {"OPENAI_API_KEY", "OPENROUTER_API_KEY",
                                    "GOOGLE_API_KEY", "DEEPSEEK_API_KEY",
                                    "XAI_API_KEY", "GROK_API_KEY",
                                    "MOONSHOT_API_KEY",
                                    "LLM_API_KEY", "TELEGRAM_BOT_TOKEN",
                                    "TELEGRAM_TOKEN", "BOT_TOKEN",
                                    "DATABASE_URL", "DATABASE_PASSWORD",
                                    "REDIS_URL", "REDIS_PASSWORD"}}
            cli_env["HOME"] = str(Path.home())

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._bot_dir),
                env=cli_env,
                limit=50 * 1024 * 1024,  # 50MB buffer for large JSON lines
            )
            self._active_processes.add(process)

            # Write prompt to stdin
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

            # Read output line by line with activity-based timeout
            while True:
                current_time = asyncio.get_running_loop().time()
                time_since_activity = current_time - last_activity

                if time_since_activity > self.IDLE_TIMEOUT:
                    logger.warning(f"Claude idle timeout for user {user_id} after {self.IDLE_TIMEOUT}s. Last status: {current_status}, tool: {tool_in_progress}")
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
                    else:
                        timeout_msg += " The task may have been too complex. Try breaking it into smaller steps."
                    return LLMResponse(text=timeout_msg, model=self.model, provider=self.provider_name)

                # No-text timeout: Claude is running tools but hasn't produced
                # any user-facing text in NO_TEXT_TIMEOUT seconds.  This catches
                # the case where tool JSON events keep last_activity alive but
                # the user sees nothing.  Skips the first 120s to allow for
                # initial thinking/planning before any output.
                time_since_text = current_time - last_text_output
                time_since_start = current_time - start_time
                if time_since_text > self.NO_TEXT_TIMEOUT and tool_in_progress and time_since_start > 120:
                    logger.warning(
                        f"Claude no-text timeout for user {user_id}: {int(time_since_text)}s "
                        f"without user-facing text. Tool: {tool_in_progress}, status: {current_status}"
                    )
                    if self.on_progress_save and user_id:
                        self.on_progress_save(user_id, original_message, partial_text,
                                              f"no-text timeout after {int(time_since_text)}s", tool_in_progress)
                    process.kill()
                    await process.wait()
                    last_turn = "\n".join(last_turn_text_blocks).strip()
                    fallback = last_turn or partial_text.strip()
                    if fallback:
                        if self.on_progress_clear and user_id:
                            self.on_progress_clear(user_id)
                        return LLMResponse(
                            text=fallback + f"\n\n[Stopped: ran {tool_in_progress} for {int(time_since_text)}s with no response text.]",
                            model=self.model, provider=self.provider_name, tool_use=True,
                        )
                    return LLMResponse(
                        text=f"Claude was running {tool_in_progress} for {int(time_since_text // 60)} minutes "
                             f"without producing any response. The task may need to be broken into smaller steps.",
                        model=self.model, provider=self.provider_name,
                    )

                # Log warning when approaching idle timeout
                if time_since_activity > self.IDLE_TIMEOUT * 0.8 and time_since_activity <= self.IDLE_TIMEOUT * 0.8 + 30:
                    logger.warning(f"Claude approaching idle timeout for user {user_id}: {int(time_since_activity)}s idle. Status: {current_status}")

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
                    last_activity = asyncio.get_running_loop().time()
                    line_str = line.decode().strip()
                    if line_str:
                        try:
                            data = json.loads(line_str)
                            msg_type = data.get("type")

                            if msg_type == "assistant":
                                current_status = "generating response"
                                tool_in_progress = None
                                last_turn_text_blocks = []
                                msg_data = data.get("message", {})
                                for block in msg_data.get("content", []):
                                    if block.get("type") == "text":
                                        text = block.get("text", "")
                                        if text:
                                            last_turn_text_blocks.append(text)
                                            last_text_output = asyncio.get_running_loop().time()
                                            partial_text += text + "\n"
                                            # Cap at 100KB to prevent unbounded memory growth
                                            if len(partial_text) > 102400:
                                                partial_text = partial_text[-102400:]
                            elif msg_type == "tool_use":
                                tool_name = data.get("name", "tool")
                                tool_in_progress = tool_name
                                current_status = f"using {tool_name}"
                                logger.debug(f"Claude using tool: {tool_name}")
                            elif msg_type == "tool_result":
                                tool_in_progress = None
                                current_status = "processing result"

                            if msg_type == "result" and "result" in data:
                                final_result = data["result"]
                                last_text_output = asyncio.get_running_loop().time()

                            # Save progress periodically
                            current_time = asyncio.get_running_loop().time()
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
                # Detect OOM kill: kernel sends SIGKILL (-9) when memory limit
                # is hit, or stderr may mention it explicitly
                is_oom = (
                    process.returncode == -9
                    or "out of memory" in stderr_text.lower()
                    or "killed" in stderr_text.lower()
                )
                last_turn = "\n".join(last_turn_text_blocks).strip()
                fallback_text = last_turn or partial_text.strip()
                if fallback_text:
                    logger.info(f"Returning fallback text despite error for user {user_id} ({len(fallback_text)} chars, from_last_turn={bool(last_turn)})")
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    suffix = ""
                    if is_oom:
                        elapsed = int(asyncio.get_running_loop().time() - start_time)
                        suffix = (
                            f"\n\n[Hit memory limit after {elapsed // 60}m {elapsed % 60}s. "
                            f"Last action: {tool_in_progress or current_status}. "
                            f"Partial work above may be incomplete. "
                            f"Use /recover if needed.]"
                        )
                    return LLMResponse(
                        text=fallback_text + suffix, model=self.model,
                        provider=self.provider_name, tool_use=True,
                    )
                if self.on_progress_save and user_id:
                    self.on_progress_save(user_id, original_message, partial_text,
                                          f"error: {stderr_text[:100]}", tool_in_progress)
                if is_oom:
                    elapsed = int(asyncio.get_running_loop().time() - start_time)
                    oom_msg = (
                        f"Claude hit the memory limit and was killed after "
                        f"{elapsed // 60}m {elapsed % 60}s."
                    )
                    if tool_in_progress:
                        oom_msg += f" It was running: {tool_in_progress}."
                    if current_status and current_status != "thinking":
                        oom_msg += f" Status: {current_status}."
                    oom_msg += (
                        "\n\nThis usually happens when Claude reads too many large files "
                        "or accumulates too much tool output in a single session. "
                        "Try breaking the task into smaller steps, or use /clear to reset context."
                    )
                    if partial_text.strip():
                        oom_msg += "\n\nPartial progress was saved. Use /recover to see it."
                    return LLMResponse(
                        text=oom_msg, model=self.model, provider=self.provider_name,
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

            # No "result" message — use last assistant turn if available,
            # otherwise fall back to all accumulated partial text
            last_turn = "\n".join(last_turn_text_blocks).strip()
            fallback_text = last_turn or partial_text.strip()
            if fallback_text:
                logger.warning(f"No final result for user {user_id}, returning fallback text ({len(fallback_text)} chars, from_last_turn={bool(last_turn)})")
                return LLMResponse(
                    text=fallback_text, model=self.model,
                    provider=self.provider_name, tool_use=True,
                )

            if stderr_text:
                logger.warning(f"No response from Claude for user {user_id}. Stderr: {stderr_text[:500]}")
            else:
                logger.warning(f"No response from Claude for user {user_id}. Exit code: {process.returncode}. No stderr.")
            return LLMResponse(
                text="Claude produced no response. This can happen when the context is too large. Try /clear to reset, or send a shorter message.",
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
        # Use multimodal format if any message has images
        if _has_images(messages):
            api_messages = _build_claude_messages(messages)
        else:
            api_messages = [{"role": m.role, "content": m.content} for m in messages]
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": api_messages,
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(self.API_URL, headers=headers, json=body)
                try:
                    data = resp.json()
                except Exception:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Claude API returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
                    )
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

    Includes a fallback parser: if the model writes tool calls as text
    (code blocks, function-call syntax) instead of using structured
    tool_calls, we extract and execute them anyway. This handles weak
    models that don't reliably use function calling.
    """
    # Add tools to the request body
    body["tools"] = get_tools_openai()
    body["tool_choice"] = "auto"

    messages = body["messages"]
    total_input = 0
    total_output = 0
    used_tools = False
    fallback_attempts = 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except Exception as e:
                return LLMResponse(
                    text="", model=model, provider=provider_name,
                    error=f"{provider_name} request failed: {e}",
                )

            try:
                data = resp.json()
            except Exception:
                return LLMResponse(
                    text="", model=model, provider=provider_name,
                    error=f"{provider_name} returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}",
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

            # No structured tool calls — check if the model wrote them as text
            text = message.get("content", "") or ""

            if text and fallback_attempts < MAX_FALLBACK_ATTEMPTS:
                extracted = extract_tool_calls_from_text(text)
                if extracted:
                    fallback_attempts += 1
                    used_tools = True
                    logger.info(
                        f"[{provider_name}] Fallback: extracted {len(extracted)} tool call(s) "
                        f"from text response (attempt {fallback_attempts}/{MAX_FALLBACK_ATTEMPTS})"
                    )

                    # Add the model's text as an assistant message
                    messages.append({"role": "assistant", "content": text})

                    # Execute each extracted tool call and add results
                    results_text = []
                    for tc in extracted:
                        tc_name = tc["name"]
                        tc_args = tc["arguments"]
                        logger.info(f"[{provider_name}] Fallback exec: {tc_name}({json.dumps(tc_args)[:200]})")
                        result = await execute_tool(tc_name, tc_args)
                        logger.info(f"[{provider_name}] Fallback result: {result[:200]}")

                        if len(result) > 30000:
                            result = result[:30000] + "\n\n[Truncated — full output was " + str(len(result)) + " chars]"

                        results_text.append(f"[Tool result for {tc_name}]:\n{result}")

                    # Add results as a user message (since we can't use tool role
                    # without a matching tool_call_id from the model)
                    combined_results = "\n\n".join(results_text)
                    messages.append({
                        "role": "user",
                        "content": (
                            f"I executed the tool calls you wrote in your message. "
                            f"Here are the results:\n\n{combined_results}\n\n"
                            f"Now respond to the user based on these results. "
                            f"Do NOT write the commands again — they have already been executed. "
                            f"Just report the outcome naturally."
                        ),
                    })

                    body["messages"] = messages
                    continue

            # No tool calls (structured or text) — this is the final response
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
        # GPT-4o, GPT-4.1, GPT-5.x all support vision
        return any(prefix in self.model for prefix in ("gpt-4", "gpt-5")) or "vision" in self.model

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # GPT-5.x and o-series models require max_completion_tokens, not max_tokens.
        # o-series and GPT-5.x also reject the temperature parameter entirely.
        is_reasoning = (
            self.model.startswith("o1")
            or self.model.startswith("o3")
            or self.model.startswith("o4")
        )
        is_gpt5 = self.model.startswith("gpt-5")
        uses_completion_tokens = is_reasoning or is_gpt5
        rejects_temperature = is_reasoning or is_gpt5

        token_key = "max_completion_tokens" if uses_completion_tokens else "max_tokens"
        if _has_images(messages) and self.supports_vision:
            body_messages = _build_openai_messages(system_prompt, messages)
        else:
            body_messages = [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ]
        body = {
            "model": self.model,
            token_key: max_tokens,
            "messages": body_messages,
        }
        if not rejects_temperature:
            body["temperature"] = temperature
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
        # Most OpenRouter models support vision, but some text-only models don't.
        # Default to True — the API will error if the model can't handle images.
        m = self.model.lower()
        no_vision_keywords = ("nemotron", "hermes", "mixtral")
        return not any(kw in m for kw in no_vision_keywords)

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nickathens/MyOldMachine",
            "X-Title": "MyOldMachine",
        }
        if _has_images(messages) and self.supports_vision:
            body_messages = _build_openai_messages(system_prompt, messages)
        else:
            body_messages = [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ]
        body = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "messages": body_messages,
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
        url = f"{self.API_URL}/{self.model}:generateContent"

        # Build Gemini conversation format (with multimodal support)
        if _has_images(messages):
            contents = _build_gemini_contents(messages)
        else:
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
        fallback_attempts = 0

        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            for iteration in range(MAX_TOOL_ITERATIONS):
                try:
                    resp = await client.post(url, headers=headers, json=body)
                except Exception as e:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Gemini request failed: {e}",
                    )

                try:
                    data = resp.json()
                except Exception:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Gemini returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}",
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

                # No function calls — check if model wrote them as text
                text = "".join(text_parts)

                if text and fallback_attempts < MAX_FALLBACK_ATTEMPTS:
                    extracted = extract_tool_calls_from_text(text)
                    if extracted:
                        fallback_attempts += 1
                        used_tools = True
                        logger.info(
                            f"[gemini] Fallback: extracted {len(extracted)} tool call(s) "
                            f"from text response (attempt {fallback_attempts}/{MAX_FALLBACK_ATTEMPTS})"
                        )

                        # Add model's text response
                        contents.append({
                            "role": "model",
                            "parts": [{"text": text}],
                        })

                        # Execute and build results
                        results_text = []
                        for tc in extracted:
                            tc_name = tc["name"]
                            tc_args = tc["arguments"]
                            logger.info(f"[gemini] Fallback exec: {tc_name}({json.dumps(tc_args)[:200]})")
                            result = await execute_tool(tc_name, tc_args)
                            logger.info(f"[gemini] Fallback result: {result[:200]}")

                            if len(result) > 30000:
                                result = result[:30000] + "\n\n[Truncated — full output was " + str(len(result)) + " chars]"

                            results_text.append(f"[Tool result for {tc_name}]:\n{result}")

                        combined_results = "\n\n".join(results_text)
                        contents.append({
                            "role": "user",
                            "parts": [{"text": (
                                f"I executed the tool calls you wrote in your message. "
                                f"Here are the results:\n\n{combined_results}\n\n"
                                f"Now respond to the user based on these results. "
                                f"Do NOT write the commands again — they have already been executed. "
                                f"Just report the outcome naturally."
                            )}],
                        })

                        body["contents"] = contents
                        continue

                # No tool calls (structured or text) — final response
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


class DeepSeekProvider(LLMProvider):
    """DeepSeek API — OpenAI-compatible with tool-use support.

    Uses api.deepseek.com/v1 endpoint. DeepSeek V3.2.
    $0.28/$0.42 per MTok ($0.028 cached input — 90% discount).
    128K context. Both deepseek-chat and deepseek-reasoner support tool calls.
    No vision support in the API.
    """

    BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def supports_vision(self) -> bool:
        return False  # DeepSeek V3.2 API does not support vision

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # deepseek-reasoner ignores temperature (no error, just no effect)
        is_reasoner = "reasoner" in self.model
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ],
        }
        if not is_reasoner:
            body["temperature"] = temperature
        return await _openai_tool_loop(
            url=f"{self.BASE_URL}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
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
        # All current Grok models (4.1 Fast, 4.20) support vision.
        m = self.model
        return ("vision" in m or "grok-4-1-fast" in m or "grok-4-fast" in m
                or m.startswith("grok-4.20"))

    def _is_reasoning_model(self) -> bool:
        """Check if this is a Grok reasoning model (uses max_completion_tokens, no temperature)."""
        # Non-reasoning: grok-4-1-fast-non-reasoning, grok-4.20-*-non-reasoning.
        # Reasoning: grok-4-1-fast-reasoning, grok-4.20-*-reasoning,
        #   grok-4.20-multi-agent-*.
        m = self.model
        if "non-reasoning" in m:
            return False
        if "reasoning" in m:
            return True
        if m.startswith("grok-4") and "fast" not in m:
            return True
        if "mini" in m:
            return True
        return False

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        is_reasoning = self._is_reasoning_model()
        token_key = "max_completion_tokens" if is_reasoning else "max_tokens"
        if _has_images(messages) and self.supports_vision:
            body_messages = _build_openai_messages(system_prompt, messages)
        else:
            body_messages = [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ]
        body = {
            "model": self.model,
            token_key: max_tokens,
            "messages": body_messages,
        }
        if not is_reasoning:
            body["temperature"] = temperature
        return await _openai_tool_loop(
            url=f"{self.BASE_URL}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
        )


class KimiProvider(LLMProvider):
    """Moonshot Kimi API — OpenAI-compatible with tool-use support.

    Uses api.moonshot.ai/v1 endpoint (redirects to api.kimi.ai).
    Kimi K2.5 is multimodal (vision + tools), K2 Thinking for reasoning.
    256K context. $0.60/$3.00 per MTok (K2.5), $0.60/$2.50 (K2).
    Temperature clamped to [0, 1].
    Note: kimi-latest was discontinued January 28, 2026.
    """

    BASE_URL = "https://api.moonshot.ai/v1"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "kimi"

    @property
    def supports_vision(self) -> bool:
        # K2.5 is multimodal (vision), K2 variants are text-only
        return "k2.5" in self.model

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Moonshot clamps temperature to [0, 1]
        clamped_temp = max(0.0, min(1.0, temperature))
        if _has_images(messages) and self.supports_vision:
            body_messages = _build_openai_messages(system_prompt, messages)
        else:
            body_messages = [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ]
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": clamped_temp,
            "messages": body_messages,
        }
        return await _openai_tool_loop(
            url=f"{self.BASE_URL}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
        )


class MiniMaxProvider(LLMProvider):
    """MiniMax API — OpenAI-compatible with tool-use support.

    Uses api.minimax.io/v1 endpoint.
    M2.7: text-only, strong reasoning, 205K ctx, $0.30/$1.20 per MTok.
    M2.5: multimodal (vision + tools), 205K ctx.
    M2.7-highspeed: faster variant (~100 TPS).
    """

    BASE_URL = "https://api.minimax.io/v1"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "minimax"

    @property
    def supports_vision(self) -> bool:
        # M2.5 is multimodal (vision), M2.7 and others are text-only
        return "m2.5" in self.model.lower()

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if _has_images(messages) and self.supports_vision:
            body_messages = _build_openai_messages(system_prompt, messages)
        else:
            body_messages = [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ]
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": body_messages,
        }
        return await _openai_tool_loop(
            url=f"{self.BASE_URL}/chat/completions",
            headers=headers,
            body=body,
            model=self.model,
            provider_name=self.provider_name,
        )


class OllamaProvider(LLMProvider):
    """Ollama models (local or cloud) with tool-use support."""

    def __init__(self, model: str, api_key: str = "", base_url: str = "http://localhost:11434"):
        super().__init__(model, api_key)
        self.base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        if self.base_url.endswith("ollama.com"):
            return "ollama-cloud"
        return "ollama"

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        # Ollama supports OpenAI-compatible /v1/chat/completions endpoint
        # which includes tool-use support
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # OpenAI-compat endpoint uses max_tokens at root level, not options
        # Ollama vision models (llava, bakllava, etc.) support images via OpenAI format
        if _has_images(messages):
            body_messages = _build_openai_messages(system_prompt, messages)
        else:
            body_messages = [
                {"role": "system", "content": system_prompt},
                *[{"role": m.role, "content": m.content} for m in messages],
            ]
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": body_messages,
        }

        # Check if Ollama is reachable before trying
        is_cloud = self.base_url.endswith("ollama.com")
        try:
            probe_headers = {}
            if self.api_key:
                probe_headers["Authorization"] = f"Bearer {self.api_key}"
            async with httpx.AsyncClient(timeout=10.0) as probe:
                probe_resp = await probe.get(f"{self.base_url}/api/tags", headers=probe_headers)
                if probe_resp.status_code == 401:
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error="Ollama Cloud authentication failed. Check your API key (LLM_API_KEY in .env)."
                    )
                if probe_resp.status_code != 200:
                    error_hint = "Check your API key at ollama.com/settings/keys" if is_cloud else "Is it running? (ollama serve)"
                    return LLMResponse(
                        text="", model=self.model, provider=self.provider_name,
                        error=f"Cannot connect to Ollama. {error_hint}"
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            error_hint = "Check your internet connection." if is_cloud else "Is it running? (ollama serve)"
            return LLMResponse(
                text="", model=self.model, provider=self.provider_name,
                error=f"Cannot connect to Ollama. {error_hint}"
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
                native_headers = {"Content-Type": "application/json"}
                if self.api_key:
                    native_headers["Authorization"] = f"Bearer {self.api_key}"
                async with httpx.AsyncClient(timeout=600.0) as client:
                    resp = await client.post(url, headers=native_headers, json=native_body)
                    try:
                        data = resp.json()
                    except Exception:
                        return LLMResponse(
                            text="", model=self.model, provider=self.provider_name,
                            error=f"Ollama returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}",
                        )
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
        "ollama-cloud": lambda: OllamaProvider(
            model, api_key, "https://ollama.com"
        ),
        "openrouter": lambda: OpenRouterProvider(model, api_key),
        "deepseek": lambda: DeepSeekProvider(model, api_key),
        "grok": lambda: GrokProvider(model, api_key),
        "xai": lambda: GrokProvider(model, api_key),
        "kimi": lambda: KimiProvider(model, api_key),
        "moonshot": lambda: KimiProvider(model, api_key),
        "minimax": lambda: MiniMaxProvider(model, api_key),
    }
    factory = providers.get(provider.lower())
    if not factory:
        supported = ", ".join(sorted(providers.keys()))
        raise ValueError(f"Unknown LLM provider: {provider}. Supported: {supported}")
    return factory()
