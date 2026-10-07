#!/usr/bin/env python3
"""Convert an SVG file into a standalone JS-rendered HTML preview."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build standalone HTML that recreates an SVG via JavaScript DOM calls.")
    parser.add_argument("svg", type=Path, help="Input SVG file.")
    parser.add_argument("--out", type=Path, default=Path("logo.html"), help="Output HTML file.")
    parser.add_argument("--title", default="Vectorized Logo", help="HTML document title.")
    return parser.parse_args()


def strip_namespace(name: str) -> str:
    if "}" in name:
        return name.rsplit("}", 1)[1]
    return name


def clean_attrs(attrs: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in attrs.items():
        local = strip_namespace(key)
        if local == "xmlns":
            continue
        cleaned[local] = value
    return cleaned


# Elements whose text is content, so its spaces matter.
TEXT_CONTENT = {"text", "tspan", "textPath", "title", "desc", "style", "script"}


def node_to_data(element: ET.Element) -> dict:
    """The element as {tag, attrs, children}; a child is a node or a string.

    Text is kept in document order, including the tail after a nested element
    and the spaces between words: `<text>Hello <tspan>World</tspan> again</text>`
    used to rebuild as "HelloWorld" (text stripped, tail dropped). Whitespace
    that is only indentation between elements is dropped.
    """
    tag = strip_namespace(element.tag)
    keep_space = tag in TEXT_CONTENT

    def text_piece(value: str | None) -> list[str]:
        if not value:
            return []
        return [value] if keep_space or value.strip() else []

    children: list = text_piece(element.text)
    for child in list(element):
        children.append(node_to_data(child))
        children.extend(text_piece(child.tail))
    return {"tag": tag, "attrs": clean_attrs(element.attrib), "children": children}


# Absolute SVG lengths in CSS px (96 per inch). A relative width (100%, em)
# says nothing about the drawing's size, so the viewBox decides then.
_UNIT_PX = {"": 1.0, "px": 1.0, "pt": 96 / 72, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}


def _length_px(value: str) -> float | None:
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*", value or "")
    if not m or m.group(2).lower() not in _UNIT_PX:
        return None
    return float(m.group(1)) * _UNIT_PX[m.group(2).lower()]


def max_width_for(svg_data: dict) -> str:
    """The drawing's natural width in px.

    The digits used to be pulled out of the width whatever its unit, so
    width="100%" made a 100 px logo, shown at 70 px, and "297mm" (Inkscape's
    default unit) one of 297 instead of 1123 (Linux bot sweep 2026-10-07).
    """
    attrs = svg_data.get("attrs", {})
    px = _length_px(attrs.get("width", ""))
    if px:
        return f"{px:.3f}".rstrip("0").rstrip(".")
    if attrs.get("viewBox"):
        values = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", attrs["viewBox"])
        if len(values) == 4:
            return values[2]
    return "1196"


def html_for(svg_data: dict, title: str) -> str:
    # "</" in an SVG text would end the page's <script> early; the showcase
    # builder escaped it and this one did not (Linux bot sweep 2026-10-07).
    payload = json.dumps(svg_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    safe_title = re.sub(r"[<>]", "", title)
    max_width = max_width_for(svg_data)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    html,
    body {{
      width: 100%;
      height: 100%;
      margin: 0;
      background: #ffffff;
    }}

    body {{
      display: grid;
      place-items: center;
    }}

    #logo-root {{
      width: min(100vw, {max_width}px);
    }}

    #logo-root svg {{
      width: 100%;
      height: auto;
      display: block;
    }}
  </style>
</head>
<body>
  <main id="logo-root" aria-label="Vectorized logo"></main>
  <script>
    const SVG_NS = "http://www.w3.org/2000/svg";
    const LOGO = {payload};

    function createSvgNode(node) {{
      const element = document.createElementNS(SVG_NS, node.tag);
      for (const [name, value] of Object.entries(node.attrs || {{}})) {{
        element.setAttribute(name, value);
      }}
      for (const child of node.children || []) {{
        element.appendChild(typeof child === "string"
          ? document.createTextNode(child)
          : createSvgNode(child));
      }}
      return element;
    }}

    document.getElementById("logo-root").appendChild(createSvgNode(LOGO));
  </script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    root = ET.parse(args.svg).getroot()
    if strip_namespace(root.tag) != "svg":
        raise SystemExit(f"Expected SVG root element in {args.svg}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_for(node_to_data(root), args.title), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
