"""
Session management: conversation history, trimming, and daily reset.
"""

import json
import logging
from datetime import datetime, time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "daily_reset_enabled": True,
    "daily_reset_hour": 4,
    "max_messages_before_trim": 60,
    "keep_recent_messages": 30,
    "trim_tool_outputs_after_messages": 10,
    "preserve_decision_keywords": ["decided", "decision", "chose", "agreed", "confirmed"],
}


def _safe_load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default if default is not None else {}


def _safe_save_json(path: Path, data, indent=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
        f.flush()
    tmp.rename(path)


class SessionManager:
    """Manages a single user's conversation sessions."""

    def __init__(self, user_dir: Path, config: dict = None):
        self.user_dir = user_dir
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.conversation_file = user_dir / "conversation.json"
        self.summary_file = user_dir / "conversation_summary.json"
        self.meta_file = user_dir / "session_meta.json"
        user_dir.mkdir(parents=True, exist_ok=True)

    def load_conversation(self) -> list:
        return _safe_load_json(self.conversation_file, [])

    def save_conversation(self, history: list):
        history = self._smart_trim(history)
        _safe_save_json(self.conversation_file, history)

    def load_summary(self) -> str:
        data = _safe_load_json(self.summary_file, {})
        return data.get("summary", "")

    def save_summary(self, summary: str):
        _safe_save_json(self.summary_file, {
            "summary": summary,
            "updated": datetime.now().isoformat(),
        })

    def load_memories(self) -> list:
        return _safe_load_json(self.user_dir / "memories.json", [])

    def save_memories(self, memories: list):
        _safe_save_json(self.user_dir / "memories.json", memories)

    def add_memory(self, content: str):
        memories = self.load_memories()
        memories.append({"content": content, "timestamp": datetime.now().isoformat()})
        self.save_memories(memories)

    def should_daily_reset(self) -> bool:
        if not self.config.get("daily_reset_enabled"):
            return False
        meta = _safe_load_json(self.meta_file, {})
        last_reset = meta.get("last_reset")
        if not last_reset:
            return False
        try:
            last_reset_dt = datetime.fromisoformat(last_reset)
        except (ValueError, TypeError):
            return False
        now = datetime.now()
        reset_time = time(hour=self.config.get("daily_reset_hour", 4))
        today_reset = datetime.combine(now.date(), reset_time)
        return now >= today_reset and last_reset_dt < today_reset

    def perform_daily_reset(self) -> bool:
        if not self.should_daily_reset():
            return False
        if self.conversation_file.exists():
            archive = self.user_dir / f"conversation_{datetime.now():%Y%m%d_%H%M%S}.json"
            self.conversation_file.rename(archive)
        meta = _safe_load_json(self.meta_file, {})
        meta["last_reset"] = datetime.now().isoformat()
        meta["message_count"] = 0
        _safe_save_json(self.meta_file, meta)
        return True

    def _smart_trim(self, history: list) -> list:
        max_before_trim = self.config["max_messages_before_trim"]
        keep = self.config["keep_recent_messages"]
        if len(history) <= max_before_trim:
            return history
        recent = history[-keep:]
        older = history[:-keep]
        # From older messages, only keep decisions
        preserved = []
        for msg in older:
            content = msg.get("content", "")
            is_decision = any(
                kw.lower() in content.lower()
                for kw in self.config["preserve_decision_keywords"]
            )
            if is_decision:
                if len(content) > 2000:
                    preserved.append({**msg, "content": content[:1500] + "\n[trimmed]"})
                else:
                    preserved.append(msg)
        return preserved + recent
