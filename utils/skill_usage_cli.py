#!/usr/bin/env python3
"""
Skill Usage CLI — query the skill invocation database.

Usage:
  python skill_usage_cli.py summary           # Top skills by count and duration
  python skill_usage_cli.py recent [N]        # Last N invocations (default 20)
  python skill_usage_cli.py failures [N]      # Last N failures (default 20)
  python skill_usage_cli.py daily [DAYS]      # Daily breakdown (default 30 days)
  python skill_usage_cli.py skill NAME        # Stats for one skill
  python skill_usage_cli.py session ID        # All invocations in a session
  python skill_usage_cli.py cleanup [DAYS]    # Remove records older than N days (default 90)
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Dynamic path: DB lives at BOT_DIR/data/skill_usage.db
BOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BOT_DIR / "data" / "skill_usage.db"


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"No usage database found at {DB_PATH}")
        print("The database is created automatically when skills are used with hooks active.")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def fmt_time(epoch: float | None) -> str:
    if epoch is None:
        return "\u2014"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def fmt_duration(ms: int | float | None) -> str:
    if ms is None:
        return "\u2014"
    ms = int(round(ms))
    if ms < 0:
        return "0ms"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"


def cmd_summary(args):
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM skill_invocations").fetchone()[0]
        if total == 0:
            print("No invocations recorded yet.")
            return

        print(f"Total invocations: {total}\n")

        print("=== Top Skills by Invocation Count ===")
        print(f"{'Skill':<25} {'Count':>7} {'Success':>8} {'Failed':>7} {'Avg Duration':>13}")
        print("-" * 65)
        rows = conn.execute("""
            SELECT skill_name,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as ok,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail,
                   SUM(CASE WHEN success IS NULL THEN 1 ELSE 0 END) as pending,
                   AVG(duration_ms) as avg_dur
            FROM skill_invocations
            GROUP BY skill_name
            ORDER BY cnt DESC
        """).fetchall()
        for r in rows:
            fail_str = str(r['fail'])
            if r['pending'] > 0:
                fail_str += f" (+{r['pending']}?)"
            print(f"{r['skill_name']:<25} {r['cnt']:>7} {r['ok']:>8} {fail_str:>7} {fmt_duration(r['avg_dur']):>13}")

        print()

        print("=== Top Skills by Total Duration ===")
        print(f"{'Skill':<25} {'Total Time':>12} {'Count':>7} {'Avg Duration':>13}")
        print("-" * 60)
        rows = conn.execute("""
            SELECT skill_name,
                   SUM(duration_ms) as total_dur,
                   COUNT(*) as cnt,
                   AVG(duration_ms) as avg_dur
            FROM skill_invocations
            WHERE duration_ms IS NOT NULL
            GROUP BY skill_name
            ORDER BY total_dur DESC
            LIMIT 15
        """).fetchall()
        for r in rows:
            print(f"{r['skill_name']:<25} {fmt_duration(r['total_dur']):>12} {r['cnt']:>7} {fmt_duration(r['avg_dur']):>13}")

        fails = conn.execute("""
            SELECT COUNT(*) FROM skill_invocations WHERE success = 0
        """).fetchone()[0]
        if fails > 0:
            print(f"\n{fails} total failures. Run 'failures' command for details.")
    finally:
        conn.close()


def cmd_recent(args):
    n = args.count or 20
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT skill_name, started_at, duration_ms, success, error, session_id
            FROM skill_invocations
            ORDER BY started_at DESC
            LIMIT ?
        """, (n,)).fetchall()
        if not rows:
            print("No invocations recorded yet.")
            return

        print(f"{'Time':<20} {'Skill':<22} {'Duration':>10} {'Status':>8} {'Session':>10}")
        print("-" * 75)
        for r in rows:
            status = "OK" if r["success"] == 1 else ("FAIL" if r["success"] == 0 else "?")
            sess = (r["session_id"] or "")[:8]
            print(f"{fmt_time(r['started_at']):<20} {r['skill_name']:<22} "
                  f"{fmt_duration(r['duration_ms']):>10} {status:>8} {sess:>10}")
    finally:
        conn.close()


def cmd_failures(args):
    n = args.count or 20
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT skill_name, started_at, duration_ms, error, command, session_id
            FROM skill_invocations
            WHERE success = 0
            ORDER BY started_at DESC
            LIMIT ?
        """, (n,)).fetchall()
        if not rows:
            print("No failures recorded.")
            return

        print(f"Last {len(rows)} failures:\n")
        for r in rows:
            print(f"  {fmt_time(r['started_at'])}  {r['skill_name']}  ({fmt_duration(r['duration_ms'])})")
            if r["error"]:
                err = r["error"][:120].replace("\n", " ")
                print(f"    Error: {err}")
            if r["command"]:
                cmd = r["command"][:100].replace("\n", " ")
                print(f"    Command: {cmd}")
            print()
    finally:
        conn.close()


def cmd_daily(args):
    days = args.days or 30
    conn = get_db()
    try:
        cutoff = time.time() - (days * 86400)
        rows = conn.execute("""
            SELECT date(started_at, 'unixepoch', 'localtime') as day,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as ok,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail,
                   SUM(duration_ms) as total_dur,
                   COUNT(DISTINCT skill_name) as unique_skills
            FROM skill_invocations
            WHERE started_at >= ?
            GROUP BY day
            ORDER BY day DESC
        """, (cutoff,)).fetchall()
        if not rows:
            print(f"No invocations in the last {days} days.")
            return

        print(f"{'Date':<12} {'Count':>7} {'OK':>5} {'Fail':>5} {'Skills':>7} {'Total Time':>12}")
        print("-" * 55)
        for r in rows:
            print(f"{r['day']:<12} {r['cnt']:>7} {r['ok']:>5} {r['fail']:>5} "
                  f"{r['unique_skills']:>7} {fmt_duration(r['total_dur']):>12}")
    finally:
        conn.close()


def cmd_skill(args):
    name = args.name
    conn = get_db()
    try:
        stats = conn.execute("""
            SELECT COUNT(*) as cnt,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as ok,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail,
                   AVG(duration_ms) as avg_dur,
                   MIN(duration_ms) as min_dur,
                   MAX(duration_ms) as max_dur,
                   SUM(duration_ms) as total_dur,
                   AVG(ram_mb_start) as avg_ram,
                   MIN(started_at) as first_use,
                   MAX(started_at) as last_use
            FROM skill_invocations
            WHERE skill_name = ?
        """, (name,)).fetchone()

        if stats["cnt"] == 0:
            print(f"No invocations found for skill '{name}'.")
            skills = conn.execute("""
                SELECT DISTINCT skill_name FROM skill_invocations ORDER BY skill_name
            """).fetchall()
            if skills:
                print(f"Available: {', '.join(r['skill_name'] for r in skills)}")
            return

        print(f"=== Skill: {name} ===\n")
        print(f"  Total invocations:  {stats['cnt']}")
        print(f"  Successful:         {stats['ok']}")
        print(f"  Failed:             {stats['fail']}")
        print(f"  First used:         {fmt_time(stats['first_use'])}")
        print(f"  Last used:          {fmt_time(stats['last_use'])}")
        print(f"  Avg duration:       {fmt_duration(stats['avg_dur'])}")
        print(f"  Min duration:       {fmt_duration(stats['min_dur'])}")
        print(f"  Max duration:       {fmt_duration(stats['max_dur'])}")
        print(f"  Total time:         {fmt_duration(stats['total_dur'])}")
        if stats["avg_ram"] is not None:
            print(f"  Avg RAM at start:   {int(stats['avg_ram'])} MB free")

        print(f"\n  Last 10 invocations:")
        print(f"  {'Time':<20} {'Duration':>10} {'Status':>8} {'RAM Free':>10}")
        print(f"  {'-' * 52}")
        rows = conn.execute("""
            SELECT started_at, duration_ms, success, ram_mb_start, error
            FROM skill_invocations
            WHERE skill_name = ?
            ORDER BY started_at DESC
            LIMIT 10
        """, (name,)).fetchall()
        for r in rows:
            status = "OK" if r["success"] == 1 else ("FAIL" if r["success"] == 0 else "?")
            ram = f"{r['ram_mb_start']}MB" if r["ram_mb_start"] else "\u2014"
            print(f"  {fmt_time(r['started_at']):<20} {fmt_duration(r['duration_ms']):>10} "
                  f"{status:>8} {ram:>10}")
            if r["error"]:
                print(f"    Error: {r['error'][:100]}")
    finally:
        conn.close()


def cmd_session(args):
    sid = args.session_id
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT skill_name, started_at, duration_ms, success, error, ram_mb_start
            FROM skill_invocations
            WHERE session_id = ? OR session_id LIKE ?
            ORDER BY started_at ASC
        """, (sid, f"{sid}%")).fetchall()
        if not rows:
            print(f"No invocations found for session '{sid}'.")
            return

        print(f"Session: {sid}")
        print(f"Invocations: {len(rows)}\n")
        print(f"{'Time':<20} {'Skill':<22} {'Duration':>10} {'Status':>8} {'RAM Free':>10}")
        print("-" * 75)
        for r in rows:
            status = "OK" if r["success"] == 1 else ("FAIL" if r["success"] == 0 else "?")
            ram = f"{r['ram_mb_start']}MB" if r["ram_mb_start"] else "\u2014"
            print(f"{fmt_time(r['started_at']):<20} {r['skill_name']:<22} "
                  f"{fmt_duration(r['duration_ms']):>10} {status:>8} {ram:>10}")
            if r["error"]:
                print(f"  Error: {r['error'][:100]}")
    finally:
        conn.close()


def cmd_cleanup(args):
    days = args.days or 90
    conn = get_db()
    try:
        cutoff = time.time() - (days * 86400)
        before = conn.execute("SELECT COUNT(*) FROM skill_invocations").fetchone()[0]
        conn.execute("DELETE FROM skill_invocations WHERE started_at < ?", (cutoff,))
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM skill_invocations").fetchone()[0]
        removed = before - after
        print(f"Removed {removed} records older than {days} days. {after} records remaining.")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Query skill usage statistics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="Top skills by count and duration")

    p = sub.add_parser("recent", help="Last N invocations")
    p.add_argument("count", nargs="?", type=int, default=20)

    p = sub.add_parser("failures", help="Last N failures")
    p.add_argument("count", nargs="?", type=int, default=20)

    p = sub.add_parser("daily", help="Daily breakdown")
    p.add_argument("days", nargs="?", type=int, default=30)

    p = sub.add_parser("skill", help="Stats for one skill")
    p.add_argument("name", help="Skill name")

    p = sub.add_parser("session", help="Invocations in a session")
    p.add_argument("session_id", help="Session ID (or prefix)")

    p = sub.add_parser("cleanup", help="Remove old records")
    p.add_argument("days", nargs="?", type=int, default=90)

    args = parser.parse_args()
    cmd_map = {
        "summary": cmd_summary,
        "recent": cmd_recent,
        "failures": cmd_failures,
        "daily": cmd_daily,
        "skill": cmd_skill,
        "session": cmd_session,
        "cleanup": cmd_cleanup,
    }
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
