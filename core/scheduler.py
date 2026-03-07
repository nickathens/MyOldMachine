#!/usr/bin/env python3
"""
Scheduler for MyOldMachine — APScheduler Engine

Uses APScheduler 3.x with SQLite persistence for reliable job scheduling.
Supports: reminders, shell commands, and Claude agent tasks.

Key features:
- SQLite job store (survives crashes, no JSON corruption)
- Missed job recovery (fires jobs that were due while bot was down)
- APScheduler handles all timing, triggers, and persistence
- Sync loop picks up jobs added via CLI
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

logger = logging.getLogger(__name__)

# Storage location
SCHEDULER_DIR = Path(__file__).parent.parent / "data" / "scheduler"
DB_PATH = SCHEDULER_DIR / "scheduler.db"
HISTORY_DB_PATH = SCHEDULER_DIR / "history.db"


def ensure_scheduler_dir():
    """Ensure scheduler directory exists."""
    SCHEDULER_DIR.mkdir(parents=True, exist_ok=True)


def _connect_db(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and busy timeout."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def parse_natural_time(text: str) -> Optional[datetime]:
    """
    Parse natural language time expressions.

    Supports:
    - "in 5 minutes", "in 2 hours", "in 1 day"
    - "tomorrow at 9am", "tomorrow at 14:00"
    - "at 3pm", "at 15:30"
    - "next monday at 10am"
    - ISO format: "2026-02-01T15:00:00"
    """
    original_text = text.strip()
    text = text.lower().strip()
    now = datetime.now()

    # Try ISO format first
    try:
        return datetime.fromisoformat(original_text)
    except ValueError:
        pass

    # "in X minutes/hours/days"
    in_pattern = re.match(r'in\s+(\d+)\s*(min(?:ute)?s?|hours?|days?|weeks?)', text)
    if in_pattern:
        amount = int(in_pattern.group(1))
        unit = in_pattern.group(2)
        if 'min' in unit:
            return now + timedelta(minutes=amount)
        elif 'hour' in unit:
            return now + timedelta(hours=amount)
        elif 'day' in unit:
            return now + timedelta(days=amount)
        elif 'week' in unit:
            return now + timedelta(weeks=amount)

    # "tomorrow at HH:MM" or "tomorrow at Ham/pm"
    tomorrow_pattern = re.match(r'tomorrow\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    if tomorrow_pattern:
        hour = int(tomorrow_pattern.group(1))
        minute = int(tomorrow_pattern.group(2) or 0)
        ampm = tomorrow_pattern.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # "at HH:MM" or "at Ham/pm" — requires "at" keyword, HH:MM format, or am/pm suffix
    # All patterns normalized to 3 groups: (hour, minute_or_None, ampm_or_None)
    at_pattern = re.match(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    if not at_pattern:
        # Match "3pm", "10am", "10:30am" (bare number requires am/pm or colon)
        at_pattern = re.match(r'(\d{1,2}):(\d{2})\s*(am|pm)?$', text)
    if not at_pattern:
        # Match "3pm", "10am" — only 2 groups, so add a non-capturing minute group
        at_pattern = re.match(r'(\d{1,2})()\s*(am|pm)', text)
    if at_pattern:
        hour = int(at_pattern.group(1))
        minute = int(at_pattern.group(2) or 0)
        ampm = at_pattern.group(3)
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # Weekday names
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    for i, day in enumerate(weekdays):
        if day in text:
            time_match = re.search(r'at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
            hour, minute = 9, 0
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                ampm = time_match.group(3)
                if ampm == 'pm' and hour < 12:
                    hour += 12
                elif ampm == 'am' and hour == 12:
                    hour = 0
            current_weekday = now.weekday()
            days_ahead = i - current_weekday
            if days_ahead <= 0:
                days_ahead += 7
            target = now + timedelta(days=days_ahead)
            return target.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return None


# ---------------------------------------------------------------------------
# Job metadata database (parallel to APScheduler's job store)
# ---------------------------------------------------------------------------

def _init_meta_db():
    """Create the job metadata table if it doesn't exist."""
    ensure_scheduler_dir()
    conn = _connect_db(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_meta (
            job_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            job_type TEXT NOT NULL DEFAULT 'reminder',
            name TEXT NOT NULL DEFAULT '',
            notify INTEGER NOT NULL DEFAULT 1,
            command TEXT DEFAULT NULL,
            log_file TEXT DEFAULT NULL,
            repeat TEXT DEFAULT NULL,
            weekdays TEXT DEFAULT NULL,
            channel TEXT NOT NULL DEFAULT 'telegram',
            created_at TEXT NOT NULL,
            run_at TEXT NOT NULL DEFAULT '',
            raw_at TEXT NOT NULL DEFAULT '',
            created_context TEXT NOT NULL DEFAULT ''
        )
    """)
    # Upgrade path: add columns if missing
    for col, default in [("run_at", "''"), ("raw_at", "''"), ("created_context", "''")]:
        try:
            conn.execute(f"SELECT {col} FROM job_meta LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE job_meta ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}")
    conn.commit()
    conn.close()


def _init_history_db():
    """Create the execution history table."""
    ensure_scheduler_dir()
    conn = _connect_db(HISTORY_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT,
            executed_at TEXT NOT NULL,
            success INTEGER NOT NULL,
            error TEXT
        )
    """)
    conn.commit()
    conn.close()


def _save_meta(job_id: str, user_id: int, message: str, job_type: str,
               name: str, notify: bool, command: str = None,
               log_file: str = None, repeat: str = None,
               weekdays: list = None, channel: str = "telegram",
               run_at: datetime = None, raw_at: str = "",
               created_context: str = ""):
    """Save job metadata to SQLite."""
    conn = _connect_db(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO job_meta
        (job_id, user_id, message, job_type, name, notify, command,
         log_file, repeat, weekdays, channel, created_at, run_at,
         raw_at, created_context)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id, user_id, message, job_type, name, int(notify),
        command, log_file, repeat,
        json.dumps(weekdays) if weekdays else None,
        channel, datetime.now().isoformat(),
        run_at.isoformat() if run_at else datetime.now().isoformat(),
        raw_at, created_context,
    ))
    conn.commit()
    conn.close()


def _get_meta(job_id: str) -> Optional[dict]:
    """Get job metadata from SQLite."""
    conn = _connect_db(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM job_meta WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["notify"] = bool(d["notify"])
    d["weekdays"] = json.loads(d["weekdays"]) if d["weekdays"] else None
    return d


def _get_all_meta(user_id: int = None) -> list[dict]:
    """Get all job metadata, optionally filtered by user_id."""
    conn = _connect_db(DB_PATH)
    conn.row_factory = sqlite3.Row
    if user_id is not None:
        rows = conn.execute("SELECT * FROM job_meta WHERE user_id = ?", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM job_meta").fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["notify"] = bool(d["notify"])
        d["weekdays"] = json.loads(d["weekdays"]) if d["weekdays"] else None
        result.append(d)
    return result


def _delete_meta(job_id: str):
    """Delete job metadata from SQLite."""
    conn = _connect_db(DB_PATH)
    conn.execute("DELETE FROM job_meta WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()


def _log_execution(job_id: str, user_id: int, message: str,
                   success: bool, error: str = None):
    """Log job execution to history database."""
    conn = _connect_db(HISTORY_DB_PATH)
    conn.execute("""
        INSERT INTO history (job_id, user_id, message, executed_at, success, error)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (job_id, user_id, message[:100], datetime.now().isoformat(),
          int(success), error))
    # Keep only last 200 entries
    conn.execute("""
        DELETE FROM history WHERE id NOT IN (
            SELECT id FROM history ORDER BY id DESC LIMIT 200
        )
    """)
    conn.commit()
    conn.close()


def _get_history(limit: int = 20) -> list[dict]:
    """Get recent execution history."""
    conn = _connect_db(HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


# ---------------------------------------------------------------------------
# Job class
# ---------------------------------------------------------------------------

class Job:
    """Represents a scheduled job."""

    def __init__(self, job_id: str, user_id: int, message: str,
                 run_at: datetime, created_at: datetime = None,
                 repeat: str = None, channel: str = "telegram",
                 job_type: str = "reminder", name: str = None,
                 notify: bool = True, command: str = None,
                 weekdays: list = None, log_file: str = None):
        self.job_id = job_id
        self.user_id = user_id
        self.message = message
        self.run_at = run_at
        self.created_at = created_at or datetime.now()
        self.repeat = repeat
        self.channel = channel
        self.job_type = job_type
        self.name = name or job_id
        self.notify = notify
        self.command = command
        self.weekdays = weekdays
        self.log_file = log_file

    def to_dict(self) -> dict:
        d = {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "message": self.message,
            "run_at": self.run_at.isoformat() if isinstance(self.run_at, datetime) else self.run_at,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "repeat": self.repeat,
            "channel": self.channel,
            "job_type": self.job_type,
            "name": self.name,
            "notify": self.notify,
        }
        if self.command:
            d["command"] = self.command
        if self.weekdays:
            d["weekdays"] = self.weekdays
        if self.log_file:
            d["log_file"] = self.log_file
        return d

    @classmethod
    def from_meta(cls, meta: dict, run_at: datetime = None) -> 'Job':
        """Create a Job from metadata dict."""
        return cls(
            job_id=meta["job_id"],
            user_id=meta["user_id"],
            message=meta.get("message", ""),
            run_at=run_at or datetime.now(),
            created_at=datetime.fromisoformat(meta["created_at"]) if meta.get("created_at") else datetime.now(),
            repeat=meta.get("repeat"),
            channel=meta.get("channel", "telegram"),
            job_type=meta.get("job_type", "reminder"),
            name=meta.get("name"),
            notify=meta.get("notify", True),
            command=meta.get("command"),
            weekdays=meta.get("weekdays"),
            log_file=meta.get("log_file"),
        )


# ---------------------------------------------------------------------------
# Job execution functions (module-level for APScheduler serialization)
# ---------------------------------------------------------------------------

async def _send_with_retry(scheduler, user_id: int, text: str, max_retries: int = 3) -> bool:
    """Send a message with exponential backoff retry."""
    for attempt in range(max_retries):
        success = await scheduler.send_message(user_id, text)
        if success:
            return True
        if attempt < max_retries - 1:
            wait = 2 ** attempt * 5
            logger.warning(f"Send failed (attempt {attempt + 1}/{max_retries}), retrying in {wait}s...")
            await asyncio.sleep(wait)
    return False


async def _execute_reminder(job_id: str):
    """Execute a reminder job."""
    scheduler = get_scheduler()
    if not scheduler:
        logger.error(f"No scheduler for reminder {job_id}")
        return

    meta = _get_meta(job_id)
    if not meta:
        logger.error(f"No metadata for reminder {job_id}")
        return

    success = await _send_with_retry(
        scheduler, meta["user_id"],
        f"\U0001f514 Reminder: {meta['message']}"
    )
    _log_execution(job_id, meta["user_id"], meta["message"], success,
                   None if success else "Failed to send after 3 retries")

    if not meta.get("repeat"):
        if success:
            _delete_meta(job_id)
        else:
            logger.error(f"One-shot reminder {job_id} FAILED delivery -- metadata kept for recovery")


async def _execute_command(job_id: str):
    """Execute a shell command job."""
    scheduler = get_scheduler()
    if not scheduler:
        logger.error(f"No scheduler for command {job_id}")
        return

    meta = _get_meta(job_id)
    if not meta:
        logger.error(f"No metadata for command {job_id}")
        return

    # Weekday filter
    if meta.get("weekdays") and datetime.now().weekday() not in meta["weekdays"]:
        logger.info(f"Skipping command {job_id} ({meta['name']}) - not a matching weekday")
        return

    command = meta.get("command")
    if not command:
        logger.error(f"Command job {job_id} has no command set")
        _log_execution(job_id, meta["user_id"], "", False, "No command specified")
        return

    try:
        logger.info(f"Running command job {job_id} ({meta['name']}): {command[:80]}...")

        # Use sanitized environment (strips API keys/tokens)
        from core.tools import _build_command_env
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_build_command_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""

        success = proc.returncode == 0

        if meta.get("log_file"):
            try:
                with open(meta["log_file"], "a") as f:
                    f.write(output)
            except Exception as e:
                logger.warning(f"Failed to write log for job {job_id}: {e}")

        if not success:
            error_msg = f"Command job failed: {meta['name']}\nExit code: {proc.returncode}\n{output[-500:]}"
            await scheduler.send_message(meta["user_id"], error_msg)
            _log_execution(job_id, meta["user_id"], meta.get("message", ""),
                           False, f"Exit code {proc.returncode}")
        else:
            if meta.get("notify") and meta.get("message"):
                await scheduler.send_message(meta["user_id"], meta["message"])
            _log_execution(job_id, meta["user_id"], meta.get("message", ""), True)

        logger.info(f"Command job {job_id} finished (exit={proc.returncode})")

    except asyncio.TimeoutError:
        logger.error(f"Command job {job_id} timed out after 300s")
        _log_execution(job_id, meta["user_id"], "", False, "Timed out after 300s")
        await scheduler.send_message(meta["user_id"], f"Command job timed out: {meta['name']}")
    except Exception as e:
        logger.error(f"Command job {job_id} failed: {e}")
        _log_execution(job_id, meta["user_id"], "", False, str(e))
        await scheduler.send_message(meta["user_id"], f"Command job error: {meta['name']}\n{str(e)[:200]}")

    if not meta.get("repeat"):
        _delete_meta(job_id)


async def _execute_agent(job_id: str):
    """Execute a Claude agent job."""
    scheduler = get_scheduler()
    if not scheduler:
        logger.error(f"No scheduler for agent {job_id}")
        return

    meta = _get_meta(job_id)
    if not meta:
        logger.error(f"No metadata for agent {job_id}")
        return

    if not scheduler._call_claude_fn:
        logger.error(f"No Claude handler set for agent job {job_id}")
        _log_execution(job_id, meta["user_id"], meta["message"], False, "No Claude handler")
        return

    success = False
    try:
        logger.info(f"Running agent job {job_id} ({meta['name']}): {meta['message'][:50]}...")

        task_prompt = f"[Scheduled Task: {meta['name']}]\n\n{meta['message']}"
        response = await scheduler._call_claude_fn(meta["user_id"], task_prompt)

        if meta.get("notify"):
            result_msg = f"\u23f0 Scheduled task complete: {meta['name']}\n\n{response}"
            if len(result_msg) > 4000:
                result_msg = result_msg[:3900] + "\n\n... (truncated)"
            await _send_with_retry(scheduler, meta["user_id"], result_msg)

        logger.info(f"Agent job {job_id} completed successfully")
        _log_execution(job_id, meta["user_id"], meta["message"], True)
        success = True

    except Exception as e:
        logger.error(f"Agent job {job_id} failed: {e}")
        _log_execution(job_id, meta["user_id"], meta["message"], False, str(e))
        if meta.get("notify"):
            await _send_with_retry(
                scheduler, meta["user_id"],
                f"\u26a0\ufe0f Scheduled task failed: {meta['name']}\nError: {str(e)[:200]}"
            )

    if not meta.get("repeat"):
        if success:
            _delete_meta(job_id)
        else:
            logger.error(f"One-shot agent job {job_id} FAILED -- metadata kept for recovery")


_JOB_EXECUTORS = {
    "reminder": _execute_reminder,
    "command": _execute_command,
    "agent": _execute_agent,
}


# ---------------------------------------------------------------------------
# Scheduler class
# ---------------------------------------------------------------------------

class Scheduler:
    """Manages scheduled jobs using APScheduler with SQLite persistence."""

    def __init__(self, bot_token: str, api_base: str = None):
        self.bot_token = bot_token
        self.api_base = api_base  # None = use official Telegram API
        self._call_claude_fn = None
        self._sync_running = False
        self._sync_task = None

        ensure_scheduler_dir()
        _init_meta_db()
        _init_history_db()

        # Configure APScheduler with SQLite job store
        jobstores = {
            'default': SQLAlchemyJobStore(url=f'sqlite:///{DB_PATH}')
        }
        job_defaults = {
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 3600,
        }
        self._aps = AsyncIOScheduler(
            jobstores=jobstores,
            job_defaults=job_defaults,
        )
        self._aps.add_listener(self._on_job_event,
                               EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    def _on_job_event(self, event):
        """Handle APScheduler job events."""
        if hasattr(event, 'exception') and event.exception:
            logger.error(f"APScheduler job {event.job_id} raised: {event.exception}")
        elif event.code == EVENT_JOB_MISSED:
            logger.warning(f"APScheduler job {event.job_id} was missed (misfire)")

    def set_claude_handler(self, call_claude_fn):
        """Set the function to call Claude for agent jobs."""
        self._call_claude_fn = call_claude_fn

    @property
    def jobs(self) -> dict:
        """Compatibility property -- returns dict of job_id -> metadata."""
        metas = _get_all_meta()
        return {m["job_id"]: m for m in metas}

    def _build_trigger(self, run_at: datetime, repeat: str = None, weekdays: list = None):
        """Build an APScheduler trigger from our repeat/weekday parameters."""
        if not repeat:
            return DateTrigger(run_date=run_at)

        day_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu',
                   4: 'fri', 5: 'sat', 6: 'sun'}

        if repeat == "daily":
            if weekdays:
                days = ','.join(day_map[d] for d in weekdays)
                return CronTrigger(day_of_week=days, hour=run_at.hour, minute=run_at.minute, second=0)
            return CronTrigger(hour=run_at.hour, minute=run_at.minute, second=0)
        elif repeat == "weekly":
            return CronTrigger(
                day_of_week=day_map[run_at.weekday()],
                hour=run_at.hour, minute=run_at.minute, second=0,
            )
        elif repeat == "monthly":
            return CronTrigger(day=run_at.day, hour=run_at.hour, minute=run_at.minute, second=0)

        return DateTrigger(run_date=run_at)

    def add_job(self, user_id: int, message: str, run_at: datetime,
                repeat: str = None, channel: str = "telegram",
                job_type: str = "reminder", name: str = None,
                notify: bool = True, command: str = None,
                weekdays: list = None, log_file: str = None,
                created_context: str = "") -> Job:
        """Add a new job."""
        job_id = str(uuid.uuid4())[:8]

        _save_meta(
            job_id=job_id, user_id=user_id, message=message,
            job_type=job_type, name=name or message[:50],
            notify=notify, command=command, log_file=log_file,
            repeat=repeat, weekdays=weekdays, channel=channel,
            run_at=run_at,
            raw_at=run_at.isoformat() if run_at else "",
            created_context=created_context or f"bot add_job: {message[:80]}",
        )

        trigger = self._build_trigger(run_at, repeat, weekdays)
        executor_fn = _JOB_EXECUTORS.get(job_type, _execute_reminder)

        self._aps.add_job(
            executor_fn,
            trigger=trigger,
            id=job_id,
            args=[job_id],
            replace_existing=True,
        )

        logger.info(f"Added {job_type} job {job_id} for user {user_id}: {message[:50]}...")

        return Job(
            job_id=job_id, user_id=user_id, message=message,
            run_at=run_at, repeat=repeat, channel=channel,
            job_type=job_type, name=name or message[:50],
            notify=notify, command=command, weekdays=weekdays,
            log_file=log_file,
        )

    def add_agent_job(self, user_id: int, task: str, run_at: datetime,
                      repeat: str = None, name: str = None,
                      notify: bool = True) -> Job:
        """Add a proactive agent job that runs a full Claude task."""
        return self.add_job(
            user_id=user_id, message=task, run_at=run_at,
            repeat=repeat, job_type="agent",
            name=name or "Scheduled Task", notify=notify,
        )

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        try:
            self._aps.remove_job(job_id)
        except Exception:
            pass
        _delete_meta(job_id)
        logger.info(f"Removed job {job_id}")
        return True

    def get_user_jobs(self, user_id: int) -> list[Job]:
        """Get all jobs for a specific user."""
        metas = _get_all_meta(user_id=user_id)
        result = []
        for meta in metas:
            run_at = datetime.now()
            try:
                aps_job = self._aps.get_job(meta["job_id"])
                if aps_job and aps_job.next_run_time:
                    run_at = aps_job.next_run_time.replace(tzinfo=None)
            except Exception:
                pass
            result.append(Job.from_meta(meta, run_at=run_at))
        return result

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a specific job by ID."""
        meta = _get_meta(job_id)
        if not meta:
            return None
        run_at = datetime.now()
        try:
            aps_job = self._aps.get_job(job_id)
            if aps_job and aps_job.next_run_time:
                run_at = aps_job.next_run_time.replace(tzinfo=None)
        except Exception:
            pass
        return Job.from_meta(meta, run_at=run_at)

    async def send_message(self, user_id: int, text: str) -> bool:
        """Send a message via Telegram."""
        if self.api_base:
            url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        else:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json={
                    "chat_id": user_id,
                    "text": text
                })
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
            return False

    def sync_from_meta(self):
        """Sync job_meta table to APScheduler (picks up CLI-added jobs)."""
        metas = _get_all_meta()
        meta_ids = {m["job_id"] for m in metas}
        aps_ids = {j.id for j in self._aps.get_jobs()}

        for meta in metas:
            if meta["job_id"] not in aps_ids:
                try:
                    run_at_str = meta.get("run_at", "")
                    if run_at_str:
                        run_at = datetime.fromisoformat(run_at_str)
                    else:
                        run_at = datetime.now() + timedelta(minutes=1)

                    trigger = self._build_trigger(run_at, meta.get("repeat"), meta.get("weekdays"))
                    executor_fn = _JOB_EXECUTORS.get(meta.get("job_type", "reminder"), _execute_reminder)
                    self._aps.add_job(
                        executor_fn, trigger=trigger,
                        id=meta["job_id"], args=[meta["job_id"]],
                        replace_existing=True,
                    )
                    logger.info(f"Synced new job {meta['job_id']} ({meta.get('name', '')}) from metadata")
                except Exception as e:
                    logger.error(f"Failed to sync job {meta['job_id']}: {e}")

        for aps_id in aps_ids:
            if aps_id not in meta_ids:
                try:
                    self._aps.remove_job(aps_id)
                    logger.info(f"Removed orphaned APScheduler job {aps_id}")
                except Exception:
                    pass

    async def _recover_missed_jobs(self):
        """Fire one-shot jobs that were missed while bot was down."""
        now = datetime.now()
        metas = _get_all_meta()
        aps_ids = {j.id for j in self._aps.get_jobs()}

        for meta in metas:
            if meta.get("repeat"):
                continue
            if meta["job_id"] in aps_ids:
                continue
            run_at_str = meta.get("run_at", "")
            if not run_at_str:
                continue
            try:
                run_at = datetime.fromisoformat(run_at_str)
            except ValueError:
                continue
            if run_at >= now:
                continue

            delay_minutes = int((now - run_at).total_seconds() / 60)
            job_type = meta.get("job_type", "reminder")
            logger.warning(
                f"Recovering missed {job_type} job {meta['job_id']} "
                f"({meta.get('name', '')}) -- was due {delay_minutes}m ago"
            )

            try:
                if job_type == "reminder":
                    delay_note = f" (was scheduled for {run_at.strftime('%H:%M on %b %d')}, delivered late)"
                    success = await _send_with_retry(
                        self, meta["user_id"],
                        f"\U0001f514 Reminder: {meta['message']}{delay_note}"
                    )
                    _log_execution(meta["job_id"], meta["user_id"],
                                   meta["message"], success,
                                   None if success else "Recovery: failed to send")
                    if success:
                        _delete_meta(meta["job_id"])
                elif job_type == "command":
                    await _execute_command(meta["job_id"])
                elif job_type == "agent":
                    await _execute_agent(meta["job_id"])
            except Exception as e:
                logger.error(f"Failed to recover missed job {meta['job_id']}: {e}")

    async def _sync_loop(self):
        """Periodically sync metadata to APScheduler."""
        while self._sync_running:
            try:
                self.sync_from_meta()
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
            await asyncio.sleep(60)

    def start(self):
        """Start the APScheduler and sync loop."""
        if not self._aps.running:
            self._aps.start()
            self.sync_from_meta()
            job_count = len(self._aps.get_jobs())
            logger.info(f"APScheduler started with {job_count} jobs")
            self._sync_running = True
            self._sync_task = asyncio.create_task(self._sync_loop())
            asyncio.create_task(self._recover_missed_jobs())

    def stop(self):
        """Stop the APScheduler and sync loop."""
        self._sync_running = False
        if hasattr(self, '_sync_task') and self._sync_task:
            self._sync_task.cancel()
        if self._aps.running:
            self._aps.shutdown(wait=False)
            logger.info("APScheduler stopped")


# Global scheduler instance
_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Optional[Scheduler]:
    """Get the global scheduler instance."""
    return _scheduler


def init_scheduler(bot_token: str, api_base: str = None) -> Scheduler:
    """Initialize the global scheduler."""
    global _scheduler
    _scheduler = Scheduler(bot_token, api_base)
    return _scheduler
