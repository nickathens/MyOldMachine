#!/usr/bin/env python3
"""
Anchor CLI for ground-truth facts immune to the nightly model rewrite.

The person model (model.md) is fully rewritten by an LLM every reflection. Over
many rewrites a true, load-bearing fact can quietly soften or vanish (a telephone
game). Anchors fix that: they live in the user's anchors.md and are injected
VERBATIM into the memory context at read time (build_memory_context), so a pinned
fact can never drift, be softened, merged away, or dropped no matter what the LLM
does.

Anchors are promoted DELIBERATELY (by the bot or by the user via this CLI), never
auto-inferred, because an anchor is permanent ground truth and a wrong one is
worse than drift. Nothing ships pre-seeded; every anchor is per-user and added
on purpose.

Usage:
    python anchors.py --user 12345 add "fact text" --id slug --cat aesthetic
    python anchors.py --user 12345 list
    python anchors.py --user 12345 remove slug
    python anchors.py --user 12345 promote "substring of an observation" --id slug --cat behavioral
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import MemoryManager
from core.config import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description="Anchor CLI for the memory system")
    parser.add_argument("--user", "-u", type=int, required=True, help="User's Telegram ID")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add or update an anchor")
    p_add.add_argument("text", help="The anchor fact text")
    p_add.add_argument("--id", dest="anchor_id", help="Stable slug id (auto-derived if omitted)")
    p_add.add_argument("--cat", dest="category", default="",
                       help="Category (identity, behavioral, aesthetic, ...)")

    sub.add_parser("list", help="List all anchors")

    p_rm = sub.add_parser("remove", help="Remove an anchor by id")
    p_rm.add_argument("anchor_id", help="The anchor id to remove")

    p_pr = sub.add_parser("promote", help="Promote a matching observation into an anchor")
    p_pr.add_argument("needle", help="Substring identifying the observation")
    p_pr.add_argument("--id", dest="anchor_id", help="Stable slug id (auto-derived if omitted)")
    p_pr.add_argument("--cat", dest="category", default="", help="Category")

    args = parser.parse_args()

    from utils.session_guard import enforce as _enforce_session_user
    _enforce_session_user(args.user)

    mm = MemoryManager(DATA_DIR)

    if args.cmd == "add":
        r = mm.add_anchor(args.user, args.text, args.anchor_id, args.category)
        if r["status"] == "error":
            print("ERROR: anchor text is empty")
            sys.exit(1)
        cat = f" ({args.category})" if args.category else ""
        print(f"OK: {r['status']} anchor [{r['id']}]{cat} ({r['total']} total)")

    elif args.cmd == "list":
        anchors = mm.load_anchors(args.user)
        if not anchors:
            print(f"No anchors for user {args.user}.")
            return
        for a in anchors:
            cat = f"({a['category']}) " if a["category"] else ""
            print(f"  [{a['id']}] {cat}{a['text']}")
        print(f"\n--- {len(anchors)} anchor(s) for user {args.user} ---")

    elif args.cmd == "remove":
        r = mm.remove_anchor(args.user, args.anchor_id)
        if r["status"] == "not_found":
            print(f"ERROR: no anchor with id '{args.anchor_id}'. Use 'list' to see ids.")
            sys.exit(1)
        print(f"OK: removed anchor [{args.anchor_id}] ({r['total']} remaining)")

    elif args.cmd == "promote":
        r = mm.promote_observation(args.user, args.needle, args.anchor_id, args.category)
        if r["status"] == "error":
            reason = r.get("reason")
            if reason == "no_observations":
                print(f"ERROR: no observations file for user {args.user}")
            elif reason == "no_match":
                print(f"ERROR: no observation contains '{args.needle}'")
            else:
                print("ERROR: anchor text is empty")
            sys.exit(1)
        if r.get("matched", 0) > 1:
            print(f"WARNING: {r['matched']} observations matched; promoted the most recent.")
        cat = f" ({args.category})" if args.category else ""
        print(f"OK: {r['status']} anchor [{r['id']}]{cat} ({r['total']} total)")


if __name__ == "__main__":
    main()
