#!/usr/bin/env python3
"""
MemPalace search: query a single user's permanent conversation memory.

Each user owns their own ChromaDB palace at <user_dir>/mempalace/palace/. This
script searches exactly one palace -- no cross-user mixing.

Run with the shared mempalace venv Python:
    <BOT_DIR>/data/mempalace/venv/bin/python <this> "query" --user-dir <user_dir>

Arguments:
    query                The search string.
    --user-dir PATH      Required. The user's data directory (the parent of
                         their mempalace/ subtree).
    --results N          Number of hits to return. Default: 5.
    --room ROOM          Optional category filter (e.g. technical, decisions).
    --json               Emit raw JSON instead of formatted text.
"""

import argparse
import json
import sys
from pathlib import Path


def _palace_path(user_dir: Path) -> Path:
    return user_dir / "mempalace" / "palace"


def _wing_for(user_dir: Path) -> str:
    """Stable wing name within a per-user palace.

    Each user only ever has ONE wing in their own palace, so the value
    matters only for mempalace API satisfaction. Reusing `user_<basename>`
    keeps mining and search consistent without leaking the absolute path.
    """
    return f"user_{user_dir.name}"


def search_palace(query: str, user_dir: Path, n_results: int = 5, room: str | None = None) -> dict:
    palace = _palace_path(user_dir)
    if not palace.exists():
        return {"error": f"Palace not found at {palace}. Run mempalace_setup.py first."}

    try:
        from mempalace.searcher import search_memories  # type: ignore[import-not-found]
    except ImportError:
        return {"error": "mempalace not importable. Activate the mempalace venv."}

    try:
        return search_memories(
            query=query,
            palace_path=str(palace),
            wing=_wing_for(user_dir),
            room=room,
            n_results=n_results,
        )
    except Exception as e:
        return {"error": f"Search failed: {e}"}


def format_results(data: dict) -> str:
    if "error" in data:
        return f"Error: {data['error']}"

    results = data.get("results", [])
    if not results:
        return f'No results found for: "{data.get("query", "")}"'

    lines = [f'Search: "{data["query"]}" ({len(results)} results)', ""]
    for i, hit in enumerate(results, 1):
        sim = hit.get("similarity", 0)
        wing = hit.get("wing", "?")
        room = hit.get("room", "?")
        source = hit.get("source_file", "?")
        lines.append(f"[{i}] {wing}/{room} (source: {source}, similarity: {sim})")
        text = hit.get("text", "").strip()
        if len(text) > 600:
            text = text[:600] + "..."
        for line in text.split("\n"):
            lines.append(f"    {line}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search a user's MemPalace")
    parser.add_argument("query", help="What to search for")
    parser.add_argument("--user-dir", required=True, help="Path to the user's data directory")
    parser.add_argument("--results", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--room", default=None, help="Optional category filter (technical, decisions, etc.)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    user_dir = Path(args.user_dir).expanduser().resolve()
    if not user_dir.exists():
        print(f"Error: --user-dir does not exist: {user_dir}", file=sys.stderr)
        sys.exit(2)

    data = search_palace(
        query=args.query,
        user_dir=user_dir,
        n_results=args.results,
        room=args.room,
    )

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(format_results(data))


if __name__ == "__main__":
    main()
