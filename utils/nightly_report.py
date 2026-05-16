#!/usr/bin/env python3
"""Send a single nightly maintenance summary to the admin user.

Runs at 04:45 (after cleanup 03:30, system update 04:00, system probe 04:30,
and well after Time Machine's 01:00 backup window). Collects:
  - Time Machine last backup time, result, used/free bytes (from system plist)
  - Whether cleanup / system update / system probe succeeded last night
  - System probe disk free / RAM snapshot
"""

import json
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
TM_PLIST = Path("/Library/Preferences/com.apple.TimeMachine.plist")
SEND_SCRIPT = BOT_DIR / "utils" / "send_to_telegram.py"
VENV_PYTHON = BOT_DIR / ".venv" / "bin" / "python"

ADMIN_USER_ID = 8044898180

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
        lines.append(f"  Last brew update: {update_msg}")
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


def build_report() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [f"Nightly maintenance report for {today}", ""]
    parts += _time_machine_section() + [""]
    parts += _jobs_section() + [""]
    parts += _system_section()
    return "\n".join(parts).rstrip() + "\n"


def send(text: str) -> int:
    """Send the report via the bot's Telegram helper. Returns its exit code."""
    proc = subprocess.run(
        [
            str(VENV_PYTHON), str(SEND_SCRIPT),
            "--user", str(ADMIN_USER_ID),
            "--message", text,
        ],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        print(f"send_to_telegram failed: rc={proc.returncode}", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


def main() -> int:
    report = build_report()
    print(report)
    if "--no-send" in sys.argv:
        return 0
    return send(report)


if __name__ == "__main__":
    sys.exit(main())
