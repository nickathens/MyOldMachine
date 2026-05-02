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
from pathlib import Path

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
    "backup_retention": 7,
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

    # Cleanup
    if config.get("cleanup"):
        lines.append("Cleanup: ON (nightly)")
    else:
        lines.append("Cleanup: OFF")

    # Backup
    if config.get("backup_enabled"):
        path = config.get("backup_path", "not set")
        retention = config.get("backup_retention", 7)
        lines.append(f"Backup: ON (nightly)")
        lines.append(f"  Target: {path}")
        lines.append(f"  Retention: {retention} backups")
    else:
        lines.append("Backup: OFF")
        lines.append("  Use /maintenance backup /path/to/drive to enable")

    return "\n".join(lines)
