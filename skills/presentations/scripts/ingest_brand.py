#!/usr/bin/env python3
"""
Ingest a brand URL via the Firecrawl v2 `branding` format and return a JSON
partial that matches the presentations skill schema (scheme / fonts / cover).

Usage as CLI:
    python ingest_brand.py --url https://example.com --out brand.json
    python ingest_brand.py --url https://example.com            # prints to stdout

Usage as module:
    from ingest_brand import ingest_brand
    partial = ingest_brand("https://example.com")
    # partial has keys: scheme, fonts, cover (with logo)

The Firecrawl `branding` format output shape (docs.firecrawl.dev):
    data.branding.colorScheme             "light" | "dark"
    data.branding.logo                    absolute URL
    data.branding.colors.{primary, secondary, accent,
                          background, textPrimary, textSecondary}
    data.branding.fonts                   [{family: "..."}]
    data.branding.typography.fontFamilies.{primary, heading, code}
    data.branding.images.{logo, favicon, ogImage}

Requires env var FIRECRAWL_API_KEY (keys look like "fc-...").
Cost: 1 base credit per scrape (branding is a format, not JSON mode).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ingest_brand requires `requests`. Install: pip install requests", file=sys.stderr)
    raise

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
DEFAULT_TIMEOUT = 60  # Firecrawl can take a while on heavy sites
LOGO_CACHE_DIR = Path.home() / ".cache" / "presentations" / "brand_logos"
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$|^rgba?\(", re.IGNORECASE)
MAX_LOGO_BYTES = 5 * 1024 * 1024  # 5 MB hard cap on logo downloads


class BrandIngestError(RuntimeError):
    """Raised when brand ingestion cannot complete."""


def _load_api_key() -> str:
    """Resolve the Firecrawl API key from env or ~/.env files."""
    key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if key:
        return key
    # Fallback: walk up from this script to find a .env with the key
    script_dir = Path(__file__).resolve().parent
    for ancestor in (script_dir.parent.parent.parent, Path.home()):
        env_file = ancestor / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("FIRECRAWL_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    raise BrandIngestError(
        "FIRECRAWL_API_KEY is not set. Add it to your shell env or "
        "the project .env file (format: FIRECRAWL_API_KEY=fc-...)."
    )


def _fetch_branding(url: str, api_key: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """POST to Firecrawl v2 /scrape with formats=['branding']."""
    payload = {"url": url, "formats": ["branding"]}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(FIRECRAWL_URL, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout as e:
        raise BrandIngestError(f"Firecrawl timeout after {timeout}s: {e}") from e
    except requests.exceptions.RequestException as e:
        raise BrandIngestError(f"Firecrawl request failed: {e}") from e

    if resp.status_code != 200:
        snippet = (resp.text or "<empty>")[:400]
        raise BrandIngestError(
            f"Firecrawl returned HTTP {resp.status_code}: {snippet}"
        )
    try:
        body = resp.json()
    except ValueError as e:
        raise BrandIngestError(f"Firecrawl returned non-JSON body: {e}") from e
    if not body.get("success"):
        snippet = str(body)[:400]
        raise BrandIngestError(f"Firecrawl reported success=false: {snippet}")
    branding = (body.get("data") or {}).get("branding")
    if not branding:
        raise BrandIngestError("Firecrawl response missing data.branding")
    return branding


def _is_color(value: Any) -> bool:
    return isinstance(value, str) and bool(COLOR_RE.match(value.strip()))


def _pick(colors: dict[str, Any], *keys: str) -> str:
    """Return the first color-looking value for any of the given keys."""
    for k in keys:
        v = colors.get(k)
        if _is_color(v):
            return v.strip()
    return ""


def _download_logo(url: str, brand_host: str) -> str:
    """Download a logo to the cache dir. Returns local path or original URL."""
    if not url:
        return ""
    if url.startswith("data:"):
        return url
    if not url.startswith(("http://", "https://")):
        return url
    LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Stable filename per (host, url) so repeated calls reuse the cache.
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"):
        suffix = ".png"
    target = LOGO_CACHE_DIR / f"{brand_host}_{digest}{suffix}"
    if target.exists() and target.stat().st_size > 0:
        return str(target)
    try:
        with requests.get(url, timeout=30, stream=True) as r:
            r.raise_for_status()
            content_len = r.headers.get("Content-Length")
            if content_len and int(content_len) > MAX_LOGO_BYTES:
                return url  # too large, don't cache
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > MAX_LOGO_BYTES:
                    return url  # hit cap mid-stream, abandon
            target.write_bytes(bytes(buf))
        return str(target)
    except Exception:
        # Network/DNS failure on the asset shouldn't kill the whole ingest;
        # fall back to the remote URL (create_presentation.embed_image handles it).
        return url


def map_branding(branding: dict[str, Any], source_url: str = "") -> dict[str, Any]:
    """Map a Firecrawl `branding` object to the presentation JSON partial.

    Returns a dict with keys (all optional):
        scheme: {bg, text, text_dim, accent, brand_color, ...}
        fonts:  {heading, body, serif?}
        cover:  {logo, brand_url}
        _meta:  {color_scheme, source}  (for debugging; ignored by renderer)
    """
    partial: dict[str, Any] = {}

    colors = branding.get("colors") or {}
    scheme: dict[str, str] = {}

    bg = _pick(colors, "background")
    if bg:
        scheme["bg"] = bg
    text = _pick(colors, "textPrimary", "text")
    if text:
        scheme["text"] = text
    text_dim = _pick(colors, "textSecondary", "textMuted")
    if text_dim:
        scheme["text_dim"] = text_dim
    accent = _pick(colors, "accent", "primary")
    if accent:
        scheme["accent"] = accent
    brand_color = _pick(colors, "primary", "secondary", "accent")
    if brand_color:
        scheme["brand_color"] = brand_color

    if scheme:
        partial["scheme"] = scheme

    fonts: dict[str, str] = {}
    typography = branding.get("typography") or {}
    families = typography.get("fontFamilies") or {}
    heading = families.get("heading") or families.get("primary") or ""
    body = families.get("primary") or families.get("body") or ""
    if isinstance(heading, str) and heading.strip():
        fonts["heading"] = heading.split(",")[0].strip().strip("'\"")
    if isinstance(body, str) and body.strip():
        fonts["body"] = body.split(",")[0].strip().strip("'\"")

    font_list = branding.get("fonts") or []
    if not fonts.get("heading") and font_list:
        fam = (font_list[0] or {}).get("family") if isinstance(font_list[0], dict) else None
        if fam:
            fonts["heading"] = fam.strip()
    if not fonts.get("body") and len(font_list) > 1:
        fam = (font_list[1] or {}).get("family") if isinstance(font_list[1], dict) else None
        if fam:
            fonts["body"] = fam.strip()

    if fonts:
        partial["fonts"] = fonts

    images = branding.get("images") or {}
    logo_url = branding.get("logo") or images.get("logo") or ""
    cover: dict[str, Any] = {}
    if logo_url:
        host = urllib.parse.urlparse(source_url or logo_url).hostname or "brand"
        host = host.replace(".", "_")
        local = _download_logo(logo_url, host)
        cover["logo"] = local
    if source_url:
        cover["brand_url"] = source_url
    if cover:
        partial["cover"] = cover

    partial["_meta"] = {
        "color_scheme": branding.get("colorScheme", ""),
        "source": source_url,
    }
    return partial


def ingest_brand(url: str, api_key: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch a URL via Firecrawl branding format and return a presentation partial.

    Raises BrandIngestError on any failure (missing key, HTTP error, empty response).
    """
    if not url or not url.startswith(("http://", "https://")):
        raise BrandIngestError(f"Invalid URL: {url!r}")
    key = api_key or _load_api_key()
    branding = _fetch_branding(url, key, timeout=timeout)
    return map_branding(branding, source_url=url)


def merge_into(treatment: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    """Merge a brand partial into a treatment dict. Explicit treatment wins.

    Only fills keys that the treatment has not already defined.
    """
    out = dict(treatment)
    for top_key in ("scheme", "fonts"):
        src = partial.get(top_key) or {}
        if not src:
            continue
        existing = dict(out.get(top_key) or {})
        for k, v in src.items():
            existing.setdefault(k, v)
        out[top_key] = existing

    cover_src = partial.get("cover") or {}
    if cover_src:
        existing_cover = dict(out.get("cover") or {})
        for k, v in cover_src.items():
            existing_cover.setdefault(k, v)
        out["cover"] = existing_cover

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a brand URL via Firecrawl for the presentations skill.")
    ap.add_argument("--url", required=True, help="Brand site URL to scrape")
    ap.add_argument("--out", default=None, help="Write JSON partial to this path (default: stdout)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    args = ap.parse_args()

    try:
        partial = ingest_brand(args.url, timeout=args.timeout)
    except BrandIngestError as e:
        print(f"ingest_brand: {e}", file=sys.stderr)
        return 2

    text = json.dumps(partial, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
