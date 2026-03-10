#!/usr/bin/env python3
"""
MyOldMachine — Software Compatibility Layer.

Knows the version requirements and fallback install strategies for every
major package. When the system package manager can't install something
(wrong version, missing from repos, too old), this module provides
alternative install paths: Flatpak, AppImage, direct download, or
older compatible versions.

Design principles:
- Try the system package manager first (fastest, most integrated)
- Fall back to Flatpak on Linux (version-independent, sandboxed)
- Fall back to direct binary downloads when possible
- Never force a version that won't run on the hardware
- Report clearly what was installed and what was skipped
"""

import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class InstallResult:
    """Result of a compatibility-aware install attempt."""
    package: str
    installed: bool
    method: str = ""         # "system", "flatpak", "appimage", "direct", "already"
    version: str = ""        # Installed version string
    skipped_reason: str = "" # Why it was skipped (if not installed)
    notes: str = ""          # Extra info for the user


@dataclass
class PackageSpec:
    """Compatibility specification for a package."""
    name: str
    binary: str                          # Binary name to check (e.g. "blender")
    display_name: str                    # Human-friendly name (e.g. "Blender")

    # System package names per manager
    system_packages: dict = field(default_factory=dict)  # {mgr: pkg_name}

    # Flatpak fallback (Linux only)
    flatpak_id: str = ""                 # e.g. "org.blender.Blender"

    # AppImage fallback (Linux only, x86_64 primarily)
    appimage_url: str = ""               # Direct URL to download AppImage
    appimage_name: str = ""              # Filename for /usr/local/bin/

    # Brew cask name (macOS)
    brew_cask: str = ""
    brew_formula: str = ""

    # Minimum requirements
    min_ram_gb: float = 0                # Minimum RAM in GB
    min_glibc: str = ""                  # Minimum glibc version (Linux)

    # Version command for detection
    version_cmd: str = ""                # e.g. "blender --version"
    version_pattern: str = ""            # Regex to extract version from output


# ────────────────────────────────────────────────────────────────────
# Package compatibility database
# ────────────────────────────────────────────────────────────────────

PACKAGES = {
    "blender": PackageSpec(
        name="blender",
        binary="blender",
        display_name="Blender",
        system_packages={
            "apt": "blender", "dnf": "blender", "pacman": "blender",
            "zypper": "blender",
        },
        flatpak_id="org.blender.Blender",
        brew_cask="blender",
        min_ram_gb=4,
        version_cmd="blender -b --version",
        version_pattern=r"Blender\s+([\d.]+)",
    ),
    "gimp": PackageSpec(
        name="gimp",
        binary="gimp",
        display_name="GIMP",
        system_packages={
            "apt": "gimp", "dnf": "gimp", "pacman": "gimp",
            "zypper": "gimp", "apk": "gimp",
        },
        flatpak_id="org.gimp.GIMP",
        brew_cask="gimp",
        min_ram_gb=2,
        version_cmd="gimp --version",
        version_pattern=r"([\d.]+)",
    ),
    "inkscape": PackageSpec(
        name="inkscape",
        binary="inkscape",
        display_name="Inkscape",
        system_packages={
            "apt": "inkscape", "dnf": "inkscape", "pacman": "inkscape",
            "zypper": "inkscape", "apk": "inkscape",
        },
        flatpak_id="org.inkscape.Inkscape",
        brew_cask="inkscape",
        min_ram_gb=1,
        version_cmd="inkscape --version",
        version_pattern=r"Inkscape\s+([\d.]+)",
    ),
    "libreoffice": PackageSpec(
        name="libreoffice",
        binary="soffice",
        display_name="LibreOffice",
        system_packages={
            "apt": "libreoffice-calc libreoffice-writer libreoffice-impress",
            "dnf": "libreoffice-calc libreoffice-writer libreoffice-impress",
            "yum": "libreoffice-calc libreoffice-writer libreoffice-impress",
            "pacman": "libreoffice-fresh",
            "zypper": "libreoffice-calc libreoffice-writer libreoffice-impress",
        },
        flatpak_id="org.libreoffice.LibreOffice",
        brew_cask="libreoffice",
        min_ram_gb=1,
        version_cmd="soffice --version",
        version_pattern=r"([\d.]+)",
    ),
    "chromium": PackageSpec(
        name="chromium",
        binary="chromium-browser",  # Also check "chromium" in code
        display_name="Chromium",
        system_packages={
            "apt": "chromium-browser", "dnf": "chromium", "pacman": "chromium",
            "zypper": "chromium", "apk": "chromium",
        },
        flatpak_id="org.chromium.Chromium",
        brew_cask="chromium",
        min_ram_gb=2,
    ),
    "imagemagick": PackageSpec(
        name="imagemagick",
        binary="convert",
        display_name="ImageMagick",
        system_packages={
            "apt": "imagemagick", "dnf": "ImageMagick", "yum": "ImageMagick",
            "pacman": "imagemagick", "zypper": "ImageMagick", "apk": "imagemagick",
        },
        brew_formula="imagemagick",
        version_cmd="convert --version",
        version_pattern=r"ImageMagick\s+([\d.]+)",
    ),
    "rclone": PackageSpec(
        name="rclone",
        binary="rclone",
        display_name="rclone",
        system_packages={
            "apt": "rclone", "dnf": "rclone", "pacman": "rclone",
            "zypper": "rclone",
        },
        brew_formula="rclone",
        version_cmd="rclone version",
        version_pattern=r"rclone\s+v([\d.]+)",
    ),
    "ffmpeg": PackageSpec(
        name="ffmpeg",
        binary="ffmpeg",
        display_name="FFmpeg",
        system_packages={
            "apt": "ffmpeg", "dnf": "ffmpeg-free", "pacman": "ffmpeg",
            "zypper": "ffmpeg", "apk": "ffmpeg",
        },
        brew_formula="ffmpeg",
        version_cmd="ffmpeg -version",
        version_pattern=r"ffmpeg version\s+([\d.]+)",
    ),
}


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _run(cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a shell command quietly."""
    try:
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"Timed out after {timeout}s"})()
    except Exception as e:
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()


def _sudo_run(cmd: str, password: Optional[str] = None, timeout: int = 300):
    """Run with sudo."""
    full_cmd = f"sudo -S {cmd}" if password else f"sudo {cmd}"
    stdin_data = (password + "\n") if password else None
    try:
        return subprocess.run(
            full_cmd, shell=True, input=stdin_data,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "Timed out"})()
    except Exception as e:
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": str(e)})()


def _get_ram_gb() -> float:
    """Get total RAM in GB."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return round(int(result.stdout.strip()) / (1024**3), 1)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
    except Exception:
        pass
    return 0


def _get_glibc_version() -> str:
    """Get glibc version on Linux. Returns '' on non-Linux or failure."""
    if platform.system() != "Linux":
        return ""
    try:
        result = subprocess.run(
            ["ldd", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout or result.stderr or ""
        import re
        match = re.search(r"(\d+\.\d+)", output)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def _check_flatpak_available() -> bool:
    """Check if Flatpak is installed."""
    return shutil.which("flatpak") is not None


def _install_flatpak(password: Optional[str] = None) -> bool:
    """Try to install Flatpak itself."""
    # Detect package manager and install flatpak.
    # Note: each command runs separately under sudo to avoid && breaking
    # the sudo password pipe (sudo -S only applies to the first command
    # in a && chain).
    for mgr, binary, update_cmd, install_cmd in [
        ("apt", "apt-get", "apt-get update -qq",
         "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq flatpak"),
        ("dnf", "dnf", None, "dnf install -y flatpak"),
        ("pacman", "pacman", None, "pacman -S --noconfirm --needed flatpak"),
        ("zypper", "zypper", None, "zypper install -y flatpak"),
    ]:
        if shutil.which(binary):
            if update_cmd:
                _sudo_run(update_cmd, password, timeout=60)
            result = _sudo_run(install_cmd, password, timeout=120)
            if result.returncode == 0 and shutil.which("flatpak"):
                # Add Flathub remote
                _sudo_run(
                    "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo",
                    password, timeout=60,
                )
                return True
            break
    return False


def _install_via_flatpak(flatpak_id: str, password: Optional[str] = None) -> bool:
    """Install a package via Flatpak."""
    if not flatpak_id:
        return False

    if not _check_flatpak_available():
        logger.info("Flatpak not available, attempting to install it...")
        if not _install_flatpak(password):
            logger.warning("Could not install Flatpak")
            return False

    # Ensure Flathub is added
    _sudo_run(
        "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo",
        password, timeout=60,
    )

    # Install the package
    result = _sudo_run(
        f"flatpak install -y --noninteractive flathub {flatpak_id}",
        password, timeout=600,
    )
    return result.returncode == 0


def _get_installed_version(spec: PackageSpec) -> str:
    """Get the version of an installed package. Returns '' if not found."""
    if not spec.version_cmd:
        return ""
    result = _run(spec.version_cmd, timeout=10)
    output = (result.stdout or "") + (result.stderr or "")
    if spec.version_pattern and output:
        import re
        match = re.search(spec.version_pattern, output)
        if match:
            return match.group(1)
    return ""


def _is_binary_available(spec: PackageSpec) -> bool:
    """Check if the binary for a package is available (system or flatpak)."""
    if shutil.which(spec.binary):
        return True
    # Some packages have alternative binary names
    if spec.name == "chromium" and shutil.which("chromium"):
        return True
    # Check if installed via Flatpak (creates a wrapper)
    if spec.flatpak_id and _check_flatpak_available():
        result = _run(f"flatpak list --app --columns=application 2>/dev/null | grep -q '{spec.flatpak_id}'")
        if result.returncode == 0:
            return True
    return False


# ────────────────────────────────────────────────────────────────────
# Main installation logic
# ────────────────────────────────────────────────────────────────────

def smart_install(
    package_name: str,
    package_manager: str,
    password: Optional[str] = None,
    install_cmd_tpl: str = "",
    dry_run: bool = False,
) -> InstallResult:
    """
    Install a package using the best available method for this machine.

    Strategy:
    1. Check if already installed → skip
    2. Check hardware requirements → skip if insufficient
    3. Try system package manager
    4. Try Flatpak (Linux only, for GUI apps)
    5. Report result with details

    Args:
        package_name: Key from PACKAGES dict (e.g. "blender")
        package_manager: System package manager (e.g. "apt", "dnf", "brew")
        password: Sudo password for installations
        install_cmd_tpl: Install command template with {pkgs} placeholder
        dry_run: If True, don't actually install anything

    Returns:
        InstallResult with installation outcome
    """
    spec = PACKAGES.get(package_name)
    if not spec:
        return InstallResult(
            package=package_name, installed=False,
            skipped_reason=f"Unknown package: {package_name}",
        )

    result = InstallResult(package=package_name, installed=False)

    # Step 1: Already installed?
    if _is_binary_available(spec):
        result.installed = True
        result.method = "already"
        result.version = _get_installed_version(spec)
        return result

    # Step 2: Check hardware requirements
    if spec.min_ram_gb > 0:
        ram = _get_ram_gb()
        if ram > 0 and ram < spec.min_ram_gb:
            result.skipped_reason = (
                f"{spec.display_name} needs {spec.min_ram_gb}GB RAM, "
                f"this machine has {ram}GB. "
                f"It may run but will be very slow."
            )
            result.notes = (
                f"You can try installing {spec.display_name} manually later. "
                f"The bot can help: just ask it to install {spec.display_name}."
            )
            # Don't skip — try anyway, just warn. Low RAM doesn't prevent install.
            logger.warning(result.skipped_reason)

    if dry_run:
        result.notes = f"[DRY RUN] Would try: system pkg → flatpak fallback"
        return result

    is_linux = platform.system() == "Linux"
    is_macos = platform.system() == "Darwin"

    # Step 3: Try system package manager
    if is_linux and install_cmd_tpl:
        pkg_name = spec.system_packages.get(package_manager, "")
        if pkg_name:
            # For packages with multiple sub-packages (like libreoffice)
            logger.info(f"Installing {spec.display_name} via {package_manager}: {pkg_name}")
            r = _sudo_run(install_cmd_tpl.format(pkgs=pkg_name), password, timeout=600)
            if r.returncode == 0 and _is_binary_available(spec):
                result.installed = True
                result.method = "system"
                result.version = _get_installed_version(spec)
                return result
            else:
                logger.warning(
                    f"System install of {spec.display_name} failed: "
                    f"{(r.stderr or '')[:200]}"
                )

    elif is_macos:
        brew = _find_brew()
        if brew:
            if spec.brew_cask:
                logger.info(f"Installing {spec.display_name} via brew cask: {spec.brew_cask}")
                r = _run(f"{brew} install --cask {spec.brew_cask} 2>&1", timeout=600)
                combined = (r.stdout or "") + (r.stderr or "")
                if r.returncode == 0 or "already installed" in combined:
                    if _is_binary_available(spec) or Path(f"/Applications/{spec.display_name}.app").exists():
                        result.installed = True
                        result.method = "system"
                        result.version = _get_installed_version(spec)
                        return result
                else:
                    logger.warning(f"Brew cask install of {spec.display_name} failed: {combined[:200]}")

            elif spec.brew_formula:
                logger.info(f"Installing {spec.display_name} via brew formula: {spec.brew_formula}")
                r = _run(f"{brew} install {spec.brew_formula} 2>&1", timeout=600)
                combined = (r.stdout or "") + (r.stderr or "")
                if r.returncode == 0 or "already installed" in combined:
                    if _is_binary_available(spec):
                        result.installed = True
                        result.method = "system"
                        result.version = _get_installed_version(spec)
                        return result
                else:
                    logger.warning(f"Brew formula install of {spec.display_name} failed: {combined[:200]}")

    # Step 4a: macOS direct download fallback
    # When brew cask fails (e.g. app requires Big Sur+ but we're on Catalina),
    # try downloading an older compatible version directly.
    if is_macos and not result.installed:
        if _install_macos_direct(spec, password):
            result.installed = True
            result.method = "direct"
            result.version = _get_installed_version(spec)
            return result

    # Step 4b: Flatpak fallback (Linux only, GUI apps)
    if is_linux and spec.flatpak_id:
        logger.info(f"Trying Flatpak for {spec.display_name}: {spec.flatpak_id}")
        if _install_via_flatpak(spec.flatpak_id, password):
            result.installed = True
            result.method = "flatpak"
            result.notes = (
                f"Installed via Flatpak. Run with: flatpak run {spec.flatpak_id}\n"
                f"The bot knows how to use Flatpak-installed apps."
            )
            return result
        else:
            logger.warning(f"Flatpak install of {spec.display_name} also failed")

    # Step 5: Nothing worked
    result.skipped_reason = (
        f"Could not install {spec.display_name} via system packages"
    )
    if is_linux and spec.flatpak_id:
        result.skipped_reason += " or Flatpak"
    result.skipped_reason += "."
    result.notes = (
        f"Ask the bot to help install {spec.display_name} after setup — "
        f"it can troubleshoot the specific error on your machine."
    )

    return result


def smart_install_batch(
    packages: list[str],
    package_manager: str,
    password: Optional[str] = None,
    install_cmd_tpl: str = "",
    dry_run: bool = False,
) -> list[InstallResult]:
    """Install a list of packages using compatibility-aware logic."""
    results = []
    for pkg in packages:
        r = smart_install(pkg, package_manager, password, install_cmd_tpl, dry_run)
        results.append(r)
    return results


def print_install_summary(results: list[InstallResult]):
    """Print a summary of installation results."""
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"

    installed = [r for r in results if r.installed]
    skipped = [r for r in results if not r.installed]

    if installed:
        print(f"\n{GREEN}Installed ({len(installed)}):{NC}")
        for r in installed:
            method_note = f" [{r.method}]" if r.method != "already" else " [already installed]"
            version_note = f" v{r.version}" if r.version else ""
            print(f"  {GREEN}✓{NC} {PACKAGES[r.package].display_name}{version_note}{method_note}")
            if r.notes:
                print(f"    {BLUE}→{NC} {r.notes}")

    if skipped:
        print(f"\n{YELLOW}Not installed ({len(skipped)}):{NC}")
        for r in skipped:
            display = PACKAGES[r.package].display_name if r.package in PACKAGES else r.package
            print(f"  {RED}✗{NC} {display}")
            if r.skipped_reason:
                print(f"    {YELLOW}Reason:{NC} {r.skipped_reason}")
            if r.notes:
                print(f"    {BLUE}Tip:{NC} {r.notes}")

    total = len(results)
    ok_count = len(installed)
    print(f"\n{GREEN if ok_count == total else YELLOW}Result: {ok_count}/{total} packages installed{NC}")


def _find_brew() -> Optional[str]:
    """Find brew binary."""
    for path in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]:
        if Path(path).exists():
            return path
    result = _run("which brew 2>/dev/null")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


# ────────────────────────────────────────────────────────────────────
# macOS direct download fallbacks
# ────────────────────────────────────────────────────────────────────

# Direct DMG/zip download URLs for macOS when Homebrew cask fails.
# Keyed by package name. Each entry has:
#   url: direct download URL (Intel x86_64)
#   type: "dmg" or "zip"
#   app_name: name of the .app inside the DMG (for dmg type)
#   note: version/compat info
#
# These are the last versions compatible with macOS 10.15 Catalina.
# Updated March 2026.
_MACOS_DIRECT_DOWNLOADS = {
    "blender": {
        "url": "https://download.blender.org/release/Blender3.6/blender-3.6.23-macos-x64.dmg",
        "type": "dmg",
        "app_name": "Blender.app",
        "note": "Blender 3.6.23 LTS — last version supporting macOS 10.15",
    },
    "inkscape": {
        "url": "https://media.inkscape.org/dl/resources/file/Inkscape-1.4.3_x86_64.dmg",
        "type": "dmg",
        "app_name": "Inkscape.app",
        "note": "Inkscape 1.4.3 — supports macOS 10.13+",
    },
    "libreoffice": {
        "url": "https://download.documentfoundation.org/libreoffice/stable/25.8.5/mac/x86_64/LibreOffice_25.8.5_MacOS_x86-64.dmg",
        "type": "dmg",
        "app_name": "LibreOffice.app",
        "note": "LibreOffice 25.8.5 — last branch supporting macOS 10.15",
    },
    "rclone": {
        "url": "https://downloads.rclone.org/rclone-current-osx-amd64.zip",
        "type": "zip",
        "binary": "rclone",
        "note": "rclone — official static binary, all macOS versions",
    },
    "imagemagick": {
        # ImageMagick has no standalone macOS binary. Retry brew after permissions fix.
        "type": "brew_retry",
        "formula": "imagemagick",
        "note": "ImageMagick — retry via Homebrew (permissions may have been fixed)",
    },
}


def _install_macos_direct(spec: PackageSpec, password: Optional[str] = None) -> bool:
    """Try to install a macOS package via direct download when Homebrew fails.

    Handles DMG mounting/copying, ZIP extraction, and brew retries.
    Returns True if the package was successfully installed.
    """
    import tempfile

    dl_info = _MACOS_DIRECT_DOWNLOADS.get(spec.name)
    if not dl_info:
        return False

    dl_type = dl_info.get("type", "")

    if dl_type == "brew_retry":
        # Retry brew install — permissions may have been fixed since the first attempt
        brew = _find_brew()
        if not brew:
            return False
        formula = dl_info.get("formula", spec.brew_formula or spec.name)
        logger.info(f"Retrying brew install of {spec.display_name} (permissions may be fixed now)...")
        r = _run(f"{brew} install {formula} 2>&1", timeout=600)
        return r.returncode == 0 and _is_binary_available(spec)

    url = dl_info.get("url", "")
    if not url:
        return False

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"myoldmachine_{spec.name}_"))

    try:
        if dl_type == "dmg":
            return _install_from_dmg(spec, url, dl_info, tmp_dir, password)
        elif dl_type == "zip":
            return _install_from_zip(spec, url, dl_info, tmp_dir, password)
    except Exception as e:
        logger.warning(f"Direct install of {spec.display_name} failed: {e}")
        return False
    finally:
        try:
            import shutil as _shutil
            _shutil.rmtree(str(tmp_dir), ignore_errors=True)
        except Exception:
            pass

    return False


def _install_from_dmg(
    spec: PackageSpec,
    url: str,
    dl_info: dict,
    tmp_dir: Path,
    password: Optional[str],
) -> bool:
    """Download a DMG, mount it, copy the .app to /Applications."""
    import shlex as _shlex
    dmg_path = tmp_dir / f"{spec.name}.dmg"
    app_name = dl_info.get("app_name", f"{spec.display_name}.app")

    logger.info(f"Downloading {spec.display_name} DMG...")
    # Follow redirects (-L) for sites like inkscape.org that redirect
    dl = _run(f"curl -fsSL -o {_shlex.quote(str(dmg_path))} {_shlex.quote(url)}", timeout=300)
    if dl.returncode != 0:
        logger.warning(f"Download failed: {dl.stderr[:200]}")
        return False

    if not dmg_path.exists() or dmg_path.stat().st_size < 1_000_000:
        logger.warning(f"Downloaded file too small or missing: {dmg_path}")
        return False

    # Mount the DMG
    mount_point = tmp_dir / "mnt"
    mount_point.mkdir(exist_ok=True)
    mount_result = _run(
        f"hdiutil attach {_shlex.quote(str(dmg_path))} -mountpoint {_shlex.quote(str(mount_point))} -nobrowse -quiet",
        timeout=60,
    )
    if mount_result.returncode != 0:
        logger.warning(f"Failed to mount DMG: {mount_result.stderr[:200]}")
        return False

    try:
        # Find the .app inside the mounted volume
        app_path = mount_point / app_name
        if not app_path.exists():
            # Search for any .app in the mount
            apps = list(mount_point.glob("*.app"))
            if apps:
                app_path = apps[0]
            else:
                logger.warning(f"No .app found in DMG at {mount_point}")
                return False

        # Copy to /Applications
        dest = Path(f"/Applications/{app_path.name}")
        safe_dest = _shlex.quote(str(dest))
        safe_src = _shlex.quote(str(app_path))
        if dest.exists():
            _sudo_run(f"rm -rf {safe_dest}", password, timeout=30)

        logger.info(f"Installing {app_path.name} to /Applications...")
        result = _sudo_run(f"cp -R {safe_src} /Applications/", password, timeout=120)
        if result.returncode != 0:
            logger.warning(f"Failed to copy app: {result.stderr[:200]}")
            return False

        # Remove quarantine attribute so macOS doesn't block it
        _sudo_run(f"xattr -rd com.apple.quarantine {safe_dest}", password, timeout=10)

        if dest.exists():
            logger.info(f"{spec.display_name} installed to /Applications/{app_path.name}")
            return True

    finally:
        # Always unmount
        _run(f"hdiutil detach {_shlex.quote(str(mount_point))} -quiet -force", timeout=30)

    return False


def _install_from_zip(
    spec: PackageSpec,
    url: str,
    dl_info: dict,
    tmp_dir: Path,
    password: Optional[str],
) -> bool:
    """Download a ZIP, extract it, install binary to /usr/local/bin."""
    import shlex as _shlex
    zip_path = tmp_dir / f"{spec.name}.zip"
    binary_name = dl_info.get("binary", spec.binary)

    logger.info(f"Downloading {spec.display_name}...")
    dl = _run(f"curl -fsSL -o {_shlex.quote(str(zip_path))} {_shlex.quote(url)}", timeout=120)
    if dl.returncode != 0:
        logger.warning(f"Download failed: {dl.stderr[:200]}")
        return False

    # Extract
    extract_dir = tmp_dir / "extracted"
    extract_dir.mkdir(exist_ok=True)
    _run(f"unzip -o {_shlex.quote(str(zip_path))} -d {_shlex.quote(str(extract_dir))}", timeout=60)

    # Find the binary
    binary_path = None
    for f in extract_dir.rglob(binary_name):
        if f.is_file():
            binary_path = f
            break

    if not binary_path:
        logger.warning(f"Binary '{binary_name}' not found in extracted archive")
        return False

    # Install to /usr/local/bin
    dest = f"/usr/local/bin/{binary_name}"
    _sudo_run(f"cp {_shlex.quote(str(binary_path))} {_shlex.quote(dest)}", password, timeout=30)
    _sudo_run(f"chmod +x {_shlex.quote(dest)}", password, timeout=10)

    # Ensure /usr/local/bin is in PATH for this process
    import os
    if "/usr/local/bin" not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"/usr/local/bin:{os.environ.get('PATH', '')}"

    if shutil.which(binary_name):
        logger.info(f"{spec.display_name} installed to {dest}")
        return True

    return False
