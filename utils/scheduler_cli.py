#!/usr/bin/env python3
"""
CLI for managing scheduled jobs.

This CLI writes directly to the job_meta SQLite database.
The running bot's APScheduler sync loop (every 60s) picks up new jobs automatically.

Usage:
    # Reminders (a text message delivered at the set time)
    python scheduler_cli.py add --user 12345 --at "2026-02-17 08:00" --message "Pay rent"
    python scheduler_cli.py add --user 12345 --at "tomorrow 09:00" --message "Call dentist"
    python scheduler_cli.py add --user 12345 --at "in 30 minutes" --message "Check oven"
    python scheduler_cli.py add --user 12345 --at "09:00" --message "Countdown" --repeat daily --until "2026-03-17 09:00"

    # Agent tasks (wakes the assistant to actually DO the thing at the set
    # time — a reminder only texts about it). --no-notify runs it silently.
    python scheduler_cli.py add --user 12345 --at "14:25" --type agent --message "Attempt the Higgsfield login and report the result"
    python scheduler_cli.py add --user 12345 --at "07:00" --type agent --repeat daily --no-notify --message "Refresh the news digest cache"

    # Update (reword without losing the schedule)
    python scheduler_cli.py update --id abc123 --user 12345 --message "New reminder text"

    # Management
    python scheduler_cli.py list --user 12345
    python scheduler_cli.py remove --id abc123 --user 12345
"""

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.scheduler import (
    parse_natural_time,
    _init_meta_db,
    _save_meta,
    _get_meta,
    _get_all_meta,
    _delete_meta,
)


def parse_time(time_str: str) -> datetime:
    """Parse time string, raising on failure."""
    result = parse_natural_time(time_str)
    if result is None:
        raise ValueError(
            f"Cannot parse time: '{time_str}'\n"
            "Supported: '2026-02-17 08:00', 'tomorrow 09:00', "
            "'in 30 minutes', '09:00', 'at 3pm'"
        )
    return result


def cmd_add(args):
    """Add a new reminder or agent job."""
    run_at = parse_time(args.at)

    # A past one-shot does not error out in the engine — the sync loop treats
    # it as missed and fires it ~65s after creation. So a typo'd year goes off
    # immediately, possibly at night. Mirror the Mini App's guard and make
    # fire-now an explicit choice. Recurring jobs are exempt: their run_at is
    # just the time-of-day anchor.
    if not args.repeat and run_at < datetime.now() and not args.allow_past:
        print(f"ERROR: Cannot schedule a one-time event in the past "
              f"({run_at:%Y-%m-%d %H:%M}).")
        print("  A past one-shot fires immediately on the next sync (~60s). "
              "If that is intended, pass --allow-past.")
        return 1

    # Parse end date if provided
    end_date = None
    if args.until:
        end_date = parse_time(args.until)
        if end_date <= run_at:
            print(f"ERROR: --until ({end_date.strftime('%Y-%m-%d %H:%M')}) "
                  f"must be after --at ({run_at.strftime('%Y-%m-%d %H:%M')})")
            return 1

    job_id = str(uuid.uuid4())[:8]
    _save_meta(
        job_id=job_id,
        user_id=args.user,
        message=args.message,
        job_type=args.type,
        name=args.message[:50],
        notify=not args.no_notify,
        repeat=args.repeat,
        run_at=run_at,
        raw_at=args.at,
        created_context=f"cli: {args.message[:80]}",
        end_date=end_date,
    )
    repeat_text = f" (repeats {args.repeat})" if args.repeat else ""
    until_text = f" (until {end_date.strftime('%Y-%m-%d %H:%M')})" if end_date else ""
    print(f"Added {args.type} job {job_id} for {run_at:%Y-%m-%d %H:%M}{repeat_text}{until_text}: {args.message}")
    print("The bot will pick this up within 60 seconds.")
    return 0


def cmd_update(args):
    """Update a job's message or name without changing the schedule."""
    if not args.user:
        print("ERROR: --user is required for update.")
        return 1

    meta = _get_meta(args.id)
    if not meta:
        print(f"ERROR: Job '{args.id}' not found.")
        return 1

    if meta["user_id"] != args.user:
        print(f"ERROR: Job '{args.id}' belongs to user {meta['user_id']}, not {args.user}.")
        return 1

    from core.scheduler import _connect_db, DB_PATH

    conn = _connect_db(DB_PATH)
    updates = []
    params = []
    if args.message is not None:
        updates.append("message = ?")
        params.append(args.message)
        # Also update name if it was auto-derived from old message
        if args.name is None and meta.get("name") == meta.get("message", "")[:50]:
            updates.append("name = ?")
            params.append(args.message[:50])
    if args.name is not None:
        updates.append("name = ?")
        params.append(args.name)

    if not updates:
        conn.close()
        print("ERROR: Provide --message and/or --name to update.")
        return 1

    params.append(args.id)
    conn.execute(f"UPDATE job_meta SET {', '.join(updates)} WHERE job_id = ?", params)
    conn.commit()
    conn.close()

    print(f"OK: Job '{args.id}' updated.")
    if args.message is not None:
        print(f"  New message: {args.message}")
    if args.name is not None:
        print(f"  New name: {args.name}")
    print("  Schedule unchanged. Job ID, trigger, and recurrence preserved.")
    return 0


def cmd_list(args):
    """List scheduled jobs."""
    metas = _get_all_meta(user_id=args.user)
    if not metas:
        print("No jobs found.")
        return 0

    # Sort by run_at
    metas.sort(key=lambda m: m.get("run_at") or m["created_at"])

    for m in metas:
        repeat = f" [{m.get('repeat', '')}]" if m.get("repeat") else ""
        job_type = m.get("job_type", "reminder")
        type_tag = f" [{job_type}]" if job_type != "reminder" else ""
        run_at = m.get("run_at", "?")[:16] or "pending"

        end_date_str = ""
        if m.get("end_date"):
            end_date_str = f" until:{m['end_date'][:10]}"

        weekdays_str = ""
        if m.get("weekdays"):
            wds = m["weekdays"] if isinstance(m["weekdays"], list) else json.loads(m["weekdays"])
            day_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
            weekdays_str = f" ({','.join(day_names[d] for d in wds)})"

        display = m.get("name", m.get("message", ""))[:60]
        print(f"  {m['job_id']}  {run_at}{repeat}{end_date_str}{weekdays_str}{type_tag}  {display}")

    print(f"\nTotal: {len(metas)} job(s)")
    return 0


def cmd_remove(args):
    """Remove a job by ID."""
    if not args.user:
        print("ERROR: --user is required for remove.")
        return 1

    meta = _get_meta(args.id)
    if not meta:
        print(f"Job {args.id} not found.")
        return 1

    if meta["user_id"] != args.user:
        print(f"ERROR: Job '{args.id}' belongs to user {meta['user_id']}, not {args.user}.")
        return 1

    if meta.get("job_type") == "command" and not args.force:
        print(f"ERROR: Job '{args.id}' is a system command ({meta.get('name', '')}).")
        print("  Use --force to remove system command jobs.")
        return 1

    _delete_meta(args.id)
    print(f"Removed job {args.id}")
    print("APScheduler will sync within 60 seconds.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scheduler CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    add_p = sub.add_parser("add", help="Add a reminder or agent job")
    add_p.add_argument("--user", type=int, required=True, help="Telegram user ID")
    add_p.add_argument("--at", type=str, required=True, help="When: 'YYYY-MM-DD HH:MM', 'tomorrow 09:00', 'in 30 minutes'")
    add_p.add_argument("--message", type=str, required=True, help="Reminder text, or the task for --type agent")
    add_p.add_argument("--type", type=str, default="reminder",
                       choices=["reminder", "agent"],
                       help="reminder = text the message at the set time; "
                            "agent = wake the assistant to perform the message as a task")
    add_p.add_argument("--no-notify", action="store_true",
                       help="Agent jobs: don't send the task result to the user (run silently)")
    add_p.add_argument("--allow-past", action="store_true",
                       help="Allow a one-time job in the past (fires immediately on next sync)")
    add_p.add_argument("--repeat", type=str, default=None,
                       choices=["daily", "weekly", "biweekly", "monthly"],
                       help="Repeat schedule")
    add_p.add_argument("--until", type=str, default=None,
                       help="End date for recurring reminders. Job auto-expires after this date.")

    # update
    update_p = sub.add_parser("update", help="Update a job's message or name without changing its schedule")
    update_p.add_argument("--id", type=str, required=True, help="Job ID to update")
    update_p.add_argument("--user", type=int, default=None, help="User ID (ownership check)")
    update_p.add_argument("--message", type=str, default=None, help="New reminder message")
    update_p.add_argument("--name", type=str, default=None, help="New job name")

    # list
    list_p = sub.add_parser("list", help="List all jobs")
    list_p.add_argument("--user", type=int, required=True, help="User ID")

    # remove
    rm_p = sub.add_parser("remove", help="Remove a job")
    rm_p.add_argument("--id", type=str, required=True, help="Job ID to remove")
    rm_p.add_argument("--user", type=int, default=None, help="User ID (ownership check)")
    rm_p.add_argument("--force", action="store_true", help="Allow removing system command jobs")

    return parser


def main():
    args = build_parser().parse_args()

    from utils.session_guard import enforce as _enforce_session_user
    if getattr(args, "user", None) is not None:
        _enforce_session_user(args.user)

    # Ensure DB tables exist
    _init_meta_db()

    handlers = {
        "add": cmd_add,
        "update": cmd_update,
        "list": cmd_list,
        "remove": cmd_remove,
    }

    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
