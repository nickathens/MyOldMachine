"""Shared sudo helpers for installer scripts and runtime self-install code.

The bot stores its sudo password at ~/.sudo_pass (mode 0600) so unattended
provisioning, updates, and skill installs can run without interactive
prompts. This module centralizes:

  - get_sudo_password()  -- read the stored password
  - sudo_run()           -- run a single shell command with sudo, password
                            piped via stdin (never echoed, never logged)

Before this module existed, six separate copies of get_sudo_password()
and three near-identical sudo_run() variants drifted across the codebase.
A single change to the storage location (e.g., switching to a keyring)
required editing six files; in practice it just rotted in place.

Keep this module minimal. Callers that need to assemble multiple sudo
commands (e.g., apt-get update + apt-get install) should call sudo_run()
multiple times rather than baking domain logic in here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

SUDO_PASS_FILE = Path.home() / ".sudo_pass"


def get_sudo_password() -> Optional[str]:
    """Return the stored sudo password, or None if not set."""
    if SUDO_PASS_FILE.exists():
        return SUDO_PASS_FILE.read_text(encoding="utf-8").strip()
    return None


def _failed_result(stderr: str) -> subprocess.CompletedProcess:
    """Build a synthetic failure result so callers don't have to special-case
    timeouts and exceptions. Returncode 1, empty stdout, supplied stderr."""
    return subprocess.CompletedProcess(args="", returncode=1, stdout="", stderr=stderr)


def sudo_run(
    cmd: str,
    password: Optional[str] = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run a shell command with sudo. Password (if supplied) is piped via stdin.

    Returns a CompletedProcess (or a synthetic failure result on timeout /
    exception) with returncode, stdout, stderr.

    The shell=True invocation here is intentional: callers pass formed
    command strings (e.g., "apt-get install -y nodejs"). Never let
    untrusted user input flow into cmd; treat it like a static template.
    """
    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    try:
        return subprocess.run(
            full_cmd,
            shell=True,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _failed_result(f"Timed out after {timeout}s")
    except Exception as exc:
        return _failed_result(str(exc))
