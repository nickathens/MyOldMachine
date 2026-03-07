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


def _detect_linux_pkg_manager() -> str:
    """Detect the Linux package manager at runtime."""
    for mgr, binary in [
        ("apt", "apt-get"),
        ("dnf", "dnf"),
        ("yum", "yum"),
        ("pacman", "pacman"),
        ("zypper", "zypper"),
        ("apk", "apk"),
    ]:
        if shutil.which(binary):
            return mgr
    return ""


# Lazily cached package manager
_linux_pkg_manager: Optional[str] = None


def _get_linux_pkg_manager() -> str:
    """Get cached Linux package manager."""
    global _linux_pkg_manager
    if _linux_pkg_manager is None:
        _linux_pkg_manager = _detect_linux_pkg_manager()
    return _linux_pkg_manager


# Maps apt package names to equivalents on other package managers.
# Only needed for packages where the name differs. If a package has the same
# name across managers, it doesn't need an entry here.
_APT_TO_PKG = {
    "dnf": {
        "python3-pip": "python3-pip",
        "ffmpeg": "ffmpeg-free",
        "openssh-server": "openssh-server",
        "tesseract-ocr": "tesseract",
        "poppler-utils": "poppler-utils",
        "espeak-ng": "espeak-ng",
    },
    "yum": {
        "tesseract-ocr": "tesseract",
        "poppler-utils": "poppler-utils",
        "espeak-ng": "espeak-ng",
    },
    "pacman": {
        "python3-pip": "python-pip",
        "ffmpeg": "ffmpeg",
        "tesseract-ocr": "tesseract",
        "poppler-utils": "poppler",
        "espeak-ng": "espeak-ng",
        "openssh-server": "openssh",
    },
    "zypper": {
        "tesseract-ocr": "tesseract-ocr",
        "poppler-utils": "poppler-tools",
        "espeak-ng": "espeak-ng",
        "openssh-server": "openssh",
    },
    "apk": {
        "python3-pip": "py3-pip",
        "ffmpeg": "ffmpeg",
        "tesseract-ocr": "tesseract-ocr",
        "poppler-utils": "poppler-utils",
        "espeak-ng": "espeak-ng",
        "openssh-server": "openssh",
    },
}


def _translate_pkg_name(apt_name: str, mgr: str) -> str:
    """Translate an apt package name to the equivalent for the given manager."""
    if mgr == "apt":
        return apt_name
    mapping = _APT_TO_PKG.get(mgr, {})
    return mapping.get(apt_name, apt_name)  # Fall back to same name


def _run(cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a shell command. Returns a result object even on timeout/error."""
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Command timed out after {timeout}s: {cmd}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"Timed out after {timeout}s"})()
    except Exception as e:
        logger.warning(f"Command error: {cmd}: {e}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()



def _sudo_run(cmd: str, password: Optional[str] = None, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command with sudo, passing password safely via stdin."""
    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    try:
        return subprocess.run(
            full_cmd, shell=True, input=stdin_data,
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"sudo command timed out after {timeout}s: {cmd}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"Timed out after {timeout}s"})()
    except Exception as e:
        logger.warning(f"sudo command error: {cmd}: {e}")
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()



def check_binary(name: str) -> bool:
    """Check if a binary is available on PATH."""
    return shutil.which(name) is not None


def check_pip_package(package: str) -> bool:
    """Check if a pip package is installed in the current venv."""
    name = package.split(">=")[0].split("==")[0].split("<")[0].strip()
    pip = str(_VENV_PIP) if _VENV_PIP.exists() else f"{_VENV_PYTHON} -m pip"
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

    # Check custom verification commands first.
    # Prepend the venv's bin dir so "python3" resolves to the venv Python,
    # ensuring module checks work for packages installed in the venv.
    venv_bin = str(Path(sys.executable).parent)
    checks = deps.get("check", {})
    for name, cmd in checks.items():
        result = _run(f"PATH={venv_bin}:$PATH {cmd}")
        if result.returncode != 0:
            missing.append(f"system:{name}")

    # System packages — only check those not already verified by custom checks.
    # deps.json can have "apt", "dnf", "pacman", "zypper", "apk", "brew" keys.
    # Fall back to "apt" key and translate package names if a manager-specific key
    # isn't present.
    checked_names = set(checks.keys())
    if _is_macos():
        pkg_key = "brew"
    elif _is_linux():
        mgr = _get_linux_pkg_manager()
        # Use manager-specific key if present, otherwise fall back to "apt"
        pkg_key = mgr if mgr in deps else "apt"
    else:
        pkg_key = "apt"
    for pkg in deps.get(pkg_key, []):
        if pkg in checked_names or f"system:{pkg}" in missing:
            continue
        if _is_macos():
            result = _run(f"brew list {pkg} 2>/dev/null")
        elif _is_linux():
            mgr = _get_linux_pkg_manager()
            translated = _translate_pkg_name(pkg, mgr)
            if mgr == "apt":
                result = _run(f"dpkg -l {translated} 2>/dev/null | grep -q '^ii'")
            elif mgr in ("dnf", "yum", "zypper"):
                result = _run(f"rpm -q {translated} 2>/dev/null")
            elif mgr == "pacman":
                result = _run(f"pacman -Q {translated} 2>/dev/null")
            elif mgr == "apk":
                result = _run(f"apk info -e {translated} 2>/dev/null")
            else:
                # No package manager — check if the binary exists directly
                result = type("R", (), {"returncode": 0 if shutil.which(pkg) else 1})()
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
            mgr = _get_linux_pkg_manager()
            translated = [_translate_pkg_name(p, mgr) for p in system_missing]
            pkgs = " ".join(translated)
            logger.info(f"Installing via {mgr}: {pkgs}")
            if mgr == "apt":
                _sudo_run("apt-get update -qq", password)
                result = _sudo_run(f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkgs}", password)
            elif mgr == "dnf":
                result = _sudo_run(f"dnf install -y {pkgs}", password)
            elif mgr == "yum":
                result = _sudo_run(f"yum install -y {pkgs}", password)
            elif mgr == "pacman":
                result = _sudo_run(f"pacman -S --noconfirm --needed {pkgs}", password)
            elif mgr == "zypper":
                result = _sudo_run(f"zypper install -y {pkgs}", password)
            elif mgr == "apk":
                result = _sudo_run(f"apk add {pkgs}", password)
            else:
                logger.warning(f"No supported package manager found ({mgr})")
                result = type("R", (), {"returncode": 1, "stderr": f"Unknown package manager: {mgr}"})()
        else:
            result = type("R", (), {"returncode": 1, "stderr": "Unsupported OS"})()

        if result.returncode == 0:
            installed.extend(system_missing)
        else:
            logger.error(f"Failed to install system packages: {result.stderr[:200]}")
            # Try Flatpak fallback for each failed system package (Linux only)
            if _is_linux():
                still_failed = []
                for pkg in system_missing:
                    try:
                        from install.compat import PACKAGES as COMPAT_PKGS, _install_via_flatpak
                        # Find a compat entry matching this package name or binary
                        compat_spec = COMPAT_PKGS.get(pkg)
                        if not compat_spec:
                            # Try matching by looking at system_packages values
                            for spec in COMPAT_PKGS.values():
                                pkg_names = spec.system_packages.values()
                                if pkg in pkg_names or any(pkg in pn for pn in pkg_names):
                                    compat_spec = spec
                                    break
                        if compat_spec and compat_spec.flatpak_id:
                            logger.info(f"Trying Flatpak for {pkg}: {compat_spec.flatpak_id}")
                            if _install_via_flatpak(compat_spec.flatpak_id, password):
                                installed.append(f"{pkg} (flatpak)")
                                continue
                    except ImportError:
                        pass
                    still_failed.append(pkg)
                failed.extend(still_failed)
            else:
                failed.extend(system_missing)

    # Pip packages — use venv pip explicitly
    pip_missing = [m.split(":", 1)[1] for m in missing if m.startswith("pip:")]
    if pip_missing:
        pkgs = " ".join(pip_missing)
        pip = str(_VENV_PIP) if _VENV_PIP.exists() else f"{_VENV_PYTHON} -m pip"
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

    # Post-install commands (e.g. "playwright install chromium")
    post_install = deps.get("post_install")
    if post_install and not failed:
        cmds = [post_install] if isinstance(post_install, str) else post_install
        venv_bin = str(Path(sys.executable).parent)
        env_prefix = f"PATH={venv_bin}:$PATH"
        for cmd in cmds:
            logger.info(f"Running post-install: {cmd}")
            # install-deps needs sudo on Linux (installs system libraries).
            # On non-apt Linux, playwright install-deps will fail because it
            # only supports Ubuntu/Debian. Treat this as a warning, not a failure —
            # the user can install Chromium deps manually with the bot's help.
            is_install_deps = "install-deps" in cmd
            if is_install_deps and _is_linux():
                result = _sudo_run(f"{env_prefix} {cmd}", password, timeout=300)
            else:
                result = _run(f"{env_prefix} {cmd}", timeout=300)
            if result.returncode != 0:
                if is_install_deps and _is_linux():
                    mgr = _get_linux_pkg_manager()
                    if mgr != "apt":
                        # Playwright install-deps only works on apt-based distros.
                        # Log as warning, not failure — Chromium may still work
                        # if the user has the right system libs already.
                        logger.warning(
                            f"Post-install '{cmd}' failed (expected on {mgr}-based systems). "
                            f"Browser skill may need manual system library installation."
                        )
                        installed.append(f"post:{cmd} (skipped — non-apt)")
                        continue
                logger.error(f"Post-install failed: {cmd}: {result.stderr[:200]}")
                failed.append(f"post:{cmd}")
            else:
                installed.append(f"post:{cmd}")

    if not failed:
        _verified_cache.add(skill_path.name)

    success = len(failed) == 0
    return success, installed


def clear_cache():
    """Clear the verified dependencies cache."""
    _verified_cache.clear()
