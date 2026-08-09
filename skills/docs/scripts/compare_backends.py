#!/usr/bin/env python3
"""Run both docs backends over the same files and print what differs.

The routing table in convert.py was chosen by measurement, not by reading
release notes, and the right documents to measure are yours rather than mine.
This prints, per file, the wall clock for each backend and the tokens each one
has that the other lacks, with number formatting normalised so that 2560.0 and
2560 do not read as a difference.

Usage:  python3 compare_backends.py FILE [FILE ...]

A large "only markitdown" column that is all NaN and Unnamed is filler, not
content. A non-empty "only markitdown" column of real words on a pdf is the
ligature loss that keeps pdf on markitdown.

Two shapes worth naming, because both are silent:

  a large "only anydoc" column on a csv means markitdown misread the header
  and collapsed the table to one column, which is data gone rather than
  formatting;

  a small "only markitdown" column on a docx, a word or two, is usually anydoc
  breaking a word at a field boundary ("contact" arriving as "ntact"), which
  is why docx is not routed to it.
"""

import re
import sys
import time
import unicodedata
from pathlib import Path

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
TOKEN = re.compile(r"[^\W_]+(?:[.,]\d+)?", re.UNICODE)
FILLER = re.compile(r"NaN|Unnamed:\s*\d+")


def tokens(text: str) -> set:
    for glyph, plain in LIGATURES.items():
        text = text.replace(glyph, plain)
    out = set()
    for word in TOKEN.findall(unicodedata.normalize("NFKC", text)):
        word = word.replace(",", ".")
        if re.fullmatch(r"-?\d+\.\d+", word):
            word = "%g" % float(word)
        out.add(word.casefold())
    return out


def timed(fn, path):
    start = time.perf_counter()
    try:
        return (time.perf_counter() - start) * 1000, fn(path), None
    except Exception as exc:  # noqa: BLE001 - a backend refusing is a result
        return (time.perf_counter() - start) * 1000, None, f"{type(exc).__name__}: {exc}"


def via_anydoc(path):
    import anydoc

    return anydoc.to_markdown(str(path))


def via_markitdown(path):
    from markitdown import MarkItDown

    return MarkItDown().convert(str(path)).text_content


def main(paths) -> int:
    print(f"{'file':34s} {'anydoc':>9s} {'markit':>9s} {'a-only':>7s} {'m-only':>7s} {'filler':>7s}")
    for raw in paths:
        path = Path(raw)
        a_ms, a_md, a_err = timed(via_anydoc, path)
        m_ms, m_md, m_err = timed(via_markitdown, path)
        name = path.name[:34]
        if a_err:
            print(f"{name:34s}  anydoc refused: {a_err[:60]}")
        if m_err:
            print(f"{name:34s}  markitdown refused: {m_err[:60]}")
        if a_md is None or m_md is None:
            continue
        a, m = tokens(a_md), tokens(m_md)
        print(
            f"{name:34s} {a_ms:8.1f}m {m_ms:8.1f}m "
            f"{len(a - m):7d} {len(m - a):7d} {len(FILLER.findall(m_md)):7d}"
        )
        if m - a:
            print(f"    only markitdown has: {sorted(m - a)[:12]}")
        if a - m:
            print(f"    only anydoc has    : {sorted(a - m)[:12]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1:]))
