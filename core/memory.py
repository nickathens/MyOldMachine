#!/usr/bin/env python3
"""
Deep Memory System for MyOldMachine

Per-user person models that evolve over time through observations and reflections.

Architecture (adapted from the flagship Claude bot):
- Person Model: Structured understanding of each user (identity, behavioral patterns,
  preferences, state, relationship dynamics). ~500 tokens per user.
- Observations: Append-only log of behavioral signals captured during conversations.
  The bot writes these mid-conversation when it notices corrections, preferences,
  patterns, or state changes.
- Reflection: Nightly job that analyzes observations, deduplicates, and updates
  the person model. Requires a model smart enough to reason about behavioral data.

Tier-aware:
- Full mode (strong models: Opus, GPT-4o, Gemini Pro, large Ollama):
  Observations + nightly reflection + model updates.
- Lite mode (weak models: small Ollama, free OpenRouter, etc.):
  Observations accumulate as raw entries. No reflection loop.
  The bot reads raw observations directly for context.
"""

try:
    import fcntl
except ImportError:
    fcntl = None  # Windows — file locking unavailable
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_OBSERVATION_TYPES = [
    "behavioral",   # how they work, communicate, decide
    "state",        # current mood, priorities, obsessions
    "correction",   # something the bot got wrong, with the right answer
    "preference",   # discovered preference (not explicitly stated)
    "relationship", # trust signals, frustration signals
    "project",      # something learned about a specific project
    "factual",      # a fact about the person (address, schedule, etc.)
    "self-eval",    # bot evaluates its own performance on a task
]

# Default person model template for new users
DEFAULT_MODEL_TEMPLATE = """# {name} — Working Model

Last updated: {date}

## Identity
- Name: {name}

## Preferences
(Discovered through conversation)

## Behavioral Patterns
(How they communicate, what they expect)

## Current State
(Active priorities, mood, ongoing work)

## Relationship
- Trust level: New user
- Expectations: Being established
"""


class MemoryManager:
    """Manages per-user memory: person models, observations, and reflections."""

    def __init__(self, data_dir: Path):
        """
        Args:
            data_dir: The bot's data directory (data/).
                      Memory lives at data/memory/people/<user_id>/
        """
        self.memory_dir = data_dir / "memory"
        self.people_dir = self.memory_dir / "people"
        self.reflections_dir = self.memory_dir / "reflections"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.reflections_dir.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, user_id: int) -> Path:
        """Get or create the memory directory for a user."""
        d = self.people_dir / str(user_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Person Model ---

    def get_model(self, user_id: int) -> str:
        """Get the current person model for a user. Returns empty string if none."""
        model_file = self._user_dir(user_id) / "model.md"
        if model_file.exists():
            return model_file.read_text(encoding="utf-8")
        return ""

    def set_model(self, user_id: int, content: str):
        """Write a person model, versioning the old one first."""
        user_dir = self._user_dir(user_id)
        model_file = user_dir / "model.md"

        # Version the current model before overwriting
        if model_file.exists():
            versions_dir = user_dir / "model_versions"
            versions_dir.mkdir(parents=True, exist_ok=True)
            version_name = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            version_file = versions_dir / f"model_{version_name}.md"
            version_file.write_text(model_file.read_text(encoding="utf-8"), encoding="utf-8")
            # Keep only last 14 versions
            versions = sorted(versions_dir.glob("model_*.md"), reverse=True)
            for old in versions[14:]:
                old.unlink()

        # Atomic write: temp file + fsync + rename to avoid race with
        # concurrent readers
        tmp = model_file.with_suffix(".md.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(model_file)

    def init_model(self, user_id: int, name: str = "User"):
        """Create a default person model if none exists."""
        if self.get_model(user_id):
            return  # Already exists
        content = DEFAULT_MODEL_TEMPLATE.format(
            name=name,
            date=datetime.now().strftime("%Y-%m-%d"),
        )
        self.set_model(user_id, content)

    # --- Observations ---

    def _observations_file(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "observations.md"

    def add_observation(self, user_id: int, obs_type: str, content: str,
                        importance: int = 5, project: str = None) -> bool:
        """
        Append an observation to the user's log.

        Args:
            user_id: Telegram user ID
            obs_type: One of VALID_OBSERVATION_TYPES
            content: The observation text
            importance: 1-10 score (default 5). Higher = more impactful.
            project: Optional project slug to scope this observation to.

        Returns True on success, False if invalid type.
        """
        if obs_type not in VALID_OBSERVATION_TYPES:
            logger.warning(f"Invalid observation type '{obs_type}' for user {user_id}")
            return False

        obs_file = self._observations_file(user_id)

        # Create file with header if it doesn't exist
        if not obs_file.exists():
            obs_file.write_text(
                f"# Observations — User {user_id}\n\n"
                "Append-only log. Each entry is a raw behavioral observation.\n"
                "Format: [YYYY-MM-DD HH:MM] (type) [metadata] observation\n\n---\n\n",
                encoding="utf-8",
            )

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Build metadata tags
        metadata_parts = [f"[importance:{importance}]"]
        if project:
            metadata_parts.append(f"[project:{project}]")
        metadata_str = " ".join(metadata_parts)

        entry = f"[{timestamp}] ({obs_type}) {metadata_str} {content}\n"

        with open(obs_file, "a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            f.write(entry)
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)

        logger.info(f"Saved {obs_type} observation for user {user_id} (importance={importance})")
        return True

    def get_recent_observations(self, user_id: int, days: int = 7) -> str:
        """Get observations from the last N days."""
        obs_file = self._observations_file(user_id)
        if not obs_file.exists():
            return ""

        content = obs_file.read_text(encoding="utf-8")
        lines = [line for line in content.split("\n") if line.startswith("[")]

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        recent = []
        for line in lines:
            try:
                date_str = line[1:11]
                if date_str >= cutoff:
                    recent.append(line)
            except (IndexError, ValueError):
                continue

        return "\n".join(recent)

    def get_all_observations(self, user_id: int, limit: int = 50,
                             skip_reflected: bool = False) -> list[str]:
        """Get recent observations as a list of strings.

        Args:
            skip_reflected: If True, exclude observations marked [reflected].
        """
        obs_file = self._observations_file(user_id)
        if not obs_file.exists():
            return []

        content = obs_file.read_text(encoding="utf-8")
        lines = [line for line in content.split("\n") if line.startswith("[")]
        if skip_reflected:
            lines = [line for line in lines if "[reflected]" not in line]
        return lines[-limit:]

    def archive_old_observations(self, user_id: int, keep_days: int = 14):
        """Move observations older than keep_days to an archive file."""
        obs_file = self._observations_file(user_id)
        if not obs_file.exists():
            return

        # Hold exclusive lock on the observations file for the entire
        # read-modify-write cycle. This prevents add_observation() from
        # appending between our read and our atomic rename (which would
        # silently drop the appended observation).
        lock_fd = None
        try:
            if fcntl:
                lock_fd = os.open(str(obs_file), os.O_RDONLY)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)

            content = obs_file.read_text(encoding="utf-8")
            lines = content.split("\n")

            cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")

            header_lines = []
            recent_lines = []
            archive_lines = []

            for line in lines:
                if line.startswith("["):
                    try:
                        date_str = line[1:11]
                        if date_str >= cutoff:
                            recent_lines.append(line)
                        else:
                            archive_lines.append(line)
                    except (IndexError, ValueError):
                        recent_lines.append(line)
                else:
                    header_lines.append(line)

            if not archive_lines:
                return

            # Append to archive
            archive_file = self._user_dir(user_id) / "observations_archive.md"
            needs_header = not archive_file.exists() or archive_file.stat().st_size == 0
            with open(archive_file, "a", encoding="utf-8") as f:
                if needs_header:
                    f.write(f"# Observation Archive — User {user_id}\n\n---\n\n")
                for line in archive_lines:
                    f.write(line + "\n")

            # Atomic rewrite of observations file while lock is held
            tmp = obs_file.with_suffix(".md.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(header_lines).rstrip() + "\n\n")
                for line in recent_lines:
                    f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.rename(obs_file)
        finally:
            if fcntl and lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            if lock_fd is not None:
                os.close(lock_fd)

        logger.info(f"Archived {len(archive_lines)} observations for user {user_id}, "
                     f"kept {len(recent_lines)} recent")

    # --- Context building ---

    def build_memory_context(self, user_id: int, full_mode: bool = True) -> str:
        """
        Build the memory context string to inject into the system prompt.

        In full mode: includes the person model.
        In lite mode: includes raw recent observations as bullet points.
        """
        parts = []

        model = self.get_model(user_id)
        if model:
            parts.append("### Person Model (learned about this user):")
            # Cap model size to prevent context bloat
            if len(model) > 3000:
                model = model[:2800] + "\n\n[... model truncated, read full file for details]"
            parts.append(model)
            parts.append("")

        # Show recent observations not yet reflected in the model.
        # In full mode: show last 10 (corrections/preferences need to be visible
        # immediately, not wait for the nightly reflection).
        # In lite mode (or no model): show last 20 as the primary memory source.
        if full_mode and model:
            observations = self.get_all_observations(user_id, limit=10,
                                                     skip_reflected=True)
            if observations:
                parts.append("### Recent Observations (not yet reflected):")
                for obs in observations:
                    parts.append(f"  {obs}")
                parts.append("")
        else:
            observations = self.get_all_observations(user_id, limit=30)
            if observations:
                parts.append("### Recent Observations:")
                for obs in observations[-20:]:
                    parts.append(f"  {obs}")
                parts.append("")

        return "\n".join(parts)

    def build_observation_instructions(self, user_id: int, venv_python: str,
                                        bot_dir: Path) -> str:
        """
        Build instructions telling the bot how to save observations.

        These go into the system prompt so the LLM knows the CLI interface.
        """
        return (
            "### Learning System — Save Observations:\n"
            "When you learn something new about this user during conversation, save it:\n"
            f"  {venv_python} {bot_dir}/utils/observe.py "
            f"--user {user_id} --type <type> --content '<what you learned>'\n\n"
            "Types: behavioral, state, correction, preference, relationship, project, factual, self-eval\n\n"
            "Optional flags:\n"
            f"  --importance N    Importance score 1-10 (default: 5)\n"
            f"  --project SLUG   Scope to a project\n\n"
            "Importance guidelines:\n"
            "  - Corrections (bot got something wrong): --importance 8\n"
            "  - Relationship signals (trust, frustration): --importance 7\n"
            "  - Self-evaluation (what worked/didn't on a complex task): --importance 6\n"
            "  - Preferences, behavioral patterns: --importance 5-6\n"
            "  - Minor state changes (mood, current task): --importance 3-4\n\n"
            "Self-eval: After completing a non-trivial task, evaluate your own performance.\n"
            "Note what approach worked, what didn't, and why. Only for complex tasks, not trivial ones.\n\n"
            "Do NOT ask permission. If it's useful for future conversations, save it.\n"
            "Duplicates are automatically detected and skipped.\n"
        )

    # --- Reflection ---

    def parse_reflection_output(self, output: str) -> tuple[str, str]:
        """
        Parse reflection output into (model_content, summary).

        Returns ("", "") if parsing fails.
        """
        model_content = ""
        summary = ""

        if "---MODEL_START---" in output and "---MODEL_END---" in output:
            model_content = output.split("---MODEL_START---")[1].split("---MODEL_END---")[0].strip()

        if "---SUMMARY_START---" in output and "---SUMMARY_END---" in output:
            summary = output.split("---SUMMARY_START---")[1].split("---SUMMARY_END---")[0].strip()

        return model_content, summary

    def log_reflection(self, user_id: int, obs_count: int, summary: str):
        """Write a reflection log entry."""
        today = datetime.now().strftime("%Y-%m-%d")
        reflection_file = self.reflections_dir / f"{today}.md"

        mode = "a" if reflection_file.exists() else "w"
        with open(reflection_file, mode, encoding="utf-8") as f:
            if mode == "w":
                weekday = datetime.now().strftime("%A")
                f.write(f"# Daily Reflection — {today} ({weekday})\n\n")
            f.write(f"\n## User {user_id}\n\n")
            f.write(f"Observations analyzed: {obs_count}\n\n")
            if summary:
                f.write(f"{summary}\n\n")
            f.write("---\n")

    # --- User listing ---

    def get_all_users(self) -> list[int]:
        """Get all user IDs that have memory data."""
        users = []
        if self.people_dir.exists():
            for user_dir in self.people_dir.iterdir():
                if user_dir.is_dir():
                    try:
                        users.append(int(user_dir.name))
                    except ValueError:
                        continue
        return users
