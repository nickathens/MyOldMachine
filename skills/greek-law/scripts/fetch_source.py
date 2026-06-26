#!/usr/bin/env python3
"""Fetch any Greek legal HTML page and extract its readable text.

Works on sources that serve the document body in static HTML: e-nomothesia.gr
(consolidated law text, verified) and individual areiospagos.gr decision pages
(legacy windows-1253, pass --encoding windows-1253). Encoding is sniffed
otherwise; override with --encoding when the output looks garbled.

This is fetch and read, not search, and it only sees static HTML. kodiko.gr,
lawspot.gr and et.gr render their text with JavaScript, so use the browser
skill for those, and for any keyword search (see legal_search.py for the map).

Usage:
  python fetch_source.py "https://www.e-nomothesia.gr/kat-dikasteria-dikaiosune/kya-35236-oik-2026.html"
  python fetch_source.py "URL" --encoding windows-1253 --max-chars 8000 --json
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse

import _common

KNOWN_HOSTS = (
    "kodiko.gr", "lawspot.gr", "e-nomothesia.gr",
    "areiospagos.gr", "et.gr", "europa.eu", "ddee.gr",
)


def fetch(url, *, encoding=None, max_chars=None):
    r = _common.http_get(url)
    r.raise_for_status()
    return {
        "url": str(r.url),
        "title": _common.page_title(r.content, encoding=encoding),
        "text": _common.extract_text(r.content, encoding=encoding, max_chars=max_chars),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Greek legal HTML fetch and extract")
    p.add_argument("url")
    p.add_argument("--encoding", default=None,
                   help="override charset, e.g. windows-1253")
    p.add_argument("--max-chars", type=int, default=8000)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    host = (urlparse(args.url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in KNOWN_HOSTS):
        print(
            f"note: {host or 'this host'} is not a known Greek legal source; "
            "extraction is best effort.",
            file=sys.stderr,
        )

    doc = fetch(args.url, encoding=args.encoding, max_chars=args.max_chars)
    if args.json:
        _common.emit(doc, as_json=True)
    else:
        _common.emit(
            f"{doc['title']}\nΠηγή: {doc['url']}\n\n{doc['text']}", as_json=False
        )


if __name__ == "__main__":
    main()
