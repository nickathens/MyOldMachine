#!/usr/bin/env python3
"""
MyOldMachine — OS Detection & Compatibility Layer.

Detects OS type, version, architecture, and determines what features
are available on this specific machine. Every other install module
imports from here rather than doing its own detection.

macOS version map:
    10.11 = El Capitan      10.15 = Catalina       14.x = Sonoma
    10.12 = Sierra           11.x = Big Sur         15.x = Sequoia
    10.13 = High Sierra      12.x = Monterey
    10.14 = Mojave           13.x = Ventura

Key compatibility boundaries:
    < 10.13  Homebrew won't install at all
    < 10.14  Homebrew won't install (dropped support 2023)
    < 10.15  No zsh by default, 32-bit app support, Python 2 only
      10.15  Catalina — first no-32-bit, zsh default, read-only system volume
      11.0+  Big Sur — new version numbering (11, 12, 13...), universal binaries
      12.0+  Monterey — Shortcuts, some SystemPreferences → System Settings migration
      13.0+  Ventura — System Settings fully replaces System Preferences
      14.0+  Sonoma — widgets on desktop, game mode
      15.0+  Sequoia — iPhone mirroring, window tiling
"""

import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# macOS version names for user-friendly display
MACOS_NAMES = {
    "10.11": "El Capitan",
    "10.12": "Sierra",
    "10.13": "High Sierra",
    "10.14": "Mojave",
    "10.15": "Catalina",
    "11": "Big Sur",
    "12": "Monterey",
    "13": "Ventura",
    "14": "Sonoma",
    "15": "Sequoia",
}


@dataclass
class OSInfo:
    """Complete OS detection result used by all install modules."""

    # Core identity
    os_type: str = "unknown"          # "linux", "macos", "windows"
    distro: str = ""                  # "ubuntu", "debian", etc. (Linux only)
    version: str = ""                 # "24.04", "15.3", etc.
    version_major: int = 0            # 15, 24, etc.
    version_minor: int = 0            # 3, 04, etc.
    version_name: str = ""            # "Sequoia", "Noble Numbat", etc.
    arch: str = ""                    # "x86_64", "arm64"
    hostname: str = ""

    # Package manager
    package_manager: str = ""         # "apt", "brew"
    brew_path: str = ""               # Full path to brew binary (macOS)

    # Capabilities (determined by version)
    has_homebrew_support: bool = True  # macOS < 10.14 can't use modern Homebrew
    has_sip: bool = True              # System Integrity Protection (macOS 10.11+)
    has_apfs: bool = False            # APFS filesystem (macOS 10.13+)
    has_zsh_default: bool = False     # zsh is default shell (macOS 10.15+)
    has_system_settings: bool = False # System Settings vs System Preferences (macOS 13+)
    has_universal_binaries: bool = False  # Apple Silicon support (macOS 11+)
    is_apple_silicon: bool = False    # arm64 Mac
    has_python3_builtin: bool = False # macOS ships with python3

    # Xcode CLT (macOS)
    has_xcode_clt: bool = False           # Xcode Command Line Tools installed
    xcode_clt_version: str = ""           # e.g. "15.1.0"

    # Disk space
    disk_free_gb: float = 0               # Free disk space in GB

    # Constraints
    min_homebrew_version: str = "10.14"
    warnings: list = field(default_factory=list)
    blockers: list = field(default_factory=list)  # Fatal issues that prevent install

    def __post_init__(self):
        self.warnings = list(self.warnings)
        self.blockers = list(self.blockers)

    @property
    def display_name(self) -> str:
        """Human-readable OS string, e.g. 'macOS 15.3 Sequoia (arm64)'."""
        if self.os_type == "macos":
            name = f"macOS {self.version}"
            if self.version_name:
                name += f" {self.version_name}"
            name += f" ({self.arch})"
            return name
        elif self.os_type == "linux":
            name = f"{self.distro.title()} {self.version}"
            if self.version_name:
                name += f" ({self.version_name})"
            name += f" [{self.arch}]"
            return name
        return f"{self.os_type} {self.version}"

    @property
    def is_old_mac(self) -> bool:
        """True if this Mac is old enough to need special handling (pre-Catalina)."""
        if self.os_type != "macos":
            return False
        return self._mac_version_lt(10, 15)

    @property
    def is_very_old_mac(self) -> bool:
        """True if this Mac can't use modern Homebrew (pre-Mojave)."""
        if self.os_type != "macos":
            return False
        return self._mac_version_lt(10, 14)

    def _mac_version_lt(self, major: int, minor: int = 0) -> bool:
        """Compare macOS version. Handles both 10.x and 11+ numbering."""
        if self.version_major < major:
            return True
        if self.version_major == major and self.version_minor < minor:
            return True
        return False

    def _mac_version_gte(self, major: int, minor: int = 0) -> bool:
        if self.version_major > major:
            return True
        if self.version_major == major and self.version_minor >= minor:
            return True
        return False


def detect() -> OSInfo:
    """
    Detect the current OS and return a fully populated OSInfo.
    This is the single entry point — all other modules call this.
    """
    system = platform.system()
    info = OSInfo(
        arch=platform.machine(),
        hostname=platform.node(),
    )

    if system == "Darwin":
        _detect_macos(info)
    elif system == "Linux":
        _detect_linux(info)
    elif system == "Windows":
        info.os_type = "windows"
        info.blockers.append("Windows support is not yet implemented.")
    else:
        info.os_type = "unknown"
        info.blockers.append(f"Unsupported operating system: {system}")

    return info


def _detect_macos(info: OSInfo):
    """Populate OSInfo for macOS."""
    info.os_type = "macos"
    info.package_manager = "brew"

    # Get macOS version from sw_vers (most reliable)
    try:
        result = subprocess.run(
            ["sw_vers", "-productVersion"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info.version = result.stdout.strip()  # e.g. "15.3.1" or "10.14.6"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Fallback to platform.mac_ver()
        ver = platform.mac_ver()[0]
        if ver:
            info.version = ver

    # Parse version components
    if info.version:
        parts = info.version.split(".")
        try:
            info.version_major = int(parts[0])
            info.version_minor = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            pass

    # Look up version name
    if info.version_major >= 11:
        info.version_name = MACOS_NAMES.get(str(info.version_major), "")
    else:
        key = f"{info.version_major}.{info.version_minor}"
        info.version_name = MACOS_NAMES.get(key, "")

    # Architecture details
    info.is_apple_silicon = info.arch == "arm64"

    # Set capability flags based on version
    _set_macos_capabilities(info)

    # Find Homebrew (if already installed)
    info.brew_path = _find_brew()

    # Check Xcode CLT
    _check_xcode_clt(info)

    # Check disk space
    _check_disk_space(info)

    # Check for blockers and warnings
    _check_macos_compatibility(info)


def _set_macos_capabilities(info: OSInfo):
    """Set boolean capability flags based on macOS version."""
    # SIP: introduced in 10.11 (El Capitan)
    info.has_sip = info._mac_version_gte(10, 11)

    # APFS: default from 10.13 (High Sierra)
    info.has_apfs = info._mac_version_gte(10, 13)

    # Homebrew: requires 10.14+ (Mojave) as of 2023
    info.has_homebrew_support = info._mac_version_gte(10, 14)

    # zsh default shell: 10.15 (Catalina)
    info.has_zsh_default = info._mac_version_gte(10, 15)

    # Universal binaries / Apple Silicon support: 11.0 (Big Sur)
    info.has_universal_binaries = info._mac_version_gte(11)

    # System Settings (not System Preferences): 13.0 (Ventura)
    info.has_system_settings = info._mac_version_gte(13)

    # Python 3 bundled: 12.3+ removed Python 2, Xcode CLT provides python3
    # But on older macOS, python3 might not exist at all without manual install
    info.has_python3_builtin = info._mac_version_gte(12, 3)


def _check_macos_compatibility(info: OSInfo):
    """Add warnings and blockers based on macOS version."""

    # BLOCKER: pre-High Sierra (10.13) — too old for anything
    if info._mac_version_lt(10, 13):
        info.blockers.append(
            f"macOS {info.version} ({info.version_name}) is too old. "
            f"Homebrew and modern Python require at least macOS 10.13 (High Sierra). "
            f"Consider installing Linux on this machine instead."
        )
        return  # No point checking further

    # BLOCKER: pre-Mojave (10.14) — Homebrew dropped support
    if info._mac_version_lt(10, 14):
        info.blockers.append(
            f"macOS {info.version} ({info.version_name}) is not supported by Homebrew. "
            f"Homebrew requires macOS 10.14 (Mojave) or later. "
            f"Options: (1) upgrade macOS if hardware supports it, "
            f"(2) install Linux on this machine, "
            f"(3) install dependencies manually via MacPorts or source compilation."
        )
        return

    # WARNING: pre-Catalina (10.15) — works but with caveats
    if info._mac_version_lt(10, 15):
        info.warnings.append(
            f"macOS {info.version} ({info.version_name}) is old but supported. "
            f"Some features may not work perfectly. Default shell is bash, not zsh."
        )

    # WARNING: pre-Big Sur (11.0) — Intel only, 10.x versioning
    if info._mac_version_lt(11):
        info.warnings.append(
            "Running macOS 10.x (pre-Big Sur). This is an Intel Mac. "
            "All features should work but some modern tools may drop support soon."
        )

    # WARNING: Catalina (10.15) read-only system volume
    if info.version_major == 10 and info.version_minor == 15:
        info.warnings.append(
            "macOS Catalina uses a read-only system volume. "
            "Some system-level configurations may need alternative approaches."
        )


def _check_xcode_clt(info: OSInfo):
    """Check if Xcode Command Line Tools are installed (macOS)."""
    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            info.has_xcode_clt = True
            # Try to get version
            ver_result = subprocess.run(
                ["pkgutil", "--pkg-info", "com.apple.pkg.CLTools_Executables"],
                capture_output=True, text=True, timeout=5
            )
            if ver_result.returncode == 0:
                for line in ver_result.stdout.splitlines():
                    if line.startswith("version:"):
                        info.xcode_clt_version = line.split(":", 1)[1].strip()
                        break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not info.has_xcode_clt:
        info.warnings.append(
            "Xcode Command Line Tools not installed. Required for git and compilation. "
            "The installer will attempt: xcode-select --install"
        )


def _check_disk_space(info: OSInfo):
    """Check available disk space."""
    import os as _os
    try:
        st = _os.statvfs("/")
        info.disk_free_gb = round((st.f_bavail * st.f_frsize) / (1024**3), 1)
    except Exception:
        return

    if info.disk_free_gb < 2:
        info.blockers.append(
            f"Only {info.disk_free_gb} GB free disk space. "
            f"MyOldMachine needs at least 2 GB free for dependencies."
        )
    elif info.disk_free_gb < 5:
        info.warnings.append(
            f"Low disk space: {info.disk_free_gb} GB free. "
            f"Some skills may not install properly. 5+ GB recommended."
        )


def _detect_linux(info: OSInfo):
    """Populate OSInfo for Linux."""
    info.os_type = "linux"
    info.package_manager = "apt"

    # Read /etc/os-release
    os_release = Path("/etc/os-release")
    if os_release.exists():
        data = {}
        for line in os_release.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key] = value.strip('"')

        info.distro = data.get("ID", "unknown")
        info.version = data.get("VERSION_ID", "")
        info.version_name = data.get("VERSION_CODENAME", data.get("PRETTY_NAME", ""))

        if info.version:
            parts = info.version.split(".")
            try:
                info.version_major = int(parts[0])
                info.version_minor = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                pass

        # Check if this is a supported distro
        id_like = data.get("ID_LIKE", "")
        if info.distro not in ("ubuntu", "debian") and "debian" not in id_like:
            info.blockers.append(
                f"Unsupported Linux distribution: {info.distro}. "
                f"Only Ubuntu/Debian (and derivatives like Linux Mint, Pop!_OS) are supported."
            )
    else:
        info.warnings.append(
            "Could not read /etc/os-release. Assuming Debian-based system."
        )
        info.distro = "unknown"

    # Check if apt is actually available
    try:
        result = subprocess.run(
            ["which", "apt-get"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            info.blockers.append(
                "apt-get not found. This installer requires a Debian-based system with apt."
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check disk space (same function used for macOS)
    _check_disk_space(info)


def _find_brew() -> str:
    """Find Homebrew binary path."""
    for path in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]:
        if Path(path).exists():
            return path
    try:
        result = subprocess.run(
            ["which", "brew"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def print_detection_summary(info: OSInfo):
    """Print a formatted summary of OS detection results."""
    BLUE = "\033[0;34m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    NC = "\033[0m"

    print(f"{BLUE}[OS]{NC} {info.display_name}")

    if info.os_type == "macos":
        chip = "Apple Silicon" if info.is_apple_silicon else "Intel"
        print(f"{BLUE}[OS]{NC} Chip: {chip}")
        if info.brew_path:
            print(f"{GREEN}[OK]{NC} Homebrew found: {info.brew_path}")
        elif info.has_homebrew_support:
            print(f"{BLUE}[OS]{NC} Homebrew not installed (will install)")
        else:
            print(f"{RED}[OS]{NC} Homebrew not supported on this version")

        # Xcode CLT
        if info.has_xcode_clt:
            ver_str = f" ({info.xcode_clt_version})" if info.xcode_clt_version else ""
            print(f"{GREEN}[OK]{NC} Xcode Command Line Tools{ver_str}")
        else:
            print(f"{YELLOW}[WARN]{NC} Xcode Command Line Tools not installed")

        # Disk space
        if info.disk_free_gb > 0:
            color = GREEN if info.disk_free_gb >= 5 else YELLOW if info.disk_free_gb >= 2 else RED
            print(f"{color}[OS]{NC} Disk: {info.disk_free_gb} GB free")

        # Show capability summary for Macs
        caps = []
        if info.has_sip:
            caps.append("SIP")
        if info.has_apfs:
            caps.append("APFS")
        if info.has_zsh_default:
            caps.append("zsh")
        else:
            caps.append("bash")
        if info.has_system_settings:
            caps.append("System Settings")
        else:
            caps.append("System Preferences")
        if caps:
            print(f"{BLUE}[OS]{NC} Features: {', '.join(caps)}")

    for w in info.warnings:
        print(f"{YELLOW}[WARN]{NC} {w}")
    for b in info.blockers:
        print(f"{RED}[BLOCKED]{NC} {b}")


# Quick CLI test
if __name__ == "__main__":
    info = detect()
    print_detection_summary(info)
    if info.blockers:
        print(f"\n{len(info.blockers)} blocker(s) found — install cannot proceed.")
        sys.exit(1)
    elif info.warnings:
        print(f"\n{len(info.warnings)} warning(s) — install can proceed with caveats.")
    else:
        print("\nAll clear — no compatibility issues detected.")
