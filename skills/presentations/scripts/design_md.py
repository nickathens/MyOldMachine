#!/usr/bin/env python3
"""
DESIGN.md ingest for the presentations skill.

Reads a VoltAgent-style DESIGN.md file (YAML frontmatter + markdown body) and
maps its rich token taxonomy (colors, typography, rounded, spacing, components)
into the simpler shape consumed by create_presentation.py:

    {
      "name":          str,                          # display name
      "color_palette": {bg,text,text_dim,text_mid,accent,brand_color,cream},
      "fonts":         {heading,body,serif?},        # mapped to Google Fonts
      "fonts_original":{heading,body,serif?},        # the proprietary names
      "mood":          str,                          # short blurb
      "avoid":         [str, ...],                   # Don'ts pulled from body
      "prose":         str,                          # body markdown
      "_path":         str,                          # source file path
    }

The output dict is shape-compatible with references.apply_reference(treatment, ref).

Library resolution:
    design_library/<name>/DESIGN.md   (mirrors the upstream voltagent layout)
    design_library/<name>.md          (flat alternative)

CLI:
    python design_md.py list
    python design_md.py show stripe
    python design_md.py show /abs/path/to/DESIGN.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

LIB_DIR = Path(__file__).resolve().parent.parent / "design_library"

COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))$")


class DesignMdError(RuntimeError):
    """Raised when a DESIGN.md file cannot be loaded or parsed."""


# ─────────────────────────────────────────────
#  Frontmatter
# ─────────────────────────────────────────────

def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, body_markdown). Frontmatter sits between leading ---/---."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    fm = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    return fm, body


def _yaml_load(fm: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(fm) or {}
    except yaml.YAMLError as exc:
        raise DesignMdError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise DesignMdError("frontmatter must be a mapping at top level")
    return data


# ─────────────────────────────────────────────
#  Color extraction
# ─────────────────────────────────────────────

# Token-name preference lists (most-canonical first). The first matching token
# wins. Lower-case keys, normalized by `_norm_key`.
_BG_KEYS = ["canvas", "background", "bg", "surface-base", "base", "page", "paper"]
_TEXT_KEYS = ["ink", "text-primary", "textprimary", "text", "foreground", "fg", "on-canvas"]
_TEXT_DIM_KEYS = [
    "ink-subtle", "text-tertiary", "text-muted", "muted-foreground",
    "muted", "ink-tertiary", "text-quaternary",
]
_TEXT_MID_KEYS = [
    "ink-muted", "text-secondary", "textsecondary", "secondary-text",
    "text-mid", "subdued",
]
_ACCENT_KEYS = ["primary", "accent", "brand", "highlight", "interactive"]
_BRAND_KEYS = ["brand-primary", "brand", "secondary", "primary-strong", "logo"]
_CREAM_KEYS = ["cream", "off-white", "paper", "warm-white", "ivory"]


def _norm_key(k: str) -> str:
    return str(k).strip().lower().replace("_", "-")


def _flatten_colors(colors: Any, prefix: str = "") -> dict[str, str]:
    """Flatten a (possibly nested) colors map into {dotted-token: '#hex'} pairs.

    Skip values that don't look like CSS colors. Nested groups are kept as a
    flat keyspace where the leaf name still matches the preference lists above.
    """
    out: dict[str, str] = {}
    if isinstance(colors, dict):
        for k, v in colors.items():
            key = _norm_key(k)
            if isinstance(v, dict):
                out.update(_flatten_colors(v, prefix=key))
            elif isinstance(v, str) and COLOR_RE.match(v.strip()):
                # Store under both the leaf key and a prefixed version, but
                # leaf key wins on duplicates so prefixed groups don't shadow it.
                out.setdefault(key, v.strip())
                if prefix:
                    out.setdefault(f"{prefix}-{key}", v.strip())
    return out


def _pick_color(flat: dict[str, str], candidates: list[str]) -> str:
    for k in candidates:
        v = flat.get(_norm_key(k))
        if v:
            return v
    return ""


def map_colors(colors: Any) -> dict[str, str]:
    flat = _flatten_colors(colors)
    if not flat:
        return {}
    out: dict[str, str] = {}
    bg = _pick_color(flat, _BG_KEYS)
    if bg:
        out["bg"] = bg
    text = _pick_color(flat, _TEXT_KEYS)
    if text:
        out["text"] = text
    text_mid = _pick_color(flat, _TEXT_MID_KEYS)
    if text_mid:
        out["text_mid"] = text_mid
    text_dim = _pick_color(flat, _TEXT_DIM_KEYS)
    if text_dim:
        out["text_dim"] = text_dim
    accent = _pick_color(flat, _ACCENT_KEYS)
    if accent:
        out["accent"] = accent
    brand = _pick_color(flat, _BRAND_KEYS)
    # Avoid emitting brand_color identical to accent — the skill treats them
    # as a duo, and a duplicate would suppress the brand-glow effect.
    if brand and brand != accent:
        out["brand_color"] = brand
    cream = _pick_color(flat, _CREAM_KEYS)
    if cream:
        out["cream"] = cream
    return out


# ─────────────────────────────────────────────
#  Typography mapping
# ─────────────────────────────────────────────

# Proprietary → free Google Font equivalents (all targets exist in FONT_CATALOG).
# Conservative defaults: Inter for sans, Source Serif 4 for serifs, JetBrains
# Mono for mono. Override per treatment if you want a different free pairing.
PROPRIETARY_FONT_MAP: dict[str, str] = {
    # Apple
    "sf pro": "Inter",
    "sf pro display": "Inter",
    "sf pro text": "Inter",
    "-apple-system": "Inter",
    "system-ui": "Inter",
    "system": "Inter",
    "helvetica": "Inter",
    "helvetica neue": "Inter",
    "arial": "Inter",
    # Linear
    "linear display": "Inter",
    "linear text": "Inter",
    "linear mono": "JetBrains Mono",
    # Stripe (sohne-var is a CSS @font-face name used on stripe.com)
    "sohne-var": "Inter",
    # Notion (custom Inter-derivative)
    "notion sans": "Inter",
    "notion-sans": "Inter",
    # Runway
    "abcnormal": "Inter",
    # Lineto / Commercial Type / GT Foundry
    "söhne": "Inter",
    "soehne": "Inter",
    "neue haas grotesk": "Inter",
    "neue haas grotesk display": "Inter",
    "neue haas grotesk text": "Inter",
    "gt america": "Inter",
    "gt america mono": "JetBrains Mono",
    "gt walsheim": "Inter",
    "gt sectra": "Source Serif 4",
    "tiempos headline": "Cormorant Garamond",
    "tiempos text": "Source Serif 4",
    # Pangram Pangram / ABC Dinamo
    "abc diatype": "Inter",
    "abc whyte": "Inter",
    "abc normal": "Inter",
    "abc favorit": "Inter",
    # Klim / others
    "founders grotesk": "Inter",
    "founders grotesk mono": "JetBrains Mono",
    "domaine display": "Cormorant Garamond",
    "untitled sans": "Inter",
    "national 2": "Inter",
    "national": "Inter",
    "söhne mono": "JetBrains Mono",
    # Vercel / GitHub / modern
    "geist": "Inter",
    "geist sans": "Inter",
    "geist mono": "JetBrains Mono",
    "mona sans": "Inter",
    "hubot sans": "Inter",
    "berkeley mono": "JetBrains Mono",
    # Stripe / Notion / Figma
    "sohne": "Inter",
    "graphik": "Inter",
    "circular": "Inter",
    "circular std": "Inter",
    "lyon display": "Cormorant Garamond",
    "lyon text": "Source Serif 4",
    "whyte": "Inter",
    "tt commons": "Inter",
    # Atlassian / Slack / common stacks
    "charlie display": "Inter",
    "charlie text": "Inter",
    "lato": "Lato",
    "roboto": "Inter",
    "open sans": "Inter",
    # Mono fallbacks
    "ibm plex mono": "IBM Plex Mono",
    "jetbrains mono": "JetBrains Mono",
    "fira code": "Fira Code",
    "menlo": "JetBrains Mono",
    "monaco": "JetBrains Mono",
    "consolas": "JetBrains Mono",
    "ui-monospace": "JetBrains Mono",
}

# Heading-size token candidates (most-display-y first). The first match drives
# fonts.heading.
_HEADING_TOKENS = [
    "display-xl", "display-2xl", "display-lg", "display-md", "display-sm", "display",
    "h1", "hero", "headline", "heading", "title", "card-title", "subhead",
    "h2", "h3",
]
_BODY_TOKENS = [
    "body", "body-lg", "body-md", "body-sm", "body-default",
    "paragraph", "text", "default",
]
_MONO_TOKENS = ["mono", "code"]


def _strip_font_string(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    # Take the first family in a CSS-style stack.
    first = raw.split(",")[0].strip()
    return first.strip().strip("'\"").strip()


def _resolve_font(name: str) -> str:
    """Map a proprietary font name to a Google Font equivalent.

    Returns the original name if no mapping is known. Empty string in → empty out.
    """
    if not name:
        return ""
    key = name.strip().lower()
    if key in PROPRIETARY_FONT_MAP:
        return PROPRIETARY_FONT_MAP[key]
    # Heuristic for unknown families: anything mentioning "mono" gets the mono
    # fallback; "serif" gets the serif fallback; otherwise leave as-is and let
    # build_font_link's pass-through handling carry it.
    lower = key
    if "mono" in lower:
        return "JetBrains Mono"
    return name


def _pick_font_family(typography: Any, tokens: list[str]) -> str:
    if not isinstance(typography, dict):
        return ""
    by_key = {_norm_key(k): v for k, v in typography.items()}
    for tok in tokens:
        spec = by_key.get(_norm_key(tok))
        if not spec:
            continue
        if isinstance(spec, dict):
            family = _strip_font_string(spec.get("fontFamily") or spec.get("fontfamily") or spec.get("family") or "")
            if family:
                return family
        elif isinstance(spec, str):
            family = _strip_font_string(spec)
            if family:
                return family
    return ""


def map_fonts(frontmatter: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (mapped_fonts, original_fonts).

    `mapped_fonts` uses Google-Fonts-resolved names suitable for the skill's
    catalog; `original_fonts` preserves the proprietary names for reference.
    """
    typography = frontmatter.get("typography") or {}
    heading_raw = _pick_font_family(typography, _HEADING_TOKENS)
    body_raw = _pick_font_family(typography, _BODY_TOKENS)

    # Some files put fonts under a top-level `fonts:` block (similar to refs)
    # or `fontFamilies:`. Fall back to those.
    fonts_block = frontmatter.get("fonts") or frontmatter.get("fontFamilies") or {}
    if isinstance(fonts_block, dict):
        if not heading_raw:
            heading_raw = _strip_font_string(
                fonts_block.get("heading") or fonts_block.get("display") or fonts_block.get("primary") or ""
            )
        if not body_raw:
            body_raw = _strip_font_string(
                fonts_block.get("body") or fonts_block.get("text") or fonts_block.get("primary") or ""
            )

    original = {}
    if heading_raw:
        original["heading"] = heading_raw
    if body_raw:
        original["body"] = body_raw

    mapped = {}
    if heading_raw:
        mapped["heading"] = _resolve_font(heading_raw)
    if body_raw:
        mapped["body"] = _resolve_font(body_raw)

    return mapped, original


# ─────────────────────────────────────────────
#  Body extraction
# ─────────────────────────────────────────────

_DONT_HEADING_RE = re.compile(r"^#+\s*Don'?ts?\b", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^#+\s+\S", re.MULTILINE)

# Match `**Name** (#hex): role description...` — common DESIGN.md prose format
# for color palettes when no YAML frontmatter is present.
_PROSE_COLOR_RE = re.compile(
    r"\*\*([^*]+)\*\*\s*\(`?(#[0-9a-fA-F]{3,8})`?\)\s*[:\-–—]\s*([^\n]+)",
    re.MULTILINE,
)

# Match `**Label**: family` or `**Label**: \`family\`` for font extraction.
_PROSE_FONT_RE = re.compile(
    r"\*\*([^*]+)\*\*\s*[:\-–—]\s*`?([A-Za-z][A-Za-z0-9 +/.-]+?)`?(?=[,;\n]|\sfont|\swith|\s+fallback)",
    re.MULTILINE,
)

# Heuristic role-to-slot maps. The description text is matched against these
# keyword groups in priority order. First match wins per slot.
_ROLE_KEYWORDS = {
    "bg": ("page background", "canvas", "primary background", "main background", "default page", "page surface", "light-section background"),
    "text": ("primary text", "headings", "body text", "main text", "text and headings", "default text", "headline", "title"),
    "text_mid": ("secondary text", "description copy", "subhead", "lead paragraph", "intro paragraph", "primary body text"),
    "text_dim": ("tertiary text", "muted", "subtle", "footnote", "caption", "deselected", "placeholder", "disabled"),
    "accent": ("accent", "primary cta", "primary button", "brand mark", "link emphasis", "interactive accent", "brand accent", "primary link", "link color"),
    "brand_color": ("brand color", "logo", "primary brand", "secondary brand"),
}


def parse_prose_palette(body: str) -> dict[str, str]:
    """Extract a palette from prose `**Name** (#hex): description` items.

    Best-effort: when DESIGN.md ships without YAML frontmatter, this picks the
    first hex whose description text matches each slot's keyword group.
    """
    matches = _PROSE_COLOR_RE.findall(body)
    if not matches:
        return {}
    out: dict[str, str] = {}
    for slot, keywords in _ROLE_KEYWORDS.items():
        for _name, hex_code, description in matches:
            desc = description.lower()
            if any(kw in desc for kw in keywords):
                out.setdefault(slot, hex_code.strip())
                break
    return out


def parse_prose_fonts(body: str) -> tuple[dict[str, str], dict[str, str]]:
    """Pick heading/body fonts from a prose Typography section.

    Looks for `**Primary**: Family` or `**Heading**: Family` style labels.
    """
    heading_keywords = ("primary", "heading", "display", "headline", "sans", "main", "universal", "single", "all surfaces")
    body_keywords = ("body", "paragraph", "text", "default")
    mono_keywords = ("monospace", "mono", "code")

    heading_raw = ""
    body_raw = ""
    fallback_first = ""
    for label, family in _PROSE_FONT_RE.findall(body):
        lbl = label.strip().lower()
        fam = family.strip()
        if not fam or len(fam) > 40:
            continue
        if any(k in lbl for k in mono_keywords):
            continue
        if not fallback_first:
            fallback_first = fam
        if not heading_raw and any(k in lbl for k in heading_keywords):
            heading_raw = fam
        elif not body_raw and any(k in lbl for k in body_keywords):
            body_raw = fam
    if not heading_raw and fallback_first:
        heading_raw = fallback_first  # take whatever was first if no labeled match
    if heading_raw and not body_raw:
        body_raw = heading_raw  # single-family system

    original = {}
    mapped = {}
    if heading_raw:
        original["heading"] = heading_raw
        mapped["heading"] = _resolve_font(heading_raw)
    if body_raw:
        original["body"] = body_raw
        mapped["body"] = _resolve_font(body_raw)
    return mapped, original


def extract_dont_list(body: str) -> list[str]:
    """Pull bullet items from a `## Don't` (or similar) section."""
    m = _DONT_HEADING_RE.search(body)
    if not m:
        return []
    start = m.end()
    nxt = _NEXT_HEADING_RE.search(body, pos=start)
    region = body[start: nxt.start() if nxt else len(body)]
    items: list[str] = []
    for line in region.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            text = s[2:].strip()
            # Strip leading "Don't " phrasing if present, keep the principle.
            text = re.sub(r"^(?:Don'?t|Avoid|Never|No)\s+", "", text, flags=re.IGNORECASE)
            if text:
                items.append(text.rstrip("."))
    return items


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

def _first_h1(body: str) -> str:
    """Pull the first H1 heading text, used as a name fallback."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            txt = s[2:].strip()
            # Strip "Design System Inspired by " / "Inspired by " / etc.
            txt = re.sub(r"^(?:design\s+system\s+inspired\s+by\s+|inspired\s+by\s+)", "", txt, flags=re.IGNORECASE)
            return txt
    return ""


def _first_paragraph(body: str) -> str:
    """Return the first non-heading prose paragraph, used as a mood fallback."""
    chunks: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith(("#", "|", "-", "*", ">", "    ", "\t")):
            if chunks:
                break
            continue
        if not line.strip():
            if chunks:
                break
            continue
        chunks.append(line.strip())
    return " ".join(chunks).strip()


def parse_design_md(text: str, source_path: str = "") -> dict[str, Any]:
    """Parse a DESIGN.md document. Returns a reference-shaped dict.

    Supports two formats:
      - YAML-frontmatter style (Linear, Apple, Notion, Stripe, ...)
      - Prose-only markdown (Vercel, Runway, ...) — heuristic extractor

    Raises DesignMdError on malformed YAML or when both modes yield nothing.
    """
    fm_text, body = _split_frontmatter(text)
    fm: dict[str, Any] = _yaml_load(fm_text) if fm_text.strip() else {}

    name = (fm.get("name") or fm.get("title") or "").strip()
    description = (fm.get("description") or fm.get("summary") or "").strip()
    palette = map_colors(fm.get("colors") or fm.get("colour") or {})
    fonts_mapped, fonts_orig = map_fonts(fm)

    # Prose fallbacks fill any holes the frontmatter missed (or supply
    # everything for files without frontmatter at all).
    if not palette:
        palette = parse_prose_palette(body)
    if not fonts_mapped:
        fonts_mapped, fonts_orig = parse_prose_fonts(body)
    if not name:
        name = _first_h1(body)
    if not description:
        description = _first_paragraph(body)

    if not palette and not fonts_mapped:
        raise DesignMdError(
            "could not extract any colors or fonts from this DESIGN.md "
            "(no YAML frontmatter and prose patterns did not match)"
        )

    avoid = extract_dont_list(body)

    out: dict[str, Any] = {
        "name": name,
        "color_palette": palette,
        "fonts": fonts_mapped,
        "fonts_original": fonts_orig,
        "mood": description,
        "avoid": avoid,
        "prose": body.strip(),
    }
    if source_path:
        out["_path"] = str(source_path)
    return out


def list_library() -> list[str]:
    """Return sorted list of library entry slugs (folder name or stem)."""
    if not LIB_DIR.exists():
        return []
    seen: set[str] = set()
    for child in LIB_DIR.iterdir():
        if child.is_dir() and (child / "DESIGN.md").is_file():
            seen.add(child.name.lower())
        elif child.is_file() and child.suffix.lower() == ".md" and child.stem.lower() not in {"readme", "index"}:
            seen.add(child.stem.lower())
    return sorted(seen)


def resolve_path(name_or_path: str) -> Path:
    """Resolve a CLI/JSON value into an actual DESIGN.md path.

    Accepts: bare name (stripe → design_library/stripe/DESIGN.md or stripe.md),
    folder path, or absolute file path.
    """
    raw = name_or_path.strip()
    if not raw:
        raise DesignMdError("design_md target is empty")
    p = Path(raw).expanduser()
    if p.is_file():
        return p
    if p.is_dir() and (p / "DESIGN.md").is_file():
        return p / "DESIGN.md"

    slug = raw.lower().replace(" ", "-").replace("_", "-")
    candidates = [
        LIB_DIR / slug / "DESIGN.md",
        LIB_DIR / f"{slug}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    available = ", ".join(list_library()) or "(none)"
    raise DesignMdError(f"design_md target {name_or_path!r} not found. Available: {available}")


def load_design(name_or_path: str) -> dict[str, Any]:
    path = resolve_path(name_or_path)
    text = path.read_text(encoding="utf-8")
    return parse_design_md(text, source_path=str(path))


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect VoltAgent DESIGN.md files for the presentations skill.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List entries available in design_library/")
    show = sub.add_parser("show", help="Print parsed DESIGN.md as JSON")
    show.add_argument("target", help="Library slug (e.g. linear) or path to a DESIGN.md")
    args = ap.parse_args()

    if args.cmd == "list":
        for name in list_library():
            print(name)
        return 0

    if args.cmd == "show":
        try:
            data = load_design(args.target)
        except DesignMdError as e:
            print(f"design_md: {e}", file=sys.stderr)
            return 2
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
