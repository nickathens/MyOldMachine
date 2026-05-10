"""Cross-platform borgbackup installer.

Provides install_borg() which:
  1. Returns immediately if borg is already on PATH.
  2. On Linux: installs the OS package (borgbackup / borg) via the detected
     package manager. Tries pip as a last-resort fallback.
  3. On macOS: installs via Homebrew. Tries pip as a last-resort fallback.

Designed to be called from the wizard's backup setup step. Always returns a
(success, message) tuple — never raises. The caller decides what to do with
a failure (typically: warn user, fall back to tarball backups).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

# Linux package names per package manager. Most distros call it
# "borgbackup", Arch and a couple of others use "borg".
LINUX_BORG_PACKAGES = {
    "apt": "borgbackup",
    "dnf": "borgbackup",
    "yum": "borgbackup",
    "pacman": "borg",
    "zypper": "borgbackup",
    "apk": "borgbackup",
}

LINUX_INSTALL_CMDS = {
    "apt": "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkg}",
    "dnf": "dnf install -y {pkg}",
    "yum": "yum install -y {pkg}",
    "pacman": "pacman -S --noconfirm --needed {pkg}",
    "zypper": "zypper install -y {pkg}",
    "apk": "apk add --no-cache {pkg}",
}


def have_borg() -> bool:
    return shutil.which("borg") is not None


def _detect_pkg_manager() -> Optional[str]:
    """Return the first package manager we can find on PATH."""
    for mgr in ("apt", "dnf", "yum", "pacman", "zypper", "apk"):
        if shutil.which(mgr):
            return mgr
    return None


def _try_pip_install_borg() -> Tuple[bool, str]:
    """Last-resort: pip install borgbackup into the bot's venv.

    The bot's venv is at <repo>/.venv/ — its bin dir is the same dir this
    script runs from in production, so a venv pip install puts borg on PATH
    for any process that activates the venv.
    """
    # Use the same Python as the bot's venv so the install lands there.
    pip_cmd = [sys.executable, "-m", "pip", "install", "--quiet", "borgbackup"]
    try:
        result = subprocess.run(
            pip_cmd, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "pip install borgbackup timed out after 10 min"
    except OSError as e:
        return False, f"pip install borgbackup failed: {e}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()
        last = stderr[-1] if stderr else "unknown error"
        return False, f"pip install borgbackup failed: {last}"

    if not have_borg():
        # pip succeeded but borg still isn't on PATH (rare — venv not active?)
        return False, "pip install borgbackup succeeded but borg still not on PATH"

    return True, "Installed borgbackup via pip"


def _install_via_brew() -> Tuple[bool, str]:
    brew = shutil.which("brew")
    if not brew:
        return False, "brew not found on PATH"
    try:
        result = subprocess.run(
            [brew, "install", "borgbackup"],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return False, "brew install borgbackup timed out"
    except OSError as e:
        return False, f"brew install borgbackup failed: {e}"

    if result.returncode != 0:
        return False, (
            f"brew install borgbackup failed (rc={result.returncode}): "
            f"{(result.stderr or '').strip()[:200]}"
        )

    if not have_borg():
        return False, "brew install borgbackup completed but borg not on PATH"

    return True, "Installed borgbackup via Homebrew"


def _install_via_linux_pkg(sudo_password: Optional[str]) -> Tuple[bool, str]:
    """Install via the detected package manager. Uses sudo_run from install.sudo."""
    mgr = _detect_pkg_manager()
    if not mgr:
        return False, "no supported Linux package manager found"

    pkg = LINUX_BORG_PACKAGES.get(mgr)
    cmd_template = LINUX_INSTALL_CMDS.get(mgr)
    if not pkg or not cmd_template:
        return False, f"no borg package mapping for package manager: {mgr}"

    # apt may need a refresh if its cache is stale (rare on a freshly-provisioned
    # MOM but harmless to call again).
    refresh_cmds = {
        "apt": "apt-get update -y -qq",
        "dnf": "dnf check-update -y --quiet || true",
        "pacman": "pacman -Sy --noconfirm",
        "zypper": "zypper refresh",
        "apk": "apk update",
    }

    # Defer importing install.sudo so this module is importable in tests
    # that don't have a sudo password configured.
    try:
        from install.sudo import sudo_run
    except ImportError:
        # Running outside the install context — try plain subprocess.
        return _install_via_plain_subprocess(mgr, pkg, cmd_template, refresh_cmds)

    # Refresh first if applicable.
    refresh = refresh_cmds.get(mgr)
    if refresh:
        sudo_run(refresh, password=sudo_password, timeout=300)

    install_cmd = cmd_template.format(pkg=pkg)
    result = sudo_run(install_cmd, password=sudo_password, timeout=600)
    if result.returncode != 0:
        stderr = (getattr(result, "stderr", "") or "").strip()[:200]
        return False, (
            f"package manager install failed (rc={result.returncode}): {stderr}"
        )

    if not have_borg():
        return False, f"{mgr} reported success but borg not on PATH"

    return True, f"Installed {pkg} via {mgr}"


def _install_via_plain_subprocess(mgr: str, pkg: str, cmd_template: str,
                                  refresh_cmds: dict) -> Tuple[bool, str]:
    """Fallback path for environments where install.sudo isn't available
    (e.g. unit tests). Skips refresh to keep things deterministic.
    """
    install_cmd = cmd_template.format(pkg=pkg)
    try:
        result = subprocess.run(
            install_cmd, shell=True, capture_output=True, text=True, timeout=600,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"plain install failed: {e}"
    if result.returncode != 0:
        return False, f"plain install failed (rc={result.returncode})"
    if not have_borg():
        return False, f"{mgr} reported success but borg not on PATH"
    return True, f"Installed {pkg} via {mgr} (plain)"


def install_borg(sudo_password: Optional[str] = None) -> Tuple[bool, str]:
    """Install borgbackup if not already present.

    Order of attempts:
      1. Already installed → success
      2. Linux: package manager → pip fallback
      3. macOS: Homebrew → pip fallback
    """
    if have_borg():
        return True, "borgbackup already installed"

    # If caller didn't supply a password, try to read the stored one (set
    # during wizard Step 7). Skip silently on failure — sudo_run will then
    # prompt or fail, the result is reported back to the user.
    if sudo_password is None:
        try:
            from install.sudo import get_sudo_password
            sudo_password = get_sudo_password()
        except ImportError:
            sudo_password = None

    if sys.platform == "darwin":
        ok, msg = _install_via_brew()
        if ok:
            return True, msg
        # Fall through to pip
        pip_ok, pip_msg = _try_pip_install_borg()
        if pip_ok:
            return True, pip_msg
        return False, f"brew failed ({msg}); pip fallback also failed ({pip_msg})"

    # Linux
    ok, msg = _install_via_linux_pkg(sudo_password)
    if ok:
        return True, msg
    pip_ok, pip_msg = _try_pip_install_borg()
    if pip_ok:
        return True, pip_msg
    return False, f"package manager failed ({msg}); pip fallback also failed ({pip_msg})"


def borg_version() -> Optional[str]:
    """Return borg's version string, or None if not installed."""
    if not have_borg():
        return None
    try:
        result = subprocess.run(
            ["borg", "--version"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip()


def passphrase_path_for(repo_dir: Path) -> Path:
    """Return the canonical on-disk path for the borg passphrase."""
    return repo_dir / "data" / "borg-passphrase"
