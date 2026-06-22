#!/usr/bin/env python3
"""
Observation CLI — Save behavioral observations about users.

Called by the bot mid-conversation when it notices something worth recording:
a correction, a preference, a behavioral pattern, a state change, etc.

Dedup/corroboration is handled by MemoryManager.add_observation (two tiers):
  - Lexical near-restatement (Jaccard): the new line is suppressed and the
    existing one is strengthened (a recurrence count), so duplicates don't pile up.
  - Semantic near-duplicate (cosine, optional, needs the mempalace venv): the new
    line is kept but linked as a corroboration, because semantic similarity
    proves recurrence, not identity. Disable with --no-semantic.

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
    --no-semantic    — skip the semantic corroboration pass (lexical dedup only)
"""

import argparse
import re
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import MemoryManager, VALID_OBSERVATION_TYPES
from core.config import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description="Observation CLI for the memory system")
    parser.add_argument("--user", "-u", type=int, required=True, help="User's Telegram ID")
    parser.add_argument("--type", "-t", help=f"Observation type: {', '.join(VALID_OBSERVATION_TYPES)}")
    parser.add_argument("--content", "-c", help="Observation content")
    parser.add_argument("--project", help="Project slug to scope this observation to")
    parser.add_argument("--importance", type=int, default=5,
                        help="Importance score 1-10 (default: 5). Corrections=8, behavioral=5, state=4")
    parser.add_argument("--no-semantic", action="store_true",
                        help="Skip the semantic corroboration pass (lexical dedup only)")
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

        # Add observation (dedup/corroboration is handled inside MemoryManager)
        result = mm.add_observation(args.user, args.type, args.content,
                                    importance=args.importance, project=args.project,
                                    use_semantic=not args.no_semantic)
        status = result.get("status")

        if status == "invalid_type":
            print(f"ERROR: Invalid type '{args.type}'. Must be one of: {', '.join(VALID_OBSERVATION_TYPES)}")
            sys.exit(1)
        elif status == "corroborated_lexical":
            print(f"CORROBORATED (lexical): existing observation strengthened to "
                  f"seen={result['seen']}, new one suppressed for user {args.user}")
        elif status == "corroborated_semantic":
            print(f"LINKED (semantic cos={result.get('score', 0.0):.2f}): kept as a "
                  f"corroborating observation for user {args.user}, seen={result['seen']}")
        else:  # saved
            extras = []
            if args.project:
                extras.append(f"project={args.project}")
            if args.importance != 5:
                extras.append(f"importance={args.importance}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            print(f"OK: Saved {args.type} observation for user {args.user}{extra_str}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
