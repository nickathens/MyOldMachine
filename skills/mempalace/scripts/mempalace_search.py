#!/usr/bin/env python3
"""
MemPalace search CLI: query the conversation memory palace.

Must run with the mempalace venv Python:
    <BOT_DIR>/data/mempalace/venv/bin/python <this_script> "query" --wing user_<id>
"""

import argparse
import json
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
PALACE_PATH = str(BOT_DIR / "data" / "mempalace" / "palace")


def search_palace(query, n_results=5, wing=None, room=None):
    if not wing or not wing.strip():
        return {"error": "Wing is required. Pass --wing <wing_name> to scope the search."}

    try:
        from mempalace.searcher import search_memories
    except ImportError:
        return {"error": "mempalace not importable. Is the mempalace venv active?"}

    palace = Path(PALACE_PATH)
    if not palace.exists():
        return {"error": f"Palace not found at {PALACE_PATH}. Run the setup script first."}

    try:
        return search_memories(
            query=query,
            palace_path=PALACE_PATH,
            wing=wing.strip(),
            room=room,
            n_results=n_results,
        )
    except Exception as e:
        return {"error": f"Search failed: {e}"}


def format_results(data):
    if "error" in data:
        return f"Error: {data['error']}"

    results = data.get("results", [])
    if not results:
        return f'No results found for: "{data.get("query", "")}"'

    lines = []
    lines.append(f'Search: "{data["query"]}" ({len(results)} results)')
    lines.append("")

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


def main():
    parser = argparse.ArgumentParser(description="Search MemPalace conversation memory")
    parser.add_argument("query", help="What to search for")
    parser.add_argument("--results", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--wing", required=True, help="Wing filter (e.g., user_12345)")
    parser.add_argument("--room", default=None, help="Room filter (technical, architecture, planning, decisions, problems, general)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    data = search_palace(
        query=args.query,
        n_results=args.results,
        wing=args.wing,
        room=args.room,
    )

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(format_results(data))


if __name__ == "__main__":
    main()
