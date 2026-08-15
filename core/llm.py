#!/usr/bin/env python3
"""
LLM Provider Abstraction Layer for MyOldMachine.

PRIMARY: Claude Code CLI — runs as subprocess with full tool-use (bash, file
read/write, etc.). This is how the bot actually controls the machine.

PARALLEL: OpenAI Codex CLI — same pattern as Claude Code, runs `codex exec
--json` as subprocess and parses the JSON Lines event stream. Authenticates
via `codex login` (ChatGPT Plus/Pro/Business plan) or OPENAI_API_KEY.

API PROVIDERS: OpenAI, Google Gemini, Kimi, MiniMax, Ollama, OpenRouter — these use
httpx for API calls with function-calling / tool-use support. The LLM sends
structured tool calls, we execute them locally, and return results.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import signal
import time
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
from core.conversation_format import wrap_turn
from core.prompt_security import wrap_tool_result
# The subscription token reader lives in core.credentials, next to the other
# keychain access and reachable from the plain call sites (nightly reflection,
# background compaction) that must not import this module's HTTP stack just to
# authenticate a subprocess. Re-exported here because this module's own turns
# are its heaviest caller.
from core.credentials import read_claude_oauth_token

logger = logging.getLogger(__name__)


_CLI_FALLBACK_DIRS = (
    "~/.local/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
)


def _find_cli_binary(name: str) -> str:
    """Locate a CLI binary, falling back through known install locations.

    Why: when the bot runs under launchd or systemd, the inherited PATH may
    miss user-local install dirs (notably ~/.local/bin used by Anthropic's
    `claude install` native installer). shutil.which() returns None and the
    health check then claims the binary is uninstalled even though it exists.

    Order: PATH → ~/.local/bin → /opt/homebrew/bin → /usr/local/bin →
    ~/.npm-global/bin → ~/.bun/bin → ~/.nvm/versions/node/*/bin. Returns
    absolute path on hit, or the literal name as a last-resort fallback so
    later code paths can still surface a clear error.
    """
    found = shutil.which(name)
    if found:
        return found
    for raw in _CLI_FALLBACK_DIRS:
        candidate = Path(raw).expanduser() / name
        if candidate.exists() and not candidate.is_dir():
            return str(candidate)
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        try:
            for version_dir in sorted(nvm_root.iterdir(), reverse=True):
                candidate = version_dir / "bin" / name
                if candidate.exists() and not candidate.is_dir():
                    return str(candidate)
        except OSError:
            pass
    return name


def _user_identity_env(user_id: Optional[int]) -> dict:
    """Build per-user identity env vars for skills running inside the CLI.

    Skills that touch user-private state (Gmail/Calendar tokens, mempalace,
    etc.) read JARVIS_USER_DIR to scope to that user's dir. Critical in the
    soft multi-user model (single OS user, multiple Telegram users) where
    OS-level isolation is not in play.
    """
    if user_id is None:
        return {}
    try:
        from core.users import resolve_user_dir
        user_dir = resolve_user_dir(user_id)
    except Exception:
        return {}
    return {
        "JARVIS_USER_ID": str(user_id),
        "JARVIS_USER_DIR": str(user_dir),
    }


def _claude_workspace_env(user_id: Optional[int]) -> dict:
    """Identity env plus a per-user CLAUDE_CONFIG_DIR for Claude CLI turns.

    The Claude CLI keys its auto-memory pool and session transcripts by
    config dir. Sharing one config dir across Telegram users means one
    shared memory pool, and private context from one user's chat bleeds
    into another's. Each user therefore gets data/users/<id>/claude.
    Fails open: if the workspace cannot be prepared, the turn still runs
    on the process-default config dir and the problem is logged, because
    a broken workspace must not take the conversation down with it.
    """
    env = _user_identity_env(user_id)
    if user_id is None:
        return env
    try:
        from core.claude_workspace import ensure_user_claude_config
        env["CLAUDE_CONFIG_DIR"] = str(ensure_user_claude_config(user_id))
    except Exception as exc:
        logger.warning(
            "per-user claude workspace unavailable for %s: %s", user_id, exc
        )
    return env


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


def _error_excerpt(text: Optional[str], limit: int = 600) -> str:
    """Return the TAIL of an error string (its last ``limit`` chars).

    The subprocess CLIs (Claude, Codex) spew deprecation/startup warnings to
    stderr that fill the HEAD of the buffer, so head-truncation (``text[:limit]``)
    hides the actual error -- which lands at the END. Keeping the tail surfaces
    it. Short strings pass through unchanged; a truncated tail is marked with a
    leading ellipsis.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


# Why a subprocess turn that produced no final result ended. A signal reaches us
# in two spellings depending on who reaped the child: asyncio reports it as a
# NEGATIVE returncode, a wrapper script reports it as 128+N. Both mean one thing.
_INTERRUPT_REASONS = {
    int(signal.SIGTERM): "the bot was stopped or restarted underneath it",
    int(signal.SIGKILL): "the process was killed outright",
    int(signal.SIGINT): "the process was interrupted",
    int(signal.SIGHUP): "the process lost the session it was running in",
}


def _interrupted_notice(returncode) -> str:
    """Trailer marking salvaged text as working notes rather than an answer.

    When a CLI turn dies before its final result, all that survives is the
    narration written along the way ("Reading the file...", "Tests green.
    Committing:"). The /stop, time-limit and OOM salvage paths already say why
    they stopped; the rest returned that narration bare, so it arrived looking
    exactly like a deliberate reply. On the Linux production bot this shipped
    twice in twenty minutes on 2026-08-15, when the service was restarted under
    a live turn and the child died with exit 143 (128+SIGTERM). The user's
    report was "your reply is strange. why?", which is the whole problem: a
    crash that does not announce itself gets read as the assistant misbehaving.
    """
    sig = None
    if isinstance(returncode, int):
        if returncode < 0:
            sig = -returncode
        elif returncode > 128:
            sig = returncode - 128
    reason = _INTERRUPT_REASONS.get(sig)
    if reason is None:
        reason = (
            "the process ended before finishing"
            if not returncode
            else f"the process failed (exit {returncode})"
        )
    return (
        "\n\n[Unfinished: the text above is my working notes, not an answer - "
        f"{reason}. Anything already written to disk is still there. "
        "Send the message again to pick it up.]"
    )


def _is_oom_exit(returncode, stderr_text: Optional[str]) -> bool:
    """True if the child was SIGKILLed, which on a memory-capped service means
    the limit was hit. Same two spellings as any signal: -9 when asyncio reaps
    the child, 137 when a wrapper reports 128+9.
    """
    lowered = (stderr_text or "").lower()
    return (
        returncode in (-int(signal.SIGKILL), 128 + int(signal.SIGKILL))
        or "out of memory" in lowered
        or "killed" in lowered
    )


class LLMProvider(ABC):
    """Base class for LLM providers."""

    # Result of the most recent health_check, set by the bot at startup and
    # after a /provider, /model, or /apikey switch. None means "not yet
    # checked". Tuple form: (healthy: bool, reason: str).
    last_health: Optional[tuple] = None

    def __init__(self, model: str, api_key: str = ""):
        self.model = model
        self.api_key = api_key
        self.last_health = None

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

    @property
    def supports_mcp(self) -> bool:
        """Whether a turn here can actually call the configured MCP servers.

        True by default: the API providers execute through `core.tools`,
        which merges MCP tools into the tool list, and the Claude CLI
        providers are handed --mcp-config (core.mcp_client.cli_config_args).
        A provider with no route overrides this to False so the system
        prompt stops offering tools the turn cannot reach.
        """
        return True

    @property
    def has_active_processes(self) -> bool:
        """Whether this provider has subprocesses currently running.

        Only meaningful for CLI providers that spawn subprocesses; API
        providers always return False.
        """
        return False

    async def graceful_shutdown(self):
        """Wait for any active processes to finish, then force-kill.

        Default no-op for providers that don't manage subprocesses.
        """
        return

    async def health_check(self) -> tuple[bool, str]:
        """Probe whether this provider can serve a request right now.

        Returns ``(healthy, reason)``. ``reason`` is a short human-readable
        explanation; on success it is the version string or "ok", on
        failure it is the actionable error.

        Subclasses override:
        - CLI providers run ``<binary> --version`` and look for dyld /
          GLIBC failure markers in stderr.
        - API providers GET the provider's public ``/models`` listing
          endpoint with a short timeout and treat 401/403 as auth failure.
        - Local Ollama probes ``/api/tags``.

        The default implementation always reports healthy so unknown
        providers are not blocked.
        """
        return True, "no health-check implemented"


# --- Helpers shared by health_check implementations -----------------------

# Fragments commonly seen in CLI binary load failures on old macOS / Linux.
# Used by ClaudeCLIProvider.health_check and CodexCLIProvider.health_check.
_CLI_LOAD_FAILURE_MARKERS = (
    "symbol not found",
    "dyld:",
    "dyld[",
    "glibc_",
    "version `glibc",
    "not a dynamic executable",
    "incompatible architecture",
    "cannot execute binary file",
)


def _looks_like_binary_load_failure(text: str) -> bool:
    """Return True if `text` matches a known dyld / glibc loader error."""
    lowered = text.lower()
    return any(marker in lowered for marker in _CLI_LOAD_FAILURE_MARKERS)


# Pre-tool text blocks shorter than this are working narration ("Reading the
# file...", "Tests green. Committing:") and are dropped from the user-facing
# reply. Blocks at or above it are treated as deliverables (a scope document,
# a report section) that must not be lost just because a tool call followed.
_DELIVERABLE_MIN_CHARS = 500


# Substrings that mark a failed turn as a CREDENTIAL problem rather than a
# transient API or tool failure. Matched against the CLI's own result text,
# which is prose and version-dependent, so this is deliberately broad: a
# false positive costs a slightly wrong explanation, a false negative sends
# the user chasing a problem only the operator can fix.
_AUTH_ERROR_MARKERS = (
    "authenticate",
    "authentication",
    "unauthorized",
    "oauth",
    "invalid api key",
    "invalid x-api-key",
    "credentials",
    "not logged in",
    "please run /login",
)


def _is_auth_failure(result_text: str, api_error_status) -> bool:
    """True if a failed turn was rejected for credentials, not content."""
    if api_error_status in (401, "401", 403, "403"):
        return True
    lowered = (result_text or "").lower()
    return any(marker in lowered for marker in _AUTH_ERROR_MARKERS)


def _stopped_response(fallback: str, model: str, provider: str) -> LLMResponse:
    """The reply for a turn the user ended with /stop.

    Shared by the in-loop stop check and the post-loop one so a stop reads the
    same to the user whichever door the read loop left by.
    """
    return LLMResponse(
        text=f"{fallback}\n\n[Stopped by /stop command]" if fallback else "Task stopped.",
        model=model, provider=provider, tool_use=True,
    )


def _compose_full_reply(final_result: str, text_blocks: list[str]) -> str:
    """Compose the user-facing reply for a successful turn.

    stream-json's "result" field (``final_result``) only contains the LAST
    assistant turn, i.e. the text emitted after the final tool call, so
    substantive content written before a tool call would be silently dropped
    if we returned it alone (the original truncation bug). But returning every
    accumulated block drowns the answer in play-by-play narration (the
    overcorrection). Middle ground: return the clean result, prepending only
    pre-tool blocks long enough to be deliverables rather than narration,
    skipping anything already contained in the result.

    The returned text always ends with ``final_result``, preserving the prior
    invariant that the real answer is never lost or replaced.
    """
    kept = []
    for block in text_blocks:
        block = block.strip()
        if len(block) >= _DELIVERABLE_MIN_CHARS and block not in final_result:
            kept.append(block)
    if not kept:
        return final_result
    return "\n\n".join(kept + [final_result])


def _extract_tool_activity(data: dict) -> tuple[list[str], bool]:
    """Tool activity carried by one claude stream-json event.

    Returns (started, finished): the names of tool_use content blocks when the
    event is an "assistant" message, and whether a "user" message carries a
    tool_result block. The claude CLI never emits top-level tool_use or
    tool_result event types; tools ride inside assistant/user messages as
    content blocks (verified live against claude CLI v2.1.169).
    """
    msg = data.get("message")
    blocks = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(blocks, list):
        return [], False
    msg_type = data.get("type")
    if msg_type == "assistant":
        names = [
            block.get("name") or "tool"
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        return names, False
    if msg_type == "user":
        finished = any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in blocks
        )
        return [], finished
    return [], False


# --- Helpers shared by CLI subprocess providers ---------------------------

async def _send_typing_periodically(chat):
    """Send Telegram 'typing' indicator every 3s while the CLI subprocess runs.

    Cancellation via task.cancel() exits cleanly; transient errors (network
    blips, chat closed) sleep and retry rather than crashing the parent loop.
    Used by ClaudeCLIProvider and CodexCLIProvider.
    """
    while True:
        try:
            if chat:
                await chat.send_action("typing")
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(3)


def _format_elapsed(seconds: float) -> str:
    """Human-friendly elapsed string: '25s' under a minute, else 'N min'.

    Progress updates currently fire on a flat 10-minute cadence, so in practice
    this always renders minutes; the sub-minute branch is kept for safety and for
    any caller reporting a shorter elapsed. Used by both CLI providers' reports.
    """
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60} min"


def _next_progress_delay(provider, count: int) -> float:
    """Seconds to wait before the next progress update.

    Walks PROGRESS_SCHEDULE for any front-loaded updates, then holds at
    PROGRESS_INTERVAL. PROGRESS_SCHEDULE is currently empty, so every update —
    including the first (count=0) — waits the full PROGRESS_INTERVAL.
    """
    schedule = provider.PROGRESS_SCHEDULE
    if count < len(schedule):
        return schedule[count]
    return provider.PROGRESS_INTERVAL


async def _read_line_with_timeout(stream, timeout: float):
    """Read one line from an asyncio stream, with a per-line timeout.

    Returns:
      - bytes (newline-terminated) on success
      - None on timeout (caller decides whether to keep waiting)
      - b'\\n' after draining an oversized line so the outer parser can
        skip the malformed record without hanging the whole turn

    Used by both CLI providers.
    """
    try:
        return await asyncio.wait_for(stream.readline(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    except (asyncio.LimitOverrunError, ValueError) as e:
        logger.warning(f"Oversized output line ({e}), draining buffer")
        # Use a fixed drain chunk size with a hard timeout instead of
        # reaching into the private stream._buffer attribute. The 1 MiB
        # chunk is large enough to flush typical overflow runs without
        # blocking indefinitely if the producer has paused.
        try:
            chunk = await asyncio.wait_for(
                stream.read(1024 * 1024),
                timeout=5,
            )
            logger.warning(f"Drained {len(chunk)} bytes from oversized line")
        except Exception as drain_err:
            logger.warning(f"Buffer drain failed: {drain_err}")
        return b'\n'


async def _http_get_health(
    url: str,
    headers: Optional[dict],
    provider_label: str,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """Shared GET health probe used by API providers.

    - 200/204 -> (True, summary)
    - 401/403 -> (False, "<provider>: invalid API key")
    - 404 -> (True, "<provider>: /models endpoint not standard, "
             "could not verify; first request will reveal real errors")
    - other 4xx/5xx -> (False, "<provider>: HTTP <code> <reason>")
    - timeout / connect error -> (False, "<provider>: <reason>")
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers or {})
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        return False, f"{provider_label}: cannot reach API ({exc.__class__.__name__})"
    except httpx.ReadTimeout:
        return False, f"{provider_label}: API read timed out after {timeout:.0f}s"
    except httpx.HTTPError as exc:
        return False, f"{provider_label}: HTTP error ({exc.__class__.__name__})"
    except Exception as exc:  # last-resort guard
        return False, f"{provider_label}: probe raised {exc.__class__.__name__}: {exc}"

    if resp.status_code in (200, 204):
        return True, f"{provider_label}: ok"
    if resp.status_code in (401, 403):
        return False, f"{provider_label}: invalid API key (HTTP {resp.status_code})"
    if resp.status_code == 404:
        return True, (
            f"{provider_label}: /models endpoint returned 404 "
            f"(non-standard for this provider, cannot pre-verify)"
        )
    body = (resp.text or "").strip().splitlines()[0] if resp.text else ""
    snippet = body[:200]
    return False, f"{provider_label}: HTTP {resp.status_code} {snippet}".rstrip()


# Anthropic models that still accept a non-default ``temperature``. Sonnet 5,
# Fable/Mythos 5, and Opus 4.7+ return HTTP 400 if sampling params are set,
# while omitting the field is accepted by every model — so temperature is sent
# only to legacy models known to take it.
_CLAUDE_SAMPLING_OK = ("sonnet-4-", "opus-4-1", "opus-4-5", "opus-4-6", "haiku")


def _claude_accepts_temperature(model: str) -> bool:
    m = model.lower()
    return any(tag in m for tag in _CLAUDE_SAMPLING_OK)


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


# Tools the CLI must never be able to call inside a bot turn.
#
# This is a DENY list and the CLI runs with --dangerously-skip-permissions, so
# it is the only fence: anything the CLI ships tomorrow is allowed by default.
# The CLI does not validate these names either -- a bogus entry is accepted in
# silence (checked against 2.1.217) -- so a typo here reads as protection and
# gives none. tests/test_disallowed_cli_tools.py pins the set for that reason.
#
# The load-bearing entries are the agent spawners below. Everything else is
# here because it wants an interactive terminal the bot does not have.
#
# Task has been denied since 2026-03-06 and the deny worked. Workflow is a
# SECOND spawner that shipped upstream afterwards and was never added, so it
# stood open: on 2026-08-04 three Workflow runs put 111 unapproved subagents
# and ~1.17M output tokens through one afternoon, and the last of them was
# destroyed by the very turn that reported it -- background runs do not outlive
# the turn that started them. Fan-out is not a capability this bot can offer
# honestly: the user cannot watch it, cannot price it, and /stop cannot reach
# past it. Any future upstream tool that spawns agents belongs in this tuple.
AGENT_SPAWNING_CLI_TOOLS = ("Task", "Workflow")

DISALLOWED_CLI_TOOLS = (
    *AGENT_SPAWNING_CLI_TOOLS,
    "EnterPlanMode",
    "AskUserQuestion",
    "Monitor",
    "EnterWorktree",
    "ExitWorktree",
    "ScheduleWakeup",
    "RemoteTrigger",
    "PushNotification",
    "NotebookEdit",
)


class ClaudeCLIProvider(LLMProvider):
    """
    Claude Code CLI provider — the primary provider for MyOldMachine.

    Runs `claude` CLI as a subprocess with --dangerously-skip-permissions,
    giving the LLM full tool-use capability: bash commands, file read/write,
    web fetch, etc. This is how the bot controls the machine.

    Output is parsed from stream-json format to track progress and extract
    the final result.
    """

    IDLE_TIMEOUT = 1800  # 30 min of no output = stuck
    ABSOLUTE_TIMEOUT = 86400  # 24 hour hard ceiling per request, even with continuous activity
    # Progress cadence. Between updates the user sees only a "typing" indicator.
    # A flat 10-minute gap is intentional: the user finds early/frequent heartbeats
    # more annoying than silence, so there is no front-loaded burst. PROGRESS_SCHEDULE
    # is the (now empty) front-load hook; empty means the first update also waits the
    # full PROGRESS_INTERVAL, so a short turn finishes before any heartbeat and stays
    # silent. Values are seconds since the previous update.
    PROGRESS_SCHEDULE = ()
    PROGRESS_INTERVAL = 600  # flat 10-minute cadence, first update included

    def __init__(self, model: str = "claude-sonnet-5", api_key: str = ""):
        super().__init__(model, api_key)
        self._bot_dir = Path(__file__).parent.parent
        # Resolve to an absolute path so sudoers can match it literally in
        # multi-user mode AND so the bot finds the binary when launchd/systemd
        # inherits a PATH that excludes ~/.local/bin (Anthropic's native
        # installer puts claude there).
        self._cli_binary = _find_cli_binary("claude")
        self._active_processes: set = set()
        # Maps user_id -> active subprocess so /stop can target a specific user's task.
        self._user_processes: dict = {}
        # Users who have issued /stop on an in-flight turn.
        self._stop_requested: set = set()
        # Callbacks set by bot.py
        self.on_progress_save = None  # (user_id, message, partial, status, tool) -> None
        self.on_progress_clear = None  # (user_id) -> None

    def _get_cli_env(self, user_id: int = None) -> dict:
        """Build the sanitized env dict for a CLI subprocess."""
        from core.tools import build_cli_env
        extra = _claude_workspace_env(user_id)
        # A stored long-lived setup-token outranks the /login keychain+file
        # store, so the CLI stops depending on the rotating per-store
        # credentials behind the daily re-login outage. Injected via `extra`,
        # which bypasses the `.*_TOKEN$` env scrub in core/tools. Skipped when an
        # explicit CLAUDE_CODE_OAUTH_TOKEN is already in the environment, so an
        # operator override still wins.
        if "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ:
            oauth_token = read_claude_oauth_token()
            if oauth_token:
                extra["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        return build_cli_env(
            provider_keys=frozenset({
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_CONFIG_DIR",
                "CLAUDE_CODE_OAUTH_TOKEN",
            }),
            extra=extra,
        )

    def stop_user(self, user_id: int) -> bool:
        """Signal the active Claude process for user_id to stop.

        Returns True if a process was killed, False if there was no active task.
        """
        proc = self._user_processes.get(user_id)
        if proc is None:
            return False
        self._stop_requested.add(user_id)
        try:
            if proc.returncode is None:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return True

    @property
    def provider_name(self) -> str:
        return "claude-cli"

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return True

    async def health_check(self) -> tuple[bool, str]:
        """Run `claude --version` and detect dyld / GLIBC load failures.

        Catches the macOS 10.15 'Symbol not found: _ubrk_clone' / Linux
        'GLIBC_X.Y not found' family before any user message can trigger it.
        """
        binary = self._cli_binary
        if not binary or (not Path(binary).exists() and not shutil.which(binary)):
            # Re-probe in case the binary was installed after provider init
            # (e.g. user ran `claude install` while the bot was running).
            rediscovered = _find_cli_binary("claude")
            if rediscovered != "claude" and Path(rediscovered).exists():
                self._cli_binary = rediscovered
                binary = rediscovered
            else:
                return False, (
                    "Claude CLI binary not found. Install with one of: "
                    "`claude install` (Anthropic native installer, recommended), "
                    "`npm i -g @anthropic-ai/claude-code`, or switch provider via /provider."
                )
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return False, f"Claude CLI cannot be executed: {exc}"
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return False, "Claude CLI --version timed out (10s); binary may be hanging."
        text = ((stdout or b"") + (stderr or b"")).decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            first_line = text.splitlines()[0] if text else "ok"
            # --version passes with zero valid credentials, so a dead login
            # read as healthy right up until a user's message failed (the
            # 2026-07-20 outage). Only a real turn exercises auth. Startup
            # plus every 4h, so this costs a few trivial turns a day.
            #
            # Scope limit worth knowing: this probes the provider's default
            # env, so it catches a broadly dead credential but NOT a single
            # per-user workspace whose credential has detached. Guarding
            # that is reconcile_shared_credentials' job, and a failure there
            # now surfaces as a loud turn failure rather than silence.
            auth_ok, auth_detail = await self._auth_probe()
            if not auth_ok:
                return False, auth_detail
            return True, f"{first_line} (auth ok)"
        if _looks_like_binary_load_failure(text):
            return False, (
                f"Claude CLI binary fails to load on this OS: "
                f"{text[:300]}. "
                f"This usually means macOS < 13 or glibc < 2.31. "
                f"Switch provider via /provider (claude-api, openrouter, "
                f"ollama, gemini, etc.)."
            )
        return False, (
            f"Claude CLI --version exited {proc.returncode}: "
            f"{text[:300] if text else '(no output)'}"
        )

    async def _auth_probe(self) -> tuple[bool, str]:
        """Smallest real turn that proves the CLI can authenticate.

        Tools are disabled so the probe cannot touch the machine, and the
        prompt is one token. Failure is read from the result event's
        is_error, because the CLI reports auth rejection there with
        subtype still "success" (verified live 2026-07-20).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_binary, "-p", "ok",
                "--output-format", "json",
                "--disallowedTools", "*",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._get_cli_env(None),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return False, f"Claude CLI auth probe could not start: {exc}"
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return False, "Claude CLI auth probe timed out (90s)."

        # This probe is a real turn on the DEFAULT config dir, so it refreshes
        # like any other -- and that refresh rotates the shared login into the
        # macOS keychain, stranding the file every per-user turn reads. Fold it
        # back now rather than leaving the machine diverged until whenever the
        # next user happens to write (2026-07-22: 05:00 probe, first user turn
        # dead at 12:43). Runs on failure too: a probe that could not refresh is
        # exactly the case where the other store holds the login that works.
        try:
            from core.claude_workspace import heal_credential_chains
            healed = heal_credential_chains()
            if healed:
                logger.warning("auth probe credential repair: %s", healed)
        except Exception:  # never fail a health check over the repair
            logger.exception("credential chain healing failed after auth probe")

        raw = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("is_error"):
            detail = _error_excerpt(data.get("result") or err, 200)
            if _is_auth_failure(data.get("result"), data.get("api_error_status")):
                return False, (
                    f"Claude CLI is not authenticated: {detail}. "
                    f"Re-run `claude` interactively on this machine to log in."
                )
            return False, f"Claude CLI probe turn failed: {detail}"
        if proc.returncode != 0:
            return False, (
                f"Claude CLI auth probe exited {proc.returncode}: "
                f"{_error_excerpt(err or raw, 200) or '(no output)'}"
            )
        return True, "authenticated"

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
            prompt += wrap_turn(msg.role, msg.content) + "\n"
        prompt += "\nContinue the conversation naturally, responding to the latest message."

        from core.config import get_llm_effort
        # Configured MCP servers, or nothing. This provider runs its own agent
        # loop and never reaches core.tools, so a server in mcp_servers.json is
        # unreachable from here unless it is passed on the command line.
        try:
            from core.mcp_client import cli_config_args
            mcp_args = cli_config_args()
        except Exception:
            logger.exception("MCP config failed; running this turn without MCP")
            mcp_args = []
        cmd = [
            self._cli_binary,
            "-p",
            "--model", self.model,
            "--effort", get_llm_effort(),
            "--dangerously-skip-permissions",
            # One comma-joined argument, never one argv entry per tool: the
            # flag is variadic (<tools...>), so loose names would run on and
            # swallow whatever followed them.
            "--disallowedTools", ",".join(DISALLOWED_CLI_TOOLS),
            "--output-format", "stream-json",
            "--verbose",
            *mcp_args,
            "-",  # Read from stdin
        ]

        typing_task = None
        process = None
        start_time = asyncio.get_running_loop().time()
        last_activity = start_time
        last_progress_message = start_time
        last_progress_content = ""
        last_progress_save = start_time
        progress_count = 0
        final_result = None
        result_is_error = False
        api_error_status = None
        partial_text = ""
        all_text_blocks = []  # Every text block in order, for reply composition
        last_turn_text_blocks = []
        current_status = "thinking"
        tool_in_progress = None

        try:
            if chat:
                typing_task = asyncio.create_task(_send_typing_periodically(chat))

            cli_env = self._get_cli_env(user_id)

            cwd = str(self._bot_dir)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=cli_env,
                limit=50 * 1024 * 1024,  # 50MB buffer for large JSON lines
            )
            self._active_processes.add(process)
            if user_id is not None:
                # Drop any stale stop flag from a previous turn before
                # registering this process, so the incoming process isn't
                # killed by a leftover request.
                self._stop_requested.discard(user_id)
                self._user_processes[user_id] = process

            # Write prompt to stdin
            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

            # Read output line by line with activity-based timeout
            while True:
                current_time = asyncio.get_running_loop().time()
                time_since_activity = current_time - last_activity
                elapsed = current_time - start_time

                # Honor /stop requests from the user
                if user_id is not None and user_id in self._stop_requested:
                    logger.info(f"Claude stop requested for user {user_id} after {int(elapsed)}s")
                    if process.returncode is None:
                        try:
                            process.kill()
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    await process.wait()
                    last_turn = "\n".join(last_turn_text_blocks).strip()
                    fallback = final_result or last_turn or partial_text.strip()
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    return _stopped_response(fallback, self.model, self.provider_name)

                # Absolute ceiling: kill the turn even if Claude is producing
                # activity continuously. Prevents runaway multi-hour sessions.
                if elapsed > self.ABSOLUTE_TIMEOUT:
                    logger.warning(
                        f"Claude absolute timeout for user {user_id} after {int(elapsed)}s. "
                        f"Status: {current_status}, tool: {tool_in_progress}"
                    )
                    if self.on_progress_save and user_id:
                        self.on_progress_save(user_id, original_message, partial_text,
                                              f"absolute timeout after {int(elapsed)}s", tool_in_progress)
                    if process.returncode is None:
                        try:
                            process.kill()
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    await process.wait()
                    last_turn = "\n".join(last_turn_text_blocks).strip()
                    fallback = final_result or last_turn or partial_text.strip()
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    if fallback:
                        return LLMResponse(
                            text=fallback + f"\n\n[Hit {self.ABSOLUTE_TIMEOUT // 3600}-hour time limit. Break into smaller steps.]",
                            model=self.model, provider=self.provider_name, tool_use=True,
                        )
                    return LLMResponse(
                        text=f"Claude hit the {self.ABSOLUTE_TIMEOUT // 3600}-hour time limit. Try breaking the task into smaller steps.",
                        model=self.model, provider=self.provider_name,
                    )

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
                            text=_compose_full_reply(final_result, all_text_blocks)
                            + f"\n\n[Task incomplete - Claude stopped responding after {self.IDLE_TIMEOUT // 60} minutes]",
                            model=self.model, provider=self.provider_name, tool_use=True,
                        )
                    timeout_msg = f"Claude stopped responding after {self.IDLE_TIMEOUT // 60} minutes of inactivity."
                    if tool_in_progress:
                        timeout_msg += f" Was running: {tool_in_progress}"
                    if partial_text:
                        timeout_msg += "\n\nPartial progress was saved. Use /recover to see it."
                    else:
                        timeout_msg += " The task may have been too complex. Try breaking it into smaller steps."
                    return LLMResponse(text=timeout_msg, model=self.model, provider=self.provider_name)

                # Deliberately no separate "no text while a tool runs" kill
                # here. A single long tool call (stem separation, ffmpeg,
                # renders) legitimately produces no text and no stream events
                # for many minutes, especially on old hardware, so any short
                # no-text threshold would kill real work. IDLE_TIMEOUT and
                # ABSOLUTE_TIMEOUT above are the safety nets, and the progress
                # report below names the running tool so a stuck one is
                # visible to the user.

                # Log warning when approaching idle timeout
                if time_since_activity > self.IDLE_TIMEOUT * 0.8 and time_since_activity <= self.IDLE_TIMEOUT * 0.8 + 30:
                    logger.warning(f"Claude approaching idle timeout for user {user_id}: {int(time_since_activity)}s idle. Status: {current_status}")

                # Send progress updates on a flat cadence (PROGRESS_INTERVAL, with
                # PROGRESS_SCHEDULE empty). Between updates the user sees only a
                # "typing" indicator; a short turn finishes before the first update
                # and stays silent, which is what the user asked for.
                next_delay = _next_progress_delay(self, progress_count)
                time_since_progress = current_time - last_progress_message
                if time_since_progress >= next_delay and chat:
                    try:
                        elapsed_str = _format_elapsed(elapsed)
                        if tool_in_progress:
                            status_line = f"Currently running: {tool_in_progress}"
                        else:
                            status_line = f"Status: {current_status}"
                        snippet = ""
                        if last_turn_text_blocks:
                            snippet = last_turn_text_blocks[-1][-300:]
                        elif partial_text:
                            snippet = partial_text[-300:]
                        snippet_line = f"Latest output: {snippet}" if snippet else ""
                        content_fingerprint = "\n".join(p for p in [status_line, snippet_line] if p)
                        if content_fingerprint == last_progress_content:
                            msg = f"Still working... ({elapsed_str} elapsed)"
                        else:
                            last_progress_content = content_fingerprint
                            header = f"Still working ({elapsed_str} elapsed):"
                            msg = "\n".join(p for p in [header, status_line, snippet_line] if p)
                        await chat.send_message(msg)
                        last_progress_message = current_time
                        progress_count += 1
                    except Exception:
                        pass

                # Cap the read timeout so the loop wakes regularly for /stop
                # checks, the absolute-timeout check, and the next scheduled
                # progress update. Without the progress cap the loop could sleep
                # up to 30s and fire the scheduled 10-minute update late. Only
                # apply that cap when a chat is present;
                # headless callers send no updates and should not busy-wake. Floor
                # at 1s so we never pass a non-positive value to wait_for.
                idle_remaining = self.IDLE_TIMEOUT - time_since_activity
                absolute_remaining = self.ABSOLUTE_TIMEOUT - elapsed
                read_timeout = min(30.0, idle_remaining, absolute_remaining)
                if chat:
                    read_timeout = min(read_timeout, next_delay - time_since_progress)
                read_timeout = max(1.0, read_timeout)
                line = await _read_line_with_timeout(process.stdout, timeout=read_timeout)

                if line:
                    last_activity = asyncio.get_running_loop().time()
                    line_str = line.decode(errors="replace").strip()
                    if line_str:
                        try:
                            data = json.loads(line_str)
                            msg_type = data.get("type")

                            if msg_type == "assistant":
                                current_status = "generating response"
                                tool_in_progress = None
                                last_turn_text_blocks = []
                                msg_data = data.get("message") or {}
                                for block in msg_data.get("content") or []:
                                    if block.get("type") == "text":
                                        text = block.get("text", "")
                                        if text:
                                            last_turn_text_blocks.append(text)
                                            all_text_blocks.append(text)
                                            # Same 100KB cap as partial_text below,
                                            # dropping oldest blocks first
                                            while (
                                                sum(len(b) for b in all_text_blocks) > 102400
                                                and len(all_text_blocks) > 1
                                            ):
                                                all_text_blocks.pop(0)
                                            # partial_text stays append-only for
                                            # progress saving and recovery context
                                            partial_text += text + "\n"
                                            # Cap at 100KB to prevent unbounded memory growth
                                            if len(partial_text) > 102400:
                                                partial_text = partial_text[-102400:]
                                # Tool calls ride inside "assistant" events as
                                # tool_use content blocks, never as a top-level
                                # event type (verified live against the CLI)
                                tool_names, _ = _extract_tool_activity(data)
                                if tool_names:
                                    tool_in_progress = ", ".join(tool_names)
                                    current_status = f"using {tool_in_progress}"
                                    logger.debug(f"Claude using tool(s): {tool_in_progress}")
                            elif msg_type == "user":
                                # Tool results ride inside "user" events as
                                # tool_result content blocks
                                _, tool_finished = _extract_tool_activity(data)
                                if tool_finished:
                                    tool_in_progress = None
                                    current_status = "processing result"

                            if msg_type == "result":
                                if "result" in data:
                                    final_result = data["result"]
                                # The CLI reports a FAILED turn inside an
                                # otherwise normal result event. Measured
                                # against the live CLI 2026-07-20: an auth
                                # failure carries is_error true while subtype
                                # is still "success", so subtype cannot be
                                # used to detect it -- is_error is the only
                                # reliable signal. (The status field is
                                # api_error_status, snake_case.)
                                if data.get("is_error"):
                                    result_is_error = True
                                    api_error_status = data.get("api_error_status")

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

            # A /stop almost always lands HERE, not in the loop's stop check.
            # stop_user() SIGKILLs immediately, but the loop only tests the
            # flag at the top of an iteration while it is parked in
            # _read_line_with_timeout, so EOF arrives first and the loop
            # leaves by the `line == b''` door without ever re-testing.
            # Claim the stop before the exit-code guard below, which cannot
            # tell our own SIGKILL from a kernel OOM kill: on 2026-08-04 a
            # 54-minute turn the user stopped deliberately was reported to
            # them as "[Hit memory limit]", and every stopped turn carrying
            # partial output had been misreported the same way.
            if user_id is not None and user_id in self._stop_requested:
                logger.info(
                    f"Claude stop confirmed for user {user_id} after "
                    f"{int(asyncio.get_running_loop().time() - start_time)}s "
                    f"(exit {process.returncode})"
                )
                if self.on_progress_clear and user_id:
                    self.on_progress_clear(user_id)
                last_turn = "\n".join(last_turn_text_blocks).strip()
                return _stopped_response(
                    final_result or last_turn or partial_text.strip(),
                    self.model, self.provider_name,
                )

            # A turn the CLI itself marked failed. This MUST be checked
            # before the exit-code guard below, which requires an empty
            # final_result and so never fires for these: the CLI exits
            # non-zero but still emits result text, so an auth failure
            # sailed through as a normal 73-char assistant reply, was
            # texted to the user, and was stored in conversation history.
            # That is how the 2026-07-20 outage ran silently for 90
            # minutes. Setting .error routes it to the admin alert and
            # keeps it out of history (see _save_and_send in bot.py).
            if result_is_error:
                detail = _error_excerpt(final_result or stderr_text, 300) or "CLI reported is_error"
                logger.error(
                    f"Claude turn FAILED for user {user_id} "
                    f"(is_error, api_error_status={api_error_status}, "
                    f"exit {process.returncode}): {detail}"
                )
                if self.on_progress_clear and user_id:
                    self.on_progress_clear(user_id)
                if _is_auth_failure(final_result, api_error_status):
                    # The "partial work" here is only the auth error string
                    # itself, so there is nothing worth salvaging. Say what
                    # is actually wrong instead of relaying CLI wording.
                    return LLMResponse(
                        text=(
                            "I can't reach Claude right now because the bot's "
                            "login was rejected. This needs fixing on the "
                            "machine, and nothing you did caused it. Your "
                            "message was not lost, just resend it once the "
                            "login is renewed."
                        ),
                        model=self.model, provider=self.provider_name,
                        error=f"auth failure: {detail}",
                    )
                # Other failures can arrive after real work, so keep any
                # genuine output rather than discarding it -- but still mark
                # the turn failed so it alerts and stays out of history.
                last_turn = "\n".join(last_turn_text_blocks).strip()
                salvage = last_turn or partial_text.strip()
                return LLMResponse(
                    text=(salvage + _interrupted_notice(process.returncode)) if salvage
                    else f"That turn failed before it finished: {detail}",
                    model=self.model, provider=self.provider_name,
                    error=detail, tool_use=bool(salvage),
                )

            if process.returncode != 0 and not final_result:
                logger.error(f"Claude error for user {user_id} (exit {process.returncode}): {stderr_text}")
                # Detect OOM kill: kernel sends SIGKILL (-9) when memory limit
                # is hit, or stderr may mention it explicitly
                is_oom = _is_oom_exit(process.returncode, stderr_text)
                last_turn = "\n".join(last_turn_text_blocks).strip()
                fallback_text = last_turn or partial_text.strip()
                if fallback_text:
                    logger.info(f"Returning fallback text despite error for user {user_id} ({len(fallback_text)} chars, from_last_turn={bool(last_turn)})")
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    # Never ship salvaged narration bare: it reads as a real reply.
                    suffix = _interrupted_notice(process.returncode)
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
                                          f"error: {_error_excerpt(stderr_text, 100)}", tool_in_progress)
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
                    text=f"Error (exit code {process.returncode}): {_error_excerpt(stderr_text, 500)}",
                    model=self.model, provider=self.provider_name,
                    error=_error_excerpt(stderr_text, 200),
                )

            # Success
            if self.on_progress_clear and user_id:
                self.on_progress_clear(user_id)

            if final_result:
                return LLMResponse(
                    text=_compose_full_reply(final_result, all_text_blocks),
                    model=self.model,
                    provider=self.provider_name, tool_use=True,
                )

            # No "result" message — use last assistant turn if available,
            # otherwise fall back to the full partial_text
            last_turn = "\n".join(last_turn_text_blocks).strip()
            if last_turn:
                logger.warning(f"No final result for user {user_id}, returning last turn ({len(last_turn)} chars)")
                return LLMResponse(
                    text=last_turn + _interrupted_notice(process.returncode), model=self.model,
                    provider=self.provider_name, tool_use=True,
                )
            if partial_text.strip():
                fallback_text = partial_text.strip()
                logger.warning(f"No final result for user {user_id}, returning full partial_text ({len(fallback_text)} chars)")
                return LLMResponse(
                    text=fallback_text + _interrupted_notice(process.returncode), model=self.model,
                    provider=self.provider_name, tool_use=True,
                )

            elapsed = time.monotonic() - start_time
            logger.warning(
                f"No response from Claude for user {user_id}. "
                f"elapsed={elapsed:.1f}s, exit={process.returncode}, "
                f"last_status={current_status}, last_tool={tool_in_progress}, "
                f"partial_text_len={len(partial_text)}, "
                f"stderr={_error_excerpt(stderr_text, 200) if stderr_text else 'none'}"
            )
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
                    try:
                        process.kill()
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                    await process.wait()
            if user_id is not None:
                if self._user_processes.get(user_id) is process:
                    self._user_processes.pop(user_id, None)
                self._stop_requested.discard(user_id)
                # If the CLI refreshed its OAuth token during this turn it
                # left the fresh token somewhere private: a detached copy of
                # the credential symlink, or (on macOS) a per-config-dir
                # keychain item that from then on SHADOWS the shared file
                # entirely. Fold either one back so the other users'
                # workspaces don't go stale and this one rejoins the shared
                # chain. Never let a hiccup here disturb the finished turn.
                try:
                    from core.claude_workspace import reconcile_shared_credentials
                    reconcile_shared_credentials(user_id)
                except Exception as exc:
                    logger.debug(
                        "credential reconcile skipped for %s: %s", user_id, exc
                    )

    @property
    def has_active_processes(self) -> bool:
        return bool(self._active_processes)

    async def graceful_shutdown(self):
        """Wait briefly for active Claude processes, then force-kill.

        Bounded at 10 seconds to align with systemd's TimeoutStopSec.
        A long wait here previously caused the bot to hang during restart.
        """
        if not self._active_processes:
            return

        procs = list(self._active_processes)
        logger.info(f"Waiting up to 10s for {len(procs)} active Claude processes...")
        for _ in range(10):
            if not self._active_processes:
                return
            await asyncio.sleep(1)

        remaining = [p for p in self._active_processes if p.returncode is None]
        if not remaining:
            return

        logger.warning(f"Force-killing {len(remaining)} Claude processes still running after 10s")
        for proc in remaining:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError, OSError) as e:
                logger.warning(f"Failed to kill Claude process: {e}")
        for proc in remaining:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("Claude process did not exit after SIGKILL within 5s")


class CodexCLIProvider(LLMProvider):
    """
    OpenAI Codex CLI provider — parallel to ClaudeCLIProvider.

    Runs `codex exec --json` as a subprocess with --sandbox danger-full-access,
    giving the LLM full tool-use (bash, file edits, web search, MCP tools, etc.).

    Auth: either `codex login` (ChatGPT Plus/Pro/Business/Edu/Enterprise plan,
    no API credits needed) or OPENAI_API_KEY in the environment.

    Stdout is a JSON Lines stream of events: thread.started, turn.started,
    turn.completed (with usage), turn.failed, item.started/completed.
    The final answer is the last item.completed with item.type == "agent_message".
    """

    IDLE_TIMEOUT = 1800
    ABSOLUTE_TIMEOUT = 86400  # 24 hour hard ceiling per request, even with continuous activity
    # Flat 10-minute progress cadence, no front-loaded burst. See ClaudeCLIProvider
    # for the rationale; the two providers run parallel loops and must stay in sync.
    PROGRESS_SCHEDULE = ()
    PROGRESS_INTERVAL = 600  # flat 10-minute cadence, first update included

    def __init__(self, model: str = "gpt-5.5", api_key: str = ""):
        super().__init__(model, api_key)
        self._bot_dir = Path(__file__).parent.parent
        # Absolute path for sudoers literal command matching AND so launchd /
        # systemd processes find the binary when ~/.local/bin or brew dirs
        # aren't on the inherited PATH.
        self._cli_binary = _find_cli_binary("codex")
        self._active_processes: set = set()
        self._user_processes: dict = {}
        self._stop_requested: set = set()
        self.on_progress_save = None
        self.on_progress_clear = None

    def stop_user(self, user_id: int) -> bool:
        proc = self._user_processes.get(user_id)
        if proc is None:
            return False
        self._stop_requested.add(user_id)
        try:
            if proc.returncode is None:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        return True

    @property
    def provider_name(self) -> str:
        return "codex-cli"

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        # GPT-5.x and GPT-4o families used by Codex CLI all support vision input
        return True

    @property
    def supports_mcp(self) -> bool:
        """No route from here: `codex exec` has no MCP flag at all.

        Its servers are registered in ~/.codex/config.toml via `codex mcp
        add`, which this bot does not manage, so the tools in
        mcp_servers.json are unreachable on a Codex turn.
        """
        return False

    async def health_check(self) -> tuple[bool, str]:
        """Run `codex --version` and detect dyld / GLIBC load failures.

        Same shape as ClaudeCLIProvider.health_check — catches the binary
        being incompatible with the host OS before any user-facing failure.
        """
        binary = self._cli_binary
        if not binary or (not Path(binary).exists() and not shutil.which(binary)):
            rediscovered = _find_cli_binary("codex")
            if rediscovered != "codex" and Path(rediscovered).exists():
                self._cli_binary = rediscovered
                binary = rediscovered
            else:
                return False, (
                    "Codex CLI binary not found. Install with: "
                    "`npm i -g @openai/codex`, or switch provider via /provider."
                )
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return False, f"Codex CLI cannot be executed: {exc}"
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return False, "Codex CLI --version timed out (10s); binary may be hanging."
        text = ((stdout or b"") + (stderr or b"")).decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            first_line = text.splitlines()[0] if text else "ok"
            return True, first_line
        if _looks_like_binary_load_failure(text):
            return False, (
                f"Codex CLI binary fails to load on this OS: "
                f"{text[:300]}. "
                f"This usually means macOS < 13 or glibc < 2.31. "
                f"Switch provider via /provider (openai, openrouter, "
                f"ollama, gemini, etc.)."
            )
        return False, (
            f"Codex CLI --version exited {proc.returncode}: "
            f"{text[:300] if text else '(no output)'}"
        )

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7,
                       chat=None, user_id: int = None, original_message: str = "") -> LLMResponse:
        prompt = system_prompt + "\n\n"
        for msg in messages:
            prompt += wrap_turn(msg.role, msg.content) + "\n"
        prompt += "\nContinue the conversation naturally, responding to the latest message."

        cmd = [
            self._cli_binary,
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox", "danger-full-access",
            "-m", self.model,
            "-",  # Read prompt from stdin
        ]

        typing_task = None
        process = None
        start_time = asyncio.get_running_loop().time()
        last_activity = start_time
        last_progress_message = start_time
        last_progress_content = ""
        last_progress_save = start_time
        progress_count = 0
        agent_message_blocks: list = []
        partial_text = ""
        total_input = 0
        total_output = 0
        current_status = "thinking"
        tool_in_progress = None
        turn_failed_message = None

        try:
            if chat:
                typing_task = asyncio.create_task(_send_typing_periodically(chat))

            # Codex CLI: allow-list env (SAFE_ENV_VARS) plus the provider's
            # own auth vars. Codex authenticates via `codex login` (token at
            # ~/.codex/auth.json) or OPENAI_API_KEY. Per-instance api_key
            # override gets injected into the env after the allow-list build.
            from core.tools import build_cli_env
            extra = {"OPENAI_API_KEY": self.api_key} if self.api_key else {}
            extra = {**_user_identity_env(user_id), **extra}
            cli_env = build_cli_env(
                provider_keys=frozenset({
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "CODEX_HOME",
                }),
                extra=extra or None,
            )

            cwd = str(self._bot_dir)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=cli_env,
                limit=50 * 1024 * 1024,
            )
            self._active_processes.add(process)
            if user_id is not None:
                self._stop_requested.discard(user_id)
                self._user_processes[user_id] = process

            process.stdin.write(prompt.encode())
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

            while True:
                current_time = asyncio.get_running_loop().time()
                time_since_activity = current_time - last_activity
                elapsed = current_time - start_time

                if user_id is not None and user_id in self._stop_requested:
                    logger.info(f"Codex stop requested for user {user_id} after {int(elapsed)}s")
                    if process.returncode is None:
                        try:
                            process.kill()
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    await process.wait()
                    fallback = "\n\n".join(agent_message_blocks).strip() or partial_text.strip()
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    return _stopped_response(fallback, self.model, self.provider_name)

                if elapsed > self.ABSOLUTE_TIMEOUT:
                    logger.warning(
                        f"Codex absolute timeout for user {user_id} after {int(elapsed)}s. "
                        f"Status: {current_status}, tool: {tool_in_progress}"
                    )
                    if self.on_progress_save and user_id:
                        self.on_progress_save(user_id, original_message, partial_text,
                                              f"absolute timeout after {int(elapsed)}s", tool_in_progress)
                    if process.returncode is None:
                        try:
                            process.kill()
                        except (ProcessLookupError, PermissionError, OSError):
                            pass
                    await process.wait()
                    fallback = "\n\n".join(agent_message_blocks).strip() or partial_text.strip()
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    if fallback:
                        return LLMResponse(
                            text=fallback + f"\n\n[Hit {self.ABSOLUTE_TIMEOUT // 3600}-hour time limit. Break into smaller steps.]",
                            model=self.model, provider=self.provider_name, tool_use=True,
                        )
                    return LLMResponse(
                        text=f"Codex hit the {self.ABSOLUTE_TIMEOUT // 3600}-hour time limit. Try breaking the task into smaller steps.",
                        model=self.model, provider=self.provider_name,
                    )

                if time_since_activity > self.IDLE_TIMEOUT:
                    logger.warning(f"Codex idle timeout for user {user_id} after {self.IDLE_TIMEOUT}s. Last status: {current_status}, tool: {tool_in_progress}")
                    if self.on_progress_save and user_id:
                        self.on_progress_save(user_id, original_message, partial_text,
                                              f"timeout after {self.IDLE_TIMEOUT}s", tool_in_progress)
                    process.kill()
                    await process.wait()
                    fallback = "\n\n".join(agent_message_blocks).strip()
                    if fallback:
                        if self.on_progress_clear and user_id:
                            self.on_progress_clear(user_id)
                        return LLMResponse(
                            text=fallback + f"\n\n[Task incomplete - Codex stopped responding after {self.IDLE_TIMEOUT // 60} minutes]",
                            model=self.model, provider=self.provider_name, tool_use=True,
                        )
                    timeout_msg = f"Codex stopped responding after {self.IDLE_TIMEOUT // 60} minutes of inactivity."
                    if tool_in_progress:
                        timeout_msg += f" Was running: {tool_in_progress}"
                    if partial_text:
                        timeout_msg += "\n\nPartial progress was saved. Use /recover to see it."
                    else:
                        timeout_msg += " The task may have been too complex. Try breaking it into smaller steps."
                    return LLMResponse(text=timeout_msg, model=self.model, provider=self.provider_name)

                # No-text kill removed deliberately (same reasoning as the Claude
                # provider): long tool runs (stem separation, transcription,
                # builds, large downloads) routinely produce no user-facing text
                # for many minutes on modest hardware, so a short no-text timeout
                # kills healthy work, not stalls. Genuine stalls are covered by
                # the idle timeout, the absolute cap, and tool-named progress
                # reports.

                if time_since_activity > self.IDLE_TIMEOUT * 0.8 and time_since_activity <= self.IDLE_TIMEOUT * 0.8 + 30:
                    logger.warning(f"Codex approaching idle timeout for user {user_id}: {int(time_since_activity)}s idle. Status: {current_status}")

                # Flat progress cadence (see ClaudeCLIProvider.complete for the
                # rationale): PROGRESS_SCHEDULE is empty, so every update waits the
                # full PROGRESS_INTERVAL and a short turn stays silent.
                next_delay = _next_progress_delay(self, progress_count)
                time_since_progress = current_time - last_progress_message
                if time_since_progress >= next_delay and chat:
                    try:
                        elapsed_str = _format_elapsed(elapsed)
                        if tool_in_progress:
                            status_line = f"Currently running: {tool_in_progress}"
                        else:
                            status_line = f"Status: {current_status}"
                        snippet = ""
                        if agent_message_blocks:
                            snippet = agent_message_blocks[-1][-300:]
                        elif partial_text:
                            snippet = partial_text[-300:]
                        snippet_line = f"Latest output: {snippet}" if snippet else ""
                        content_fingerprint = "\n".join(p for p in [status_line, snippet_line] if p)
                        if content_fingerprint == last_progress_content:
                            msg = f"Still working... ({elapsed_str} elapsed)"
                        else:
                            last_progress_content = content_fingerprint
                            header = f"Still working ({elapsed_str} elapsed):"
                            msg = "\n".join(p for p in [header, status_line, snippet_line] if p)
                        await chat.send_message(msg)
                        last_progress_message = current_time
                        progress_count += 1
                    except Exception:
                        pass

                # Cap the read timeout so the loop wakes in time for the next
                # scheduled progress update (only when a chat is attached;
                # headless callers send none and must not busy-wake), plus the
                # /stop and absolute-timeout checks. Floor at 1s.
                idle_remaining = self.IDLE_TIMEOUT - time_since_activity
                absolute_remaining = self.ABSOLUTE_TIMEOUT - elapsed
                read_timeout = min(30.0, idle_remaining, absolute_remaining)
                if chat:
                    read_timeout = min(read_timeout, next_delay - time_since_progress)
                read_timeout = max(1.0, read_timeout)
                line = await _read_line_with_timeout(process.stdout, timeout=read_timeout)

                if line:
                    last_activity = asyncio.get_running_loop().time()
                    line_str = line.decode(errors="replace").strip()
                    if line_str:
                        try:
                            data = json.loads(line_str)
                            evt_type = data.get("type")

                            if evt_type == "turn.started":
                                current_status = "thinking"
                                tool_in_progress = None
                            elif evt_type == "turn.completed":
                                current_status = "done"
                                tool_in_progress = None
                                usage = data.get("usage") or {}
                                total_input += usage.get("input_tokens", 0) or 0
                                total_output += usage.get("output_tokens", 0) or 0
                            elif evt_type == "turn.failed":
                                err = data.get("error") or {}
                                turn_failed_message = err.get("message") if isinstance(err, dict) else str(err)
                                logger.warning(f"Codex turn.failed for user {user_id}: {turn_failed_message}")
                            elif evt_type == "error":
                                turn_failed_message = data.get("message") or "stream error"
                                logger.warning(f"Codex stream error for user {user_id}: {turn_failed_message}")
                            elif evt_type in ("item.started", "item.updated", "item.completed"):
                                item = data.get("item") or {}
                                item_type = item.get("type")
                                if item_type == "agent_message":
                                    if evt_type == "item.completed":
                                        text = item.get("text") or ""
                                        if text:
                                            agent_message_blocks.append(text)
                                            partial_text += text + "\n"
                                            if len(partial_text) > 102400:
                                                partial_text = partial_text[-102400:]
                                        current_status = "generating response"
                                elif item_type == "command_execution":
                                    cmd_str = item.get("command") or "command"
                                    if evt_type == "item.started":
                                        tool_in_progress = f"shell: {cmd_str[:80]}"
                                        current_status = f"running {tool_in_progress}"
                                    elif evt_type == "item.completed":
                                        tool_in_progress = None
                                        current_status = "processing result"
                                elif item_type == "file_change":
                                    if evt_type == "item.completed":
                                        changes = item.get("changes") or []
                                        paths = ", ".join((c.get("path") or "?") for c in changes[:3])
                                        tool_in_progress = None
                                        current_status = f"edited {paths}"
                                elif item_type == "mcp_tool_call":
                                    server = item.get("server") or "mcp"
                                    tool = item.get("tool") or "tool"
                                    if evt_type == "item.started":
                                        tool_in_progress = f"{server}.{tool}"
                                        current_status = f"calling {tool_in_progress}"
                                    elif evt_type == "item.completed":
                                        tool_in_progress = None
                                        current_status = "processing result"
                                elif item_type == "web_search":
                                    if evt_type == "item.completed":
                                        q = item.get("query") or ""
                                        current_status = f"web_search: {q[:80]}"
                                elif item_type == "reasoning":
                                    # Reasoning items aren't user-visible
                                    if evt_type == "item.completed":
                                        current_status = "thinking"

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
                    break

                if process.returncode is not None:
                    break

            await process.wait()
            stderr_bytes = await process.stderr.read()
            stderr_text = stderr_bytes.decode(errors="replace").strip()

            # Same EOF-beats-the-flag race as the Claude provider above: claim
            # the stop before the exit-code guard reads our own SIGKILL as OOM.
            if user_id is not None and user_id in self._stop_requested:
                logger.info(
                    f"Codex stop confirmed for user {user_id} after "
                    f"{int(asyncio.get_running_loop().time() - start_time)}s "
                    f"(exit {process.returncode})"
                )
                if self.on_progress_clear and user_id:
                    self.on_progress_clear(user_id)
                return _stopped_response(
                    "\n\n".join(agent_message_blocks).strip() or partial_text.strip(),
                    self.model, self.provider_name,
                )

            if process.returncode != 0 and not agent_message_blocks:
                logger.error(f"Codex error for user {user_id} (exit {process.returncode}): {stderr_text}")
                is_oom = _is_oom_exit(process.returncode, stderr_text)
                fallback_text = partial_text.strip()
                if fallback_text:
                    if self.on_progress_clear and user_id:
                        self.on_progress_clear(user_id)
                    # Never ship salvaged narration bare: it reads as a real reply.
                    suffix = _interrupted_notice(process.returncode)
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
                                          f"error: {_error_excerpt(stderr_text, 100)}", tool_in_progress)
                if is_oom:
                    elapsed = int(asyncio.get_running_loop().time() - start_time)
                    oom_msg = (
                        f"Codex hit the memory limit and was killed after "
                        f"{elapsed // 60}m {elapsed % 60}s."
                    )
                    if tool_in_progress:
                        oom_msg += f" It was running: {tool_in_progress}."
                    oom_msg += (
                        "\n\nThis usually happens when Codex reads too many large files "
                        "or accumulates too much tool output in a single session. "
                        "Try breaking the task into smaller steps, or use /clear to reset context."
                    )
                    return LLMResponse(
                        text=oom_msg, model=self.model, provider=self.provider_name,
                        error="OOM killed",
                    )
                err_detail = turn_failed_message or _error_excerpt(stderr_text, 500) or f"exit {process.returncode}"
                hint = ""
                if "auth" in err_detail.lower() or "login" in err_detail.lower() or "unauthor" in err_detail.lower():
                    hint = "\n\nRun `codex login` to authenticate with your ChatGPT plan, or set OPENAI_API_KEY."
                return LLMResponse(
                    text=f"Codex error: {err_detail}{hint}",
                    model=self.model, provider=self.provider_name,
                    error=_error_excerpt(err_detail, 200),
                )

            if self.on_progress_clear and user_id:
                self.on_progress_clear(user_id)

            final_text = "\n\n".join(agent_message_blocks).strip()
            if final_text:
                return LLMResponse(
                    text=final_text, model=self.model,
                    provider=self.provider_name, tool_use=True,
                    input_tokens=total_input, output_tokens=total_output,
                )

            if partial_text.strip():
                fallback_text = partial_text.strip()
                logger.warning(f"No agent_message for Codex user {user_id}, returning full partial_text ({len(fallback_text)} chars)")
                return LLMResponse(
                    text=fallback_text + _interrupted_notice(process.returncode), model=self.model,
                    provider=self.provider_name, tool_use=True,
                    input_tokens=total_input, output_tokens=total_output,
                )

            if turn_failed_message:
                return LLMResponse(
                    text=f"Codex failed: {turn_failed_message}",
                    model=self.model, provider=self.provider_name,
                    error=_error_excerpt(turn_failed_message, 200),
                )
            elapsed = time.monotonic() - start_time
            logger.warning(
                f"No response from Codex for user {user_id}. "
                f"elapsed={elapsed:.1f}s, exit={process.returncode}, "
                f"last_status={current_status}, last_tool={tool_in_progress}, "
                f"partial_text_len={len(partial_text)}, "
                f"stderr={_error_excerpt(stderr_text, 200) if stderr_text else 'none'}"
            )
            return LLMResponse(
                text="Codex produced no response. This can happen when the context is too large. Try /clear to reset, or send a shorter message.",
                model=self.model, provider=self.provider_name,
                error="No output",
            )

        except FileNotFoundError:
            return LLMResponse(
                text="Codex CLI is not installed. Install it with `npm install -g @openai/codex` "
                     "(requires Node.js 22+), then run `codex login` or set OPENAI_API_KEY.",
                model=self.model, provider=self.provider_name,
                error="codex binary not found",
            )
        except Exception as e:
            logger.exception(f"Failed to call Codex for user {user_id}")
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
                    try:
                        process.kill()
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                    await process.wait()
            if user_id is not None:
                if self._user_processes.get(user_id) is process:
                    self._user_processes.pop(user_id, None)
                self._stop_requested.discard(user_id)

    @property
    def has_active_processes(self) -> bool:
        return bool(self._active_processes)

    async def graceful_shutdown(self):
        if not self._active_processes:
            return
        procs = list(self._active_processes)
        logger.info(f"Waiting up to 10s for {len(procs)} active Codex processes...")
        for _ in range(10):
            if not self._active_processes:
                return
            await asyncio.sleep(1)
        remaining = [p for p in self._active_processes if p.returncode is None]
        if not remaining:
            return
        logger.warning(f"Force-killing {len(remaining)} Codex processes still running after 10s")
        for proc in remaining:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError, OSError) as e:
                logger.warning(f"Failed to kill Codex process: {e}")
        for proc in remaining:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("Codex process did not exit after SIGKILL within 5s")


class FreeCCProvider(ClaudeCLIProvider):
    """Free Claude Code proxy provider.

    Routes Claude CLI traffic through a local free-claude-code proxy server
    (github.com/Alishahryar1/free-claude-code) that translates requests to
    alternative backends (Gemini, DeepSeek, Groq, etc.).

    Inherits all of ClaudeCLIProvider's tool-use, streaming, timeouts, and
    /stop handling. Only differences: forces ANTHROPIC_BASE_URL to the proxy,
    and health_check probes the proxy before checking the CLI binary.
    """

    DEFAULT_PROXY_URL = "http://localhost:8082/v1"

    def __init__(self, model: str = "claude-sonnet-5", api_key: str = ""):
        super().__init__(model, api_key)
        self._proxy_url = os.environ.get(
            "FCC_PROXY_URL", self.DEFAULT_PROXY_URL
        )

    @property
    def provider_name(self) -> str:
        return "fcc"

    async def health_check(self) -> tuple[bool, str]:
        """Check the proxy is running, then verify the CLI binary exists."""
        health_url = self._proxy_url.removesuffix("/v1") + "/health"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(health_url)
                if resp.status_code >= 500:
                    return False, (
                        f"fcc: proxy at {self._proxy_url} returned {resp.status_code}. "
                        f"Is the free-claude-code server running?"
                    )
        except httpx.ConnectError:
            return False, (
                f"fcc: cannot connect to proxy at {self._proxy_url}. "
                f"Start it with: uv run fcc-server (in the free-claude-code directory)"
            )
        except Exception as e:
            return False, f"fcc: proxy health check failed: {e}"
        return await super().health_check()

    def _get_cli_env(self, user_id: int = None) -> dict:
        """Build env with ANTHROPIC_BASE_URL forced to the proxy URL."""
        from core.tools import build_cli_env
        return build_cli_env(
            provider_keys=frozenset({
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CONFIG_DIR",
            }),
            extra={
                "ANTHROPIC_BASE_URL": self._proxy_url,
                **(_claude_workspace_env(user_id) or {}),
            },
        )


class ClaudeAPIProvider(LLMProvider):
    """Anthropic Claude API — text-only, no tool execution layer."""

    API_URL = "https://api.anthropic.com/v1/messages"
    MODELS_URL = "https://api.anthropic.com/v1/models"

    @property
    def provider_name(self) -> str:
        return "claude-api"

    @property
    def supports_tool_use(self) -> bool:
        return False  # Claude API provider doesn't use our tool layer

    @property
    def supports_vision(self) -> bool:
        return True

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "claude-api: ANTHROPIC_API_KEY (LLM_API_KEY) is empty"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        return await _http_get_health(self.MODELS_URL, headers, "claude-api")

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
            "system": system_prompt,
            "messages": api_messages,
        }
        if _claude_accepts_temperature(self.model):
            body["temperature"] = temperature
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
                    error_obj = data.get("error") or {}
                    error_msg = error_obj.get("message", str(data)) if isinstance(error_obj, dict) else str(error_obj)
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
                error_obj = data.get("error") or {}
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
            message = choice.get("message") or {}
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
                    func = tc.get("function") or {}
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

                    result = wrap_tool_result(func_name, result)
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

                        result = wrap_tool_result(tc_name, result)
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

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "openai: OPENAI_API_KEY (LLM_API_KEY) is empty"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return await _http_get_health(f"{self.base_url}/models", headers, "openai")

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

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "openrouter: OPENROUTER_API_KEY (LLM_API_KEY) is empty"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/nickathens/MyOldMachine",
            "X-Title": "MyOldMachine",
        }
        return await _http_get_health(f"{self.BASE_URL}/models", headers, "openrouter")

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

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "gemini: GEMINI_API_KEY (LLM_API_KEY) is empty"
        # Gemini takes the key in a query string, not a header.
        url = f"{self.API_URL}?key={self.api_key}"
        return await _http_get_health(url, None, "gemini")

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
                    error_obj = data.get("error") or {}
                    error_msg = error_obj.get("message", str(data)) if isinstance(error_obj, dict) else str(error_obj)
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

                content = candidates[0].get("content") or {}
                parts = content.get("parts") or []

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

                        result = wrap_tool_result(func_name, result)
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

                            result = wrap_tool_result(tc_name, result)
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

    Uses api.deepseek.com/v1 endpoint. DeepSeek V4 (Flash and Pro), V3.2 legacy.
    V4 Flash: $0.14/$0.28 per MTok. V4 Pro: $1.74/$3.48 per MTok.
    Cached input discounted ~90%. V4 has 1M context; V3.2 aliases have 128K.
    deepseek-v4-flash/pro, deepseek-chat, deepseek-reasoner all support tool calls.
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
        return False  # DeepSeek V4 API does not support vision

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "deepseek: DEEPSEEK_API_KEY (LLM_API_KEY) is empty"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return await _http_get_health(f"{self.BASE_URL}/models", headers, "deepseek")

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
        # Current Grok vision-capable: 4.5 (native video), 4.20 family, 4.1 Fast,
        # 4-0709, any *vision* model.
        m = self.model
        return ("vision" in m or "grok-4-1-fast" in m or "grok-4-fast" in m
                or m == "grok-4-0709"
                or "grok-4.5" in m or "grok-4-5" in m
                or "grok-4.20" in m or "grok-4-20" in m)

    def _is_reasoning_model(self) -> bool:
        """Check if this is a Grok reasoning model (uses max_completion_tokens, no temperature)."""
        # Non-reasoning: grok-4-1-fast-non-reasoning, grok-code-fast-1.
        # Reasoning: grok-4-1-fast-reasoning, grok-4-0709, grok-3-mini.
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

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "grok: XAI_API_KEY (LLM_API_KEY) is empty"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return await _http_get_health(f"{self.BASE_URL}/models", headers, "grok")

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
    K2.7-Code is latest (token-efficient agentic coding), K2.6 prior long-horizon
    coding agent, K2.5 multimodal (vision + tools), K2 Thinking for reasoning.
    256K context. K2.7-Code/K2.6: $0.95/$4.00, K2.5: $0.60/$3.00, K2: $0.60/$2.50 per MTok.
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
        return "k2.5" in self.model.lower()

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "kimi: MOONSHOT_API_KEY (LLM_API_KEY) is empty"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return await _http_get_health(f"{self.BASE_URL}/models", headers, "kimi")

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

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "minimax: MINIMAX_API_KEY (LLM_API_KEY) is empty"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # MiniMax /models endpoint shape is undocumented for some accounts;
        # _http_get_health treats 404 as a permissive "could not pre-verify"
        # so this is safe to call here.
        return await _http_get_health(f"{self.BASE_URL}/models", headers, "minimax")

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


class ZaiProvider(LLMProvider):
    """Z.ai (Zhipu) GLM API — OpenAI-compatible with tool-use support.

    Uses api.z.ai/api/paas/v4 endpoint. GLM-5.2 is the flagship: open-weights,
    long-horizon coding/agentic, usable 1M context, two thinking-effort levels.
    GLM-5.2: $1.40/$4.40 per MTok (cached input $0.26 per MTok).
    Temperature must be within [0.0, 1.0] (per docs.z.ai) — clamped here.
    GLM-5.2 and the other text models are text-only; z.ai vision models
    (GLM-5V-Turbo, GLM-4.6V, GLM-4.5V) carry a 'V' marker.
    """

    BASE_URL = "https://api.z.ai/api/paas/v4"

    def __init__(self, model: str, api_key: str = ""):
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "zai"

    @property
    def supports_vision(self) -> bool:
        # z.ai vision models carry a 'V' marker (glm-5v-turbo, glm-4.6v, glm-4.5v).
        m = self.model.lower()
        return "v-turbo" in m or m.endswith("v")

    async def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "zai: ZAI_API_KEY (LLM_API_KEY) is empty"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return await _http_get_health(f"{self.BASE_URL}/models", headers, "zai")

    async def complete(self, system_prompt, messages, max_tokens=8192, temperature=0.7, **kwargs):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # z.ai GLM API requires temperature within [0.0, 1.0]
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

    async def health_check(self) -> tuple[bool, str]:
        is_cloud = self.base_url.endswith("ollama.com")
        label = self.provider_name
        if is_cloud and not self.api_key:
            return False, f"{label}: OLLAMA_API_KEY (LLM_API_KEY) is empty (cloud requires a key)"
        headers: dict = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # Ollama exposes /api/tags as the canonical "is the server up?"
        # endpoint — already used as a probe inside complete().
        url = f"{self.base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            hint = "check internet connection" if is_cloud else "is `ollama serve` running?"
            return False, f"{label}: cannot reach {self.base_url} ({hint})"
        except httpx.ReadTimeout:
            return False, f"{label}: {self.base_url}/api/tags read timed out after 5s"
        except httpx.HTTPError as exc:
            return False, f"{label}: HTTP error ({exc.__class__.__name__})"
        except Exception as exc:
            return False, f"{label}: probe raised {exc.__class__.__name__}: {exc}"
        if resp.status_code == 200:
            return True, f"{label}: ok"
        if resp.status_code == 401:
            return False, f"{label}: invalid API key (HTTP 401)"
        return False, f"{label}: HTTP {resp.status_code} from {url}"

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
            logger.info("Ollama OpenAI-compat endpoint unavailable, falling back to native API")
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
                    msg = data.get("message") or {}
                    text = msg.get("content", "")
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
        "codex": lambda: CodexCLIProvider(model, api_key),
        "codex-cli": lambda: CodexCLIProvider(model, api_key),
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
        "zai": lambda: ZaiProvider(model, api_key),
        "glm": lambda: ZaiProvider(model, api_key),
        "zhipu": lambda: ZaiProvider(model, api_key),
        "fcc": lambda: FreeCCProvider(model, api_key),
    }
    factory = providers.get(provider.lower())
    if not factory:
        supported = ", ".join(sorted(providers.keys()))
        raise ValueError(f"Unknown LLM provider: {provider}. Supported: {supported}")
    return factory()
