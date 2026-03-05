"""
Job scheduler with SQLite persistence.
Supports reminders and recurring tasks.
"""

import asyncio
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def parse_natural_time(text: str) -> Optional[datetime]:
    """Parse natural language time: 'in 30 minutes', 'tomorrow at 9am', 'at 3pm', etc."""
    original = text.strip()
    text = text.lower().strip()
    now = datetime.now()

    try:
        return datetime.fromisoformat(original)
    except ValueError:
        pass

    m = re.match(r"in\s+(\d+)\s*(min(?:ute)?s?|hours?|days?|weeks?)", text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if "min" in unit:
            return now + timedelta(minutes=amount)
        elif "hour" in unit:
            return now + timedelta(hours=amount)
        elif "day" in unit:
            return now + timedelta(days=amount)
        elif "week" in unit:
            return now + timedelta(weeks=amount)

    m = re.match(r"tomorrow\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    m = re.match(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    return None


class Job:
    def __init__(self, job_id, user_id, message, run_at, repeat=None, job_type="reminder", name=None):
        self.job_id = job_id
        self.user_id = user_id
        self.message = message
        self.run_at = run_at
        self.repeat = repeat
        self.job_type = job_type
        self.name = name or job_id


class Scheduler:
    """Simple scheduler with SQLite persistence and asyncio execution."""

    def __init__(self, db_path: Path, send_fn=None):
        self.db_path = db_path
        self.send_fn = send_fn
        self._running = False
        self._task = None
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = _connect_db(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                job_type TEXT NOT NULL DEFAULT 'reminder',
                name TEXT NOT NULL DEFAULT '',
                repeat TEXT DEFAULT NULL,
                run_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def add_job(self, user_id: int, message: str, run_at: datetime,
                repeat: str = None, job_type: str = "reminder", name: str = None) -> Job:
        job_id = str(uuid.uuid4())[:8]
        conn = _connect_db(self.db_path)
        conn.execute(
            "INSERT INTO jobs (job_id, user_id, message, job_type, name, repeat, run_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, user_id, message, job_type, name or message[:50],
             repeat, run_at.isoformat(), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        return Job(job_id, user_id, message, run_at, repeat, job_type, name)

    def remove_job(self, job_id: str) -> bool:
        conn = _connect_db(self.db_path)
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        conn.commit()
        conn.close()
        return True

    def get_user_jobs(self, user_id: int) -> list[Job]:
        conn = _connect_db(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs WHERE user_id = ? ORDER BY run_at", (user_id,)).fetchall()
        conn.close()
        return [
            Job(r["job_id"], r["user_id"], r["message"],
                datetime.fromisoformat(r["run_at"]), r["repeat"], r["job_type"], r["name"])
            for r in rows
        ]

    def get_job(self, job_id: str) -> Optional[Job]:
        conn = _connect_db(self.db_path)
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        if not r:
            return None
        return Job(r["job_id"], r["user_id"], r["message"],
                   datetime.fromisoformat(r["run_at"]), r["repeat"], r["job_type"], r["name"])

    async def _check_and_fire(self):
        """Check for due jobs and fire them."""
        now = datetime.now()
        conn = _connect_db(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs WHERE run_at <= ?", (now.isoformat(),)).fetchall()

        for r in rows:
            job_id = r["job_id"]
            user_id = r["user_id"]
            message = r["message"]
            repeat = r["repeat"]

            # Fire the job
            if self.send_fn:
                try:
                    await self.send_fn(user_id, f"Reminder: {message}")
                except Exception as e:
                    logger.error(f"Failed to send reminder {job_id}: {e}")

            # Handle repeat or cleanup
            if repeat == "daily":
                new_run = datetime.fromisoformat(r["run_at"]) + timedelta(days=1)
                conn.execute("UPDATE jobs SET run_at = ? WHERE job_id = ?",
                             (new_run.isoformat(), job_id))
            elif repeat == "weekly":
                new_run = datetime.fromisoformat(r["run_at"]) + timedelta(weeks=1)
                conn.execute("UPDATE jobs SET run_at = ? WHERE job_id = ?",
                             (new_run.isoformat(), job_id))
            else:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

        conn.commit()
        conn.close()

    async def _run_loop(self):
        while self._running:
            try:
                await self._check_and_fire()
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            await asyncio.sleep(30)

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Scheduler started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("Scheduler stopped")
