"""
Tool Execution Layer for MyOldMachine.

Gives any LLM provider (OpenAI, Gemini, OpenRouter, Ollama) the ability to
execute commands on the machine. The LLM sends structured tool calls via its
native function-calling API, this module executes them, and returns results.

Tools:
  - run_command: Execute a shell command (foreground or background)
  - read_file: Read a file's contents
  - write_file: Write content to a file
  - list_directory: List files in a directory
  - check_process: Poll or kill a background process by ID

Architecture (OpenClaw-inspired):
  1. Process Management — ProcessRegistry tracks all spawned processes by ID.
     Long-running commands can be backgrounded and polled later.
  2. Environment Hardening — sanitized env vars, blocked dangerous env names,
     clean PATH that doesn't leak bot internals.
  3. Unified Tool Schema — tools defined once in TOOL_DEFINITIONS, transformed
     per-provider via get_tools_openai() / get_tools_gemini().
  4. Output Streaming — commands stream stdout/stderr in chunks, returned
     incrementally for long-running processes.
  5. Script Preflight — validates write_file content against file extension
     to catch shell syntax in Python files, etc.
"""

import asyncio
import json
import logging
import os
import platform
import re
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- Constants ---

MAX_OUTPUT_CHARS = 50_000
COMMAND_TIMEOUT = 120  # seconds (foreground)
BACKGROUND_TIMEOUT = 3600  # 1 hour max for background processes
MAX_TOOL_ITERATIONS = 25  # max tool-call rounds per user message
STREAM_CHUNK_INTERVAL = 5  # seconds between output checks during streaming


# ============================================================================
# 1. UNIFIED TOOL SCHEMA
# ============================================================================
# Single source of truth. Transformed per-provider by get_tools_openai() and
# get_tools_gemini(). No more maintaining two separate copies.

TOOL_DEFINITIONS = [
    {
        "name": "run_command",
        "description": (
            "Execute a shell command on this machine and return stdout+stderr. "
            "Use for installing packages, running scripts, checking system status, "
            "managing files, and any task that requires shell access. "
            "Commands run as the current user with sudo available. "
            "Set background=true for long-running commands (installs, builds, "
            "downloads) — returns a process_id you can poll with check_process."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run in background (default: false). Use for commands that take >30s.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Custom timeout in seconds (default: 120 for foreground, 3600 for background)",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Returns the file text. "
            "Use for reading config files, scripts, logs, or any text file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does. Creates parent directories as needed. "
            "Content is validated against the file extension to prevent errors."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories at the given path. "
            "Returns names, sizes, and types (file/dir)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory to list (default: home directory)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_process",
        "description": (
            "Check on a background process started with run_command(background=true). "
            "Returns current output and status. Use action='kill' to terminate it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": "The process ID returned by run_command",
                },
                "action": {
                    "type": "string",
                    "description": "Action: 'status' (default) or 'kill'",
                },
            },
            "required": ["process_id"],
        },
    },
]


def get_tool_names() -> set[str]:
    """Return the set of available tool names."""
    return {t["name"] for t in TOOL_DEFINITIONS}


def get_tools_openai() -> list[dict]:
    """Transform unified tool definitions to OpenAI-compatible format."""
    tools = []
    for tool in TOOL_DEFINITIONS:
        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        })
    return tools


def get_tools_gemini() -> list[dict]:
    """Transform unified tool definitions to Gemini format.

    Gemini doesn't support some JSON Schema keywords that OpenAI does.
    We strip unsupported fields and ensure the schema is Gemini-safe.
    """
    declarations = []
    for tool in TOOL_DEFINITIONS:
        params = _strip_gemini_unsupported(tool["parameters"])
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": params,
        })
    return [{"functionDeclarations": declarations}]


def _strip_gemini_unsupported(schema: dict) -> dict:
    """Remove JSON Schema keywords Gemini doesn't support."""
    unsupported = {"additionalProperties", "default", "minimum", "maximum",
                   "minItems", "maxItems", "pattern", "format"}
    cleaned = {}
    for key, value in schema.items():
        if key in unsupported:
            continue
        if isinstance(value, dict):
            cleaned[key] = _strip_gemini_unsupported(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _strip_gemini_unsupported(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


# Legacy aliases for backward compatibility with llm.py imports
TOOLS_OPENAI = None  # Lazy — set on first access
TOOLS_GEMINI = None


def _ensure_legacy_aliases():
    """Populate legacy aliases on first use."""
    global TOOLS_OPENAI, TOOLS_GEMINI
    if TOOLS_OPENAI is None:
        TOOLS_OPENAI = get_tools_openai()
    if TOOLS_GEMINI is None:
        TOOLS_GEMINI = get_tools_gemini()


# ============================================================================
# 2. ENVIRONMENT HARDENING
# ============================================================================

# Environment variables that should never be passed to spawned commands
BLOCKED_ENV_VARS = {
    # Bot internals — don't leak API keys or tokens
    "TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN",
    "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    # Session / auth tokens
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN",
    "DATABASE_URL", "DATABASE_PASSWORD",
    "REDIS_URL", "REDIS_PASSWORD",
    # SSH
    "SSH_AUTH_SOCK",  # Don't let commands access SSH agent
}

# Patterns in env var names that indicate secrets
BLOCKED_ENV_PATTERNS = [
    r".*_SECRET.*",
    r".*_TOKEN$",
    r".*_PASSWORD$",
    r".*_KEY$",
    r".*_CREDENTIALS.*",
]

# Env vars that ARE safe to keep (overrides pattern matching)
SAFE_ENV_VARS = {
    "HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "LC_ALL",
    "LC_CTYPE", "TERM", "DISPLAY", "XDG_RUNTIME_DIR", "XDG_DATA_HOME",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_DIRS",
    "XDG_CONFIG_DIRS", "DBUS_SESSION_BUS_ADDRESS",
    "TMPDIR", "TMP", "TEMP", "EDITOR", "VISUAL", "PAGER",
    "COLORTERM", "LS_COLORS", "HOSTNAME",
    "http_proxy", "https_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    # Python (not PYTHONHOME — it can break spawned Python processes)
    "PYTHONPATH",
    # Node
    "NODE_PATH", "NODE_ENV", "NPM_CONFIG_PREFIX",
    # Homebrew
    "HOMEBREW_PREFIX", "HOMEBREW_CELLAR", "HOMEBREW_REPOSITORY",
    # Locale
    "LANGUAGE", "LC_MESSAGES", "LC_NUMERIC", "LC_TIME", "LC_COLLATE",
    "LC_MONETARY", "LC_PAPER", "LC_NAME", "LC_ADDRESS", "LC_TELEPHONE",
    "LC_MEASUREMENT", "LC_IDENTIFICATION",
}


def _is_env_var_safe(name: str) -> bool:
    """Check if an environment variable is safe to pass to commands."""
    if name in SAFE_ENV_VARS:
        return True
    if name in BLOCKED_ENV_VARS:
        return False
    for pattern in BLOCKED_ENV_PATTERNS:
        if re.match(pattern, name, re.IGNORECASE):
            return False
    return True


def _build_command_env() -> dict:
    """Build a sanitized environment for command execution.

    - Strips API keys, tokens, and secrets from inherited env
    - Ensures standard paths are present (including Homebrew on macOS)
    - Doesn't leak the bot's Python venv
    """
    # Start from a filtered copy of the current environment
    env = {}
    for key, value in os.environ.items():
        if _is_env_var_safe(key):
            env[key] = value

    # Always set HOME
    env["HOME"] = str(Path.home())
    env["USER"] = os.environ.get("USER", "")
    env["LOGNAME"] = os.environ.get("LOGNAME", os.environ.get("USER", ""))

    # Build a clean PATH
    if platform.system() == "Darwin":
        standard_paths = [
            "/opt/homebrew/bin", "/opt/homebrew/sbin",
            "/usr/local/bin", "/usr/local/sbin",
            "/usr/bin", "/bin", "/usr/sbin", "/sbin",
        ]
    else:
        standard_paths = [
            "/usr/local/bin", "/usr/bin", "/bin",
            "/usr/local/sbin", "/usr/sbin", "/sbin",
            "/snap/bin",  # Ubuntu snap packages
        ]

    # Preserve user's PATH entries but ensure standard paths are included
    current_path = env.get("PATH", "")
    path_parts = current_path.split(":") if current_path else []

    # Remove bot's venv from PATH — commands should use system Python
    venv = os.environ.get("VIRTUAL_ENV", "")
    if venv:
        path_parts = [p for p in path_parts if not p.startswith(venv)]
        # Don't pass VIRTUAL_ENV either
        env.pop("VIRTUAL_ENV", None)

    # Add standard paths that are missing
    for sp in standard_paths:
        if sp not in path_parts:
            path_parts.append(sp)

    # User's local bin should be early in PATH
    user_local_bin = str(Path.home() / ".local" / "bin")
    if user_local_bin not in path_parts:
        path_parts.insert(0, user_local_bin)

    env["PATH"] = ":".join(p for p in path_parts if p)
    return env


# ============================================================================
# 3. SAFETY LAYER
# ============================================================================

# Commands that should never be executed
BLOCKED_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-?[a-zA-Z]*r[a-zA-Z]*\s+/\s*($|[;&|])",
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-?[a-zA-Z]*r[a-zA-Z]*\s+/\*",
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-?[a-zA-Z]*r[a-zA-Z]*\s+/(bin|boot|dev|etc|home|lib|lib64|opt|proc|root|run|sbin|srv|sys|tmp|usr|var)\s*($|[;&|])",
    r"rm\s+.*--no-preserve-root",
    r"mkfs\.",
    r"dd\s+if=.*of=/dev/[sh]d",
    r"dd\s+if=.*of=/dev/nvme",
    r">\s*/dev/[sh]d",
    r"chmod\s+(-R\s+)?777\s+/\s*($|[;&|])",
    r":\(\)\s*\{.*\|.*&\s*\}\s*;\s*:",
    r">\s*/dev/sda",
    r"mv\s+/\s",
    # Additional patterns
    r"curl\s+.*\|\s*sudo\s+bash",  # piping untrusted scripts to sudo bash
    r"wget\s+.*\|\s*sudo\s+bash",
]

# Paths the LLM should never write to
BLOCKED_WRITE_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/sudoers.d/",
    "/etc/hosts", "/boot/", "/boot/grub/",
    "/etc/crontab", "/var/spool/cron/",
]


_COMPILED_BLOCKED = [re.compile(p) for p in BLOCKED_PATTERNS]


def _is_command_blocked(command: str) -> str | None:
    """Return a reason string if the command is blocked, else None."""
    for pattern in _COMPILED_BLOCKED:
        if pattern.search(command):
            return f"Blocked: dangerous command pattern detected"
    return None


def _is_write_blocked(path: str) -> str | None:
    """Return a reason string if writing to this path is blocked, else None."""
    resolved = str(Path(path).expanduser().resolve())
    for blocked in BLOCKED_WRITE_PATHS:
        if resolved == blocked or resolved.startswith(blocked):
            return f"Blocked: cannot write to protected path: {blocked}"
    return None


# ============================================================================
# 4. PROCESS MANAGEMENT
# ============================================================================

@dataclass
class ManagedProcess:
    """A tracked process with output buffer and metadata."""
    process_id: str
    command: str
    process: Optional[asyncio.subprocess.Process] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    output_chunks: list[str] = field(default_factory=list)
    return_code: Optional[int] = None
    is_background: bool = False
    _read_offset: int = 0  # Track what the LLM has already seen

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    @property
    def full_output(self) -> str:
        return "".join(self.output_chunks)

    def consume_new_output(self) -> str:
        """Return output the LLM hasn't seen yet, advancing the read offset.

        This mutates state — call once per check, not in logging.
        """
        full = self.full_output
        new = full[self._read_offset:]
        self._read_offset = len(full)
        return new

    def status_summary(self) -> str:
        """Brief status line."""
        if self.is_running:
            return f"running ({self.elapsed:.0f}s elapsed, {len(self.full_output)} chars output)"
        else:
            return f"finished (exit code {self.return_code}, {self.elapsed:.1f}s, {len(self.full_output)} chars)"


class ProcessRegistry:
    """Tracks all spawned processes for poll/kill/cleanup.

    Inspired by OpenClaw's ProcessSupervisor — simplified for single-user.
    """

    def __init__(self, max_processes: int = 20):
        self._processes: dict[str, ManagedProcess] = {}
        self._max_processes = max_processes
        self._lock = asyncio.Lock()

    def _generate_id(self) -> str:
        return uuid.uuid4().hex[:8]

    async def register(self, command: str, process: asyncio.subprocess.Process,
                       background: bool = False) -> ManagedProcess:
        """Register a new process for tracking."""
        async with self._lock:
            # Clean up old finished processes if at capacity
            if len(self._processes) >= self._max_processes:
                self._cleanup_finished()

            proc_id = self._generate_id()
            managed = ManagedProcess(
                process_id=proc_id,
                command=command,
                process=process,
                is_background=background,
            )
            self._processes[proc_id] = managed
            return managed

    def get(self, process_id: str) -> Optional[ManagedProcess]:
        return self._processes.get(process_id)

    async def kill(self, process_id: str) -> str:
        """Kill a process and its children."""
        managed = self._processes.get(process_id)
        if not managed:
            return f"Process {process_id} not found"
        if not managed.is_running:
            return f"Process {process_id} already finished (exit code {managed.return_code})"

        try:
            pid = managed.process.pid
            # Kill process group on Unix to get children too
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                managed.process.terminate()

            # Give it 5 seconds to die
            try:
                await asyncio.wait_for(managed.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    managed.process.kill()
                await managed.process.wait()

            managed.return_code = managed.process.returncode
            managed.finished_at = time.time()
            return f"Process {process_id} killed (was running: {managed.command[:80]})"

        except Exception as e:
            return f"Error killing process {process_id}: {e}"

    def list_running(self) -> list[ManagedProcess]:
        return [p for p in self._processes.values() if p.is_running]

    def list_all(self) -> list[ManagedProcess]:
        return list(self._processes.values())

    def _cleanup_finished(self):
        """Remove oldest finished processes to make room."""
        finished = [(k, v) for k, v in self._processes.items() if not v.is_running]
        finished.sort(key=lambda x: x[1].finished_at or 0)
        # Remove oldest half
        to_remove = finished[:len(finished) // 2 + 1]
        for key, _ in to_remove:
            del self._processes[key]

    async def cleanup_all(self):
        """Kill all running processes (for shutdown)."""
        for managed in self.list_running():
            await self.kill(managed.process_id)
        self._processes.clear()


# Global process registry
_registry = ProcessRegistry()


def get_process_registry() -> ProcessRegistry:
    return _registry


# ============================================================================
# 5. SCRIPT PREFLIGHT VALIDATION
# ============================================================================

# Maps file extensions to patterns that indicate wrong content type
_PREFLIGHT_CHECKS = {
    # Python files should not contain bare shell syntax
    ".py": {
        "patterns": [
            (r"^\s*#!/bin/(ba)?sh", "Shebang is for shell but file is .py"),
            (r"^\s*(if\s+\[|then\s*$|fi\s*$|do\s*$|done\s*$)", "Shell control flow in Python file"),
            (r"^\s*echo\s+[\"']", "Bare 'echo' command (use print() in Python)"),
            (r"^\s*export\s+\w+=", "Shell 'export' in Python file"),
            (r"^\s*apt(-get)?\s+install", "Bare apt command in Python file"),
            (r"^\s*brew\s+install", "Bare brew command in Python file"),
        ],
        "min_matches": 2,  # Need at least 2 matches to flag (single match could be a string)
    },
    # JavaScript/Node files
    ".js": {
        "patterns": [
            (r"^\s*#!/bin/(ba)?sh", "Shebang is for shell but file is .js"),
            (r"^\s*(if\s+\[|then\s*$|fi\s*$)", "Shell control flow in JS file"),
            (r"^\s*echo\s+[\"']", "Bare 'echo' in JS file (use console.log)"),
            (r"^\s*export\s+\w+=(?![\s]*\{)", "Shell 'export' in JS file"),
        ],
        "min_matches": 2,
    },
    # Shell scripts should not contain Python-style syntax predominantly
    ".sh": {
        "patterns": [
            (r"^\s*def\s+\w+\s*\(", "Python function definition in shell script"),
            (r"^\s*class\s+\w+", "Python class definition in shell script"),
            (r"^\s*import\s+\w+", "Python import in shell script"),
            (r"^\s*from\s+\w+\s+import", "Python from-import in shell script"),
        ],
        "min_matches": 2,
    },
}


def _preflight_validate(path: str, content: str) -> Optional[str]:
    """Validate file content against its extension.

    Returns a warning string if the content appears to be the wrong language
    for the file extension. Returns None if everything looks fine.

    This catches a common LLM failure mode: writing shell commands into .py files
    or Python code into .sh files.
    """
    ext = Path(path).suffix.lower()
    check = _PREFLIGHT_CHECKS.get(ext)
    if not check:
        return None

    lines = content.split("\n")[:50]  # Only check first 50 lines
    matches = []
    for line in lines:
        for pattern, description in check["patterns"]:
            if re.search(pattern, line):
                matches.append(f"  Line: {line.strip()[:60]} — {description}")
                break  # One match per line is enough

    if len(matches) >= check["min_matches"]:
        warning = (
            f"WARNING: Content appears to be wrong language for {ext} file.\n"
            f"Found {len(matches)} suspicious patterns:\n"
            + "\n".join(matches[:5])
            + "\n\nFile was written anyway, but you should verify the content is correct."
        )
        return warning

    return None


# ============================================================================
# 6. FALLBACK TOOL-CALL PARSER
# ============================================================================
# When weak models (small Ollama, free OpenRouter) write tool calls as text
# instead of using structured function calling, this parser extracts them
# from the response text and executes them. This is a safety net — the
# primary path is always structured tool_calls.

# Patterns that match code blocks containing tool-like invocations
# Group 1 = language tag, Group 2 = block content
_TOOL_CODE_BLOCK_RE = re.compile(
    r"```(tool_code|bash|shell|sh|python|json|)\s*\n(.*?)```",
    re.DOTALL,
)

# Pattern for JSON-style tool calls: {"name": "run_command", "arguments": {...}}
_JSON_TOOL_CALL_RE = re.compile(
    r'\{\s*"(?:name|function)"\s*:\s*"(\w+)"\s*,\s*"(?:arguments|parameters|params)"\s*:\s*(\{[^}]*\})\s*\}',
    re.DOTALL,
)

# Pattern for function-call style: run_command(command="ls -la")
_FUNC_CALL_RE = re.compile(
    r'\b(run_command|read_file|write_file|list_directory|check_process)\s*\(([^)]*)\)',
)

# Max fallback attempts per response to prevent infinite loops
MAX_FALLBACK_ATTEMPTS = 5


def extract_tool_calls_from_text(text: str) -> list[dict]:
    """Extract tool calls that a weak model wrote as text instead of structured calls.

    Returns a list of dicts with 'name' and 'arguments' keys, or empty list
    if no tool calls were found in the text.

    This handles three common patterns:
    1. Code blocks with shell commands (```bash\npython scheduler_cli.py ...```)
    2. JSON-style tool calls ({"name": "run_command", "arguments": {...}})
    3. Function-call syntax (run_command(command="ls -la"))
    """
    tool_names = get_tool_names()
    calls = []

    # Strategy 1: Look for JSON-style tool calls in text
    for match in _JSON_TOOL_CALL_RE.finditer(text):
        name = match.group(1)
        if name not in tool_names:
            continue
        try:
            args = json.loads(match.group(2))
            calls.append({"name": name, "arguments": args})
            logger.info(f"[fallback] Extracted JSON tool call: {name}")
        except json.JSONDecodeError:
            continue

    if calls:
        return calls

    # Strategy 2: Look for function-call syntax
    for match in _FUNC_CALL_RE.finditer(text):
        name = match.group(1)
        raw_args = match.group(2).strip()
        if name not in tool_names:
            continue

        args = _parse_func_args(raw_args)
        if args is not None:
            calls.append({"name": name, "arguments": args})
            logger.info(f"[fallback] Extracted function-call: {name}")

    if calls:
        return calls

    # Strategy 3: Look for code blocks containing shell commands
    for match in _TOOL_CODE_BLOCK_RE.finditer(text):
        block_content = match.group(2).strip()
        if not block_content:
            continue

        # Check the language tag — tool_code is a strong signal from weak models
        lang_tag = (match.group(1) or "").strip().lower()
        is_tool_code_tag = lang_tag == "tool_code"

        # Skip blocks that are clearly just output/examples (multi-line with no commands)
        lines = [l.strip() for l in block_content.split("\n") if l.strip()]
        if not lines:
            continue

        # Check if it looks like a command to execute
        # Must start with a recognizable command pattern
        first_line = lines[0]

        # Skip if it's clearly documentation/output (starts with #, //, or is a table)
        if first_line.startswith(("#!", "#!/")):
            # Shebang — this is a script, treat the whole block as a script to run
            calls.append({"name": "run_command", "arguments": {"command": block_content}})
            logger.info(f"[fallback] Extracted script from code block")
            break

        if first_line.startswith("#") or first_line.startswith("//"):
            # If it's tagged as tool_code, skip comment lines but don't skip the block
            if not is_tool_code_tag:
                continue

        # Reconstruct the full command line: if the model split the command
        # across multiple lines (e.g., "python\n/path/to/script.py --args"),
        # join them into a single command before checking indicators.
        full_line = " ".join(lines)

        # Common command names (checked with and without trailing space/args)
        command_names = [
            "python", "python3", "pip", "pip3",
            "apt", "apt-get", "brew",
            "sudo", "cd", "ls", "cat", "echo",
            "mkdir", "cp", "mv", "rm",
            "curl", "wget",
            "git", "npm", "node",
            "systemctl", "launchctl",
            "docker", "docker-compose",
            "bash", "sh",
        ]

        # Check both the first line and the reconstructed full line
        is_command = False

        # Check for command names — match with or without trailing space
        for name in command_names:
            for check_line in (first_line, full_line):
                cl = check_line.lstrip("$ ")
                if cl == name or cl.startswith(name + " ") or cl.startswith(name + "\t"):
                    is_command = True
                    break
            if is_command:
                break

        # Also check for paths, pipelines, ./ prefix
        if not is_command:
            for check_line in (first_line, full_line):
                cl = check_line.lstrip("$ ")
                if (cl.startswith("/") or cl.startswith("./") or
                        "|" in cl or "&&" in cl):
                    is_command = True
                    break

        # Also check for $ prefix
        if not is_command and first_line.startswith("$ "):
            is_command = True

        # tool_code language tag is a strong signal — if the block contains
        # anything that looks remotely executable, treat it as a command
        if not is_command and is_tool_code_tag:
            # If tagged tool_code and has any content at all, it's meant to be executed
            non_comment = [l for l in lines if not l.startswith("#")]
            if non_comment:
                is_command = True
                logger.info(f"[fallback] tool_code tag detected, treating as command")

        # Skip blocks that look like documentation/code examples (not commands)
        # Only skip if NOT tagged as tool_code and has import/def/class patterns
        if is_command and not is_tool_code_tag:
            doc_patterns = ["import ", "from ", "def ", "class ", "function ", "const ", "let ", "var "]
            if any(first_line.startswith(p) for p in doc_patterns):
                is_command = False

        if is_command:
            # Build the command from the block content
            cmd_lines = [l.lstrip("$ ") for l in lines if l.strip() and not l.strip().startswith("#")]

            if not cmd_lines:
                continue

            # Detect if this is a single command split across lines (e.g., "python\n/path/to/script.py")
            # vs multiple sequential commands (e.g., "cd /tmp\npython script.py")
            # Heuristic: if the first line is just a bare command name, join with space (single command)
            # Otherwise join with && (sequential commands)
            first_cmd = cmd_lines[0].strip()
            if len(cmd_lines) > 1 and first_cmd in command_names:
                # Single command split across lines — join with space
                cmd = " ".join(cmd_lines)
            elif len(cmd_lines) > 1:
                # Multiple sequential commands — join with &&
                cmd = " && ".join(cmd_lines)
            else:
                cmd = cmd_lines[0]

            calls.append({"name": "run_command", "arguments": {"command": cmd}})
            logger.info(f"[fallback] Extracted shell command from code block: {cmd[:80]}")
            break  # Only extract one command block to avoid running unrelated examples

    return calls


def _parse_func_args(raw: str) -> dict | None:
    """Parse function-call style arguments like: command="ls -la", background=true

    Returns a dict or None if parsing fails.
    """
    if not raw:
        return {}

    # Try as JSON first (some models write run_command({"command": "ls"}))
    raw_stripped = raw.strip()
    if raw_stripped.startswith("{"):
        try:
            return json.loads(raw_stripped)
        except json.JSONDecodeError:
            pass

    # Parse key=value pairs
    args = {}
    # Match key="value" or key='value' or key=value patterns
    kv_pattern = re.compile(r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|(\S+))')
    for match in kv_pattern.finditer(raw):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else (
            match.group(3) if match.group(3) is not None else match.group(4)
        )
        if value is None:
            continue
        # Convert boolean strings
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        args[key] = value

    return args if args else None


# ============================================================================
# 7. OUTPUT STREAMING
# ============================================================================

async def _stream_process_output(managed: ManagedProcess, timeout: float):
    """Read process output in chunks, storing in managed.output_chunks.

    For foreground processes, this blocks until completion or timeout.
    For background processes, this runs as a fire-and-forget task.
    """
    process = managed.process
    if not process or not process.stdout:
        return

    start_time = time.time()

    async def read_stream(stream, prefix=""):
        """Read from a stream line by line and store chunks."""
        try:
            while True:
                try:
                    remaining = timeout - (time.time() - start_time)
                    if remaining <= 0:
                        return  # Overall timeout reached
                    line = await asyncio.wait_for(
                        stream.readline(),
                        timeout=min(STREAM_CHUNK_INTERVAL, remaining)
                    )
                except asyncio.TimeoutError:
                    if time.time() - start_time >= timeout:
                        return  # Overall timeout reached
                    continue

                if line:
                    text = line.decode(errors="replace")
                    managed.output_chunks.append(prefix + text)

                    # Check total output size
                    if len(managed.full_output) > MAX_OUTPUT_CHARS:
                        managed.output_chunks.append(
                            f"\n[Output truncated at {MAX_OUTPUT_CHARS} chars]\n"
                        )
                        return
                else:
                    return  # EOF
        except Exception as e:
            managed.output_chunks.append(f"\n[Stream error: {e}]\n")

    # Read stdout and stderr concurrently
    tasks = []
    if process.stdout:
        tasks.append(asyncio.create_task(read_stream(process.stdout)))
    if process.stderr:
        tasks.append(asyncio.create_task(read_stream(process.stderr, "stderr: ")))

    if tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            # Timeout — kill the process if it's foreground
            if not managed.is_background:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                managed.output_chunks.append(f"\n[Timed out after {timeout}s]\n")

    # Wait for process to finish
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass

    managed.return_code = process.returncode
    managed.finished_at = time.time()


# ============================================================================
# TOOL EXECUTION
# ============================================================================

async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "run_command":
            return await _run_command(
                arguments.get("command", ""),
                background=arguments.get("background", False),
                timeout=arguments.get("timeout"),
            )
        elif name == "read_file":
            return _read_file(arguments.get("path", ""))
        elif name == "write_file":
            return _write_file(arguments.get("path", ""), arguments.get("content", ""))
        elif name == "list_directory":
            return _list_directory(arguments.get("path", str(Path.home())))
        elif name == "check_process":
            return await _check_process(
                arguments.get("process_id", ""),
                arguments.get("action", "status"),
            )
        else:
            return f"Error: Unknown tool '{name}'"
    except Exception as e:
        logger.exception(f"Tool execution error: {name}")
        return f"Error executing {name}: {str(e)}"


async def _run_command(command: str, background: bool = False,
                       timeout: Optional[int] = None) -> str:
    """Execute a shell command with safety checks, process tracking, and streaming."""
    if not command.strip():
        return "Error: Empty command"

    blocked = _is_command_blocked(command)
    if blocked:
        return blocked

    effective_timeout = timeout or (BACKGROUND_TIMEOUT if background else COMMAND_TIMEOUT)
    logger.info(f"Executing command (bg={background}): {command[:200]}")

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
            env=_build_command_env(),
            start_new_session=(platform.system() != "Windows"),
        )

        # Register in process registry
        managed = await _registry.register(command, process, background=background)

        if background:
            # Start streaming in background, return immediately with process ID
            task = asyncio.create_task(_stream_process_output(managed, effective_timeout))

            def _bg_done(t):
                if not t.cancelled() and t.exception():
                    logger.error(f"Background stream error for {managed.process_id}: {t.exception()}")

            task.add_done_callback(_bg_done)
            return (
                f"Background process started.\n"
                f"Process ID: {managed.process_id}\n"
                f"Command: {command[:100]}\n"
                f"Timeout: {effective_timeout}s\n\n"
                f"Use check_process(process_id='{managed.process_id}') to see output and status.\n"
                f"Use check_process(process_id='{managed.process_id}', action='kill') to stop it."
            )

        # Foreground: stream output and wait for completion
        await _stream_process_output(managed, effective_timeout)

        output = managed.full_output
        if not output:
            output = f"(Command completed with exit code {managed.return_code})"

        if managed.return_code is not None and managed.return_code != 0:
            output = f"Exit code: {managed.return_code}\n{output}"

        # Truncate if too large
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n\n[Truncated — {len(output)} chars total]"

        return output

    except Exception as e:
        return f"Error running command: {str(e)}"


async def _check_process(process_id: str, action: str = "status") -> str:
    """Check on or kill a background process."""
    if not process_id:
        # List all tracked processes
        all_procs = _registry.list_all()
        if not all_procs:
            return "No tracked processes."
        lines = ["Tracked processes:"]
        for p in all_procs:
            status = "RUNNING" if p.is_running else f"DONE (exit {p.return_code})"
            lines.append(f"  {p.process_id}: [{status}] {p.command[:60]}  ({p.elapsed:.0f}s)")
        return "\n".join(lines)

    managed = _registry.get(process_id)
    if not managed:
        return f"Process {process_id} not found. It may have been cleaned up."

    if action == "kill":
        return await _registry.kill(process_id)

    # Status check — return new output since last check
    new_output = managed.consume_new_output()
    status = managed.status_summary()

    result = f"Process {process_id}: {status}\n"
    result += f"Command: {managed.command[:100]}\n"

    if new_output:
        result += f"\n--- New output ({len(new_output)} chars) ---\n"
        if len(new_output) > MAX_OUTPUT_CHARS:
            result += new_output[:MAX_OUTPUT_CHARS] + "\n[Truncated]"
        else:
            result += new_output
    elif managed.is_running:
        result += "\n(No new output since last check)"
    else:
        # Finished — show full output if LLM hasn't seen it
        full = managed.full_output
        if full:
            result += f"\n--- Full output ({len(full)} chars) ---\n"
            if len(full) > MAX_OUTPUT_CHARS:
                result += full[:MAX_OUTPUT_CHARS] + "\n[Truncated]"
            else:
                result += full
        else:
            result += "\n(No output produced)"

    return result


def _read_file(path: str) -> str:
    """Read a file's contents."""
    if not path:
        return "Error: No path specified"

    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"

    # Check file size before reading
    try:
        size = p.stat().st_size
    except OSError as e:
        return f"Error: Cannot stat file: {e}"

    if size > 5 * 1024 * 1024:  # 5MB limit
        return f"Error: File too large ({size / (1024*1024):.1f}MB). Max is 5MB for read_file."

    # Detect likely binary files
    binary_extensions = {
        '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
        '.mp3', '.mp4', '.wav', '.flac', '.ogg', '.avi', '.mkv', '.mov',
        '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar',
        '.exe', '.dll', '.so', '.dylib', '.o', '.pyc', '.pyo',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx',
        '.sqlite', '.db', '.sqlite3',
    }
    if p.suffix.lower() in binary_extensions:
        return f"Error: {p.name} appears to be a binary file ({p.suffix}). Use run_command to inspect it (e.g., file, hexdump, strings)."

    try:
        content = p.read_text(errors="replace")
        if len(content) > MAX_OUTPUT_CHARS:
            content = content[:MAX_OUTPUT_CHARS] + f"\n\n[Truncated — {len(content)} chars total]"
        return content
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error reading file: {str(e)}"


def _write_file(path: str, content: str) -> str:
    """Write content to a file with preflight validation."""
    if not path:
        return "Error: No path specified"

    blocked = _is_write_blocked(path)
    if blocked:
        return blocked

    if len(content) > 1024 * 1024:  # 1MB write limit
        return f"Error: Content too large ({len(content) / 1024:.0f}KB). Max write size is 1MB."

    p = Path(path).expanduser()

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

        result = f"Written {len(content)} bytes to {path}"

        # Run preflight validation and append warning if needed
        warning = _preflight_validate(path, content)
        if warning:
            result += f"\n\n{warning}"
            logger.warning(f"Preflight warning for {path}: content may be wrong language")

        return result
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


def _list_directory(path: str) -> str:
    """List directory contents."""
    if not path:
        path = str(Path.home())

    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: Directory not found: {path}"
    if not p.is_dir():
        return f"Error: Not a directory: {path}"

    try:
        entries = []
        for item in sorted(p.iterdir()):
            try:
                stat = item.stat()
                kind = "dir" if item.is_dir() else "file"
                size = stat.st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f}MB"
                entries.append(f"  {kind:4s}  {size_str:>8s}  {item.name}")
            except (PermissionError, OSError):
                entries.append(f"  ????          {item.name}")

        if not entries:
            return f"Directory is empty: {path}"

        result = f"Contents of {path}:\n" + "\n".join(entries)
        if len(result) > MAX_OUTPUT_CHARS:
            result = result[:MAX_OUTPUT_CHARS] + "\n\n[Truncated]"
        return result
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error listing directory: {str(e)}"
