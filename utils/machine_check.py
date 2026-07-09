#!/usr/bin/env python3
"""Machine sanity check -- assert this box against a per-machine baseline and report drift.

REPORT ONLY. It never installs, never edits PATH, never restarts anything. It surfaces
what regressed so a human fixes it on purpose. This is the layer that catches a silent
capability loss (a tool that vanishes from PATH, a Python module that stops importing, a
skill that quietly goes un-ready) instead of discovering it the next time you reach for it.

It does NOT re-probe the machine. The nightly system probe (core/system_probe.py, 04:30)
already writes data/system_caps.json with every binary, python module, and skill-readiness
result. This checker just diffs that fresh snapshot against a baseline of what was present
before, so the only new work is comparison and alerting. Pure JSON diff, so it is inherently
cross-platform (Linux and macOS alike) with no systemd, borg, or port assumptions.

The baseline (data/machine_baseline.json) is generated per machine, never committed
(data/* is gitignored), and ratchets forward: a newly installed capability raises the bar
and is protected from then on, while a capability that drops out stays flagged until it
returns or is explicitly re-blessed with --capture. That high-water-mark policy is what
keeps a vanished tool from silently becoming the new "normal" on the next run.

Design mirrors utils/nightly_report.py: reuse core.config.get_allowed_users for the admin
id and shell out to utils/send_to_telegram.py for the ping (which reloads the token from
.env itself, so the alert survives the scheduler's secret-stripped environment).

Usage:
    python utils/machine_check.py                 # print the summary (exit 1 on drift)
    python utils/machine_check.py --notify         # nightly: ping admin on drift only, ratchet baseline
    python utils/machine_check.py --capture        # (re)bless the current machine as the baseline
    python utils/machine_check.py --json           # machine-readable results
"""

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BOT_DIR / "data"
BASELINE_NAME = "machine_baseline.json"
CAPS_NAME = "system_caps.json"
SEND_SCRIPT = BOT_DIR / "utils" / "send_to_telegram.py"
VENV_PYTHON = BOT_DIR / ".venv" / "bin" / "python"
PYTHON_CMD = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

sys.path.insert(0, str(BOT_DIR))
from utils.safe_json import save_json  # noqa: E402

# The three capability dimensions compared against the baseline. Each maps a
# system_caps.json section to the predicate that means "this capability is good".
_DIMENSIONS = ("tools", "modules", "skills")
_DIMENSION_LABEL = {
    "tools": "Tools missing",
    "modules": "Python modules missing",
    "skills": "Skills degraded",
}


def load_caps(data_dir: Path) -> dict:
    """Load the latest system probe snapshot. Empty dict if it was never probed."""
    caps_file = data_dir / CAPS_NAME
    try:
        return json.loads(caps_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def good_sets(caps: dict) -> dict:
    """Extract the set of currently-good capabilities from a probe snapshot.

    tools   -- binaries reported available
    modules -- python modules that imported
    skills  -- skills whose deps are all satisfied (ready)
    """
    binaries = caps.get("binaries", {})
    modules = caps.get("python_modules", {})
    skills = caps.get("skills", {})
    return {
        "tools": {n for n, info in binaries.items() if isinstance(info, dict) and info.get("available")},
        "modules": {n for n, ok in modules.items() if ok},
        "skills": {n for n, info in skills.items() if isinstance(info, dict) and info.get("ready")},
    }


def is_empty(sets: dict) -> bool:
    """True if the probe yielded no good capabilities at all.

    A caps file that parses but carries nothing usable (a truncated or
    partial probe) must not be diffed, or every baseline entry would read as a
    regression and fire a false drift alert. Treat it as "no data" instead.
    """
    return not any(sets[d] for d in _DIMENSIONS)


def load_baseline(data_dir: Path) -> dict | None:
    """Load the declared baseline, or None if this machine has none yet."""
    path = data_dir / BASELINE_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def baseline_sets(baseline: dict) -> dict:
    """The good-capability sets recorded in a baseline document."""
    return {d: set(baseline.get(d, [])) for d in _DIMENSIONS}


def write_baseline(data_dir: Path, sets: dict, now: datetime) -> dict:
    """Persist a baseline document (atomic) and return it."""
    doc = {
        "_comment": ("Per-machine capability baseline for utils/machine_check.py. "
                     "Generated from data/system_caps.json, never committed. Ratchets "
                     "forward: re-bless with `machine_check.py --capture` after a "
                     "deliberate capability removal."),
        "captured_at": now.isoformat(timespec="seconds"),
        "os": platform.system(),
        "tools": sorted(sets["tools"]),
        "modules": sorted(sets["modules"]),
        "skills": sorted(sets["skills"]),
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    save_json(data_dir / BASELINE_NAME, doc, indent=2)
    return doc


def evaluate(current: dict, baseline: dict) -> dict:
    """Diff current good-sets against the baseline. Regression = was good, now not."""
    base = baseline_sets(baseline)
    regressions = {d: sorted(base[d] - current[d]) for d in _DIMENSIONS}
    has_drift = any(regressions[d] for d in _DIMENSIONS)
    return {"regressions": regressions, "has_drift": has_drift}


def ratchet(current: dict, baseline: dict) -> dict:
    """High-water-mark union: fold newly-good capabilities into the baseline while
    keeping every previously-good one (so a regression stays flagged until it returns
    or is explicitly re-blessed). Returns the new good-sets."""
    base = baseline_sets(baseline)
    return {d: base[d] | current[d] for d in _DIMENSIONS}


def _counts(sets: dict) -> str:
    return (f"{len(sets['tools'])} tools, {len(sets['modules'])} modules, "
            f"{len(sets['skills'])} skills")


def _regression_lines(regressions: dict) -> list:
    """One 'Label: a, b, c' line per dimension that actually regressed."""
    lines = []
    for d in _DIMENSIONS:
        if regressions[d]:
            lines.append(f"  {_DIMENSION_LABEL[d]}: {', '.join(regressions[d])}")
    return lines


def build_summary(evaluation: dict, current: dict) -> str:
    """Digest/stdout line: one green line, or the drift detail. Shared renderer so
    the printed summary and the ping can never describe drift differently."""
    if not evaluation["has_drift"]:
        return f"Machine sanity: OK. {_counts(current)} match baseline."
    return "\n".join(["Machine sanity: DRIFT DETECTED"] + _regression_lines(evaluation["regressions"]))


def build_drift_ping(regressions: dict, now: datetime) -> str:
    """The Telegram alert body for a regression event. Report only, no fix implied."""
    stamp = now.strftime("%Y-%m-%d %H:%M")
    lines = [f"Machine sanity check: DRIFT DETECTED ({stamp})", "Regressions from baseline:"]
    lines += _regression_lines(regressions)
    lines.append("")
    lines.append("Report only, nothing was changed. Run utils/machine_check.py for detail.")
    return "\n".join(lines)


def notify_admin(message: str, user_id: int) -> bool:
    """Ping the admin via the bot's Telegram helper (reused, not reimplemented).

    send_to_telegram.py reloads the token from .env, so the alert still goes out
    under the scheduler's secret-stripped environment. Returns True on success.
    """
    try:
        proc = subprocess.run(
            [PYTHON_CMD, str(SEND_SCRIPT), "--user", str(user_id), "--message", message],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"machine_check: notify failed to spawn ({e.__class__.__name__})", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"machine_check: notify exit {proc.returncode}: {proc.stderr.strip()[:200]}", file=sys.stderr)
    return proc.returncode == 0


def _resolve_admin(explicit: int | None) -> int | None:
    """Admin Telegram id: explicit flag wins, else the first allowed user."""
    if explicit:
        return explicit
    try:
        from core.config import get_allowed_users
        allowed = get_allowed_users()
    except Exception:
        return None
    return allowed[0] if allowed else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Machine sanity check (report only)")
    parser.add_argument("--notify", action="store_true",
                        help="Nightly mode: ping admin on drift only, then ratchet the baseline forward")
    parser.add_argument("--capture", action="store_true",
                        help="(Re)bless the current machine as the baseline, clearing any stale drift")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--user-id", type=int, default=None, help="Telegram id to notify (default: first allowed user)")
    parser.add_argument("--data-dir", default=None, help="Override data dir (default: <repo>/data)")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR
    now = datetime.now()

    caps = load_caps(data_dir)
    current = good_sets(caps)

    # No probe data (fresh install before the first probe, or a truncated file):
    # nothing to compare, so this is a benign no-op, never a drift alert.
    if not caps or is_empty(current):
        msg = "Machine sanity: no probe data yet (system_caps.json missing or empty); skipping."
        print(json.dumps({"ok": True, "skipped": "no-caps"}) if args.json else msg)
        return 0

    # Explicit (re)bless: current becomes the new baseline, wiping outstanding drift.
    if args.capture:
        write_baseline(data_dir, current, now)
        print(f"Machine sanity: baseline captured ({_counts(current)}). Future drift from this set will alert.")
        return 0

    baseline = load_baseline(data_dir)

    # First run on this machine: establish the baseline silently (no alert on
    # night one), then future drift from this set is what fires.
    if baseline is None:
        write_baseline(data_dir, current, now)
        print(f"Machine sanity: baseline established ({_counts(current)}). Future drift from this set will alert.")
        return 0

    evaluation = evaluate(current, baseline)

    # The scheduled nightly path (--notify) maintains the baseline: fold in any
    # newly-good capabilities so they are protected from now on, while the union
    # keeps every regression flagged until it is restored or re-blessed. A plain
    # manual check never mutates the baseline.
    if args.notify:
        write_baseline(data_dir, ratchet(current, baseline), now)

    if args.json:
        print(json.dumps({"ok": not evaluation["has_drift"], "regressions": evaluation["regressions"]}, indent=2))
    else:
        print(build_summary(evaluation, current))

    if args.notify and evaluation["has_drift"]:
        admin = _resolve_admin(args.user_id)
        if admin:
            sent = notify_admin(build_drift_ping(evaluation["regressions"], now), admin)
            print(f"machine_check: drift ping sent={sent}", file=sys.stderr)
        else:
            print("machine_check: drift detected but no admin user to notify", file=sys.stderr)

    # Drift is a normal self-reported result, not a checker failure. Under --notify
    # (the scheduled path) always exit 0, so the scheduler does not ALSO fire its
    # generic "Command job failed" ping on top of our own drift ping (it notifies on
    # any non-zero exit regardless of the job's notify flag). A genuine checker crash
    # still raises and exits non-zero, turning that same path into a free "the guard
    # itself broke" alert. Manual runs exit 1 on drift for scripting convenience.
    if args.notify:
        return 0
    return 1 if evaluation["has_drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
