"""
Atomic .env file writes.

A direct write_text() is non-atomic: a crash mid-write leaves a truncated
.env with no provider config, breaking the next bot start. The single
helper here is shared by `bot.py` (runtime /provider, /apikey, etc.) and
`install/wizard.py` (initial setup) so both code paths get identical
fsync+rename semantics and 0600 permissions.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def atomic_env_write(env_file: Path, new_content: str) -> None:
    """Write .env atomically via temp file + fsync + rename, preserving 0600."""
    tmp = env_file.with_suffix(".env.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        tmp.rename(env_file)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
