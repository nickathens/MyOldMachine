#!/usr/bin/env python3
"""
Generate images using Pollinations.ai free API.
No API key required. No VPN required. Works globally including EU/Greece.
Free tier: 768x768 max, 1 request per 15 seconds.
"""

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

# --- Config ---
API_BASE = "https://image.pollinations.ai/prompt"
MAX_RETRIES = 3
RETRY_DELAY = 16  # seconds (rate limit is 1 req/15s)
MIN_DIMENSION = 64
MAX_DIMENSION = 768

# JPEG magic bytes (SOI marker)
_JPEG_MAGIC = b"\xff\xd8\xff"


def generate_image(
    prompt: str,
    output_path: str,
    width: int = 768,
    height: int = 768,
    seed: Optional[int] = None,
    enhance: bool = False,
) -> dict:
    """Generate an image via Pollinations.ai. Returns {'success': bool, 'path': str, 'error': str}."""
    width = max(MIN_DIMENSION, min(width, MAX_DIMENSION))
    height = max(MIN_DIMENSION, min(height, MAX_DIMENSION))

    encoded_prompt = urllib.parse.quote(prompt)
    params = {
        "width": width,
        "height": height,
        "nologo": "true",
    }
    if seed is not None:
        params["seed"] = seed
    if enhance:
        params["enhance"] = "true"

    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_BASE}/{encoded_prompt}?{query}"

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=90, follow_redirects=True) as client:
                resp = client.get(url)

            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                is_image = "image" in content_type or resp.content[:3] == _JPEG_MAGIC
                if is_image:
                    out = Path(output_path)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(resp.content)
                    return {"success": True, "path": str(output_path), "error": ""}
                else:
                    body_preview = resp.content[:300].decode("utf-8", errors="replace")
                    return {
                        "success": False,
                        "path": "",
                        "error": f"Response was not an image: {body_preview}",
                    }
            elif resp.status_code == 429:
                if attempt < MAX_RETRIES - 1:
                    print(
                        f"Rate limited. Waiting {RETRY_DELAY}s... (attempt {attempt + 1}/{MAX_RETRIES})",
                        file=sys.stderr,
                    )
                    time.sleep(RETRY_DELAY)
                    continue
                else:
                    return {
                        "success": False,
                        "path": "",
                        "error": "Rate limited after all retries. Wait 15s and try again.",
                    }
            else:
                body_preview = resp.content[:300].decode("utf-8", errors="replace")
                return {
                    "success": False,
                    "path": "",
                    "error": f"API error {resp.status_code}: {body_preview}",
                }

        except httpx.TimeoutException:
            if attempt < MAX_RETRIES - 1:
                print(
                    f"Timeout. Retrying... (attempt {attempt + 1}/{MAX_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(5)
                continue
            return {"success": False, "path": "", "error": "Request timed out (90s)"}
        except Exception as e:
            return {"success": False, "path": "", "error": str(e)}

    return {"success": False, "path": "", "error": "All retries exhausted"}


def main():
    parser = argparse.ArgumentParser(description="Generate images with Pollinations.ai")
    parser.add_argument("prompt", help="Text prompt for image generation")
    parser.add_argument(
        "-o",
        "--output",
        default="/tmp/generated_image.png",
        help="Output file path (default: /tmp/generated_image.png)",
    )
    parser.add_argument(
        "-W",
        "--width",
        type=int,
        default=768,
        help="Image width, max 768 (default: 768)",
    )
    parser.add_argument(
        "-H",
        "--height",
        type=int,
        default=768,
        help="Image height, max 768 (default: 768)",
    )
    parser.add_argument(
        "-s",
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--enhance",
        action="store_true",
        help="Let AI enhance the prompt for better results",
    )

    args = parser.parse_args()

    print("Generating image...", file=sys.stderr)
    result = generate_image(
        prompt=args.prompt,
        output_path=args.output,
        width=args.width,
        height=args.height,
        seed=args.seed,
        enhance=args.enhance,
    )

    print(json.dumps(result, indent=2))
    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
