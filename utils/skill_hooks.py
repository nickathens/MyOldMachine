#!/usr/bin/env python3
"""
Skill Hooks Dispatcher for Claude Code.

Receives hook events (PreToolUse, PostToolUse, PostToolUseFailure, Stop)
from Claude Code, identifies skill invocations in Bash commands, and runs:
  - Resource checks (RAM/disk) before heavy skills
  - Usage logging (SQLite) for observability
  - Process + temp file cleanup on session end

Called by skill_hook_gate.sh (Pre/Post/Failure) or directly (Stop).

Cross-platform: works on Linux and macOS.

hooks.json schema:
{
  "pre": {
    "min_ram_mb": 4096,     # Block if less than this much RAM free
    "min_disk_mb": 1024     # Block if less than this much disk free
  },
  "stop": {
    "kill_processes": ["demucs"],           # Substring match on ps output
    "clean_patterns": ["/tmp/separated/"]   # Glob patterns for temp cleanup
  }
}
"""

import glob
import json
import os
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration (all paths relative to BOT_DIR)
# ---------------------------------------------------------------------------

BOT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BOT_DIR / "skills"
HOOK_STATE_DIR = Path("/tmp/skill_hooks")
USAGE_DB_PATH = BOT_DIR / "data" / "skill_usage.db"

# Telegram alert config (for skill denial notifications)
SEND_TELEGRAM_SCRIPT = BOT_DIR / "utils" / "send_to_telegram.py"

IS_MACOS = platform.system() == "Darwin"

# Pattern to extract skill name from Bash commands
# Matches: python .../skills/<skill_name>/scripts/<script>.py
SKILL_RE = re.compile(
    r"skills/([a-z0-9_-]+)/scripts/\S+\.py"
)

# Browser daemon files
BROWSER_PID_FILE = Path("/tmp/browser_daemon.pid")
BROWSER_STATE_FILES = [
    "/tmp/browser_daemon.sock",
    "/tmp/browser_daemon.pid",
    "/tmp/browser_daemon_state.json",
    "/tmp/browser_storage.json",
    "/tmp/browser_refs.json",
]

# Process patterns that indicate Playwright/Chromium instances
BROWSER_PROCESS_PATTERNS = [
    "chrome-headless-shell",
    "chromium --headless",
    "chromium-browser --headless",
]

# Global temp file patterns to clean on Stop (glob patterns)
TEMP_CLEANUP_PATTERNS = [
    "/tmp/playwright_videos_*",
    "/tmp/screenshot_*.png",
    "/tmp/scrape_*.html",
    "/tmp/moviepy_*",
    "/tmp/tmp*.mp4",
    "/tmp/tmp*.avi",
    "/tmp/tmp*.mkv",
    "/tmp/tmp*.wav",
    "/tmp/tmp*.mp3",
    "/tmp/tmp*.ogg",
    "/tmp/tmp*.png",
    "/tmp/tmp*.jpg",
    "/tmp/tmp*.gif",
    "/tmp/tmp*.svg",
    "/tmp/tmp*.pdf",
]

# How long before an idle browser daemon gets killed (seconds)
DAEMON_IDLE_TIMEOUT = 600  # 10 minutes

# How old temp files must be before cleanup (seconds)
TEMP_MAX_AGE = 7200  # 2 hours

# Docker container name prefix used by docker-services skill
DOCKER_CONTAINER_PREFIX = "claude-"


# ---------------------------------------------------------------------------
# Helpers — Cross-platform
# ---------------------------------------------------------------------------

def get_free_ram_mb() -> int:
    """Get available RAM in MB. Cross-platform (Linux + macOS)."""
    if IS_MACOS:
        return _get_free_ram_macos()
    return _get_free_ram_linux()


def _get_free_ram_linux() -> int:
    """Linux: read MemAvailable from /proc/meminfo."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 99999  # If unreadable, don't block


def _get_free_ram_macos() -> int:
    """macOS: use vm_stat to calculate free + inactive pages."""
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return 99999

        page_size = 4096  # default on Intel, 16384 on Apple Silicon
        free_pages = 0
        inactive_pages = 0

        for line in result.stdout.split("\n"):
            if "page size of" in line:
                m = re.search(r"page size of (\d+)", line)
                if m:
                    page_size = int(m.group(1))
            elif line.startswith("Pages free:"):
                free_pages = int(line.split(":")[1].strip().rstrip("."))
            elif line.startswith("Pages inactive:"):
                inactive_pages = int(line.split(":")[1].strip().rstrip("."))

        return (free_pages + inactive_pages) * page_size // (1024 * 1024)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return 99999


def get_free_disk_mb(path: str = "/tmp") -> int:
    """Get available disk space in MB. Works on Linux and macOS (POSIX)."""
    try:
        st = os.statvfs(path)
        return (st.f_bavail * st.f_frsize) // (1024 * 1024)
    except OSError:
        pass
    return 999999


def identify_skill(command: str):
    """Extract skill name from a Bash command string. Returns None if not a skill."""
    m = SKILL_RE.search(command)
    return m.group(1) if m else None


def load_hooks_json(skill_name: str) -> dict:
    """Load a skill's hooks.json. Returns empty dict if not found."""
    path = SKILLS_DIR / skill_name / "hooks.json"
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return {}


def load_all_stop_hooks() -> dict[str, dict]:
    """Scan all skills for hooks.json with stop configs. Returns {skill: stop_config}."""
    results = {}
    try:
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir():
                continue
            hooks_file = skill_dir / "hooks.json"
            if hooks_file.is_file():
                try:
                    with open(hooks_file, encoding="utf-8") as f:
                        config = json.load(f)
                    stop = config.get("stop", {})
                    if stop:
                        results[skill_dir.name] = stop
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass
    except OSError:
        pass
    return results


def touch_last_used(skill_name: str):
    """Record timestamp of last skill invocation."""
    try:
        HOOK_STATE_DIR.mkdir(exist_ok=True)
        (HOOK_STATE_DIR / f"{skill_name}_last_used").touch()
    except OSError:
        pass


def get_last_used_age(skill_name: str):
    """Seconds since skill was last used. Returns None if never."""
    p = HOOK_STATE_DIR / f"{skill_name}_last_used"
    if p.exists():
        try:
            return time.time() - p.stat().st_mtime
        except OSError:
            pass
    return None


def _verify_pid_is_browser(pid: int) -> bool:
    """Check if a PID is actually a browser-related process. Cross-platform."""
    browser_keywords = ("browser", "playwright", "chrome", "chromium")
    if not IS_MACOS:
        # Linux: read /proc/<pid>/cmdline
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
            return any(kw in cmdline for kw in browser_keywords)
        except OSError:
            return True  # /proc not readable — trust PID file
    else:
        # macOS: use ps -p <pid> -o args=
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                return any(kw in result.stdout for kw in browser_keywords)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return True  # Can't verify — trust PID file


def get_daemon_pid() -> int | None:
    """Get browser daemon PID if it's actually running and is a browser process."""
    if not BROWSER_PID_FILE.exists():
        return None
    try:
        pid = int(BROWSER_PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)  # Check if alive (signal 0)
        if not _verify_pid_is_browser(pid):
            return None  # PID was reused by a different process
        return pid
    except (ValueError, OSError):
        return None


def get_all_pids() -> list[tuple[int, str]]:
    """Get all running PIDs and their command lines. Cross-platform."""
    pids = []
    exclude = {os.getpid(), os.getppid()}
    try:
        # macOS: ps -ax -o pid,args (no --no-headers)
        # Linux: ps -eo pid,args --no-headers
        if IS_MACOS:
            cmd = ["ps", "-ax", "-o", "pid,args"]
        else:
            cmd = ["ps", "-eo", "pid,args", "--no-headers"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")

        # Skip header on macOS (first line is "PID ARGS" or similar)
        start = 1 if IS_MACOS and lines else 0

        for line in lines[start:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid in exclude:
                continue
            cmd_str = parts[1]
            if "skill_hooks.py" in cmd_str:
                continue
            pids.append((pid, cmd_str))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return pids


def find_orphaned_browser_pids(all_pids: list[tuple[int, str]],
                                exclude_pid: int | None = None) -> list[int]:
    """Find browser/Playwright PIDs that aren't the daemon."""
    orphans = []
    for pid, cmd in all_pids:
        if exclude_pid is not None and pid == exclude_pid:
            continue
        if any(pat in cmd for pat in BROWSER_PROCESS_PATTERNS):
            orphans.append(pid)
    return orphans


def find_processes_by_patterns(all_pids: list[tuple[int, str]],
                                patterns: list[str],
                                exclude_pids: set[int] | None = None) -> list[int]:
    """Find PIDs matching any of the given command-line substring patterns."""
    matched = []
    exclude = exclude_pids or set()
    str_patterns = [p for p in patterns if isinstance(p, str)]
    if not str_patterns:
        return matched
    for pid, cmd in all_pids:
        if pid in exclude:
            continue
        if any(pat in cmd for pat in str_patterns):
            matched.append(pid)
    return matched


def kill_pid(pid: int, grace_sec: float = 0.5):
    """SIGTERM a process, SIGKILL if it doesn't exit in grace_sec."""
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(grace_sec)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    except OSError:
        pass


def kill_pids_batch(pids: list[int], grace_sec: float = 0.8):
    """SIGTERM all pids, wait once, SIGKILL survivors."""
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(grace_sec)
    for pid in pids:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def clean_stale_state_files():
    """Remove browser state files when daemon is dead."""
    for f in BROWSER_STATE_FILES:
        try:
            os.unlink(f)
        except OSError:
            pass


def clean_old_temp_files(patterns: list[str] | None = None) -> int:
    """Remove temp files older than TEMP_MAX_AGE matching the given patterns."""
    cutoff = time.time() - TEMP_MAX_AGE
    cleaned = 0
    target_patterns = patterns or TEMP_CLEANUP_PATTERNS
    for pattern in target_patterns:
        for path in glob.glob(pattern):
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.unlink(path)
                    cleaned += 1
                elif os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    cleaned += 1
            except OSError:
                pass
    return cleaned


def clean_docker_containers() -> int:
    """Stop and remove orphaned docker containers started by docker-services skill."""
    from datetime import datetime, timezone

    cleaned = 0
    if not shutil.which("docker"):
        return 0
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return 0
        for name in result.stdout.strip().split("\n"):
            name = name.strip()
            if not name or not name.startswith(DOCKER_CONTAINER_PREFIX):
                continue
            inspect = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{.State.StartedAt}}", name],
                capture_output=True, text=True, timeout=5
            )
            if inspect.returncode != 0:
                continue
            started = inspect.stdout.strip()
            try:
                started = re.sub(r"(\.\d{6})\d+", r"\1", started)
                dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                age_sec = (datetime.now(timezone.utc) - dt).total_seconds()
                if age_sec > TEMP_MAX_AGE:
                    subprocess.run(
                        ["docker", "rm", "-f", name],
                        capture_output=True, timeout=10
                    )
                    cleaned += 1
            except (ValueError, TypeError):
                pass
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass
    return cleaned


def log_action(message: str):
    """Append a line to the hook log."""
    try:
        HOOK_STATE_DIR.mkdir(exist_ok=True)
    except OSError:
        return
    log_path = HOOK_STATE_DIR / "hook.log"
    try:
        if log_path.exists() and log_path.stat().st_size > 100_000:
            lines = log_path.read_text(encoding="utf-8").splitlines()
            log_path.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _get_admin_user_id() -> str | None:
    """Get the first admin user ID from users.json. Returns None if not found."""
    users_file = BOT_DIR / "data" / "users.json"
    if not users_file.is_file():
        return None
    try:
        with open(users_file, encoding="utf-8") as f:
            users = json.load(f)
        if isinstance(users, dict):
            for uid, profile in users.items():
                if isinstance(profile, dict) and profile.get("role") == "admin":
                    return str(uid)
        elif isinstance(users, list):
            for uid in users:
                return str(uid)  # First user is admin by convention
    except (json.JSONDecodeError, OSError):
        pass
    return None


def send_denial_alert(skill: str, reason: str):
    """Send a Telegram notification when a skill is blocked by pre-hooks.

    Runs send_to_telegram.py in a fire-and-forget subprocess so it doesn't
    block the hook response. Failures are logged but never raise.
    """
    if not SEND_TELEGRAM_SCRIPT.is_file():
        log_action("ALERT SKIP: send_to_telegram.py not found")
        return

    admin_id = _get_admin_user_id()
    if not admin_id:
        log_action("ALERT SKIP: no admin user found in users.json")
        return

    msg = f"[Skill Hook] Blocked: {skill}\n{reason}"
    try:
        subprocess.Popen(
            [
                sys.executable,
                str(SEND_TELEGRAM_SCRIPT),
                "--user", admin_id,
                "--message", msg,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log_action(f"ALERT SENT: {skill} blocked")
    except OSError as e:
        log_action(f"ALERT FAIL: {e}")


# ---------------------------------------------------------------------------
# Usage tracking (SQLite)
# ---------------------------------------------------------------------------

_db_schema_created = False


def _get_db() -> sqlite3.Connection:
    """Open (and auto-create) the usage database. Returns a connection."""
    global _db_schema_created
    USAGE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(USAGE_DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    if not _db_schema_created:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_invocations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_use_id   TEXT UNIQUE,
                session_id    TEXT,
                skill_name    TEXT NOT NULL,
                command       TEXT,
                started_at    REAL NOT NULL,
                ended_at      REAL,
                duration_ms   INTEGER,
                success       INTEGER,
                ram_mb_start  INTEGER,
                error         TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_skill_name
            ON skill_invocations(skill_name)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_id
            ON skill_invocations(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_started_at
            ON skill_invocations(started_at)
        """)
        conn.commit()
        _db_schema_created = True
    return conn


def db_record_start(tool_use_id: str, session_id: str, skill_name: str,
                    command: str, ram_mb: int):
    """Insert a new invocation record at skill start."""
    conn = None
    try:
        conn = _get_db()
        conn.execute(
            """INSERT OR IGNORE INTO skill_invocations
               (tool_use_id, session_id, skill_name, command, started_at, ram_mb_start)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tool_use_id, session_id, skill_name, command[:500], time.time(), ram_mb)
        )
        conn.commit()
    except (sqlite3.Error, OSError):
        pass
    finally:
        if conn:
            conn.close()


def db_record_end(tool_use_id: str, success: bool):
    """Update an invocation record with end time and success status."""
    conn = None
    try:
        conn = _get_db()
        now = time.time()
        conn.execute(
            """UPDATE skill_invocations
               SET ended_at = ?,
                   duration_ms = CAST((? - started_at) * 1000 AS INTEGER),
                   success = ?
               WHERE tool_use_id = ?""",
            (now, now, 1 if success else 0, tool_use_id)
        )
        conn.commit()
    except (sqlite3.Error, OSError):
        pass
    finally:
        if conn:
            conn.close()


def db_record_failure(tool_use_id: str, error: str):
    """Update an invocation record with failure info."""
    conn = None
    try:
        conn = _get_db()
        now = time.time()
        if not isinstance(error, str):
            error = str(error)
        conn.execute(
            """UPDATE skill_invocations
               SET ended_at = ?,
                   duration_ms = CAST((? - started_at) * 1000 AS INTEGER),
                   success = 0,
                   error = ?
               WHERE tool_use_id = ?""",
            (now, now, error[:1000], tool_use_id)
        )
        conn.commit()
    except (sqlite3.Error, OSError):
        pass
    finally:
        if conn:
            conn.close()


def db_cleanup_old(days: int = 90):
    """Remove records older than N days."""
    conn = None
    try:
        conn = _get_db()
        cutoff = time.time() - (days * 86400)
        conn.execute(
            "DELETE FROM skill_invocations WHERE started_at < ?", (cutoff,)
        )
        conn.commit()
    except (sqlite3.Error, OSError):
        pass
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def handle_pre_tool_use(data: dict) -> dict | None:
    """
    PreToolUse: check resources before running a skill.
    Returns JSON output dict, or None to pass through.
    """
    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    skill = identify_skill(command)
    if not skill:
        return None

    config = load_hooks_json(skill)
    pre = config.get("pre", {})

    # RAM check
    min_ram = pre.get("min_ram_mb")
    if min_ram is not None:
        if not isinstance(min_ram, (int, float)):
            log_action(f"PRE WARN {skill}: min_ram_mb is {type(min_ram).__name__}, expected int")
        else:
            free = get_free_ram_mb()
            if free < min_ram:
                reason = (
                    f"Insufficient RAM for {skill}. "
                    f"Need {min_ram}MB free, only {free}MB available. "
                    f"Close heavy processes (browser, blender) or try a lighter operation."
                )
                log_action(f"PRE BLOCKED {skill}: need {min_ram}MB RAM, have {free}MB")
                send_denial_alert(skill, f"RAM: need {min_ram}MB, have {free}MB")
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }

    # Disk space check
    min_disk = pre.get("min_disk_mb")
    if min_disk is not None:
        if not isinstance(min_disk, (int, float)):
            log_action(f"PRE WARN {skill}: min_disk_mb is {type(min_disk).__name__}, expected int")
        else:
            free_disk = get_free_disk_mb()
            if free_disk < min_disk:
                reason = (
                    f"Insufficient disk space for {skill}. "
                    f"Need {min_disk}MB free, only {free_disk}MB available. "
                    f"Clean up temp files or remove unused data."
                )
                log_action(f"PRE BLOCKED {skill}: need {min_disk}MB disk, have {free_disk}MB")
                send_denial_alert(skill, f"Disk: need {min_disk}MB, have {free_disk}MB")
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }

    touch_last_used(skill)
    log_action(f"PRE OK {skill}")

    # Record invocation start in usage DB
    tool_use_id = data.get("tool_use_id", "")
    session_id = data.get("session_id", "")
    if tool_use_id:
        ram = get_free_ram_mb()
        db_record_start(tool_use_id, session_id, skill, command, ram)

    return None


def handle_post_tool_use(data: dict) -> dict | None:
    """PostToolUse: record usage timestamp and mark success."""
    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    skill = identify_skill(command)
    if skill:
        touch_last_used(skill)
        tool_use_id = data.get("tool_use_id", "")
        if tool_use_id:
            db_record_end(tool_use_id, success=True)
    return None


def handle_post_tool_use_failure(data: dict) -> dict | None:
    """PostToolUseFailure: record failure with error details."""
    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    skill = identify_skill(command)
    if skill:
        touch_last_used(skill)
        tool_use_id = data.get("tool_use_id", "")
        error = data.get("error", "unknown error")
        if not isinstance(error, str):
            error = str(error)
        if tool_use_id:
            db_record_failure(tool_use_id, error)
        log_action(f"FAILURE {skill}: {error[:200]}")
    return None


def handle_stop(data: dict) -> dict | None:
    """
    Stop: session-end cleanup.
    Kill orphaned browsers, idle daemons, skill processes, stale files,
    and orphaned docker containers.
    """
    actions = []
    all_pids = get_all_pids()
    killed_pids: set[int] = set()

    # 1. Browser-specific cleanup
    daemon_pid = get_daemon_pid()
    kill_daemon = False

    if daemon_pid:
        age = get_last_used_age("browser")
        if age is not None and age > DAEMON_IDLE_TIMEOUT:
            kill_daemon = True
        elif age is None:
            try:
                state_age = time.time() - BROWSER_PID_FILE.stat().st_mtime
                if state_age > DAEMON_IDLE_TIMEOUT:
                    kill_daemon = True
            except OSError:
                pass
        if kill_daemon:
            kill_pid(daemon_pid, grace_sec=1.0)
            killed_pids.add(daemon_pid)
            clean_stale_state_files()
            idle_info = f"idle {int(age)}s" if age is not None else "untracked"
            actions.append(f"killed idle daemon PID {daemon_pid} ({idle_info})")
    else:
        stale = [f for f in BROWSER_STATE_FILES if os.path.exists(f)]
        if stale:
            clean_stale_state_files()
            actions.append(f"cleaned {len(stale)} stale browser state files")

    # Re-scan after daemon kill to catch orphaned children
    if kill_daemon:
        all_pids = get_all_pids()
    orphans = find_orphaned_browser_pids(all_pids, exclude_pid=daemon_pid if not kill_daemon else None)
    if orphans:
        kill_pids_batch(orphans)
        killed_pids.update(orphans)
        actions.append(f"killed {len(orphans)} orphaned browser PIDs")

    # 2. Per-skill stop hooks
    stop_hooks = load_all_stop_hooks()
    all_skill_kill_pids_set: set[int] = set()
    skill_pid_labels: dict[int, str] = {}

    for skill_name, stop_config in stop_hooks.items():
        if skill_name in ("browser", "scraper", "media"):
            continue

        kill_patterns = stop_config.get("kill_processes", [])
        if kill_patterns and isinstance(kill_patterns, list):
            str_patterns = [p for p in kill_patterns if isinstance(p, str)]
            if str_patterns:
                matched = find_processes_by_patterns(
                    all_pids, str_patterns, exclude_pids=killed_pids
                )
                for pid in matched:
                    if pid not in all_skill_kill_pids_set:
                        all_skill_kill_pids_set.add(pid)
                        skill_pid_labels[pid] = skill_name

        skill_patterns = stop_config.get("clean_patterns", [])
        if skill_patterns and isinstance(skill_patterns, list):
            cleaned = clean_old_temp_files(skill_patterns)
            if cleaned:
                actions.append(f"cleaned {cleaned} {skill_name} temp files")

        if stop_config.get("docker_cleanup"):
            docker_cleaned = clean_docker_containers()
            if docker_cleaned:
                actions.append(f"removed {docker_cleaned} orphaned docker containers")

    all_skill_kill_pids = list(all_skill_kill_pids_set)
    if all_skill_kill_pids:
        kill_pids_batch(all_skill_kill_pids)
        killed_pids.update(all_skill_kill_pids)
        skill_counts: dict[str, int] = {}
        for pid in all_skill_kill_pids:
            label = skill_pid_labels.get(pid, "unknown")
            skill_counts[label] = skill_counts.get(label, 0) + 1
        for skill_name, count in skill_counts.items():
            actions.append(f"killed {count} {skill_name} processes")

    # 3. Global temp file cleanup
    cleaned = clean_old_temp_files()
    if cleaned:
        actions.append(f"cleaned {cleaned} global temp files")

    # 4. Prune old usage records
    db_cleanup_old(90)

    if actions:
        log_action(f"STOP: {'; '.join(actions)}")

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    event = data.get("hook_event_name", "")
    result = None

    if event == "PreToolUse":
        result = handle_pre_tool_use(data)
    elif event == "PostToolUse":
        result = handle_post_tool_use(data)
    elif event == "PostToolUseFailure":
        result = handle_post_tool_use_failure(data)
    elif event == "Stop":
        result = handle_stop(data)

    if result:
        json.dump(result, sys.stdout)

    sys.exit(0)


if __name__ == "__main__":
    main()
