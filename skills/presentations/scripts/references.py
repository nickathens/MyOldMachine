#!/usr/bin/env python3
"""
Aesthetic reference library for the presentations skill.

Each reference is a markdown file with YAML frontmatter that encodes a brand or
aesthetic's visual vocabulary. Loaded at build time to seed a treatment with
a baseline scheme/fonts/mood before per-treatment overrides apply.

Directory: ../references/*.md   (relative to this script)

Frontmatter shape:
    name: A24
    category: film
    mood: literary, melancholic, modern-gothic
    color_palette:
      bg: "#0a0a0a"
      text: "#f3f1ea"
      text_dim: "rgba(243,241,234,0.55)"
      accent: "#c7a25c"
      brand_color: "#b32222"
    fonts:
      heading: "Neue Haas Grotesk"
      body: "Neue Haas Grotesk"
      serif: "GT Sectra"
    avoid:
      - SaaS gradients
      - neon cyan
    texture_notes:
      - film grain
      - deep blacks
      - scarce but vivid color hits

After the frontmatter, the body is free-form prose describing the aesthetic.
Treatment authors can reference it via `--aesthetic a24` on create_presentation.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REF_DIR = Path(__file__).resolve().parent.parent / "references"


class ReferenceError(RuntimeError):
    """Raised when a reference cannot be loaded or parsed."""


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, body_markdown). Frontmatter is between leading ---/---."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end < 0:
        return "", text
    fm = text[3:end].strip("\n").strip()
    body = text[end + 4 :].lstrip("\n")
    return fm, body


def _parse_yaml(fm: str) -> dict[str, Any]:
    """Minimal YAML parser for the reference frontmatter subset.

    Supports:
      - scalar:      key: value
      - nested map:  key:\n  subkey: value
      - list:        key:\n  - value
    Values are strings; quoting is optional and stripped.
    """
    result: dict[str, Any] = {}
    lines = fm.splitlines()
    i = 0

    def _strip(val: str) -> str:
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            return val[1:-1]
        return val

    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith(" "):
            raise ReferenceError(f"Unexpected indent on top-level line: {raw!r}")
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if value:
            result[key] = _strip(value)
            i += 1
            continue
        # Block follows: either nested map or list
        block: Any = None
        i += 1
        while i < len(lines):
            bl = lines[i]
            if not bl.strip():
                i += 1
                continue
            if not bl.startswith((" ", "\t")):
                break
            stripped = bl.strip()
            if stripped.startswith("- "):
                if block is None:
                    block = []
                if not isinstance(block, list):
                    raise ReferenceError(f"Mixed list/map under {key}")
                block.append(_strip(stripped[2:]))
                i += 1
                continue
            sub_key, _, sub_val = stripped.partition(":")
            if block is None:
                block = {}
            if not isinstance(block, dict):
                raise ReferenceError(f"Mixed map/list under {key}")
            block[sub_key.strip()] = _strip(sub_val)
            i += 1
        result[key] = block if block is not None else ""
    return result


_EXCLUDED_STEMS = {"readme", "index"}


def list_references() -> list[str]:
    """Return sorted list of reference names (filename stems)."""
    if not REF_DIR.exists():
        return []
    return sorted(
        p.stem
        for p in REF_DIR.glob("*.md")
        if not p.name.startswith("_") and p.stem.lower() not in _EXCLUDED_STEMS
    )


def load_reference(name: str) -> dict[str, Any]:
    """Load one reference by name. Returns dict with frontmatter keys + 'prose'.

    Raises ReferenceError if not found or malformed.
    """
    safe = name.strip().lower().replace(" ", "-")
    path = REF_DIR / f"{safe}.md"
    if not path.exists():
        available = ", ".join(list_references()) or "(none)"
        raise ReferenceError(f"Reference {name!r} not found. Available: {available}")
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    data: dict[str, Any] = {}
    if fm:
        data = _parse_yaml(fm)
    data["prose"] = body.strip()
    data["_path"] = str(path)
    return data


def apply_reference(treatment: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    """Merge a reference's palette/fonts into a treatment. Treatment wins on every key."""
    out = dict(treatment)
    palette = ref.get("color_palette") or {}
    if isinstance(palette, dict) and palette:
        existing = dict(out.get("scheme") or {})
        for k, v in palette.items():
            existing.setdefault(k, v)
        out["scheme"] = existing

    ref_fonts = ref.get("fonts") or {}
    if isinstance(ref_fonts, dict) and ref_fonts:
        existing = dict(out.get("fonts") or {})
        for k, v in ref_fonts.items():
            existing.setdefault(k, v)
        out["fonts"] = existing
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="List or print aesthetic references.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List reference names")
    show = sub.add_parser("show", help="Print a parsed reference as JSON")
    show.add_argument("name", help="Reference name (e.g. a24, aesop)")
    args = ap.parse_args()

    if args.cmd == "list":
        for name in list_references():
            print(name)
        return 0

    if args.cmd == "show":
        try:
            ref = load_reference(args.name)
        except ReferenceError as e:
            print(f"references: {e}", file=sys.stderr)
            return 2
        print(json.dumps(ref, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
