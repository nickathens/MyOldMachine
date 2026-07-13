#!/usr/bin/env python3
"""Brand-quality imagery via Higgsfield backend prompt enhancement.

Two subcommands, each a thin wrapper over the Higgsfield CLI:
  photoshoot  -> `higgsfield product-photoshoot create` (mode-specific brand prompt)
  cards       -> `higgsfield marketplace-cards create`   (marketplace main/secondary/A+)

Both accept --enhance-only to return the backend-enhanced prompts WITHOUT generating
(free, no image jobs). Otherwise every returned image is downloaded to --output-dir.

    python brand.py photoshoot --mode product_shot --prompt "hero shot" --image bottle.jpg --count 3
    python brand.py photoshoot --mode lifestyle_scene --prompt "bottle for IG" --image b.jpg --enhance-only
    python brand.py cards --scope full-set --prompt "peach lemonade can" --image can.png
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

PHOTOSHOOT_MODES = [
    "moodboard_pin", "hero_banner", "virtual_model_tryout", "conceptual_product",
    "restyle", "product_shot", "lifestyle_scene", "closeup_product_with_person",
    "social_carousel", "ad_creative_pack",
]
CARD_SCOPES = ["main", "product-images", "aplus", "full-set"]

OUTPUT_DIR = Path("/tmp/media_gen_brand")


def _collect_urls(obj) -> list[str]:
    """Recursively collect every result_url string in a JSON structure (order-preserving)."""
    urls: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "result_url" and isinstance(v, str) and v:
                urls.append(v)
            else:
                urls.extend(_collect_urls(v))
    elif isinstance(obj, list):
        for item in obj:
            urls.extend(_collect_urls(item))
    return urls


def _ext_from_url(url: str) -> str:
    tail = url.split("?", 1)[0].rsplit(".", 1)
    if len(tail) == 2 and 1 <= len(tail[1]) <= 5:
        return "." + tail[1].lower()
    return ".jpg"


def _download(urls: list[str], prefix: str, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for i, url in enumerate(urls, 1):
            try:
                resp = client.get(url)
            except Exception:  # noqa: BLE001 - skip a URL that errors, keep the rest
                continue
            if resp.status_code != 200:
                continue
            path = out_dir / f"{prefix}_{stamp}_{i}{_ext_from_url(url)}"
            path.write_bytes(resp.content)
            paths.append(str(path))
    return paths


def _run(cmd: list[str], enhance_only: bool, prefix: str, out_dir: Path, timeout: int = 900) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"timed out ({timeout}s)"}
    except Exception as e:  # noqa: BLE001 - surface any failure as JSON to the caller
        return {"success": False, "error": str(e)}

    if r.returncode != 0:
        err = (r.stderr.strip() or r.stdout.strip())
        if "authenticate" in err.lower() or "token" in err.lower():
            err = "Higgsfield not authenticated. Run: higgsfield auth login"
        return {"success": False, "error": err[:500]}

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"success": False, "error": f"Invalid JSON from CLI: {r.stdout[:300]}"}

    if enhance_only:
        return {"success": True, "enhance_only": True, "data": data}

    urls = _collect_urls(data)
    if not urls:
        return {"success": False, "error": f"No result URLs in response: {json.dumps(data)[:300]}"}
    paths = _download(urls, prefix, out_dir)
    if not paths:
        return {"success": False, "error": f"Found {len(urls)} URLs but downloaded none"}
    return {"success": True, "paths": paths, "count": len(paths), "urls": urls}


def cmd_photoshoot(args) -> dict:
    cmd = ["higgsfield", "product-photoshoot", "create", "--json",
           "--mode", args.mode, "--prompt", args.prompt, "--count", str(args.count)]
    for img in (args.image or []):
        cmd.extend(["--image", img])
    if args.brand_context:
        cmd.extend(["--brand_context", args.brand_context])
    if args.product_context:
        cmd.extend(["--product_context", args.product_context])
    if args.aspect_ratio:
        cmd.extend(["--aspect_ratio", args.aspect_ratio])
    if args.enhance_only:
        cmd.append("--enhance-only")
    return _run(cmd, args.enhance_only, "photoshoot", Path(args.output_dir))


def cmd_cards(args) -> dict:
    cmd = ["higgsfield", "marketplace-cards", "create", "--json",
           "--scope", args.scope, "--prompt", args.prompt]
    for img in (args.image or []):
        cmd.extend(["--image", img])
    if args.category:
        cmd.extend(["--category", args.category])
    if args.brand_context:
        cmd.extend(["--brand_context", args.brand_context])
    if args.product_context:
        cmd.extend(["--product_context", args.product_context])
    if args.visual_style:
        cmd.extend(["--visual_style", args.visual_style])
    if args.enhance_only:
        cmd.append("--enhance-only")
    return _run(cmd, args.enhance_only, "cards", Path(args.output_dir))


def main():
    p = argparse.ArgumentParser(description="Brand imagery via Higgsfield backend prompt enhancement")
    sub = p.add_subparsers(dest="command", required=True)

    ps = sub.add_parser("photoshoot", help="Product photoshoot (mode-specific brand prompt)")
    ps.add_argument("--mode", required=True, choices=PHOTOSHOOT_MODES, help="Photoshoot mode")
    ps.add_argument("--prompt", required=True, help="High-level intent")
    ps.add_argument("--image", action="append", help="Reference image path or upload id (repeatable)")
    ps.add_argument("--count", type=int, default=1, help="Number of variants (1-10)")
    ps.add_argument("--brand_context", default=None, help="Extra brand description")
    ps.add_argument("--product_context", default=None, help="Extra product description")
    ps.add_argument("--aspect_ratio", default=None, help="Override the mode's default aspect ratio")
    ps.add_argument("--enhance-only", action="store_true", dest="enhance_only",
                    help="Return enhanced prompts without generating (free)")
    ps.add_argument("--output-dir", default=str(OUTPUT_DIR), dest="output_dir")
    ps.set_defaults(func=cmd_photoshoot)

    cd = sub.add_parser("cards", help="Marketplace product cards (main/secondary/A+)")
    cd.add_argument("--scope", default="main", choices=CARD_SCOPES, help="Generation scope (default main)")
    cd.add_argument("--prompt", required=True, help="High-level intent")
    cd.add_argument("--image", action="append", help="Product reference image path or upload id (repeatable)")
    cd.add_argument("--category", default=None, help="Marketplace product category")
    cd.add_argument("--brand_context", default=None, help="Extra brand description")
    cd.add_argument("--product_context", default=None, help="Extra product description")
    cd.add_argument("--visual_style", default=None, help="Visual style constraints")
    cd.add_argument("--enhance-only", action="store_true", dest="enhance_only",
                    help="Return enhanced prompts without generating (free)")
    cd.add_argument("--output-dir", default=str(OUTPUT_DIR), dest="output_dir")
    cd.set_defaults(func=cmd_cards)

    args = p.parse_args()
    if args.command == "photoshoot" and not (1 <= args.count <= 10):
        print(json.dumps({"success": False, "error": "count must be 1-10"}, indent=2))
        sys.exit(1)
    result = args.func(args)
    print(json.dumps(result, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
