#!/usr/bin/env python3
"""
CLI for managing scheduled jobs.

This CLI writes directly to the job_meta SQLite database.
The running bot's APScheduler sync loop (every 60s) picks up new jobs automatically.

Usage:
    python scheduler_cli.py add --user 12345 --at "tomorrow 9am" --message "reminder text"
    python scheduler_cli.py add --user 12345 --at "YYYY-MM-DD HH:MM" --message "text" --repeat daily
    python scheduler_cli.py list --user 12345
    python scheduler_cli.py remove --id <job_id> --user 12345
"""

import argparse
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


def main():
    parser = argparse.ArgumentParser(description="Scheduler CLI")
    sub = parser.add_subparsers(dest="command")

    add_p = sub.add_parser("add")
    add_p.add_argument("--user", type=int, required=True)
    add_p.add_argument("--at", type=str, required=True)
    add_p.add_argument("--message", type=str, required=True)
    add_p.add_argument("--repeat", type=str, default=None,
                       choices=["daily", "weekly", "monthly"])

    list_p = sub.add_parser("list")
    list_p.add_argument("--user", type=int, required=True)

    rm_p = sub.add_parser("remove")
    rm_p.add_argument("--id", type=str, required=True)
    rm_p.add_argument("--user", type=int, required=True)

    args = parser.parse_args()

    # Ensure DB tables exist
    _init_meta_db()

    if args.command == "add":
        run_at = parse_natural_time(args.at)
        if not run_at:
            print(f"Could not parse time: {args.at}")
            sys.exit(1)
        job_id = str(uuid.uuid4())[:8]
        _save_meta(
            job_id=job_id,
            user_id=args.user,
            message=args.message,
            job_type="reminder",
            name=args.message[:50],
            notify=True,
            repeat=args.repeat,
            run_at=run_at,
            raw_at=args.at,
            created_context=f"cli: {args.message[:80]}",
        )
        repeat_text = f" (repeats {args.repeat})" if args.repeat else ""
        print(f"Added job {job_id} for {run_at:%Y-%m-%d %H:%M}{repeat_text}: {args.message}")
        print("The bot will pick this up within 60 seconds.")

    elif args.command == "list":
        metas = _get_all_meta(user_id=args.user)
        if not metas:
            print("No jobs found.")
            return
        for m in metas:
            repeat = f" [{m.get('repeat', '')}]" if m.get("repeat") else ""
            job_type = m.get("job_type", "reminder")
            type_tag = f" [{job_type}]" if job_type != "reminder" else ""
            run_at = m.get("run_at", "?")
            print(f"  {m['job_id']}  {run_at}{repeat}{type_tag}  {m.get('message', '')[:60]}")

    elif args.command == "remove":
        meta = _get_meta(args.id)
        if not meta:
            print(f"Job {args.id} not found.")
            sys.exit(1)
        if meta["user_id"] != args.user:
            print("User mismatch.")
            sys.exit(1)
        if meta.get("job_type") == "command":
            print(f"Job {args.id} is a system command ({meta.get('name', '')}). "
                  f"System jobs can't be removed via CLI.")
            sys.exit(1)
        _delete_meta(args.id)
        print(f"Removed job {args.id}")
        print("APScheduler will sync within 60 seconds.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
