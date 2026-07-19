"""Per-user Claude CLI workspaces (config dir, auto-memory pool, transcripts).

Why: the bot serves several Telegram users from ONE OS account and ONE
working directory. The Claude CLI keys all of its persistent state -- the
auto-memory pool it loads into every session, plus session transcripts --
by (config dir, project cwd). With one shared config dir, every user's
sessions read and write the SAME memory pool, so facts learned in one
user's private chat resurface in another user's chat. That is exactly the
2026-07-19 incident: an animation built in one user's session was
described to another user as their own work, because the shared pool
carried the project note with no owner attached.

Fix: point CLAUDE_CONFIG_DIR at data/users/<id>/claude for every CLI turn
that runs on behalf of a Telegram user. Memory and transcripts become
private per user; the filesystem, skills, and shared projects remain
shared machine state (the product's soft multi-user model -- see the
README "Trust model" section: this is scoping, not a security boundary).

Migration: the legacy shared pool is split ONCE, by provenance. Every
memory file the CLI writes carries an ``originSessionId`` in frontmatter;
the matching legacy transcript records which Telegram user the session
served, because the bot stamps "User's Telegram ID: <id>" into every
system prompt. Each file is COPIED into its author's private pool; files
whose origin cannot be established fall back to the primary admin. The
legacy pool is never modified beyond one marker file, so it stays intact
as a frozen backup.

Credentials: the CLI's OAuth login lives in the macOS keychain, which the
CLI only consults for the default config dir. Each workspace therefore
gets a symlink to ONE shared .credentials.json (exported from the
keychain once, or already present on Linux installs), so every session
rides the same refresh chain. If no credential can be provisioned the
workspace is refused and the turn falls back to the shared config dir,
because a private-but-unauthenticated turn would just die.

System turns (user_id None: maintenance, health checks, nightly jobs)
keep the process-default config dir and are unaffected.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).parent.parent.resolve()

# Entries shared from the legacy config dir into every per-user config dir
# via symlink. Hooks and permissions (settings*.json) and plugins are
# machine-wide concerns; .credentials.json is deliberately ONE shared file
# so all workspaces ride a single OAuth refresh chain instead of forking
# the token per user (see _ensure_shared_credentials).
_SHARED_CONFIG_ENTRIES = (
    "settings.json",
    "settings.local.json",
    "plugins",
    ".credentials.json",
)

_KEYCHAIN_SERVICE = "Claude Code-credentials"

_MIGRATION_MARKER = ".per-user-split-done"

_ORIGIN_RE = re.compile(r"originSessionId:\s*([A-Za-z0-9][A-Za-z0-9-]{6,})")
_TELEGRAM_ID_RE = re.compile(r"User's Telegram ID: (\d+)")

# How many transcript lines to scan for the Telegram ID stamp. The stamp
# sits in the system prompt of the first user message, always within the
# first few events of a transcript.
_TRANSCRIPT_SCAN_LINES = 80


def _encode_project_dir(path: Path) -> str:
    """Mirror the CLI's project-dir encoding (non-alphanumerics -> '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def default_legacy_root() -> Path:
    """The config dir the CLI used before per-user isolation."""
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(env) if env else Path.home() / ".claude"


def user_claude_dir(user_id: int) -> Path:
    from core.users import resolve_user_dir
    return resolve_user_dir(user_id) / "claude"


def _export_keychain_credentials(dest: Path) -> bool:
    """macOS: copy the CLI's OAuth credential out of the login keychain.

    The CLI stores its login in the keychain but only consults it for the
    default config dir; a session pointed at any other CLAUDE_CONFIG_DIR
    reports "Not logged in" (live-proven 2026-07-19). The file form of the
    credential, standard on Linux, works for any config dir, so the
    keychain secret is exported once to the legacy root and shared from
    there. 0600, same exposure class as the bot token in .env (see the
    README trust model: one OS account, disk is the boundary).
    """
    if sys.platform != "darwin":
        return False
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("keychain export failed: %s", exc)
        return False
    secret = proc.stdout.strip()
    if proc.returncode != 0 or not secret:
        return False
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secret)
    return True


def _ensure_shared_credentials(legacy_root: Path) -> bool:
    """True if a CLI credential file exists (or was provisioned) to share."""
    cred = legacy_root / ".credentials.json"
    if cred.exists():
        return True
    return _export_keychain_credentials(cred)


def ensure_user_claude_config(
    user_id: int,
    *,
    legacy_root: Optional[Path] = None,
    bot_dir: Optional[Path] = None,
) -> Path:
    """Create (idempotently) and return the per-user CLAUDE_CONFIG_DIR."""
    legacy_root = legacy_root or default_legacy_root()
    bot_dir = bot_dir or _BOT_DIR

    # A workspace the CLI cannot authenticate from is worse than the shared
    # dir: the turn would die with "Not logged in". Auth reaches the CLI
    # either as env keys (passed through build_cli_env) or as a credential
    # file inside the config dir. If neither is available, refuse -- the
    # caller fails open to the shared dir and the turn still runs.
    have_env_auth = any(
        os.environ.get(k)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    )
    if not _ensure_shared_credentials(legacy_root) and not have_env_auth:
        raise RuntimeError(
            "no Claude CLI credentials reachable for a per-user workspace"
        )

    cfg = user_claude_dir(user_id)
    cfg.mkdir(parents=True, exist_ok=True)

    for name in _SHARED_CONFIG_ENTRIES:
        src, dst = legacy_root / name, cfg / name
        try:
            if src.exists() and not (dst.exists() or dst.is_symlink()):
                dst.symlink_to(src)
        except OSError as exc:
            logger.warning("could not link %s into %s: %s", name, cfg, exc)

    # Seed CLI state so a fresh config dir never stalls a headless -p run:
    # onboarding marked done, plus the logged-in account identity carried
    # over from the machine's existing state file.
    state = cfg / ".claude.json"
    if not state.exists():
        seed: dict = {"hasCompletedOnboarding": True}
        for candidate in (legacy_root / ".claude.json", Path.home() / ".claude.json"):
            try:
                if candidate.exists():
                    legacy_state = json.loads(candidate.read_text())
                    for key in ("oauthAccount", "userID"):
                        if key in legacy_state:
                            seed[key] = legacy_state[key]
                    break
            except (OSError, ValueError) as exc:
                logger.warning("unreadable CLI state %s: %s", candidate, exc)
        try:
            state.write_text(json.dumps(seed))
        except OSError as exc:
            logger.warning("could not seed %s: %s", state, exc)

    try:
        migrate_legacy_memory(legacy_root, bot_dir)
    except Exception as exc:
        # The split must never block a turn; isolation works even if the
        # legacy split has to be retried on a later turn.
        logger.warning("legacy memory split failed (will retry): %s", exc)

    return cfg


def _session_user_id(transcript: Path) -> Optional[int]:
    """Telegram user a legacy session served, from the bot's prompt stamp."""
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(_TRANSCRIPT_SCAN_LINES):
                line = fh.readline()
                if not line:
                    break
                m = _TELEGRAM_ID_RE.search(line)
                if m:
                    return int(m.group(1))
    except OSError:
        return None
    return None


def migrate_legacy_memory(legacy_root: Path, bot_dir: Path) -> dict:
    """One-time provenance split of the legacy shared memory pool.

    Copies every memory file into the private pool of the user whose
    session wrote it and rebuilds each user's MEMORY.md index from the
    legacy one. Returns {filename: user_id} for this run ({} once done).
    Runs on the event-loop thread only, so the marker file is enough to
    keep it single-shot; copies are idempotent if two runs ever race.
    """
    enc = _encode_project_dir(bot_dir)
    legacy_mem = legacy_root / "projects" / enc / "memory"
    if not legacy_mem.is_dir():
        return {}
    marker = legacy_mem / _MIGRATION_MARKER
    if marker.exists():
        return {}

    from core.config import get_primary_admin_id
    admin_id = get_primary_admin_id()

    files = [p for p in sorted(legacy_mem.glob("*.md")) if p.name != "MEMORY.md"]
    assignment: dict = {}
    unresolved: list = []
    for path in files:
        uid: Optional[int] = None
        try:
            m = _ORIGIN_RE.search(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            m = None
        if m:
            uid = _session_user_id(
                legacy_root / "projects" / enc / f"{m.group(1)}.jsonl"
            )
        if uid is None:
            uid = admin_id
        if uid is None:
            unresolved.append(path.name)
            continue
        dest = user_claude_dir(uid) / "projects" / enc / "memory"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / path.name)
        assignment[path.name] = uid

    # Split the index: each entry line references its file as (file.md).
    index = legacy_mem / "MEMORY.md"
    if index.exists():
        per_user: dict = {}
        for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"\(([^()\s]+\.md)\)", line)
            uid = assignment.get(m.group(1)) if m else None
            if uid is None and line.strip() and admin_id is not None:
                uid = admin_id  # headers / unmatched lines stay with the admin
            if uid is not None:
                per_user.setdefault(uid, []).append(line)
        for uid, lines in per_user.items():
            dest = user_claude_dir(uid) / "projects" / enc / "memory"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "MEMORY.md").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )

    if unresolved:
        # No admin to fall back to (bootstrap window before users.json
        # exists). Leave the marker unwritten so the split retries once
        # roles are recorded.
        logger.warning("memory split incomplete, unassigned: %s", unresolved)
        return assignment

    marker.write_text(
        json.dumps(
            {"split": {k: v for k, v in sorted(assignment.items())},
             "note": "legacy pool preserved as backup"},
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("legacy memory pool split per user: %s", assignment)
    return assignment
