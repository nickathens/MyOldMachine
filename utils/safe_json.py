"""
Atomic JSON file operations.

All writes go through a temp file + rename to prevent corruption
from crashes or power failures.
"""

import json
import os
from pathlib import Path


def load_json(path: Path, default=None):
    """Load a JSON file safely, returning default on any error."""
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default if default is not None else {}


def save_json(path: Path, data, indent=2):
    """Save JSON atomically: write to temp file, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(path)
