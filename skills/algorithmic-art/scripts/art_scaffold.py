#!/usr/bin/env python3
"""Wrap a p5.js sketch in a self-contained HTML page.

Two inputs: `setup` and `draw` BODIES (--draw, optional --setup), which the
template wraps in its own functions, or a COMPLETE sketch that defines its
own setup() and draw() (--sketch). A --draw file that defines them is taken
as a complete sketch: nested inside the template's draw() those functions
were only declared, never run, and the canvas stayed black (SKILL.md's own
particle pattern did exactly that; Linux bot review 2026-09-27). Either way the
page loads p5 from a CDN and seeds random() and noise() before the sketch.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

DEFINES_LIFECYCLE = re.compile(r"\bfunction\s+(setup|draw)\s*\(")

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
        TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__SEED__", str(seed))
        .replace("__WIDTH__", str(width))
        .replace("__HEIGHT__", str(height))
        .replace("__SETUP_BODY__", setup_body.strip() or "background(0);")
        .replace("__DRAW_BODY__", draw_body.strip())
    )


def build_sketch(*, title: str, seed: int, width: int, height: int, sketch: str) -> str:
    """A complete sketch, with the seed (and a canvas, if it makes none) set before its setup()."""
    canvas = "" if "createCanvas(" in sketch else f"createCanvas({width}, {height});"
    script = (f"const SEED = {seed};\n{sketch.strip()}\n\n"
              "// seed before the sketch's own setup(); p5 finds setup on window at load\n"
              "const __sketchSetup = typeof setup === 'function' ? setup : function () {};\n"
              f"window.setup = function () {{ {canvas} randomSeed(SEED); noiseSeed(SEED); __sketchSetup(); }};\n")
    page = TEMPLATE.replace("__TITLE__", html.escape(title))
    start = page.index("<script>", page.index("<body>"))
    end = page.index("</script>", start)
    return page[:start] + "<script>\n" + script + "    " + page[end:]


def read_source(path: str | None) -> str:
    if path in (None, "-"):
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Build a p5.js HTML page from draw()/setup() bodies or a complete sketch.")
    p.add_argument("-o", "--output", required=True, help="Output HTML path.")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--draw", help="Path to the draw() BODY, or '-' for stdin.")
    source.add_argument("--sketch", help="Path to a COMPLETE sketch defining setup() and draw(), or '-'.")
    p.add_argument("--setup", help="Path to setup() body (with --draw). Optional.")
    p.add_argument("--title", default="Generative Sketch")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1080)
    args = p.parse_args()
    if args.setup == "-" and "-" in (args.draw, args.sketch):
        p.error("only one input can come from stdin")

    code = read_source(args.sketch if args.sketch else args.draw)
    if args.draw and DEFINES_LIFECYCLE.search(code):
        print("note: the --draw file defines setup()/draw(), so it is used as a complete sketch", file=sys.stderr)
        args.sketch = args.draw
    if args.sketch:
        if args.setup:
            p.error("--setup goes with --draw; a complete sketch has its own setup()")
        page = build_sketch(title=args.title, seed=args.seed, width=args.width, height=args.height, sketch=code)
    else:
        setup_body = read_source(args.setup) if args.setup else ""
        page = build(
            title=args.title,
            seed=args.seed,
            width=args.width,
            height=args.height,
            setup_body=setup_body,
            draw_body=code,
        )
    Path(args.output).write_text(page, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
