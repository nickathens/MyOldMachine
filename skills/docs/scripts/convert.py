#!/usr/bin/env python3
"""Convert a document to Markdown through whichever backend reads it best.

markitdown owns everything. Four formats are routed to anydoc instead, and
each one is here because markitdown measurably loses data on it, not because
anydoc is faster:

  .ods    markitdown cannot open it at all (UnsupportedFormatException)
  .doc    markitdown cannot open legacy binary Word at all
  .csv    markitdown reads csv through pandas, which infers a header. Given a
          real 11 column file whose first line is a comment, it inferred one
          column and dropped 3298 of 3824 cells. anydoc dropped none.
  .xlsx   same pandas path, so empty cells become NaN, unlabelled columns
          become "Unnamed: 3", and integers become floats. 183 to 756 filler
          tokens per accounting workbook from markitdown, zero from anydoc,
          with no cell content lost either way. anydoc omits the sheet heading
          on a single sheet workbook.

Everything else stays on markitdown, including three formats this router
carried at first and should not have:

  .pdf    anydoc failed to parse 15 of 107 files markitdown read fine, and
          drops the fi ligature on most of the rest, so "Please find below"
          comes out "Please nd below".
  .docx   anydoc corrupts text at field boundaries: in a real invoice template
          "contact" came out as "ntact". markitdown's only cost is a short
          "![](data:image/png;base64...)" placeholder per image, truncated,
          not the payload. Corruption beats cosmetics.
  .odt .rtf .epub .pptx .odp
          never measured. There was not one such file in the corpus, so
          routing them was a guess copied from anydoc's format table.

Anything anydoc refuses falls back to markitdown instead of failing. That is
what rescues the one malformed workbook in the corpus and would rescue the
next.

Measured over 195 real documents on Linux: 107 pdf, 72 xlsx, 9 csv, 6 docx,
1 legacy doc. Re-measure on your own documents rather than trusting those
numbers, and measure any format before adding it here:

    python3 skills/docs/scripts/compare_backends.py YOUR_FILE ...

Usage:
    convert.py FILE [-o OUT.md] [--backend auto|anydoc|markitdown]

Markdown goes to stdout (or -o). The backend that produced it is announced on
stderr, so piping stdout stays clean.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType

# Formats where markitdown measurably loses data. See the module docstring for
# what was measured on each. Every entry is checked against anydoc's own format
# table by tests/test_docs_convert.py, so a format anydoc drops upstream shows
# up as a failing test rather than a silent fallback.
ANYDOC_FIRST = frozenset({".doc", ".ods", ".xlsx", ".csv"})

# Read far enough to see a magic number. Zip-based formats (xlsx, ods) cannot
# be sniffed from a header at all, so this only catches the case that matters:
# pdf bytes arriving under a routed extension, which routing on the extension
# alone would hand to the backend that mangles pdf.
SNIFF_BYTES = 4096


def _anydoc() -> ModuleType | None:
    """Return the anydoc module, or None if it is not usable in this process.

    This performs the real import. Asking importlib whether the module is
    findable answers a different question: anydoc is a compiled Rust
    extension, so a broken or mismatched shared object is findable and still
    raises on import. A findable-but-dead anydoc used to make every routed
    document fail instead of falling back.

    Python does not cache a failed import, so a broken install pays this
    attempt once per document, which is the right price for staying up.
    """
    try:
        import anydoc
    except Exception:  # noqa: BLE001 - any import fault means "not usable here"
        return None
    return anydoc


def _have_anydoc() -> bool:
    return _anydoc() is not None


def _looks_like_pdf(path: Path) -> bool:
    mod = _anydoc()
    if mod is None:
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(SNIFF_BYTES)
    except OSError:
        return False
    # Compare against the library's own value rather than a string, so this
    # cannot drift if the Format repr changes upstream.
    return mod.format_from_bytes(head) == mod.format_from_extension("pdf")


def pick_backend(path: Path) -> str:
    """Return "anydoc" or "markitdown" for this file, before any conversion."""
    if path.suffix.lower() not in ANYDOC_FIRST:
        return "markitdown"
    # An install where anydoc never landed, or landed broken, still has to
    # convert documents, so it degrades to the old single backend.
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

    mod = _anydoc()
    if mod is None:
        # pick_backend already imported it successfully, so reaching here means
        # anydoc died between two calls. Convert the document anyway.
        print("[docs] anydoc became unusable, using markitdown", file=sys.stderr)
        return _via_markitdown(path), "markitdown (anydoc unusable)"

    try:
        return _via_anydoc(path), "anydoc"
    except mod.ConvertError as exc:
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
        # Non-Latin ledgers and contracts are a normal case, and stdout is not
        # always utf-8. Python coerces a bare C locale on its own, but an
        # ISO-8859 locale or a set PYTHONIOENCODING does not get coerced, and
        # writing to that stream raises UnicodeEncodeError. Measured, not
        # assumed. A captured StringIO has no reconfigure and needs none.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
        print(f"[docs] {used}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
