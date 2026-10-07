#!/usr/bin/env python3
"""Search and fetch Greek government acts from Διαύγεια (diavgeia.gov.gr).

Διαύγεια is the open transparency registry: every administrative act the Greek
state publishes (αποφάσεις, συμβάσεις, διορισμοί), with an open REST API that
needs no key and is licensed CC BY. This wraps the two endpoints a legal
grounding task needs: keyword search and fetch by ΑΔΑ.

Verified live against the opendata API. Working query params: q, size, page,
from_issue_date, to_issue_date (date only; a time is misread). Both bounds are
Athens midnight at the START of the day, and an act's date is stored either at
UTC midnight or at Athens midnight, so --to sends the day after and drops what
lands on it, and dates print in Athens time (re-measured 2026-10-07).

Usage:
  python diavgeia.py search "προμήθεια" --size 10 --from 2024-01-01 --to 2024-03-31
  python diavgeia.py get ΡΨ0Ι46ΜΤΛΡ-Α7Κ --json
Add --json to search for the raw API payload.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import _common

ATHENS = ZoneInfo("Europe/Athens")

SEARCH_URL = "https://diavgeia.gov.gr/opendata/search"
DECISION_URL = "https://diavgeia.gov.gr/luminapi/api/decisions/{ada}"


def _fmt_date(ms):
    """The issue date as it reads in Greece: an act of 28 March may be stored
    at 2024-03-27T22:00Z (Athens midnight), which UTC prints as the 27th."""
    if ms is None:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=ATHENS).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(ms)


def _day(value):
    """argparse type: a YYYY-MM-DD day, kept as written."""
    date.fromisoformat(value)
    return value


def search(query, *, size=10, page=0, from_date=None, to_date=None):
    params = {"q": query or "", "size": size, "page": page}
    if from_date:
        params["from_issue_date"] = from_date
    if to_date:
        # the API stops at Athens midnight at the START of to_issue_date, which
        # leaves out the acts of that day stored at UTC midnight
        params["to_issue_date"] = (date.fromisoformat(to_date) + timedelta(days=1)).isoformat()
    r = _common.http_get(SEARCH_URL, params=params, accept_json=True)
    r.raise_for_status()
    data = r.json()
    if to_date and isinstance(data.get("decisions"), list):
        data["decisions"] = [d for d in data["decisions"]
                             if _fmt_date(d.get("issueDate")) <= to_date]
        if isinstance(data.get("info"), dict):
            data["info"]["total_includes_next_day"] = True
    return data


def get(ada):
    r = _common.http_get(DECISION_URL.format(ada=ada), accept_json=True)
    r.raise_for_status()
    return r.json()


def summarize(data):
    out = []
    info = data.get("info", {})
    total = info.get("total")
    if total is not None:
        about = "περίπου " if info.get("total_includes_next_day") else ""
        out.append(f"Σύνολο αποτελεσμάτων: {about}{total}")
        out.append("")
    for d in data.get("decisions", []):
        out.append(f"ΑΔΑ: {d.get('ada', '')}")
        out.append(f"  Ημ/νία έκδοσης: {_fmt_date(d.get('issueDate'))}")
        out.append(f"  Θέμα: {(d.get('subject') or '').strip()}")
        out.append(f"  Φορέας (id): {d.get('organizationId', '')}")
        if d.get("documentUrl"):
            out.append(f"  Έγγραφο: {d['documentUrl']}")
        out.append("")
    if not data.get("decisions"):
        out.append("Καμία απόφαση δεν βρέθηκε.")
    return "\n".join(out).rstrip()


def main(argv=None):
    p = argparse.ArgumentParser(description="Διαύγεια open API client")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("search", help="keyword search over published acts")
    ps.add_argument("query")
    ps.add_argument("--size", type=int, default=10)
    ps.add_argument("--page", type=int, default=0)
    ps.add_argument("--from", dest="from_date", default=None, type=_day,
                    help="first issue date, YYYY-MM-DD")
    ps.add_argument("--to", dest="to_date", default=None, type=_day,
                    help="last issue date, YYYY-MM-DD (its whole day is included)")
    ps.add_argument("--json", action="store_true")

    pg = sub.add_parser("get", help="fetch one decision by ΑΔΑ (raw JSON)")
    pg.add_argument("ada")

    args = p.parse_args(argv)
    if args.cmd == "search":
        data = search(args.query, size=args.size, page=args.page,
                      from_date=args.from_date, to_date=args.to_date)
        _common.emit(data if args.json else summarize(data), as_json=args.json)
    elif args.cmd == "get":
        _common.emit(get(args.ada), as_json=True)


if __name__ == "__main__":
    main()
