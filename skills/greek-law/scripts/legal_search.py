#!/usr/bin/env python3
"""Grounding source registry for the greek-law skill.

One place that records every primary source, what it covers, how it is reached,
and whether it is verified for direct fetching or needs the browser skill. The
skill consults this to pick the right grounding tool and to stay honest about
coverage. Run with no arguments (or --list) to print the map; --json for the
machine readable form.

Status values:
  verified         direct fetch works and is tested
  fetch-only       a known document URL fetches; keyword search needs browser
  browser-required no open API; search and download need the browser skill
  stub             paid; recommend a subscription, do not block on one
"""
from __future__ import annotations

import argparse
import json

STATUSES = ("verified", "fetch-only", "browser-required", "stub")

SOURCES = [
    {
        "id": "diavgeia",
        "name": "Διαύγεια (diavgeia.gov.gr)",
        "covers": "Government administrative acts: αποφάσεις, συμβάσεις, διορισμοί.",
        "access": "Open REST API, no key, CC BY.",
        "status": "verified",
        "tool": "diavgeia.py search QUERY | diavgeia.py get ADA",
    },
    {
        "id": "eurlex",
        "name": "EUR-Lex (eur-lex.europa.eu)",
        "covers": "EU law: regulations, directives, consolidated texts, in Greek.",
        "access": "Public CELEX HTML; answers 202 then 200, retried.",
        "status": "verified",
        "tool": "eurlex.py CELEX",
    },
    {
        "id": "enomothesia",
        "name": "e-nomothesia.gr",
        "covers": "Consolidated, current text of laws and codes, by category.",
        "access": "Static HTML document pages; the statute body is in the page.",
        "status": "verified",
        "tool": "fetch_source.py URL",
    },
    {
        "id": "kodiko",
        "name": "kodiko.gr, lawspot.gr",
        "covers": "Consolidated law text and commentary.",
        "access": ("JavaScript app: only the title and metadata are in the static "
                   "HTML, the statute body loads later. Use the browser skill."),
        "status": "browser-required",
        "tool": "browser skill: open the page, read the rendered text",
    },
    {
        "id": "areiospagos",
        "name": "Άρειος Πάγος (areiospagos.gr)",
        "covers": "Supreme civil and criminal case law from 2006 onward.",
        "access": ("A known decision URL fetches and extracts (legacy windows-1253, "
                   "pass --encoding windows-1253). Keyword search is a legacy form; "
                   "use the browser skill to search."),
        "status": "fetch-only",
        "tool": "fetch_source.py DECISION_URL --encoding windows-1253  (search: browser)",
    },
    {
        "id": "et",
        "name": "Εθνικό Τυπογραφείο (et.gr)",
        "covers": "Authoritative ΦΕΚ: every law and decree as published.",
        "access": "JavaScript app, no open API; search and PDF download need the browser.",
        "status": "browser-required",
        "tool": "browser skill: open et.gr, search ΦΕΚ, download the PDF",
    },
    {
        "id": "paid",
        "name": "NOMOS, Ισοκράτης, Sakkoulas, Qualex",
        "covers": "Annotated case law and the deepest current coverage.",
        "access": "Paid subscription. Recommend to a professional; never required.",
        "status": "stub",
        "tool": "subscription; see SKILL.md, Paid databases",
    },
]


def render():
    lines = ["Πηγές τεκμηρίωσης (grounding sources)", ""]
    for s in SOURCES:
        lines.append(f"[{s['status']}] {s['name']}")
        lines.append(f"  Καλύπτει: {s['covers']}")
        lines.append(f"  Πρόσβαση: {s['access']}")
        lines.append(f"  Εργαλείο: {s['tool']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv=None):
    p = argparse.ArgumentParser(description="greek-law grounding source registry")
    p.add_argument("--list", action="store_true", help="print the source map (default)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if args.json:
        print(json.dumps(SOURCES, ensure_ascii=False, indent=2))
    else:
        print(render())


if __name__ == "__main__":
    main()
