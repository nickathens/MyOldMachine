#!/usr/bin/env python3
"""Search torrent indexers via Jackett and return ranked results as JSON."""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

JACKETT_URL = "http://127.0.0.1:9117"
API_KEY_PATH = Path.home() / ".jackett-api-key"


def load_api_key() -> str:
    if not API_KEY_PATH.exists():
        sys.stderr.write(
            f"Jackett API key not found at {API_KEY_PATH}. "
            "Run: jq -r .APIKey ~/.config/jackett/Jackett/ServerConfig.json > ~/.jackett-api-key && chmod 600 ~/.jackett-api-key\n"
        )
        sys.exit(2)
    key = API_KEY_PATH.read_text().strip()
    if not key:
        sys.stderr.write(f"{API_KEY_PATH} is empty\n")
        sys.exit(2)
    return key


def query_jackett(query: str, api_key: str, timeout: int) -> list[dict]:
    params = urllib.parse.urlencode({"apikey": api_key, "Query": query})
    url = f"{JACKETT_URL}/api/v2.0/indexers/all/results?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        sys.stderr.write(f"Jackett unreachable at {JACKETT_URL}: {e}\n")
        sys.stderr.write("Is the container running? Check: docker ps --filter name=jackett\n")
        sys.exit(3)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Jackett returned malformed JSON: {e}\n")
        sys.exit(3)
    return data.get("Results", [])


def normalize(
    results: list[dict],
    min_seeders: int,
    max_size_gb: float,
) -> list[dict]:
    GB = 1024**3
    normalized = []
    for r in results:
        seeders = r.get("Seeders") or 0
        size = r.get("Size") or 0
        magnet = r.get("MagnetUri")
        link = r.get("Link")
        download = magnet if magnet else link
        if not download:
            continue
        if seeders < min_seeders:
            continue
        size_gb = size / GB
        if size_gb > max_size_gb:
            continue
        normalized.append(
            {
                "title": r.get("Title") or "(no title)",
                "seeders": seeders,
                "peers": r.get("Peers") or 0,
                "size_gb": round(size_gb, 2),
                "tracker": r.get("Tracker") or "?",
                "category": r.get("CategoryDesc") or "?",
                "magnet": download,
            }
        )
    normalized.sort(key=lambda r: r["seeders"], reverse=True)
    return normalized


def main():
    parser = argparse.ArgumentParser(
        description="Search torrent indexers via Jackett. Returns top results as JSON ranked by seeders."
    )
    parser.add_argument("query", help="Search query (movie/show/anime/game/etc.)")
    parser.add_argument("--limit", type=int, default=10, help="Max results to return (default: 10)")
    parser.add_argument("--min-seeders", type=int, default=5, help="Minimum seeders (default: 5)")
    parser.add_argument(
        "--max-size-gb",
        type=float,
        default=50.0,
        help="Maximum file size in GB (default: 50)",
    )
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout in seconds (default: 45)")
    args = parser.parse_args()

    api_key = load_api_key()
    raw = query_jackett(args.query, api_key, args.timeout)
    results = normalize(raw, args.min_seeders, args.max_size_gb)[: args.limit]

    output = {
        "query": args.query,
        "total_returned": len(results),
        "results": [
            {"index": i + 1, **r} for i, r in enumerate(results)
        ],
    }
    if not results:
        output["hint"] = (
            "Zero results. Either Jackett has no indexers configured "
            "(visit http://127.0.0.1:9117 and add some), the query is too narrow, "
            "or every result was filtered by min-seeders/max-size-gb."
        )
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
