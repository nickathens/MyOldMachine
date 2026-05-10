#!/usr/bin/env python3
"""
Observation CLI — Save behavioral observations about users.

Called by the bot mid-conversation when it notices something worth recording:
a correction, a preference, a behavioral pattern, a state change, etc.

Usage:
    python observe.py --user 12345 --type behavioral --content "Prefers short answers"
    python observe.py --user 12345 --type correction --content "Bot got X wrong" --importance 8
    python observe.py --user 12345 --type project --content "Feature Y works" --project my-app
    python observe.py --user 12345 --list
    python observe.py --user 12345 --list --type preference
    python observe.py --user 12345 --search "memory"

Types:
    behavioral  — how they work, communicate, decide
    state       — current mood, priorities, obsessions
    correction  — something the bot got wrong, with the right answer
    preference  — discovered preference (not explicitly stated)
    relationship — trust signals, frustration signals, what's working
    project     — something learned about a specific project
    factual     — a fact about the person (address, schedule, contact, etc.)
    self-eval   — bot evaluates its own performance on a task

Optional flags:
    --project SLUG   — scope observation to a project (routes to project state during reflection)
    --importance N   — importance score 1-10 (default: 5). Higher = more impactful.
"""

import argparse
import re
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import MemoryManager, VALID_OBSERVATION_TYPES
from core.config import DATA_DIR

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


def _is_duplicate(new_content: str, existing_lines: list, threshold: float = 0.6) -> bool:
    """Check if new_content is semantically similar to any recent observation.

    Uses Jaccard similarity on keyword sets. Returns True if any recent
    observation (last 20) shares >= threshold proportion of keywords.
    """
    new_kws = _extract_keywords(new_content)
    if len(new_kws) < 2:
        return False  # Too short to meaningfully deduplicate

    # Only check the last 20 observations
    recent = existing_lines[-20:] if len(existing_lines) > 20 else existing_lines

    for line in recent:
        # Extract the content part after the metadata prefix
        # Format: [YYYY-MM-DD HH:MM] (type) [metadata] content
        content_match = re.search(r'\)\s+(?:\[.*?\]\s*)*(.+)$', line)
        if not content_match:
            continue
        existing_content = content_match.group(1)
        existing_kws = _extract_keywords(existing_content)

        if len(existing_kws) < 2:
            continue

        # Jaccard similarity
        intersection = new_kws & existing_kws
        union = new_kws | existing_kws
        if len(union) == 0:
            continue
        similarity = len(intersection) / len(union)

        if similarity >= threshold:
            return True

    return False


def main():
    parser = argparse.ArgumentParser(description="Observation CLI for the memory system")
    parser.add_argument("--user", "-u", type=int, required=True, help="User's Telegram ID")
    parser.add_argument("--type", "-t", help=f"Observation type: {', '.join(VALID_OBSERVATION_TYPES)}")
    parser.add_argument("--content", "-c", help="Observation content")
    parser.add_argument("--project", help="Project slug to scope this observation to")
    parser.add_argument("--importance", type=int, default=5,
                        help="Importance score 1-10 (default: 5). Corrections=8, behavioral=5, state=4")
    parser.add_argument("--list", "-l", action="store_true", help="List recent observations")
    parser.add_argument("--search", "-s", help="Search observations for a keyword")
    parser.add_argument("--limit", type=int, default=20, help="Number of entries to show (default: 20)")

    args = parser.parse_args()

    from utils.session_guard import enforce as _enforce_session_user
    _enforce_session_user(args.user)

    mm = MemoryManager(DATA_DIR)

    if args.search:
        # Search observations
        observations = mm.get_all_observations(args.user, limit=200)
        query = args.search.lower()
        matches = [o for o in observations if query in o.lower()]
        for line in matches:
            print(line)
        print(f"\n--- {len(matches)} matches for '{args.search}' ---")

    elif args.list:
        # List recent observations
        observations = mm.get_all_observations(args.user, limit=args.limit)
        if args.type:
            observations = [o for o in observations if f"({args.type})" in o]
        if not observations:
            print(f"No observations found for user {args.user}")
            return
        for line in observations:
            print(line)
        print(f"\n--- {len(observations)} observations ---")

    elif args.type and args.content:
        # Validate importance range
        if args.importance < 1 or args.importance > 10:
            print(f"ERROR: Importance must be 1-10, got {args.importance}")
            sys.exit(1)

        # Validate project slug if provided
        if args.project and not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', args.project):
            print(f"ERROR: Invalid project slug '{args.project}'. Use alphanumeric, hyphens, underscores only.")
            sys.exit(1)

        # Deduplication check
        existing = mm.get_all_observations(args.user, limit=50)
        if _is_duplicate(args.content, existing):
            print(f"DEDUP: Similar observation already exists for user {args.user}, skipping")
            return

        # Add observation
        success = mm.add_observation(args.user, args.type, args.content,
                                     importance=args.importance, project=args.project)
        if success:
            extras = []
            if args.project:
                extras.append(f"project={args.project}")
            if args.importance != 5:
                extras.append(f"importance={args.importance}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            print(f"OK: Saved {args.type} observation for user {args.user}{extra_str}")
        else:
            print(f"ERROR: Invalid type '{args.type}'. Must be one of: {', '.join(VALID_OBSERVATION_TYPES)}")
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
