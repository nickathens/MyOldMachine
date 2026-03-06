"""
Tool Execution Layer for MyOldMachine.

Gives any LLM provider (OpenAI, Gemini, OpenRouter, Ollama) the ability to
execute commands on the machine. The LLM sends structured tool calls via its
native function-calling API, this module executes them, and returns results.

Tools:
  - run_command: Execute a shell command
  - read_file: Read a file's contents
  - write_file: Write content to a file
  - list_directory: List files in a directory

Safety:
  - Blocked command patterns (rm -rf /, format, etc.)
  - Max output size per tool call
  - Execution timeout per command
  - Max tool-call iterations per request
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --- Safety ---

MAX_OUTPUT_CHARS = 50_000
COMMAND_TIMEOUT = 120  # seconds
MAX_TOOL_ITERATIONS = 25  # max tool-call rounds per user message

# Commands that should never be executed
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/\s*$",          # rm -rf /
    r"rm\s+-rf\s+/\*",            # rm -rf /*
    r"mkfs\.",                     # format filesystem
    r"dd\s+if=.*of=/dev/[sh]d",   # overwrite disk
    r">\s*/dev/[sh]d",            # redirect to raw disk
    r"chmod\s+-R\s+777\s+/\s*$",  # chmod 777 /
    r":()\{.*\|.*&\s*\};:",       # fork bomb
]


def _is_command_blocked(command: str) -> str | None:
    """Return a reason string if the command is blocked, else None."""
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return f"Blocked: dangerous command pattern detected ({pattern})"
    return None


# --- Tool Definitions ---

# OpenAI-compatible format (used by OpenAI, OpenRouter, Ollama)
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Execute a shell command on this machine and return stdout+stderr. "
                "Use for installing packages, running scripts, checking system status, "
                "managing files, and any task that requires shell access. "
                "Commands run as the current user with sudo available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (e.g. 'ls -la /home', 'pip install requests')",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file. Creates the file if it doesn't exist, "
                "overwrites if it does. Creates parent directories as needed."
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
    },
    {
        "type": "function",
        "function": {
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
    },
]

# Gemini format
TOOLS_GEMINI = [
    {
        "functionDeclarations": [
            {
                "name": "run_command",
                "description": (
                    "Execute a shell command on this machine and return stdout+stderr. "
                    "Use for installing packages, running scripts, checking system status, "
                    "managing files, and any task that requires shell access. "
                    "Commands run as the current user with sudo available."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute",
                        },
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "read_file",
                "description": (
                    "Read the contents of a file. Returns the file text."
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
                    "overwrites if it does. Creates parent directories as needed."
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
                            "description": "Absolute path to the directory to list",
                        },
                    },
                    "required": [],
                },
            },
        ]
    }
]


# --- Tool Execution ---

async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "run_command":
            return await _run_command(arguments.get("command", ""))
        elif name == "read_file":
            return _read_file(arguments.get("path", ""))
        elif name == "write_file":
            return _write_file(arguments.get("path", ""), arguments.get("content", ""))
        elif name == "list_directory":
            return _list_directory(arguments.get("path", str(Path.home())))
        else:
            return f"Error: Unknown tool '{name}'"
    except Exception as e:
        logger.exception(f"Tool execution error: {name}")
        return f"Error executing {name}: {str(e)}"


async def _run_command(command: str) -> str:
    """Execute a shell command with safety checks."""
    if not command.strip():
        return "Error: Empty command"

    blocked = _is_command_blocked(command)
    if blocked:
        return blocked

    logger.info(f"Executing command: {command[:200]}")

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
            env={**os.environ, "HOME": str(Path.home())},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=COMMAND_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return f"Error: Command timed out after {COMMAND_TIMEOUT}s"

        output = ""
        if stdout:
            output += stdout.decode(errors="replace")
        if stderr:
            if output:
                output += "\n--- stderr ---\n"
            output += stderr.decode(errors="replace")

        if not output:
            output = f"(Command completed with exit code {process.returncode})"

        if process.returncode != 0:
            output = f"Exit code: {process.returncode}\n{output}"

        # Truncate if too large
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n\n[Truncated — {len(output)} chars total]"

        return output

    except Exception as e:
        return f"Error running command: {str(e)}"


def _read_file(path: str) -> str:
    """Read a file's contents."""
    if not path:
        return "Error: No path specified"

    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"

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
    """Write content to a file."""
    if not path:
        return "Error: No path specified"

    p = Path(path).expanduser()

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
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
