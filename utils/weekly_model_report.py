#!/usr/bin/env python3
"""Weekly person-model change report.

Nightly reflection rewrites data/memory/people/<uid>/model.md and versions
the pre-rewrite copy into model_versions/ (14 kept, core.memory.set_model).
The mechanics are tested, but nobody reviews whether a night's edit made the
model better: the loop is closed on process and open on quality. This closes
it the cheap way (ported from the production bot, 2026-08-11): once a week,
diff each user's current model against the version nearest N days back and
send that user a short section-by-section change note, so a bad rewrite is
spotted in seconds instead of drifting for months.

Deterministic by design: pure file diff, no LLM call, so it can never
hallucinate a change and costs nothing against provider budgets.

Self-gating, so the weekly job can be registered unconditionally: a user is
considered only when their model.md exists AND at least one version snapshot
exists (i.e. reflection is actually rewriting models), and a week with no
changes is recorded but not sent (a product user should not get "no changes"
chatter; the admin's heartbeat surface is the nightly report).

The composed text is recorded to data/users/<uid>/weekly_model_report_log.jsonl
with sent/delivered flags (same contract as the nightly report's record), and
the full unified diff is written to data/memory/people/<uid>/weekly_reports/.
ponytail: weekly_reports/ is never pruned (~10KB/week); upgrade path is a
retention sweep in utils/cleanup.py if it ever matters.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from core.config import DATA_DIR  # noqa: E402
from core.memory import MemoryManager  # noqa: E402
from core.users import get_user, is_registered, resolve_user_dir  # noqa: E402
from utils.send_to_telegram import send_message  # noqa: E402

# Nightly churn, not a model change: reflection stamps this line on every
# rewrite, so diffing it would report a change every single week.
_LAST_UPDATED_RE = re.compile(r"^Last updated:", re.IGNORECASE)

# core.memory.set_model writes model_YYYY-MM-DD_HHMMSS.md (six-digit time).
_SNAPSHOT_RE = re.compile(r"^model_(\d{4}-\d{2}-\d{2}_\d{6})\.md$")
_SNAPSHOT_TS_FMT = "%Y-%m-%d_%H%M%S"

# Telegram hard limit is 4096; stay under it with headroom.
_MAX_MESSAGE_CHARS = 3500
_MAX_SECTION_BULLETS = 6
_SNIPPET_CHARS = 90


def pick_baseline(versions_dir: Path, now: datetime, days: int) -> tuple[Path, datetime] | None:
    """Snapshot whose timestamp is closest to `days` days before `now`.

    Closest-to-target rather than newest-older-than, so reflection skip-days
    can never starve the report of a baseline.
    """
    candidates = []
    if versions_dir.is_dir():
        for f in sorted(versions_dir.iterdir()):
            m = _SNAPSHOT_RE.match(f.name)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), _SNAPSHOT_TS_FMT)
            except ValueError:
                continue
            candidates.append((f, ts))
    if not candidates:
        return None
    target = now.timestamp() - days * 86400
    return min(candidates, key=lambda c: abs(c[1].timestamp() - target))


def split_sections(text: str) -> dict[str, list[str]]:
    """Split a model file into {section heading: body lines}.

    Content before the first "## " heading (title, Last updated) is dropped:
    the title never changes and the timestamp churns nightly.
    """
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None and not _LAST_UPDATED_RE.match(line):
            sections[current].append(line)
    return sections


def _display_name(heading: str) -> str:
    """Heading minus its parenthetical, e.g. 'Current State (active work)'."""
    return heading.split(" (")[0].strip()


def _snippet(line: str) -> str:
    text = line.strip().lstrip("-*+ ").strip()
    if len(text) > _SNIPPET_CHARS:
        text = text[:_SNIPPET_CHARS].rstrip() + "..."
    return text


def section_changes(old_text: str, new_text: str) -> list[dict]:
    """Per-section change summary between two model files.

    Returns one dict per changed section, in the new file's order (removed
    sections last): {name, added, removed, snippet, note}.
    """
    old_sections = split_sections(old_text)
    new_sections = split_sections(new_text)
    ordered = list(new_sections) + [s for s in old_sections if s not in new_sections]

    results = []
    for heading in ordered:
        name = _display_name(heading)
        if heading not in old_sections:
            results.append({"name": name, "added": 0, "removed": 0,
                            "snippet": "", "note": "section added"})
            continue
        if heading not in new_sections:
            results.append({"name": name, "added": 0, "removed": 0,
                            "snippet": "", "note": "section removed"})
            continue
        old_lines = [ln for ln in old_sections[heading] if ln.strip()]
        new_lines = [ln for ln in new_sections[heading] if ln.strip()]
        added = removed = 0
        snippet = ""
        for d in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
            if d.startswith("+++") or d.startswith("---") or d.startswith("@@"):
                continue
            if d.startswith("+"):
                added += 1
                if not snippet:
                    snippet = _snippet(d[1:])
            elif d.startswith("-"):
                removed += 1
        if added or removed:
            if not snippet:  # pure removals: show what left the model
                for d in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
                    if d.startswith("-") and not d.startswith("---"):
                        snippet = _snippet(d[1:])
                        break
            results.append({"name": name, "added": added, "removed": removed,
                            "snippet": snippet, "note": ""})
    return results


def compose_message(name: str, baseline_ts: datetime, now: datetime,
                    changes: list[dict], diff_path: Path | None,
                    all_section_names: list[str]) -> str:
    """The short note the user reads. Self-explanatory for a product user."""
    who = f" for {name}" if name else ""
    header = (f"Weekly memory report{who}\n"
              f"How my long-term model of you changed, "
              f"{baseline_ts.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}")
    if not changes:
        return header + "\nNo changes this week."

    lines = [header]
    for ch in changes[:_MAX_SECTION_BULLETS]:
        if ch["note"]:
            lines.append(f"• {ch['name']}: {ch['note']}")
        else:
            counts = f"{ch['added']} added, {ch['removed']} removed"
            entry = f"• {ch['name']}: {counts}"
            if ch["snippet"]:
                entry += f'. "{ch["snippet"]}"'
            lines.append(entry)
    overflow = len(changes) - _MAX_SECTION_BULLETS
    if overflow > 0:
        lines.append(f"• plus {overflow} more changed sections, see the diff")

    changed_names = {ch["name"] for ch in changes}
    unchanged = [n for n in all_section_names if n not in changed_names]
    if unchanged:
        lines.append("Unchanged: " + ", ".join(unchanged))
    if diff_path is not None:
        lines.append(f"Full diff: {diff_path}")
    lines.append("Wrong or outdated? Just tell me in chat and I will correct it.")

    message = "\n".join(lines)
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[:_MAX_MESSAGE_CHARS - 20].rstrip() + "\n[truncated]"
    return message


def write_diff_file(user_dir: Path, baseline: Path, old_text: str,
                    new_text: str, now: datetime) -> Path:
    reports_dir = user_dir / "weekly_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    diff_path = reports_dir / f"{now.strftime('%Y-%m-%d')}.diff"
    diff = "\n".join(difflib.unified_diff(
        old_text.split("\n"), new_text.split("\n"),
        fromfile=baseline.name, tofile="model.md", lineterm=""))
    diff_path.write_text(diff + "\n", encoding="utf-8")
    return diff_path


def record_report(user_id: int, message: str, sent: bool, delivered: bool) -> None:
    """Durable copy of what was composed, same contract as the nightly
    report's record: never raises, so keeping the record cannot break the
    send path."""
    try:
        path = resolve_user_dir(user_id) / "weekly_model_report_log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": datetime.now().isoformat(timespec="seconds"),
                 "sent": sent, "delivered": delivered, "message": message},
                ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"weekly_model_report: could not record for user {user_id}: {e}",
              file=sys.stderr)


def deliver(token: str, user_id: int, message: str) -> bool:
    try:
        return bool(send_message(token, user_id, message))
    except Exception as e:
        print(f"weekly_model_report: send failed for user {user_id}: {e}",
              file=sys.stderr)
        return False


def process_user(mm: MemoryManager, user_id: int, token: str, now: datetime,
                 days: int, dry_run: bool) -> str:
    """Run one user's report. Returns a status keyword for the summary."""
    user_dir = mm.people_dir / str(user_id)
    model_file = user_dir / "model.md"
    if not model_file.exists():
        return "no_model"
    baseline = pick_baseline(user_dir / "model_versions", now, days)
    if baseline is None:
        # Reflection has never rewritten this model; nothing to grade yet.
        return "no_snapshots"
    if not is_registered(user_id):
        # Never message an account that has been removed from the registry.
        return "unregistered"

    baseline_file, baseline_ts = baseline
    old_text = baseline_file.read_text(encoding="utf-8")
    new_text = model_file.read_text(encoding="utf-8")
    changes = section_changes(old_text, new_text)

    profile = get_user(user_id) or {}
    name = profile.get("display_name") or profile.get("name") or ""

    if not changes:
        if not dry_run:
            record_report(user_id, "No changes this week (not sent).",
                          sent=False, delivered=False)
        return "no_changes"

    diff_path = write_diff_file(user_dir, baseline_file, old_text, new_text, now)
    all_names = [_display_name(h) for h in split_sections(new_text)]
    message = compose_message(name, baseline_ts, now, changes, diff_path, all_names)

    if dry_run:
        # Print only: a rehearsal must not write rehearsal entries into the
        # user's report log (the diff file is still written, as documented).
        print(f"--- user {user_id} (dry run) ---\n{message}\n")
        return "dry_run"

    delivered = deliver(token, user_id, message)
    record_report(user_id, message, sent=True, delivered=delivered)
    return "reported" if delivered else "undelivered"


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly person-model change report")
    parser.add_argument("--user", type=int,
                        help="Only this Telegram user id (default: all users with memory)")
    parser.add_argument("--days", type=int, default=7,
                        help="Target lookback in days (default: 7)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print instead of sending (diff files are still written)")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token and not args.dry_run:
        print("Error: TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        return 1

    mm = MemoryManager(DATA_DIR)
    users = [args.user] if args.user else sorted(mm.get_all_users())

    now = datetime.now()
    statuses: dict[str, int] = {}
    failed = False
    for uid in users:
        status = process_user(mm, uid, token, now, args.days, args.dry_run)
        statuses[status] = statuses.get(status, 0) + 1
        if status == "undelivered":
            failed = True

    summary = ", ".join(f"{v} {k}" for k, v in sorted(statuses.items())) or "0 users"
    print(f"weekly_model_report: {summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
