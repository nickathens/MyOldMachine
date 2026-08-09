#!/usr/bin/env python3
"""Convert a document to Markdown through whichever backend reads it best.

Two backends, picked per format, because measuring both over this machine's
real documents said neither wins everywhere:

  anydoc      (Rust, firecrawl/anydoc, MIT)   office formats and csv
  markitdown  (Microsoft)                     pdf, html, images, audio, zip

Measured over a real 195 document corpus on Linux, 107 pdf, 72 xlsx, 9 csv,
6 docx and 1 legacy doc. Re-measure on your own documents rather than trusting
these numbers: scripts/compare_backends.py runs both backends side by side.

  xlsx   anydoc 521 ms against markitdown 14656 ms for the same 72 files, and
         markitdown emits 52334 NaN / "Unnamed: N" filler tokens across that
         corpus which anydoc never produces. Sheet headings are identical on
         multi-sheet workbooks; anydoc omits the heading on single-sheet ones.
  doc    markitdown cannot read legacy binary .doc at all. anydoc can.
  docx   anydoc 32 ms against 982 ms, and markitdown inlines base64 image data
         URIs into the text. anydoc split one word across a field boundary in
         one of six files, which is the known cost on this side.
  pdf    anydoc failed to parse 15 of 107 files that markitdown read fine, and
         drops the fi ligature on most of the rest, so "Please find below"
         comes out "Please nd below". pdf therefore stays on markitdown.

Anything anydoc refuses falls back to markitdown instead of failing. That is
what rescues the one malformed workbook in the 72 and would rescue the next.

Usage:
    convert.py FILE [-o OUT.md] [--backend auto|anydoc|markitdown]

Markdown goes to stdout (or -o). The backend that produced it is announced on
stderr, so piping stdout stays clean.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Formats anydoc reads better than markitdown here. Deliberately excludes pdf:
# see the measurement in the module docstring. Every entry is checked against
# anydoc's own format table by tests/test_docs_convert.py, so a format anydoc
# drops upstream shows up as a failing test rather than a silent fallback.
ANYDOC_FIRST = frozenset(
    {".doc", ".docx", ".odt", ".rtf", ".epub", ".pptx", ".odp", ".xlsx", ".ods", ".csv"}
)

# Read far enough to see a magic number. Zip-based formats (docx, xlsx) cannot
# be sniffed from a header at all, so this only catches the case that matters:
# pdf bytes arriving under an office extension, which routing on the extension
# alone would hand to the backend that mangles pdf.
SNIFF_BYTES = 4096


def _have_anydoc() -> bool:
    return importlib.util.find_spec("anydoc") is not None


def _looks_like_pdf(path: Path) -> bool:
    try:
        import anydoc
    except ImportError:
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(SNIFF_BYTES)
    except OSError:
        return False
    # Compare against the library's own value rather than a string, so this
    # cannot drift if the Format repr changes upstream.
    return anydoc.format_from_bytes(head) == anydoc.format_from_extension("pdf")


def pick_backend(path: Path) -> str:
    """Return "anydoc" or "markitdown" for this file, before any conversion."""
    if path.suffix.lower() not in ANYDOC_FIRST:
        return "markitdown"
    # An install where anydoc never landed still has to convert documents, so
    # its absence degrades to the old single backend rather than raising.
    if not _have_anydoc():
        return "markitdown"
    if _looks_like_pdf(path):
        return "markitdown"
    return "anydoc"


def _via_anydoc(path: Path) -> str:
    import anydoc

    return anydoc.to_markdown(str(path))


def _via_markitdown(path: Path) -> str:
    from markitdown import MarkItDown

    return MarkItDown().convert(str(path)).text_content


def convert(path: Path, backend: str = "auto") -> tuple[str, str]:
    """Convert path to Markdown. Returns (markdown, backend_actually_used).

    A forced backend is honoured with no fallback, so `--backend anydoc` is a
    real measurement of anydoc and never quietly reports markitdown's output.
    """
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    if backend == "markitdown":
        return _via_markitdown(path), "markitdown"
    if backend == "anydoc":
        return _via_anydoc(path), "anydoc"

    if pick_backend(path) == "markitdown":
        return _via_markitdown(path), "markitdown"

    import anydoc

    try:
        return _via_anydoc(path), "anydoc"
    except anydoc.ConvertError as exc:
        # Malformed, encrypted, or a format anydoc declines. markitdown reads
        # several of these. Anything that is not a ConvertError (a missing
        # file, an OS error) is a real fault and is left to propagate.
        print(f"[docs] anydoc declined ({type(exc).__name__}), retrying with markitdown", file=sys.stderr)
        return _via_markitdown(path), "markitdown (anydoc declined)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert a document to Markdown.")
    ap.add_argument("file", help="path to the document")
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    ap.add_argument(
        "--backend",
        choices=("auto", "anydoc", "markitdown"),
        default="auto",
        help="force a backend; default auto routes by format",
    )
    args = ap.parse_args(argv)

    path = Path(args.file).expanduser()
    try:
        text, used = convert(path, args.backend)
    except Exception as exc:  # noqa: BLE001 - CLI boundary, report and exit
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).expanduser().write_text(text, encoding="utf-8")
        print(f"[docs] {used} -> {args.output} ({len(text)} chars)", file=sys.stderr)
    else:
        # Greek ledgers and contracts are the normal case here. Python coerces
        # a bare C locale to utf-8 on its own, but an ISO-8859 locale or a set
        # PYTHONIOENCODING does not get coerced, and writing Greek to that
        # stream raises UnicodeEncodeError. Measured, not assumed. A captured
        # StringIO has no reconfigure and needs none.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
        print(f"[docs] {used}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
