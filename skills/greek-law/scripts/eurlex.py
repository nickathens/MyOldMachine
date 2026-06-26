#!/usr/bin/env python3
"""Fetch an EU legal act in Greek from EUR-Lex by CELEX number.

EUR-Lex serves consolidated EU law. The public CELEX HTML URL works over plain
HTTP, but it answers 202 with an empty body while it renders the document, then
200 with the content. _common.http_get retries on 202 until the body arrives
(verified live with the GDPR, CELEX 32016R0679).

CELEX hints: 3 = legislation, then year, then R regulation, L directive,
D decision. The GDPR is 32016R0679.

Usage:
  python eurlex.py 32016R0679 --max-chars 4000
  python eurlex.py 32016R0679 --lang EN --json
"""
from __future__ import annotations

import argparse

import _common

URL = "https://eur-lex.europa.eu/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"


def fetch(celex, *, lang="EL"):
    url = URL.format(lang=lang.upper(), celex=celex)
    r = _common.http_get(url, retries=6)
    r.raise_for_status()
    if not r.content:
        raise RuntimeError(
            "EUR-Lex returned an empty body (still rendering). Try again."
        )
    return {
        "celex": celex,
        "lang": lang.upper(),
        "url": url,
        "title": _common.page_title(r.content),
        "text": _common.extract_text(r.content),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="EUR-Lex CELEX fetcher")
    p.add_argument("celex", help="CELEX number, e.g. 32016R0679")
    p.add_argument("--lang", default="EL", help="language code, default EL")
    p.add_argument("--max-chars", type=int, default=6000)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    doc = fetch(args.celex, lang=args.lang)
    if args.max_chars and len(doc["text"]) > args.max_chars:
        doc["text"] = doc["text"][: args.max_chars].rstrip() + "\n[... truncated ...]"
    if args.json:
        _common.emit(doc, as_json=True)
    else:
        _common.emit(
            f"{doc['title']}\nΠηγή: {doc['url']}\n\n{doc['text']}", as_json=False
        )


if __name__ == "__main__":
    main()
