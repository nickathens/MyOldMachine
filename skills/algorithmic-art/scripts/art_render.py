#!/usr/bin/env python3
"""Render a generative HTML sketch (p5.js or canvas) to a PNG via Playwright.

Loads the file in headless Chromium, waits for the canvas to be drawn, then
screenshots the canvas. For video capture (animated sketches), use the media
skill: skills/media/scripts/record_video.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def render(
    html_path: Path,
    output: Path,
    *,
    width: int,
    height: int,
    settle_ms: int,
) -> None:
    url = "file://" + str(html_path.resolve())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": width, "height": height}
        )
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_function(
            "() => { const c = document.querySelector('canvas'); "
            "return c && c.width > 0 && c.height > 0; }"
        )
        await page.wait_for_timeout(settle_ms)
        data_url = await page.evaluate(
            "() => document.querySelector('canvas').toDataURL('image/png')"
        )
        data = base64.b64decode(data_url.split(",", 1)[1])
        output.write_bytes(data)
        await browser.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Render generative HTML sketch to PNG.")
    p.add_argument("html", help="Path to HTML file.")
    p.add_argument("-o", "--output", required=True, help="Output PNG path.")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument(
        "--settle-ms",
        type=int,
        default=2000,
        help="Wait this long after canvas appears for the sketch to settle (ms).",
    )
    args = p.parse_args()

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"Not found: {html_path}", file=sys.stderr)
        return 1

    asyncio.run(
        render(
            html_path,
            Path(args.output),
            width=args.width,
            height=args.height,
            settle_ms=args.settle_ms,
        )
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
