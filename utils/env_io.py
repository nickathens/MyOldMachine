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
from pathlib import Path


def atomic_secret_write(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path`` at mode 0600 (temp+fsync+rename).

    The temp file is created with mode 0600 up front (and fchmod'd to be sure,
    in case a stale temp from a prior crash is reused), so the secret contents
    are never world-readable -- not even for the instant between create and
    chmod that a plain ``open()`` + ``chmod()`` would leave. The temp lives
    beside the target as ``<name>.tmp`` (e.g. ``.env.tmp``, not the old
    ``.env.env.tmp`` that ``Path.with_suffix`` produced); ``.gitignore`` covers
    it via ``*.tmp`` so a crashed write can never be committed. Raises on
    failure after cleaning up the temp.
    """
    path = Path(path)
    tmp = path.parent / (path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_env_write(env_file: Path, new_content: str) -> None:
    """Write .env atomically (temp+fsync+rename), always mode 0600.

    Thin alias for :func:`atomic_secret_write`, kept for the existing call sites
    in ``bot.py``, ``install/wizard.py`` and ``miniapp/server.py``.
    """
    atomic_secret_write(env_file, new_content)
