#!/usr/bin/env python3
"""Record a video of a webpage using Playwright."""
import asyncio
import argparse
import shutil
import uuid
from pathlib import Path
from playwright.async_api import async_playwright


async def record_video(url: str, output: str, width: int = 1920, height: int = 1080, duration: int = 5):
    """Record a video of the given URL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()

        # Create a unique temp directory for the video
        video_dir = Path(f"/tmp/playwright_videos_{uuid.uuid4().hex[:8]}")
        video_dir.mkdir(exist_ok=True)

        context = await browser.new_context(
            viewport={"width": width, "height": height},
            record_video_dir=str(video_dir),
            record_video_size={"width": width, "height": height}
        )

        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")

        # Wait for the specified duration to capture animation
        await asyncio.sleep(duration)

        await context.close()
        await browser.close()

        # Find the recorded video and move it to output path
        videos = list(video_dir.glob("*.webm"))
        if videos:
            shutil.move(str(videos[0]), output)
            print(f"Video saved to: {output}")
        else:
            print("No video was recorded")

        # Clean up unique temp directory
        shutil.rmtree(video_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Record a video of a webpage")
    parser.add_argument("url", help="URL to record")
    parser.add_argument("output", help="Output video file path")
    parser.add_argument("--width", type=int, default=1920, help="Viewport width")
    parser.add_argument("--height", type=int, default=1080, help="Viewport height")
    parser.add_argument("--duration", type=int, default=5, help="Recording duration in seconds")

    args = parser.parse_args()
    asyncio.run(record_video(args.url, args.output, args.width, args.height, args.duration))


if __name__ == "__main__":
    main()
