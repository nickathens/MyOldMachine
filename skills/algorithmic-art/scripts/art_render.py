#!/usr/bin/env python3
"""Render a generative HTML sketch (p5.js or canvas) to a PNG via Playwright.

Loads the file in headless Chromium and captures the canvas. A p5 sketch is
captured at an exact frame (--frames, default 120, about 2 s at 60 fps), or
as soon as it stops its own loop (noLoop()), so a seed gives the same still
every time. It used to wait 2 s of wall time, and two renders of one seed
differed across the whole canvas (Linux bot review 2026-09-27). A page
without p5 is captured --settle-ms after its canvas appears.

Failures say what happened: p5 not loaded (offline, CDN blocked), a
JavaScript error in the sketch, or a timeout with the frame it reached. A
canvas of one flat colour is written but warned about.

For video capture (animated sketches), use the media skill:
skills/media/scripts/record_video.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

# Runs before the page's own scripts. Our load listener fires before p5's
# global init (p5 1.x starts from a promise resolved on load), so the
# sketch's setup() and draw() are wrapped before p5 first calls them.
HOOK = """
(() => {
  const status = window.__artRender = {loaded: false, p5: false, p5Script: false, lifecycle: false,
                                       setupDone: false, hasDraw: false, frames: 0, done: false};
  window.addEventListener('load', () => {
    status.loaded = true;
    status.p5 = typeof window.p5 === 'function';
    status.p5Script = [...document.scripts].some(s => /p5/i.test(s.src));
    const userSetup = window.setup, userDraw = window.draw;
    status.lifecycle = typeof userSetup === 'function' || typeof userDraw === 'function';
    status.hasDraw = typeof userDraw === 'function';
    if (!status.p5 || !status.lifecycle) return;
    window.setup = function () {
      const out = typeof userSetup === 'function' ? userSetup.apply(this, arguments) : undefined;
      status.setupDone = true;
      if (!status.hasDraw) status.done = true;
      return out;
    };
    if (status.hasDraw) {
      window.draw = function () {
        userDraw.apply(this, arguments);
        status.frames = window.frameCount;
        const stopped = typeof window.isLooping === 'function' && !window.isLooping();
        if (window.frameCount >= __TARGET__ || stopped) {
          window.noLoop();
          status.done = true;
        }
      };
    }
  });
})();
"""

CAPTURE = "() => { const c = document.querySelector('canvas'); return c ? c.toDataURL('image/png') : null; }"


class RenderError(RuntimeError):
    pass


async def render(
    html_path: Path,
    output: Path,
    *,
    width: int,
    height: int,
    frames: int = 120,
    settle_ms: int = 2000,
    timeout_s: float = 120.0,
) -> dict:
    url = "file://" + str(html_path.resolve())
    errors: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            context = await browser.new_context(viewport={"width": width, "height": height})
            await context.add_init_script(HOOK.replace("__TARGET__", str(frames)))
            page = await context.new_page()
            page.on("pageerror", lambda exc: errors.append(f"JavaScript error: {exc}"))
            page.on("console", lambda msg: errors.append(f"console: {msg.text}")
                    if msg.type == "error" else None)
            try:
                await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
            except PlaywrightTimeout:
                raise RenderError(f"the page did not finish loading in {timeout_s:g} s "
                                  "(a script from the network hanging?)") from None

            status = await page.evaluate("window.__artRender")
            if status["lifecycle"] and not status["p5"] and status["p5Script"]:
                raise RenderError("p5.js did not load, so the sketch never ran (offline, or the CDN is "
                                  "blocked). " + "; ".join(errors))

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            p5_sketch = status["lifecycle"] and status["p5"]
            while True:
                if any(e.startswith("JavaScript error") for e in errors):
                    raise RenderError("; ".join(errors))
                status = await page.evaluate("window.__artRender")
                has_canvas = await page.evaluate(
                    "() => { const c = document.querySelector('canvas'); return !!c && c.width > 0 && c.height > 0; }")
                if p5_sketch and status["done"] and has_canvas:
                    break
                if not p5_sketch and has_canvas:
                    await page.wait_for_timeout(settle_ms)
                    break
                if loop.time() > deadline:
                    if p5_sketch and status["setupDone"]:
                        raise RenderError(f"timed out after {timeout_s:g} s at frame {status['frames']} of {frames}: "
                                          "the sketch is slow, so lower --frames, add noLoop(), or raise --timeout")
                    where = "before setup() finished" if p5_sketch else "with no canvas drawn"
                    raise RenderError(f"timed out after {timeout_s:g} s {where}. " + "; ".join(errors))
                await asyncio.sleep(0.05)

            data_url = await page.evaluate(CAPTURE)
        finally:
            await browser.close()

    data = base64.b64decode(data_url.split(",", 1)[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    image = Image.open(io.BytesIO(data))
    flat = all(low == high for low, high in image.getextrema())
    return {"size": image.size, "frames": status.get("frames", 0) if p5_sketch else None, "flat": flat}


def main() -> int:
    p = argparse.ArgumentParser(description="Render generative HTML sketch to PNG.")
    p.add_argument("html", help="Path to HTML file.")
    p.add_argument("-o", "--output", required=True, help="Output PNG path.")
    p.add_argument("--width", type=int, default=1080, help="Browser viewport width.")
    p.add_argument("--height", type=int, default=1080, help="Browser viewport height.")
    p.add_argument("--frames", type=int, default=120,
                   help="p5 sketches: capture after this many draw() frames (default 120), "
                        "or when the sketch calls noLoop().")
    p.add_argument("--settle-ms", type=int, default=2000,
                   help="Pages without p5: wait this long after the canvas appears (ms).")
    p.add_argument("--timeout", type=float, default=120.0, help="Give up after this many seconds.")
    args = p.parse_args()
    if args.frames < 1:
        p.error("--frames must be at least 1")

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"Not found: {html_path}", file=sys.stderr)
        return 1

    try:
        info = asyncio.run(render(html_path, Path(args.output), width=args.width, height=args.height,
                                  frames=args.frames, settle_ms=args.settle_ms, timeout_s=args.timeout))
    except RenderError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    at = f", frame {info['frames']}" if info["frames"] else ""
    print(f"Wrote {args.output} ({info['size'][0]}x{info['size'][1]}{at})")
    if info["flat"]:
        print("WARNING: the canvas is one flat colour: nothing was drawn, or it was all cleared",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
