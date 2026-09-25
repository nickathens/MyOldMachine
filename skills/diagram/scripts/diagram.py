#!/usr/bin/env python3
"""Render Mermaid diagrams to PNG/SVG via the Mermaid CLI (mmdc).

Reads diagram source from a file or stdin, writes the rendered output to a
target path. Dark theme by default with a transparent background, which sits
well on Telegram's chat surface.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PUPPETEER_CONFIG = SCRIPT_DIR / "puppeteer.json"
# Mermaid 12 changed three defaults that re-draw every existing diagram: the
# shaded neo look, a 120 px minimum label width for flowchart and state
# nodes, and the ELK layout for flowchart, state, class, ER and requirement
# diagrams. This file puts all three back to Mermaid 11's, and 13 of the 16
# diagram types measured then render pixel identical to 11. The layout is
# set per diagram type on purpose: a global `layout` outranks each type's
# own default, and turned the radial mindmap into a flat tree. On 11 the
# file changes nothing. A diagram's own front matter still wins, so one
# diagram can ask for `layout: elk` or `look: neo`.
MERMAID_CONFIG = SCRIPT_DIR / "mermaid.json"

THEMES = ("default", "dark", "forest", "neutral")
FORMATS = ("png", "svg", "pdf")

# The page height mermaid-cli 11 used when no -H was given. Only the width
# shapes a render: the PNG is cropped to the diagram either way.
PAGE_HEIGHT = 600


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
    """The mmdc flag that sets the page width, where the version has one.

    Mermaid CLI 12 removed -w/--width and refuses it ("error: unknown option
    '-w'"), so passing it there fails every render. Its --size is not a
    rename: it scales a PNG until the longest side is that many pixels, so a
    small diagram is blown up and a tall one squeezed. On 12 the page width
    travels in the launch config instead (see puppeteer_config). 11 and older
    take -w, which overrides that config. An unreadable version counts as
    current: guessing 11 on a 12 install fails every render, guessing 12 on
    an 11 install only narrows the page to 800 px.
    """
    if 0 < major < 12:
        return ["-w", str(width)]
    return []


def puppeteer_config(width: int) -> dict:
    """The browser launch config: scripts/puppeteer.json plus the page size.

    Puppeteer's defaultViewport does what -w did: the page is `width` px
    wide, a wider diagram is fitted to it, a narrower one renders at its
    natural size. Measured on mermaid-cli 12.0.0.
    """
    config = json.loads(PUPPETEER_CONFIG.read_text(encoding="utf-8"))
    config["defaultViewport"] = {"width": width, "height": PAGE_HEIGHT}
    return config


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

    with tempfile.TemporaryDirectory(prefix="diagram-") as tmp:
        input_path = Path(tmp) / "diagram.mmd"
        input_path.write_text(source, encoding="utf-8")
        config_path = Path(tmp) / "puppeteer.json"
        config_path.write_text(json.dumps(puppeteer_config(width)), encoding="utf-8")
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
            str(config_path),
            "-c",
            str(MERMAID_CONFIG),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"mmdc failed (code {result.returncode}):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )


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
            "Page width in pixels (default: 1600). A wider diagram is fitted "
            "to it, a narrower one keeps its natural size."
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
