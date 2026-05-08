#!/usr/bin/env python3
"""Wrap a p5.js sketch body in a self-contained HTML page.

Reads `setup` and `draw` bodies (or just `draw`) from files or stdin and writes
an HTML page that loads p5 from a CDN, sets a deterministic seed, and renders
the sketch.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "scaffold.html").read_text(
    encoding="utf-8"
)


def build(
    *,
    title: str,
    seed: int,
    width: int,
    height: int,
    setup_body: str,
    draw_body: str,
) -> str:
    return (
        TEMPLATE.replace("__TITLE__", title)
        .replace("__SEED__", str(seed))
        .replace("__WIDTH__", str(width))
        .replace("__HEIGHT__", str(height))
        .replace("__SETUP_BODY__", setup_body.strip() or "background(0);")
        .replace("__DRAW_BODY__", draw_body.strip())
    )


def read_source(path: str | None) -> str:
    if path in (None, "-"):
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Build a p5.js HTML page from sketch bodies.")
    p.add_argument("-o", "--output", required=True, help="Output HTML path.")
    p.add_argument("--draw", required=True, help="Path to draw() body, or '-' for stdin.")
    p.add_argument("--setup", help="Path to setup() body. Optional.")
    p.add_argument("--title", default="Generative Sketch")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1080)
    args = p.parse_args()

    draw_body = read_source(args.draw)
    setup_body = read_source(args.setup) if args.setup else ""

    html = build(
        title=args.title,
        seed=args.seed,
        width=args.width,
        height=args.height,
        setup_body=setup_body,
        draw_body=draw_body,
    )
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
