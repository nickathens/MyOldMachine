"""
Self-Installing Dependency Manager.

Checks skill dependencies at runtime and installs missing ones automatically.
Uses deps.json manifests from each skill directory.
"""

import json
import logging
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cache of verified dependencies so we don't check every invocation
_verified_cache: set[str] = set()

# Resolve venv pip/python once
_VENV_PIP = Path(sys.executable).parent / "pip"
_VENV_PYTHON = sys.executable


def get_sudo_password() -> Optional[str]:
    """Read stored sudo password."""
    sudo_file = Path.home() / ".sudo_pass"
    if sudo_file.exists():
        return sudo_file.read_text().strip()
    return None


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _run(cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a shell command."""
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )


def _sudo_run(cmd: str, password: Optional[str] = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command with sudo, passing password safely via stdin."""
    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    return subprocess.run(
        full_cmd, shell=True, input=stdin_data,
        capture_output=True, text=True, timeout=timeout
    )


def check_binary(name: str) -> bool:
    """Check if a binary is available on PATH."""
    return shutil.which(name) is not None


def check_pip_package(package: str) -> bool:
    """Check if a pip package is installed in the current venv."""
    name = package.split(">=")[0].split("==")[0].split("<")[0].strip()
    pip = str(_VENV_PIP) if _VENV_PIP.exists() else "pip"
    result = _run(f"{pip} show {name} 2>/dev/null")
    return result.returncode == 0


def check_npm_package(package: str) -> bool:
    """Check if an npm package is installed globally."""
    result = _run(f"npm list -g {package} 2>/dev/null")
    return result.returncode == 0


def load_deps(skill_path: Path) -> Optional[dict]:
    """Load deps.json for a skill."""
    deps_file = skill_path / "deps.json"
    if not deps_file.exists():
        return None
    try:
        return json.loads(deps_file.read_text())
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load deps.json for {skill_path.name}: {e}")
        return None


def check_skill_deps(skill_path: Path) -> list[str]:
    """
    Check which dependencies are missing for a skill.
    Returns list of human-readable missing dep descriptions.
    """
    cache_key = skill_path.name
    if cache_key in _verified_cache:
        return []

    deps = load_deps(skill_path)
    if not deps:
        _verified_cache.add(cache_key)
        return []

    missing = []

    # Check custom verification commands first
    checks = deps.get("check", {})
    for name, cmd in checks.items():
        result = _run(cmd)
        if result.returncode != 0:
            missing.append(f"system:{name}")

    # System packages — only check those not already verified by custom checks
    checked_names = set(checks.keys())
    pkg_key = "brew" if _is_macos() else "apt"
    for pkg in deps.get(pkg_key, []):
        if pkg in checked_names or f"system:{pkg}" in missing:
            continue
        # Use dpkg (Linux) or brew list (macOS) for reliable package checking
        if _is_linux():
            result = _run(f"dpkg -l {pkg} 2>/dev/null | grep -q '^ii'")
        elif _is_macos():
            result = _run(f"brew list {pkg} 2>/dev/null")
        else:
            continue
        if result.returncode != 0:
            missing.append(f"system:{pkg}")

    # Pip packages
    for pkg in deps.get("pip", []):
        if not check_pip_package(pkg):
            missing.append(f"pip:{pkg}")

    # Npm packages
    for pkg in deps.get("npm", []):
        if not check_npm_package(pkg):
            missing.append(f"npm:{pkg}")

    if not missing:
        _verified_cache.add(cache_key)

    return missing


def install_missing(skill_path: Path, notify_fn=None) -> tuple[bool, list[str]]:
    """
    Install missing dependencies for a skill.
    Returns (success, list of installed items).

    notify_fn: optional async callback to inform user, signature: (message: str) -> None
    """
    deps = load_deps(skill_path)
    if not deps:
        return True, []

    missing = check_skill_deps(skill_path)
    if not missing:
        return True, []

    password = get_sudo_password()
    installed = []
    failed = []

    # System packages
    system_missing = [m.split(":", 1)[1] for m in missing if m.startswith("system:")]
    if system_missing:
        if _is_macos():
            pkgs = " ".join(system_missing)
            logger.info(f"Installing via brew: {pkgs}")
            result = _run(f"brew install {pkgs}")
        elif _is_linux():
            pkgs = " ".join(system_missing)
            logger.info(f"Installing via apt: {pkgs}")
            _sudo_run("apt-get update -qq", password)
            result = _sudo_run(f"apt-get install -y -qq {pkgs}", password)
        else:
            result = type("R", (), {"returncode": 1, "stderr": "Unsupported OS"})()

        if result.returncode == 0:
            installed.extend(system_missing)
        else:
            logger.error(f"Failed to install system packages: {result.stderr[:200]}")
            failed.extend(system_missing)

    # Pip packages — use venv pip explicitly
    pip_missing = [m.split(":", 1)[1] for m in missing if m.startswith("pip:")]
    if pip_missing:
        pkgs = " ".join(pip_missing)
        pip = str(_VENV_PIP) if _VENV_PIP.exists() else "pip"
        logger.info(f"Installing via pip ({pip}): {pkgs}")
        result = _run(f"{pip} install {pkgs}")
        if result.returncode == 0:
            installed.extend(pip_missing)
        else:
            logger.error(f"Failed to install pip packages: {result.stderr[:200]}")
            failed.extend(pip_missing)

    # Npm packages — on Linux, global npm install needs sudo
    npm_missing = [m.split(":", 1)[1] for m in missing if m.startswith("npm:")]
    if npm_missing:
        pkgs = " ".join(npm_missing)
        logger.info(f"Installing via npm: {pkgs}")
        if _is_linux():
            result = _sudo_run(f"npm install -g {pkgs}", password)
        else:
            result = _run(f"npm install -g {pkgs}")
        if result.returncode == 0:
            installed.extend(npm_missing)
        else:
            logger.error(f"Failed to install npm packages: {result.stderr[:200]}")
            failed.extend(npm_missing)

    if not failed:
        _verified_cache.add(skill_path.name)

    success = len(failed) == 0
    return success, installed


def clear_cache():
    """Clear the verified dependencies cache."""
    _verified_cache.clear()
