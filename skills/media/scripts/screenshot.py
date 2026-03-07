#!/usr/bin/env python3
"""
Take a screenshot of a URL or local file.
Usage:
    python screenshot.py https://example.com output.png
    python screenshot.py http://localhost:8080 output.png
    python screenshot.py file:///path/to/file.html output.png

Options:
    --full-page    Capture the full scrollable page (default: viewport only)
    --width N      Viewport width in pixels (default: 1280)
    --height N     Viewport height in pixels (default: 720)
    --wait N       Wait N milliseconds after page load (default: 1000)
"""

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def take_screenshot(
    url: str,
    output: str,
    full_page: bool = False,
    width: int = 1280,
    height: int = 720,
    wait_ms: int = 1000
):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": width, "height": height}
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            # Try with domcontentloaded if networkidle times out
            print(f"Warning: {e}, retrying with domcontentloaded", file=sys.stderr)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Additional wait for any animations/JS
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)

        await page.screenshot(path=output, full_page=full_page)
        await browser.close()

    print(f"Screenshot saved: {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description="Take a screenshot of a URL")
    parser.add_argument("url", help="URL to screenshot (http://, https://, or file://)")
    parser.add_argument("output", help="Output filename (e.g., screenshot.png)")
    parser.add_argument("--full-page", action="store_true", help="Capture full scrollable page")
    parser.add_argument("--width", type=int, default=1280, help="Viewport width")
    parser.add_argument("--height", type=int, default=720, help="Viewport height")
    parser.add_argument("--wait", type=int, default=1000, help="Wait time in ms after page load")

    args = parser.parse_args()

    # Ensure output directory exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(take_screenshot(
        args.url,
        args.output,
        full_page=args.full_page,
        width=args.width,
        height=args.height,
        wait_ms=args.wait
    ))


if __name__ == "__main__":
    main()
