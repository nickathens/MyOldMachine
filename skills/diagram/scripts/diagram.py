#!/usr/bin/env python3
"""Render Mermaid diagrams to PNG/SVG via the Mermaid CLI (mmdc).

Reads diagram source from a file or stdin, writes the rendered output to a
target path. Dark theme by default with a transparent background, which sits
well on Telegram's chat surface.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PUPPETEER_CONFIG = SCRIPT_DIR / "puppeteer.json"

THEMES = ("default", "dark", "forest", "neutral")
FORMATS = ("png", "svg", "pdf")


def mmdc_major() -> int:
    """Leading version number of the mmdc on PATH, or 0 when it cannot be read."""
    try:
        result = subprocess.run(
            ["mmdc", "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    match = re.match(r"\s*v?(\d+)\.", result.stdout)
    return int(match.group(1)) if match else 0


def size_args(width: int, major: int) -> list[str]:
    """The mmdc flag that sets the output size.

    Mermaid CLI 12 removed -w/--width and refuses it ("error: unknown option
    '-w'"), so passing it there fails every render. Its replacement, --size,
    makes the longest side of a PNG that many pixels: a small diagram comes
    out larger than it did under -w, a very tall one smaller. 11 and older
    only know -w, the page width. An unreadable version gets the current flag.
    """
    if 0 < major < 12:
        return ["-w", str(width)]
    return ["--size", str(width)]


def render(
    source: str,
    output: Path,
    *,
    theme: str = "dark",
    background: str = "transparent",
    width: int = 1600,
    fmt: str | None = None,
) -> None:
    if shutil.which("mmdc") is None:
        raise RuntimeError(
            "mmdc not found. Install it via: "
            "sudo npm install -g @mermaid-js/mermaid-cli"
        )

    if fmt is None:
        fmt = output.suffix.lstrip(".").lower() or "png"
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported format: {fmt}. Choose from {FORMATS}.")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".mmd", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(source)
        input_path = Path(fh.name)

    try:
        cmd = [
            "mmdc",
            "-i",
            str(input_path),
            "-o",
            str(output),
            "-t",
            theme,
            "-b",
            background,
            *size_args(width, mmdc_major()),
            "-p",
            str(PUPPETEER_CONFIG),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"mmdc failed (code {result.returncode}):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
    finally:
        input_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render Mermaid diagrams.")
    p.add_argument(
        "source",
        nargs="?",
        help="Path to Mermaid source. Use '-' or omit to read from stdin.",
    )
    p.add_argument("-o", "--output", required=True, help="Output file path.")
    p.add_argument(
        "-t",
        "--theme",
        choices=THEMES,
        default="dark",
        help="Mermaid theme (default: dark).",
    )
    p.add_argument(
        "-b",
        "--background",
        default="transparent",
        help='Background color or "transparent" (default).',
    )
    p.add_argument(
        "-w",
        "--width",
        type=int,
        default=1600,
        help=(
            "Output size in pixels (default: 1600). Mermaid CLI 12 and later "
            "make this the longest side of a PNG; older versions use it as "
            "the page width."
        ),
    )
    p.add_argument(
        "-f",
        "--format",
        choices=FORMATS,
        default=None,
        help="Output format. Inferred from output extension if omitted.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.source or args.source == "-":
        source_text = sys.stdin.read()
    else:
        source_text = Path(args.source).read_text(encoding="utf-8")

    if not source_text.strip():
        print("Empty diagram source.", file=sys.stderr)
        return 2

    try:
        render(
            source_text,
            Path(args.output),
            theme=args.theme,
            background=args.background,
            width=args.width,
            fmt=args.format,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
