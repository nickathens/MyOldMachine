#!/usr/bin/env python3
"""
CLI for managing scheduled jobs.
Usage:
    python scheduler_cli.py add --user 12345 --at "tomorrow 9am" --message "reminder text"
    python scheduler_cli.py list --user 12345
    python scheduler_cli.py remove --id <job_id> --user 12345
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import SCHEDULER_DIR
from core.scheduler import Scheduler, parse_natural_time


def main():
    parser = argparse.ArgumentParser(description="Scheduler CLI")
    sub = parser.add_subparsers(dest="command")

    add_p = sub.add_parser("add")
    add_p.add_argument("--user", type=int, required=True)
    add_p.add_argument("--at", type=str, required=True)
    add_p.add_argument("--message", type=str, required=True)
    add_p.add_argument("--repeat", type=str, default=None)

    list_p = sub.add_parser("list")
    list_p.add_argument("--user", type=int, required=True)

    rm_p = sub.add_parser("remove")
    rm_p.add_argument("--id", type=str, required=True)
    rm_p.add_argument("--user", type=int, required=True)

    args = parser.parse_args()

    sched = Scheduler(SCHEDULER_DIR / "scheduler.db")

    if args.command == "add":
        run_at = parse_natural_time(args.at)
        if not run_at:
            print(f"Could not parse time: {args.at}")
            sys.exit(1)
        job = sched.add_job(args.user, args.message, run_at, repeat=args.repeat)
        print(f"Added job {job.job_id} for {run_at:%Y-%m-%d %H:%M}: {args.message}")

    elif args.command == "list":
        jobs = sched.get_user_jobs(args.user)
        if not jobs:
            print("No jobs found.")
            return
        for j in jobs:
            repeat = f" [{j.repeat}]" if j.repeat else ""
            print(f"  {j.job_id}  {j.run_at:%Y-%m-%d %H:%M}{repeat}  {j.message[:60]}")

    elif args.command == "remove":
        job = sched.get_job(args.id)
        if not job:
            print(f"Job {args.id} not found.")
            sys.exit(1)
        if job.user_id != args.user:
            print("User mismatch.")
            sys.exit(1)
        sched.remove_job(args.id)
        print(f"Removed job {args.id}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
