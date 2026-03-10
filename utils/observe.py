#!/usr/bin/env python3
"""
Observation CLI — Save behavioral observations about users.

Called by the bot mid-conversation when it notices something worth recording:
a correction, a preference, a behavioral pattern, a state change, etc.

Usage:
    python observe.py --user 12345 --type behavioral --content "Prefers short answers"
    python observe.py --user 12345 --type correction --content "Bot got X wrong, correct is Y"
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
"""

import argparse
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
    parser.add_argument("--list", "-l", action="store_true", help="List recent observations")
    parser.add_argument("--search", "-s", help="Search observations for a keyword")
    parser.add_argument("--limit", type=int, default=20, help="Number of entries to show (default: 20)")

    args = parser.parse_args()
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
        # Add observation
        success = mm.add_observation(args.user, args.type, args.content)
        if success:
            print(f"OK: Saved {args.type} observation for user {args.user}")
        else:
            print(f"ERROR: Invalid type '{args.type}'. Must be one of: {', '.join(VALID_OBSERVATION_TYPES)}")
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
