#!/usr/bin/env python3
"""
Session Manager for MyOldMachine

Provides:
- Configurable daily reset
- Smart context trimming (removes old tool outputs, keeps decisions)
- Topic/project-based session isolation
- Non-blocking background compaction via Claude CLI
- Session metadata tracking
"""

import json
import logging
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from pathlib import Path
from typing import Optional

# Import safe_json from utils
_utils_dir = str(Path(__file__).parent.parent / "utils")
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)
from safe_json import load_json, save_json

logger = logging.getLogger(__name__)


def _content_chars(c) -> int:
    """Best-effort character count of a message's content field.

    History items are normally `{"content": str}`, but defensively handle None
    (key present with None), lists (multi-modal), and any other type.
    """
    if c is None:
        return 0
    if isinstance(c, str):
        return len(c)
    try:
        return len(c)
    except TypeError:
        return len(str(c))


# Thread pool for non-blocking subprocess calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="compaction")

# Guard: summary file paths with a compaction task already scheduled.
# Prevents duplicate background processes when history stays long
# during first-compaction (no summary exists yet, so disk isn't trimmed).
_compaction_scheduled: set[str] = set()

# Strong references to fire-and-forget compaction tasks. Without this set,
# asyncio.create_task() returns are eligible for garbage collection and the
# background coroutine can be cancelled mid-run.
_compaction_tasks: set = set()

# Default configuration
DEFAULT_CONFIG = {
    "daily_reset_enabled": True,
    "daily_reset_hour": 4,
    "daily_reset_minute": 0,
    "max_messages_before_trim": 60,
    "keep_recent_messages": 30,
    "trim_tool_outputs_after_messages": 10,
    "preserve_decision_keywords": ["decided", "decision", "chose", "chosen", "agreed", "confirmed"],
    # Compaction settings
    "compaction_enabled": True,
    "compaction_threshold": 40,  # Min messages before tier-1 (idle) compaction can fire
    "compaction_keep_recent": 20,
    "compaction_batch_size": 10,
    # Tiered gating (see should_compact)
    "compaction_idle_minutes": 15,  # Tier 1: idle window before compacting active session
    "compaction_hard_cap_tokens": 120_000,  # Tier 2: force compact regardless of idle
    "compaction_skip_below_tokens": 30_000,  # Skip guard: too small to be worth a cache write
}


class SessionManager:
    """Manages user sessions with smart trimming, daily reset, and topic isolation."""

    def __init__(self, user_dir: Path, config: dict = None):
        self.user_dir = user_dir
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.conversation_file = user_dir / "conversation.json"
        self.summary_file = user_dir / "conversation_summary.json"
        self.session_meta_file = user_dir / "session_meta.json"
        self.topics_dir = user_dir / "topics"
        self._compaction_runner = None  # External async compaction runner (set by bot.py)
        user_dir.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)

    def set_compaction_runner(self, runner):
        """Set an external compaction runner (async callable).

        When set, compaction uses this runner instead of spawning a subprocess
        in a thread pool. This allows bot.py to route compaction through the
        global LLM semaphore on low-resource machines.

        Args:
            runner: async callable(prompt: str, summary_file: Path, batch_size: int)
        """
        self._compaction_runner = runner

    def load_session_meta(self) -> dict:
        """Load session metadata."""
        default = {
            "created": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "last_reset": None,
            "message_count": 0,
            "current_topic": None,
        }
        return load_json(self.session_meta_file, default)

    def save_session_meta(self, meta: dict):
        """Save session metadata."""
        meta["last_activity"] = datetime.now().isoformat()
        save_json(self.session_meta_file, meta)

    def load_conversation(self) -> list:
        """Load conversation history."""
        return load_json(self.conversation_file, [])

    def save_conversation(self, history: list):
        """Save conversation history with smart trimming."""
        history = self.smart_trim_conversation(history)
        save_json(self.conversation_file, history)
        # Refresh last_activity so should_compact() can measure idle time
        # against the previous turn rather than against daily reset.
        meta = self.load_session_meta()
        meta["message_count"] = len(history)
        self.save_session_meta(meta)

    def load_summary(self) -> str:
        """Load conversation summary.

        On JSON corruption: unlink the file. load_json silently returns the
        default on decode errors, which would otherwise leave the corrupted
        file on disk and log the same error every turn forever. Recover
        deterministically by removing the unreadable file.
        """
        if not self.summary_file.exists():
            return ""
        try:
            with open(self.summary_file, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError, OSError) as e:
            logger.error(f"Corrupted summary file at {self.summary_file}, deleting: {e}")
            try:
                self.summary_file.unlink(missing_ok=True)
            except OSError:
                pass
            return ""
        if not isinstance(data, dict):
            return ""
        return data.get("summary", "")

    def save_summary(self, summary: str):
        """Save conversation summary."""
        save_json(self.summary_file, {
            "summary": summary,
            "updated": datetime.now().isoformat(),
        })

    def load_memories(self) -> list:
        return load_json(self.user_dir / "memories.json", [])

    def save_memories(self, memories: list):
        save_json(self.user_dir / "memories.json", memories)

    def add_memory(self, content: str):
        memories = self.load_memories()
        memories.append({"content": content, "timestamp": datetime.now().isoformat()})
        self.save_memories(memories)

    def should_daily_reset(self) -> bool:
        """Check if daily reset should occur."""
        if not self.config.get("daily_reset_enabled"):
            return False
        meta = self.load_session_meta()
        last_reset = meta.get("last_reset")
        now = datetime.now()
        reset_time = time(
            hour=self.config.get("daily_reset_hour", 4),
            minute=self.config.get("daily_reset_minute", 0),
        )
        today_reset = datetime.combine(now.date(), reset_time)
        if not last_reset:
            # Never reset before — only reset if we're past today's reset time
            # and there's actually a conversation to reset
            return now >= today_reset and self.conversation_file.exists()
        try:
            last_reset_dt = datetime.fromisoformat(last_reset)
        except (ValueError, TypeError):
            return False
        return now >= today_reset and last_reset_dt < today_reset

    def perform_daily_reset(self) -> bool:
        """Perform daily reset if needed. Returns True if reset was performed."""
        if not self.should_daily_reset():
            return False
        if self.conversation_file.exists():
            archive_name = f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            archive_path = self.user_dir / archive_name
            self.conversation_file.rename(archive_path)
            logger.info(f"Daily reset: archived conversation to {archive_name}")
        # Clear the compaction summary — stale summary from yesterday's conversation
        # should not bleed into today's fresh session
        if self.summary_file.exists():
            self.summary_file.unlink()
            logger.info("Daily reset: cleared conversation summary")
        # Archive old topic sessions (>30 days untouched)
        self._cleanup_old_topics()
        meta = self.load_session_meta()
        meta["last_reset"] = datetime.now().isoformat()
        meta["message_count"] = 0
        self.save_session_meta(meta)
        return True

    def _cleanup_old_topics(self, max_age_days: int = 30):
        """Archive topic session files older than max_age_days."""
        if not self.topics_dir.exists():
            return
        archive_dir = self.user_dir / "topics_archive"
        cutoff = datetime.now().timestamp() - (max_age_days * 86400)
        for topic_file in self.topics_dir.glob("*.json"):
            try:
                if topic_file.stat().st_mtime < cutoff:
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    topic_file.rename(archive_dir / topic_file.name)
                    logger.info(f"Daily reset: archived old topic {topic_file.stem}")
            except Exception as e:
                logger.warning(f"Failed to archive topic {topic_file}: {e}")

    def smart_trim_conversation(self, history: list) -> list:
        """
        Intelligently trim conversation history:
        1. Keep recent messages intact
        2. Remove verbose tool outputs from older messages
        3. Preserve messages containing decisions/important info
        """
        if len(history) <= self.config["keep_recent_messages"]:
            return history

        keep_recent = self.config["keep_recent_messages"]
        recent = history[-keep_recent:]
        older = history[:-keep_recent]

        trimmed_older = []
        for msg in older:
            content = msg.get("content", "")
            role = msg.get("role", "")

            is_decision = any(
                kw.lower() in content.lower()
                for kw in self.config["preserve_decision_keywords"]
            )
            if is_decision:
                trimmed_older.append(msg)
                continue

            if role == "assistant":
                trimmed_content = self._trim_tool_outputs(content)
                trimmed_older.append({**msg, "content": trimmed_content})
            else:
                if len(content) > 2000:
                    trimmed_older.append({
                        **msg,
                        "content": content[:1500] + "\n\n[Message truncated for context management]"
                    })
                else:
                    trimmed_older.append(msg)

        return trimmed_older + recent

    def _trim_tool_outputs(self, content: str) -> str:
        """Remove verbose tool output blocks from content."""
        result = content

        # Trim long code blocks (>30 lines)
        search_from = 0
        while True:
            start = result.find('```', search_from)
            if start == -1:
                break
            end = result.find('```', start + 3)
            if end == -1:
                break
            end += 3
            block = result[start:end]
            line_count = block.count('\n')
            if line_count > 30:
                first_line = block.split('\n')[0] if '\n' in block else block
                if 'json' in first_line.lower():
                    replacement = '[Large JSON response trimmed]'
                else:
                    replacement = '[Long code block trimmed]'
                result = result[:start] + replacement + result[end:]
                search_from = start + len(replacement)
            else:
                search_from = end

        # Trim consecutive log lines (10+)
        lines = result.split('\n')
        trimmed_lines = []
        log_buffer = []
        for line in lines:
            is_log = len(line) > 10 and line[:4].isdigit() and line[4] == '-'
            if is_log:
                log_buffer.append(line)
            else:
                if len(log_buffer) >= 10:
                    trimmed_lines.append('[Log output trimmed]')
                else:
                    trimmed_lines.extend(log_buffer)
                log_buffer = []
                trimmed_lines.append(line)
        if len(log_buffer) >= 10:
            trimmed_lines.append('[Log output trimmed]')
        else:
            trimmed_lines.extend(log_buffer)

        return '\n'.join(trimmed_lines)

    def should_compact(self, history: list) -> tuple[bool, str]:
        """
        Two-tier compaction gate.

        - Tier 1 (idle-triggered): idle > N min AND msgs > threshold.
          The 1h prompt cache was about to die anyway when the user returns
          (or we still have headroom to absorb the cache miss), so this is
          a "free" compaction window.
        - Tier 2 (hard cap): estimated tokens > N. Forces compaction
          regardless of idle so a multi-hour active session can't run away.

        Skip guards:
        - Estimated tokens < skip_below_tokens: cache reads are pennies,
          summary write isn't worth it.
        - Below message threshold AND below hard cap: nothing to do.

        Token estimate is char-count / 4 (good enough for these thresholds).
        """
        if not self.config.get("compaction_enabled", True):
            return False, "disabled"

        msgs = len(history)
        threshold = self.config["compaction_threshold"]
        idle_minutes = self.config.get("compaction_idle_minutes", 15)
        hard_cap_tokens = self.config.get("compaction_hard_cap_tokens", 120_000)
        skip_below_tokens = self.config.get("compaction_skip_below_tokens", 30_000)

        total_chars = sum(_content_chars(m.get("content")) for m in history)
        est_tokens = total_chars // 4

        if est_tokens < skip_below_tokens:
            return False, f"under skip floor (~{est_tokens} tokens)"

        if est_tokens > hard_cap_tokens:
            return True, f"hard cap (~{est_tokens} tokens > {hard_cap_tokens})"

        if msgs <= threshold:
            return False, f"under msg threshold ({msgs} <= {threshold})"

        meta = self.load_session_meta()
        last_activity_str = meta.get("last_activity")
        if not last_activity_str:
            return True, f"no last_activity, msgs={msgs}"

        try:
            last_activity = datetime.fromisoformat(last_activity_str)
        except (ValueError, TypeError):
            return True, f"unparseable last_activity, msgs={msgs}"

        # Force naive comparison: our own writes are naive (datetime.now().isoformat()),
        # but a hand-edited or externally-migrated file could carry a tz offset, which
        # would crash the subtraction below. Convert to local-naive first so a UTC
        # timestamp isn't silently misread as local.
        if last_activity.tzinfo is not None:
            last_activity = last_activity.astimezone().replace(tzinfo=None)

        idle_seconds = (datetime.now() - last_activity).total_seconds()
        idle_gate_seconds = idle_minutes * 60

        if idle_seconds > idle_gate_seconds:
            return True, f"idle {idle_seconds:.0f}s > {idle_gate_seconds}s, msgs={msgs}"

        return False, f"active session (idle {idle_seconds:.0f}s)"

    def compact_conversation(self, history: list, summary_file: Path) -> tuple[list, str]:
        """
        Gradually compact older messages into a rolling summary.

        NON-BLOCKING: immediately returns trimmed history and schedules
        summarization in the background. The summary will be available
        for the next message.

        If `claude` CLI is not available, skips compaction entirely to avoid
        losing messages without a summary.
        """
        if not self.config.get("compaction_enabled", True):
            return history, ""

        batch_size = self.config["compaction_batch_size"]

        # Safety: don't compact away the entire history. Real gating is in
        # should_compact() upstream; this is just a guard against pathological
        # callers (e.g. tier-2 fires on a tiny but token-heavy history).
        if len(history) <= batch_size:
            return history, ""

        # Check if claude CLI or an external compaction runner is available.
        # Without either, we can't summarize, and trimming without
        # summarizing would lose context permanently.
        if not shutil.which("claude") and not self._compaction_runner:
            logger.info("Compaction skipped — no claude CLI and no compaction runner configured")
            return history, ""

        # Dedup guard: skip if a compaction task is already running for this file
        sf_key = str(summary_file)
        if sf_key in _compaction_scheduled:
            logger.debug(f"Compaction already scheduled for {summary_file}, skipping duplicate")
            return history, ""

        # Load existing summary
        existing_summary = ""
        if summary_file.exists():
            try:
                with open(summary_file, encoding="utf-8") as f:
                    data = json.load(f)
                    existing_summary = data.get("summary", "")
            except (json.JSONDecodeError, KeyError, OSError):
                pass

        # Take the oldest batch
        messages_to_compact = history[:batch_size]
        remaining_history = history[batch_size:]

        # Build text for compaction
        conv_text = []
        for msg in messages_to_compact:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            if len(content) > 1000:
                content = content[:800] + "\n...[truncated for summarization]"
            conv_text.append(f"<{role}>{content}</{role}>")

        prompt = (
            "You are compacting conversation history into a rolling summary. "
            "This summary will be injected as context when the conversation is "
            "resumed, so write it as a factual third-person record -- NOT as a "
            "conversation transcript.\n\n"
            "CRITICAL: The next session will NOT have access to tool results from "
            "this session. Any research findings, web scrape results, file contents, "
            "or command outputs not captured in this summary are PERMANENTLY LOST. "
            "Include actual findings and results inline, not just 'research was done'.\n\n"
            "Merge the new messages into the existing summary. Structure the "
            "output with these sections (omit empty sections):\n\n"
            "**Key decisions:** Decisions made and their rationale\n"
            "**Current work:** What was being worked on, current state\n"
            "**Completed and delivered:** Work that was FINISHED and the results "
            "were SHOWN to the user. Include the actual findings/content.\n"
            "**Pending tasks:** Anything the user asked for that isn't done yet\n"
            "**Important context:** Facts, preferences, file paths, project names, "
            "technical details needed for continuity\n\n"
            "Rules:\n"
            "- Write plain prose, no XML tags, no role markers (no <user>, "
            "<assistant>, Human:, etc.)\n"
            "- Drop: greetings, filler, repeated information, verbose tool outputs\n"
            "- Distinguish between work COMPLETED (results shown to user) vs work "
            "ATTEMPTED (started but not finished or results not delivered). "
            "Mark attempted work clearly so the next session knows to redo it.\n"
            "- Keep under 600 words\n"
            "- Provide only the merged summary, no preamble or explanation\n\n"
            + (f"Existing summary to incorporate:{chr(10)}{existing_summary}{chr(10)}{chr(10)}" if existing_summary else "")
            + f"New messages to compact:{chr(10)}{chr(10).join(conv_text)}"
        )

        _compaction_scheduled.add(sf_key)

        if self._compaction_runner:
            # Use external runner (routes through bot's semaphore on low-resource machines)
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._compaction_runner(prompt, summary_file, batch_size))
                # Hold a strong ref so the task isn't GC'd before completion
                _compaction_tasks.add(task)
                task.add_done_callback(_compaction_tasks.discard)
                task.add_done_callback(lambda _t: _compaction_scheduled.discard(sf_key))
                logger.info(f"Scheduled semaphore-aware compaction of {batch_size} messages "
                            f"({len(remaining_history)} remaining)")
            except RuntimeError:
                logger.warning("No running event loop, falling back to thread pool compaction")
                self._run_compaction_thread(prompt, summary_file, batch_size)
        else:
            # Legacy: thread pool compaction (used on capable machines or non-bot usage)
            self._run_compaction_thread(prompt, summary_file, batch_size)
            logger.info(f"Scheduled background compaction of {batch_size} messages "
                        f"({len(remaining_history)} remaining)")

        return remaining_history, existing_summary

    def _run_compaction_thread(self, prompt: str, summary_file: Path, batch_size: int):
        """Run compaction in a background thread via the thread pool."""
        sf_key = str(summary_file)

        def run_compaction_background():
            try:
                result = subprocess.run(
                    ["claude", "-p", prompt],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0 and result.stdout.strip():
                    new_summary = result.stdout.strip()
                    save_json(summary_file, {
                        "summary": new_summary,
                        "updated": datetime.now().isoformat(),
                        "compacted_messages": batch_size,
                    })
                    logger.info(f"Background compaction complete: {batch_size} messages summarized")
                else:
                    logger.warning(f"Compaction returned no output (exit {result.returncode})")
            except subprocess.TimeoutExpired:
                logger.error("Background compaction timed out")
            except Exception as e:
                logger.error(f"Background compaction failed: {e}")
            finally:
                _compaction_scheduled.discard(sf_key)

        _executor.submit(run_compaction_background)

    # --- Topic/Project Session Management ---

    def get_topic_session(self, topic_name: str) -> list:
        """Get conversation history for a specific topic."""
        topic_file = self.topics_dir / f"{self._sanitize_topic_name(topic_name)}.json"
        return load_json(topic_file, [])

    def save_topic_session(self, topic_name: str, history: list):
        """Save conversation history for a specific topic."""
        topic_file = self.topics_dir / f"{self._sanitize_topic_name(topic_name)}.json"
        save_json(topic_file, history)
        # Topic activity also counts as user activity — keep last_activity fresh
        # so a topic-then-main switch doesn't immediately trigger idle compaction.
        meta = self.load_session_meta()
        self.save_session_meta(meta)

    def list_topics(self) -> list[str]:
        """List all available topics for this user."""
        topics = []
        for f in self.topics_dir.glob("*.json"):
            topics.append(f.stem)
        return sorted(topics)

    def switch_topic(self, topic_name: Optional[str]) -> str:
        """Switch to a topic session. None means main session."""
        meta = self.load_session_meta()
        meta["current_topic"] = topic_name
        self.save_session_meta(meta)
        if topic_name:
            return f"Switched to topic: {topic_name}"
        return "Switched to main session"

    def get_current_topic(self) -> Optional[str]:
        """Get the current topic name, or None for main session."""
        meta = self.load_session_meta()
        return meta.get("current_topic")

    def _sanitize_topic_name(self, name: str) -> str:
        """Sanitize topic name for use as filename."""
        sanitized = re.sub(r'[^\w\-]', '_', name.lower())
        sanitized = re.sub(r'_+', '_', sanitized)
        return sanitized.strip('_')[:50]


_session_managers: dict[int, SessionManager] = {}


def get_session_manager(user_id: int, user_dir: Path, config: dict = None) -> SessionManager:
    """Factory function to get a SessionManager for a user (cached per user_id).

    user_dir is the resolved per-user directory (already accounting for
    multi-user slot mapping if applicable). The caller is responsible for
    creating the directory in single-user mode; in multi-user mode the
    installer provisions it with the correct ownership.

    The cache holds the resolved user_dir, so callers must invoke
    clear_session_manager(user_id) whenever the slot binding changes
    (e.g., /removeuser archives the slot dir, /adduser binds a new
    Telegram ID to a recycled slot).
    """
    cached = _session_managers.get(user_id)
    if cached is not None and cached.user_dir == user_dir:
        return cached
    mgr = SessionManager(user_dir, config)
    _session_managers[user_id] = mgr
    return mgr


def clear_session_manager(user_id: int) -> None:
    """Drop the cached SessionManager for a user.

    Call this whenever a user's slot binding changes so the next request
    rebuilds the manager against the current data dir. Without this, the
    cache from before /removeuser still points at the archived slot path.
    """
    _session_managers.pop(user_id, None)
