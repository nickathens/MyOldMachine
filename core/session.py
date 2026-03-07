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

# Thread pool for non-blocking subprocess calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="compaction")

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
    "compaction_threshold": 40,
    "compaction_keep_recent": 20,
    "compaction_batch_size": 10,
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
        user_dir.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)

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

    def load_summary(self) -> str:
        """Load conversation summary."""
        data = load_json(self.summary_file, {})
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
        meta = self.load_session_meta()
        meta["last_reset"] = datetime.now().isoformat()
        meta["message_count"] = 0
        self.save_session_meta(meta)
        return True

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

        threshold = self.config["compaction_threshold"]
        batch_size = self.config["compaction_batch_size"]

        if len(history) <= threshold:
            return history, ""

        # Check if claude CLI is available — without it we can't summarize,
        # and trimming without summarizing would lose context permanently
        import shutil
        if not shutil.which("claude"):
            logger.info("Compaction skipped — claude CLI not available")
            return history, ""

        # Load existing summary
        existing_summary = ""
        if summary_file.exists():
            try:
                with open(summary_file) as f:
                    data = json.load(f)
                    existing_summary = data.get("summary", "")
            except (json.JSONDecodeError, KeyError):
                pass

        # Take the oldest batch
        messages_to_compact = history[:batch_size]
        remaining_history = history[batch_size:]

        # Build text for compaction
        conv_text = []
        for msg in messages_to_compact:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if len(content) > 1000:
                content = content[:800] + "\n...[truncated for summarization]"
            conv_text.append(f"<{role}>{content}</{role}>")

        prompt = (
            "You are compacting conversation history. Merge the following messages "
            "into the existing summary. Keep it concise but preserve:\n"
            "- Key decisions and their rationale\n"
            "- Important facts, preferences, or requirements mentioned\n"
            "- Task outcomes and current state of ongoing work\n"
            "- File paths, project names, and technical details that may be needed later\n\n"
            "Drop: greetings, filler, repeated information, verbose tool outputs.\n\n"
            f"{'Existing summary to incorporate:\\n' + existing_summary + '\\n\\n' if existing_summary else ''}"
            f"New messages to compact:\n{chr(10).join(conv_text)}\n\n"
            "Provide only the merged summary, no preamble. Keep under 600 words."
        )

        def run_compaction_background():
            """Run Claude compaction in background thread."""
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

        _executor.submit(run_compaction_background)
        logger.info(f"Scheduled background compaction of {batch_size} messages "
                     f"({len(remaining_history)} remaining)")

        return remaining_history, existing_summary

    # --- Topic/Project Session Management ---

    def get_topic_session(self, topic_name: str) -> list:
        """Get conversation history for a specific topic."""
        topic_file = self.topics_dir / f"{self._sanitize_topic_name(topic_name)}.json"
        return load_json(topic_file, [])

    def save_topic_session(self, topic_name: str, history: list):
        """Save conversation history for a specific topic."""
        topic_file = self.topics_dir / f"{self._sanitize_topic_name(topic_name)}.json"
        save_json(topic_file, history)

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


def get_session_manager(user_id: int, users_dir: Path, config: dict = None) -> SessionManager:
    """Factory function to get a SessionManager for a user."""
    user_dir = users_dir / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return SessionManager(user_dir, config)
