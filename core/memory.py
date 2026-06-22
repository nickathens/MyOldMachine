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
import json
import logging
import os
import re
import subprocess
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

# ── Dedup / corroboration tuning ──────────────────────────────────
# Lexical Jaccard drives SUPPRESSION (a near-verbatim restatement folds into the
# existing line). Semantic cosine drives CORROBORATION only (it records that a
# pattern recurred but keeps both observations), because calibration showed that
# semantic magnitude alone cannot prove a duplicate.
RECENT_WINDOW = 20  # how many recent observations the dedup/corroboration pass scans
LEXICAL_THRESHOLD = 0.6
SEMANTIC_THRESHOLD = float(os.environ.get("MEMORY_SEMANTIC_THRESHOLD", "0.80"))
SEMANTIC_MIN_LEN = 25  # observations shorter than this are too terse to embed reliably
SEMANTIC_ENABLED_DEFAULT = os.environ.get("MEMORY_SEMANTIC_DEDUP", "1") != "0"
SEMANTIC_SCRIPT = Path(__file__).resolve().parent.parent / "utils" / "semantic.py"

# Stopwords excluded from deduplication keyword extraction
_DEDUP_STOPWORDS = frozenset({
    "the", "is", "it", "a", "an", "and", "or", "to", "for", "of", "in", "on",
    "at", "by", "with", "from", "as", "not", "but", "if", "how", "what",
    "when", "where", "who", "why", "this", "that", "all", "are", "was",
    "were", "been", "has", "have", "had", "do", "does", "did", "will",
    "can", "could", "should", "would", "may", "might", "you", "your",
    "he", "she", "they", "them", "his", "her", "its", "our", "we",
    "bot", "also", "just", "about", "being",
    "into", "more", "than", "very", "same", "other", "which",
    "user", "person", "people",
})


def _extract_keywords(text: str) -> set:
    """Extract meaningful keywords from text for deduplication."""
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    return {w for w in words if w not in _DEDUP_STOPWORDS}


def _observation_content(line: str) -> str:
    """Extract the observation content after the [ts] (type) [tags...] prefix."""
    m = re.search(r'\)\s+(?:\[[^\]]*\]\s*)*(.+)$', line)
    return m.group(1).strip() if m else ""


def _observation_timestamp(line: str) -> str:
    """Extract the 'YYYY-MM-DD HH:MM' timestamp from an observation line, or ''."""
    m = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]', line)
    return m.group(1) if m else ""


def _get_int_tag(line: str, key: str, default: int) -> int:
    """Read an integer [key:N] tag from a line, or default if absent/malformed."""
    m = re.search(r'\[' + re.escape(key) + r':(\d+)\]', line)
    if not m:
        return default
    try:
        return int(m.group(1))
    except ValueError:
        return default


def _set_tag(line: str, key: str, value) -> str:
    """Set or replace a [key:value] tag on an observation line.

    Replaces an existing tag in place; otherwise inserts it right after the
    (type) marker so the content-extraction regex still skips it cleanly.
    """
    tag = f"[{key}:{value}]"
    pat = re.compile(r'\[' + re.escape(key) + r':[^\]]*\]')
    if pat.search(line):
        return pat.sub(tag, line, count=1)
    m = re.search(r'\([\w-]+\)', line)
    if m:
        return line[:m.end()] + " " + tag + line[m.end():]
    return line + " " + tag


def _find_lexical_match(new_content: str, existing_lines: list,
                        threshold: float = LEXICAL_THRESHOLD):
    """Return the most recent observation line whose keyword set is >= threshold
    Jaccard-similar to new_content, or None.

    This is the SUPPRESSION signal: high lexical overlap means a near-verbatim
    restatement that is safe to fold into the existing observation.
    """
    new_kws = _extract_keywords(new_content)
    if len(new_kws) < 2:
        return None
    recent = existing_lines[-RECENT_WINDOW:] if len(existing_lines) > RECENT_WINDOW else existing_lines
    for line in reversed(recent):
        existing_kws = _extract_keywords(_observation_content(line))
        if len(existing_kws) < 2:
            continue
        union = new_kws | existing_kws
        if not union:
            continue
        if len(new_kws & existing_kws) / len(union) >= threshold:
            return line
    return None


# ── Anchors: ground-truth facts immune to the nightly model rewrite ───────────
# The person model (model.md) is fully rewritten by an LLM every reflection. Over
# many rewrites a true, load-bearing fact can quietly soften or vanish (a
# telephone game). Anchors fix that: they live in the user's anchors.md and are
# injected VERBATIM into the memory context at read time (build_memory_context),
# so a pinned fact can never drift, be softened, merged away, or dropped no
# matter what the LLM does. They are promoted deliberately (via anchors.py),
# never auto-inferred, because a wrong permanent anchor is worse than drift.

_ANCHOR_RE = re.compile(r'^- \[id:([\w-]+)\]\s*(?:\(([\w-]+)\)\s*)?(.+)$')

ANCHORS_HEADER = (
    "# Anchored Facts\n\n"
    "Ground-truth facts. Reproduced verbatim in the memory context every turn and "
    "never rewritten, softened, merged, or dropped by the nightly reflection. "
    "Managed deliberately via anchors.py.\n\n"
    "Format: - [id:slug] (category) fact text\n\n---\n\n"
)


def parse_anchor_line(line: str):
    """Parse one anchor line into {id, category, text}, or None if not an anchor."""
    m = _ANCHOR_RE.match(line.rstrip())
    if not m:
        return None
    return {"id": m.group(1), "category": m.group(2) or "", "text": m.group(3).strip()}


def render_anchors_section(anchors: list) -> str:
    """Render anchors as the protected, authoritative block for the memory context.

    Returns "" when there are no anchors so no empty section is ever inserted.
    The id is intentionally omitted here (it is a management handle, not context).
    """
    if not anchors:
        return ""
    lines = ["### Anchored Facts (ground truth, never auto-rewritten)", ""]
    for a in anchors:
        prefix = f"({a['category']}) " if a["category"] else ""
        lines.append(f"- {prefix}{a['text']}")
    return "\n".join(lines)


def _slugify(text: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return s[:40] or "anchor"


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

    # --- Anchors (ground-truth facts, drift-proof) ---

    def _anchors_file(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "anchors.md"

    def load_anchors(self, user_id: int) -> list:
        """Return the user's anchors as a list of {id, category, text} (empty if none)."""
        f = self._anchors_file(user_id)
        if not f.exists():
            return []
        anchors = []
        for line in f.read_text(encoding="utf-8").split("\n"):
            a = parse_anchor_line(line)
            if a:
                anchors.append(a)
        return anchors

    def _write_anchors(self, user_id: int, anchors: list):
        f = self._anchors_file(user_id)
        body = ANCHORS_HEADER
        for a in anchors:
            cat = f"({a['category']}) " if a["category"] else ""
            body += f"- [id:{a['id']}] {cat}{a['text']}\n"
        with open(f, "w", encoding="utf-8") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(body)
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def add_anchor(self, user_id: int, text: str, anchor_id: str = None,
                   category: str = "") -> dict:
        """Add or update (idempotent by id) a ground-truth anchor.

        Returns {"status": "added"|"updated"|"error", "id": slug, "total": N}.
        """
        # Collapse all whitespace (including embedded newlines) so an anchor is
        # always one line; the storage format and the parse regex both depend on
        # single-line anchors.
        text = " ".join(text.split())
        if not text:
            return {"status": "error", "reason": "empty"}
        anchors = self.load_anchors(user_id)
        if not anchor_id:
            base = _slugify(text)
            anchor_id = base
            existing_ids = {a["id"] for a in anchors}
            n = 2
            while anchor_id in existing_ids:
                anchor_id = f"{base}-{n}"
                n += 1
        replaced = False
        for a in anchors:
            if a["id"] == anchor_id:
                a["text"], a["category"], replaced = text, category, True
                break
        if not replaced:
            anchors.append({"id": anchor_id, "category": category, "text": text})
        self._write_anchors(user_id, anchors)
        return {"status": "updated" if replaced else "added", "id": anchor_id,
                "total": len(anchors)}

    def remove_anchor(self, user_id: int, anchor_id: str) -> dict:
        """Remove an anchor by id. Returns {"status": "removed"|"not_found", ...}."""
        anchors = self.load_anchors(user_id)
        kept = [a for a in anchors if a["id"] != anchor_id]
        if len(kept) == len(anchors):
            return {"status": "not_found", "id": anchor_id}
        self._write_anchors(user_id, kept)
        return {"status": "removed", "id": anchor_id, "total": len(kept)}

    def promote_observation(self, user_id: int, needle: str, anchor_id: str = None,
                            category: str = "") -> dict:
        """Promote the content of a matching observation into a verbatim anchor."""
        obs_file = self._observations_file(user_id)
        if not obs_file.exists():
            return {"status": "error", "reason": "no_observations"}
        needle_low = needle.lower()
        matches = []
        for line in obs_file.read_text(encoding="utf-8").split("\n"):
            if line.startswith("[") and needle_low in line.lower():
                content = _observation_content(line)
                if content:
                    matches.append(content)
        if not matches:
            return {"status": "error", "reason": "no_match"}
        result = self.add_anchor(user_id, matches[-1], anchor_id=anchor_id, category=category)
        result["matched"] = len(matches)
        return result

    # --- Intro flow state ---
    #
    # Two markers track the per-user intro lifecycle:
    #   .intro_shown: orientation has been delivered (set on first message)
    #   .intro_done : intro reflection has run (set on second message)
    # Filesystem markers persist across restarts and never need cleanup.

    def intro_shown(self, user_id: int) -> bool:
        return (self._user_dir(user_id) / ".intro_shown").exists()

    def mark_intro_shown(self, user_id: int):
        (self._user_dir(user_id) / ".intro_shown").touch()

    def intro_done(self, user_id: int) -> bool:
        return (self._user_dir(user_id) / ".intro_done").exists()

    def mark_intro_done(self, user_id: int):
        (self._user_dir(user_id) / ".intro_done").touch()

    # --- Observations ---

    def _observations_file(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "observations.md"

    def add_observation(self, user_id: int, obs_type: str, content: str,
                        importance: int = 5, project: str = None,
                        use_semantic: bool = True) -> dict:
        """
        Append an observation to the user's log, with two-tier dedup/corroboration.

        Args:
            user_id: Telegram user ID
            obs_type: One of VALID_OBSERVATION_TYPES
            content: The observation text
            importance: 1-10 score (default 5). Higher = more impactful.
            project: Optional project slug to scope this observation to.
            use_semantic: Run the semantic corroboration pass (lexical always runs).

        Tiers:
          - Lexical near-restatement (Jaccard >= LEXICAL_THRESHOLD): SUPPRESS the
            new line and strengthen the existing one (seen += 1, lastseen,
            importance kept at max).
          - Semantic near-duplicate (cosine >= SEMANTIC_THRESHOLD, no lexical
            match): KEEP the new line but carry the corroboration count forward
            and link it to the matched observation, because semantic similarity
            proves recurrence, not identity.

        Returns a status dict:
          {"status": "invalid_type"}
          {"status": "corroborated_lexical", "seen": N}
          {"status": "corroborated_semantic", "seen": N, "score": float}
          {"status": "saved"}
        """
        if obs_type not in VALID_OBSERVATION_TYPES:
            logger.warning(f"Invalid observation type '{obs_type}' for user {user_id}")
            return {"status": "invalid_type"}

        obs_file = self._observations_file(user_id)

        # Create file with header if it doesn't exist
        if not obs_file.exists():
            obs_file.write_text(
                f"# Observations — User {user_id}\n\n"
                "Append-only log. Each entry is a raw behavioral observation.\n"
                "Format: [YYYY-MM-DD HH:MM] (type) [metadata] observation\n\n---\n\n",
                encoding="utf-8",
            )

        existing_content = obs_file.read_text(encoding="utf-8")
        existing_lines = [ln for ln in existing_content.split("\n") if ln.startswith("[")]
        recent = existing_lines[-RECENT_WINDOW:]

        # ── Tier 1: lexical near-restatement, suppress new and corroborate existing ──
        lex = _find_lexical_match(content, existing_lines)
        if lex is not None:
            seen = _get_int_tag(lex, "seen", 1) + 1
            updated = _set_tag(lex, "seen", seen)
            updated = _set_tag(updated, "lastseen", datetime.now().strftime("%Y-%m-%d"))
            if importance > _get_int_tag(lex, "importance", 5):
                updated = _set_tag(updated, "importance", importance)
            self._rewrite_observation_line(obs_file, lex, updated)
            logger.info(f"Corroborated (lexical) observation for user {user_id}, seen={seen}")
            return {"status": "corroborated_lexical", "seen": seen}

        # ── Tier 2: semantic near-duplicate, keep new and carry corroboration forward ──
        corrob_ts = None
        seen_for_new = 1
        score = 0.0
        if use_semantic and SEMANTIC_ENABLED_DEFAULT:
            sem = self._semantic_best_match(content, recent, obs_file.parent / ".embcache.json")
            if sem is not None:
                matched_line, score = sem
                ts = _observation_timestamp(matched_line)
                if ts:  # only link to a well-formed observation; never bump on a parse miss
                    corrob_ts = ts
                    seen_for_new = _get_int_tag(matched_line, "seen", 1) + 1

        # Build and append the new entry.
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        metadata_parts = [f"[importance:{importance}]"]
        if project:
            metadata_parts.append(f"[project:{project}]")
        if seen_for_new > 1:
            metadata_parts.append(f"[seen:{seen_for_new}]")
            metadata_parts.append(f"[lastseen:{datetime.now().strftime('%Y-%m-%d')}]")
        if corrob_ts:
            metadata_parts.append(f"[corrob:{corrob_ts}]")

        entry = f"[{timestamp}] ({obs_type}) {' '.join(metadata_parts)} {content}\n"
        with open(obs_file, "a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            f.write(entry)
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)

        if corrob_ts:
            logger.info(f"Linked (semantic cos={score:.2f}) observation for user {user_id}, seen={seen_for_new}")
            return {"status": "corroborated_semantic", "seen": seen_for_new, "score": score}

        logger.info(f"Saved {obs_type} observation for user {user_id} (importance={importance})")
        return {"status": "saved"}

    def _rewrite_observation_line(self, obs_file: Path, old_line: str, new_line: str):
        """Replace the first observation line that exactly equals old_line, atomically.

        Matching is on the whole line, not a substring: a substring replace could
        corrupt a different, longer observation that old_line happens to be a
        prefix of. The lock is held across BOTH the read and the write so a
        concurrent append to the same file cannot be lost.
        """
        with open(obs_file, "r+", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            try:
                lines = f.read().split("\n")
                for i, ln in enumerate(lines):
                    if ln == old_line:
                        lines[i] = new_line
                        break
                else:
                    return  # line no longer present (concurrent change); nothing to do
                f.seek(0)
                f.write("\n".join(lines))
                f.truncate()
            finally:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def _semantic_python(self) -> Path:
        """Path to the mempalace venv python that hosts the offline embedding model."""
        override = os.environ.get("MEMPALACE_PY")
        if override:
            return Path(override)
        return self.memory_dir.parent / "mempalace" / "venv" / "bin" / "python"

    def _semantic_best_match(self, new_content: str, candidate_lines: list, cache_path: Path):
        """Return (matched_line, score) for the best semantic near-duplicate, or None.

        Embedding is delegated to utils/semantic.py under the mempalace venv (the
        only venv with the offline ONNX model). Any failure (missing venv,
        timeout, bad output) degrades silently to None, so a memory write never
        breaks or hangs on the semantic pass. This is a CORROBORATION signal only.
        """
        if not candidate_lines or len(new_content) < SEMANTIC_MIN_LEN:
            return None
        mempalace_py = self._semantic_python()
        if not mempalace_py.exists() or not SEMANTIC_SCRIPT.exists():
            return None
        payload = json.dumps({
            "new": new_content,
            "candidates": [_observation_content(line) for line in candidate_lines],
            "threshold": SEMANTIC_THRESHOLD,
            "cache": str(cache_path),
        })
        try:
            r = subprocess.run(
                [str(mempalace_py), str(SEMANTIC_SCRIPT)],
                input=payload, capture_output=True, text=True, timeout=45,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            res = json.loads(r.stdout)
        except (json.JSONDecodeError, ValueError):
            return None
        idx = res.get("match_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidate_lines):
            return None
        return candidate_lines[idx], float(res.get("score", 0.0))

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

        Anchored ground-truth facts are injected verbatim at the top in BOTH
        modes, never truncated, so a pinned fact survives every nightly rewrite
        and is never lost to the model size cap.
        """
        parts = []

        # Anchors first: authoritative, unmissable, and exempt from truncation.
        anchor_section = render_anchors_section(self.load_anchors(user_id))
        if anchor_section:
            parts.append(anchor_section)
            parts.append("")

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
            "Duplicates are auto-detected: a near-restatement strengthens the existing "
            "note (a recurrence count) instead of piling up.\n"
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
