#!/usr/bin/env python3
"""
Maintenance configuration for MyOldMachine.

Manages the maintenance.json config file that controls:
- System updates (nightly)
- Backups (nightly, requires user-configured path)
- Cleanup (nightly)
"""

import json
import logging
import platform
from pathlib import Path

from utils.backup import DEFAULT_RETENTION
from utils.safe_json import save_json

BOT_DIR = Path(__file__).parent.parent
DATA_DIR = BOT_DIR / "data"
CONFIG_FILE = DATA_DIR / "maintenance.json"

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "system_updates": True,
    "cleanup": True,
    "backup_enabled": False,
    "backup_path": "",
    # Tool: "tarball" (default for back-compat) or "borg".
    "backup_tool": "tarball",
    # Tarball mode: keep this many archives.
    "backup_retention": DEFAULT_RETENTION,
    # Borg mode: calendar-based retention.
    "backup_keep_daily": 7,
    "backup_keep_weekly": 4,
    "backup_keep_monthly": 6,
    # Borg mode: compression spec passed to borg --compression.
    "backup_compression": "zstd,3",
    # Borg mode: absolute path of file holding the encryption passphrase
    # (mode 0600). Empty when tarball is the chosen tool.
    "backup_passphrase_path": "",
    # Borg mode: extra absolute paths to include alongside the bot's data/.
    "backup_extra_paths": [],
    # macOS-only: also pull non-critical Apple updates via `softwareupdate`.
    # Off by default — on Darwin the provisioner already enables auto-install
    # of security responses (CriticalUpdateInstall=true), so this flag only
    # adds Safari/CLI-tools/supplemental/version updates on top.
    "macos_system_updates": False,
    # Auto-restart if any update requires it. Off by default so the nightly
    # job never surprise-reboots a workstation. Ignored unless macos_system_updates is on.
    "macos_system_updates_restart": False,
    # Also check the apps no package manager tracks: DaVinci Resolve (Blackmagic
    # retired the Homebrew cask), Claude Code (native install), the global npm
    # CLIs the skills install, and Flatpak apps on Linux. On by default and
    # read-only in itself — before this existed nothing on the machine had ever
    # looked at any of their versions.
    "app_update_checks": True,
    # Whether that check may install what it finds. Deliberately narrow: only
    # CLIs, and only when the leading version number does not move (a major
    # bump is reported for a human instead). GUI applications are never
    # installed unattended whatever this says — see app_updates.AUTO_INSTALLABLE.
    "app_auto_update": True,
    # Close idle, orphaned heavyweight helper apps (LibreOffice, GIMP, Inkscape,
    # Blender) that skills spawn headless and leave resident. On by default: the
    # process reaper only touches an app that is BOTH older than the window below
    # AND sitting near-idle on CPU, so live renders/conversions are never hit.
    "reap_idle_apps": True,
    # How many minutes a listed app may sit idle before it is closed.
    "reap_idle_app_minutes": 20,
    # Nightly reboot. OFF by default: an unattended reboot is only safe on a
    # machine set to bring the bot back on boot, so the admin opts in. When on,
    # the bot reboots once a night at the time below, AFTER the whole nightly
    # maintenance chain, and only if it can confirm the bot restarts on boot
    # (enabled systemd service, or a boot-launched service / auto-login on
    # macOS) -- otherwise it refuses and pings the admin instead of stranding
    # the machine at a login screen. Applies restart-needing updates and clears
    # the resource creep of an always-on box.
    "nightly_reboot": False,
    "nightly_reboot_hour": 5,
    "nightly_reboot_minute": 0,
}


def load_config() -> dict:
    """Load maintenance config, returning defaults if missing."""
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            # Merge with defaults for any missing keys
            merged = dict(DEFAULT_CONFIG)
            merged.update(config)
            return merged
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to read maintenance config: {e}")
    return dict(DEFAULT_CONFIG)


def save_config(config: dict):
    """Save maintenance config atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(CONFIG_FILE, config)


def update_config(**kwargs) -> dict:
    """Update specific maintenance config values. Returns the updated config."""
    config = load_config()
    config.update(kwargs)
    save_config(config)
    return config


def get_status_report() -> str:
    """Build a human-readable maintenance status report."""
    config = load_config()
    lines = ["Maintenance Status", ""]

    # System updates
    if config.get("system_updates"):
        lines.append("System updates: ON (nightly)")
    else:
        lines.append("System updates: OFF")

    # macOS-only: softwareupdate sub-toggle
    if platform.system() == "Darwin":
        if config.get("macos_system_updates"):
            restart_note = " + auto-restart" if config.get("macos_system_updates_restart") else ""
            lines.append(f"  macOS softwareupdate: ON{restart_note}")
        else:
            lines.append("  macOS softwareupdate: OFF (Apple security responses still auto-install)")

    # Apps outside the package manager (Resolve, Claude Code, npm CLIs, Flatpak)
    if config.get("app_update_checks", True):
        if config.get("app_auto_update", True):
            lines.append("App update checks: ON (nightly, installs CLI updates)")
        else:
            lines.append("App update checks: ON (nightly, report only)")
    else:
        lines.append("App update checks: OFF")

    # Cleanup
    if config.get("cleanup"):
        lines.append("Cleanup: ON (nightly)")
    else:
        lines.append("Cleanup: OFF")

    # Idle-app reaper (closes orphaned LibreOffice/GIMP/Inkscape/Blender)
    if config.get("reap_idle_apps", True):
        mins = config.get("reap_idle_app_minutes", 20)
        lines.append(f"Idle-app reaper: ON (closes idle helper apps after {mins} min)")
    else:
        lines.append("Idle-app reaper: OFF")

    # Nightly reboot (opt-in; reboots the machine after nightly maintenance)
    if config.get("nightly_reboot", False):
        h = config.get("nightly_reboot_hour", 5)
        m = config.get("nightly_reboot_minute", 0)
        lines.append(f"Nightly reboot: ON (daily at {h:02d}:{m:02d}, after updates)")
    else:
        lines.append("Nightly reboot: OFF")

    # Backup
    if config.get("backup_enabled"):
        path = config.get("backup_path", "not set")
        tool = (config.get("backup_tool") or "tarball").lower()
        lines.append("Backup: ON (nightly)")
        lines.append(f"  Tool: {tool}")
        lines.append(f"  Target: {path}")
        if tool == "borg":
            kd = config.get("backup_keep_daily", 7)
            kw = config.get("backup_keep_weekly", 4)
            km = config.get("backup_keep_monthly", 6)
            comp = config.get("backup_compression", "zstd,3")
            lines.append(f"  Retention: {kd} daily / {kw} weekly / {km} monthly")
            lines.append(f"  Compression: {comp}")
            extras = config.get("backup_extra_paths") or []
            if extras:
                lines.append(f"  Extra paths: {len(extras)}")
        else:
            retention = config.get("backup_retention", DEFAULT_RETENTION)
            lines.append(f"  Retention: {retention} backups")
    else:
        lines.append("Backup: OFF")
        lines.append("  Use /maintenance backup /path/to/drive to enable")

    return "\n".join(lines)
