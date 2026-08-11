#!/usr/bin/env python3
"""Send a single nightly maintenance summary to the admin user.

Runs at 04:45 (after cleanup 03:30, system update 04:00, system probe 04:30,
and well after Time Machine's 01:00 backup window). Collects:
  - Backup status (Time Machine on macOS, borg/tarball on Linux via utils/backup)
  - Whether cleanup / system update / system probe succeeded last night
  - System probe disk free / RAM snapshot
"""

import json
import platform
import plistlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BOT_DIR / "data"
HISTORY_DB = DATA_DIR / "scheduler" / "history.db"
CAPS_FILE = DATA_DIR / "system_caps.json"
UPDATE_LOG = DATA_DIR / "logs" / "system_update.log"
REPORT_LOG = DATA_DIR / "logs" / "nightly_report.jsonl"
MAINTENANCE_CONFIG = DATA_DIR / "maintenance.json"
TM_PLIST = Path("/Library/Preferences/com.apple.TimeMachine.plist")
SEND_SCRIPT = BOT_DIR / "utils" / "send_to_telegram.py"
VENV_PYTHON = BOT_DIR / ".venv" / "bin" / "python"
PYTHON_CMD = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

IS_MACOS = platform.system() == "Darwin"

sys.path.insert(0, str(BOT_DIR))
from core.config import get_primary_admin_id  # noqa: E402

JOBS_OF_INTEREST = [
    ("Nightly cleanup", "Cleanup"),
    ("Nightly system update", "System update"),
    ("Nightly system probe", "System probe"),
]


def _fmt_size(num_bytes: int) -> str:
    gb = num_bytes / (1024 ** 3)
    if gb >= 1000:
        return f"{gb / 1024:.1f} TB"
    return f"{gb:.0f} GB"


def _read_tm_plist() -> dict:
    """Read Time Machine plist via `defaults export`.

    Direct file reads are blocked by macOS TCC on this plist even though
    POSIX permissions look open, so we shell out to the system `defaults`
    binary, which is allowed to read it.
    """
    try:
        proc = subprocess.run(
            ["/usr/bin/defaults", "export", str(TM_PLIST), "-"],
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout:
            return {}
        return plistlib.loads(proc.stdout)
    except Exception:
        return {}


def _tm_running() -> bool:
    try:
        out = subprocess.run(
            ["/usr/bin/tmutil", "status"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return '"Running" = 1' in out or "Running = 1" in out
    except Exception:
        return False


def _time_machine_section() -> list[str]:
    plist = _read_tm_plist()
    dests = plist.get("Destinations") or []
    if not dests:
        return ["Time Machine", "  No destinations configured"]

    d = dests[0]
    snapshots = d.get("SnapshotDates") or []
    last = snapshots[-1] if snapshots else None
    result = d.get("RESULT")
    used = d.get("BytesUsed")
    avail = d.get("BytesAvailable")
    vol = d.get("LastKnownVolumeName") or "destination"

    status_line = "OK" if result == 0 else f"result code {result}"
    if _tm_running():
        status_line = "still running"

    lines = ["Time Machine"]
    lines.append(f"  Status: {status_line}")
    if last:
        ts = last.strftime("%Y-%m-%d %H:%M") if isinstance(last, datetime) else str(last)
        lines.append(f"  Last backup: {ts}")
    if used is not None and avail is not None:
        lines.append(f"  Used: {_fmt_size(used)}, free: {_fmt_size(avail)} on {vol}")
    return lines


def _read_maintenance_config() -> dict:
    try:
        return json.loads(MAINTENANCE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _borg_section(repo_path: str, config: dict) -> list[str]:
    try:
        from utils import backup_borg as _bb
    except Exception as e:
        return ["Backup (borg)", f"  Repo: {repo_path}", f"  borg helper not importable: {e}"]

    pass_path = config.get("backup_passphrase_path")
    passphrase = _bb.read_passphrase(pass_path) if pass_path else None
    if not passphrase:
        return ["Backup (borg)", f"  Repo: {repo_path}", "  Passphrase not available"]

    if not _bb.is_repo(repo_path):
        return ["Backup (borg)", f"  Repo: {repo_path}",
                "  Not a borg repo (run /maintenance backup-tool borg to init)"]

    listing = _bb.list_backups(repo_path, passphrase)
    archives: list[str] = []
    repo_size: str | None = None
    for raw in listing.splitlines():
        line = raw.strip()
        if not line or line.startswith(("Borg backups in", "No backups", "Total:")):
            continue
        if line.startswith("Repo size on disk:"):
            repo_size = line.split("Repo size on disk:", 1)[1].strip()
            continue
        archives.append(line)

    # Archive names end with YYYY-MM-DD_HHMM, so lexicographic max == most recent.
    last_archive = max(archives) if archives else None

    lines = ["Backup (borg)", f"  Repo: {repo_path}"]
    lines.append(f"  Last archive: {last_archive}" if last_archive else "  No archives yet")
    if repo_size:
        lines.append(f"  Repo size: {repo_size}")
    if archives:
        lines.append(f"  Count: {len(archives)} archive(s)")
    return lines


def _tarball_section(repo_path: str) -> list[str]:
    target = Path(repo_path)
    if not target.exists():
        return ["Backup (tarball)", f"  Target {repo_path} does not exist"]
    backups = sorted(
        (f for f in target.glob("myoldmachine_*.tar.gz") if f.is_file()),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    lines = ["Backup (tarball)", f"  Target: {repo_path}"]
    if not backups:
        lines.append("  No backups yet")
        return lines
    last = backups[0]
    size_mb = last.stat().st_size / (1024 * 1024)
    when = datetime.fromtimestamp(last.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    lines.append(f"  Last backup: {when}")
    lines.append(f"  Archive: {last.name} ({size_mb:.1f} MB)")
    lines.append(f"  Count: {len(backups)} archive(s)")
    return lines


def _linux_backup_section() -> list[str]:
    config = _read_maintenance_config()
    repo_path = config.get("backup_path") or ""
    if not repo_path:
        return ["Backup", "  Not configured (no backup_path set)"]
    tool = (config.get("backup_tool") or "tarball").strip().lower()
    if tool == "borg":
        return _borg_section(repo_path, config)
    return _tarball_section(repo_path)


def _backup_section() -> list[str]:
    if IS_MACOS:
        return _time_machine_section()
    return _linux_backup_section()


def _last_night_jobs() -> dict:
    """Return {pretty_name: (success_bool, error_str)} for jobs run since yesterday 18:00."""
    cutoff = (datetime.now() - timedelta(hours=18)).isoformat()
    out = {}
    if not HISTORY_DB.exists():
        return out
    try:
        conn = sqlite3.connect(str(HISTORY_DB))
        cur = conn.execute(
            "SELECT message, executed_at, success, error FROM history "
            "WHERE executed_at >= ? ORDER BY executed_at DESC",
            (cutoff,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return out

    seen = set()
    for message, _ts, success, error in rows:
        for job_msg, pretty in JOBS_OF_INTEREST:
            if message == job_msg and pretty not in seen:
                out[pretty] = (bool(success), error or "")
                seen.add(pretty)
    return out


def _jobs_section() -> list[str]:
    results = _last_night_jobs()
    lines = ["Maintenance jobs"]
    for _, pretty in JOBS_OF_INTEREST:
        if pretty not in results:
            lines.append(f"  {pretty}: did not run")
            continue
        success, error = results[pretty]
        if success:
            lines.append(f"  {pretty}: OK")
        else:
            err = error[:120] if error else "failed"
            lines.append(f"  {pretty}: FAILED ({err})")
    return lines


def _system_update_summary() -> str | None:
    """Pull the most recent 'Nightly update:' line from the update log."""
    if not UPDATE_LOG.exists():
        return None
    try:
        text = UPDATE_LOG.read_text(encoding="utf-8")
    except Exception:
        return None
    for line in reversed(text.splitlines()):
        if "Nightly update:" in line:
            i = line.find("Nightly update:")
            return line[i + len("Nightly update:"):].strip()
    return None


def _system_section() -> list[str]:
    lines = ["System"]
    update_msg = _system_update_summary()
    if update_msg:
        lines.append(f"  Last system update: {update_msg}")
    try:
        caps = json.loads(CAPS_FILE.read_text(encoding="utf-8"))
        disk_free = caps.get("disk_free_gb")
        ram = caps.get("ram_gb")
        probed_at = caps.get("probed_at", "")[:16].replace("T", " ")
        if disk_free is not None:
            lines.append(f"  Disk free: {disk_free:.0f} GB")
        if ram is not None:
            lines.append(f"  RAM: {ram} GB")
        if probed_at:
            lines.append(f"  Probed: {probed_at}")
    except Exception:
        pass
    return lines


def _miniapp_section() -> list[str]:
    """Mini App health, proven from the outside: the public URL is loaded over
    the real internet like a phone would, the app shell must actually render,
    and a drifted App button is re-pointed on the spot and reported as fixed.
    Empty when the Mini App is not installed, so plain installs see no noise."""
    try:
        from utils.miniapp_guard import build_report_section
        return build_report_section()
    except Exception as e:
        return ["Mini App", f"  Guard failed to run: {e}"]


def build_report() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"Nightly maintenance report for {today}", ""]
    parts += _backup_section() + [""]
    parts += _jobs_section() + [""]
    miniapp = _miniapp_section()
    if miniapp:
        parts += miniapp + [""]
    parts += _system_section()
    return "\n".join(parts).rstrip() + "\n"


def _record(text: str, delivered: bool) -> None:
    """Append what was sent, with a delivered flag (ported from the
    production bot's 2026-08-11 audit, finding B4).

    Without this, "what did last night's report say" has no answer on disk,
    and a delivery failure is indistinguishable from a quiet night. Never
    raises: keeping the record must not be able to break the send path.
    """
    try:
        REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "delivered": delivered,
                "chars": len(text),
                "text": text,
            }, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"nightly_report: could not record the report: {e}", file=sys.stderr)


def send(text: str) -> int:
    """Send the report via the bot's Telegram helper. Returns its exit code."""
    admin_id = get_primary_admin_id()
    if not admin_id:
        print("nightly_report: no admin or allowed users configured, nothing to send", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [
            PYTHON_CMD, str(SEND_SCRIPT),
            "--user", str(admin_id),
            "--message", text,
        ],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        print(f"send_to_telegram failed: rc={proc.returncode}", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
    _record(text, proc.returncode == 0)
    return proc.returncode


def main() -> int:
    report = build_report()
    print(report)
    if "--no-send" in sys.argv:
        return 0
    return send(report)


if __name__ == "__main__":
    sys.exit(main())
