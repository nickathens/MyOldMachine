#!/usr/bin/env python3
"""
Generate scroll-based GSAP treatments/presentations from JSON definitions.

Produces self-contained HTML documents with GSAP ScrollTrigger animations,
floating particles, and cinematic typography. Based on professional
director's treatment format.

Usage:
    python create_presentation.py --json treatment.json --output treatment.html
    python create_presentation.py --json treatment.json --output treatment.html --pdf treatment.pdf
"""

import argparse
import base64
import html as html_lib
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DEFAULT_THEME = SCRIPT_DIR / "theme.css"
GSAP_CDN = "https://cdn.jsdelivr.net/npm/gsap@3.14.1/dist"
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{3,8}$')

FONT_CATALOG = {
    "Playfair Display": "Playfair+Display:ital,wght@0,400;0,700;1,400",
    "Cormorant Garamond": "Cormorant+Garamond:ital,wght@0,400;0,600;0,700;1,400;1,600",
    "DM Serif Display": "DM+Serif+Display:ital@0;1",
    "Libre Baskerville": "Libre+Baskerville:ital,wght@0,400;0,700;1,400",
    "Abril Fatface": "Abril+Fatface",
    "Fraunces": "Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,700;1,9..144,400",
    "EB Garamond": "EB+Garamond:ital,wght@0,400;0,500;0,700;1,400",
    "Spectral": "Spectral:ital,wght@0,400;0,600;0,700;1,400",
    "Space Grotesk": "Space+Grotesk:wght@400;500;600;700",
    "Montserrat": "Montserrat:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,300;1,400",
    "Inter": "Inter:wght@300;400;500;600;700",
    "Outfit": "Outfit:wght@300;400;500;600;700",
    "Plus Jakarta Sans": "Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400",
    "Sora": "Sora:wght@400;500;600;700",
    "Manrope": "Manrope:wght@400;500;600;700;800",
    "DM Sans": "DM+Sans:ital,wght@0,400;0,500;0,700;1,400",
    "Figtree": "Figtree:wght@400;500;600;700",
    "Urbanist": "Urbanist:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400",
    "Albert Sans": "Albert+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400",
    "Unbounded": "Unbounded:wght@400;500;600;700;800;900",
    "Source Sans 3": "Source+Sans+3:ital,wght@0,300;0,400;0,600;0,700;1,300;1,400",
    "Lato": "Lato:ital,wght@0,300;0,400;0,700;1,300;1,400",
    "Nunito Sans": "Nunito+Sans:ital,wght@0,300;0,400;0,600;0,700;1,300;1,400",
    "Work Sans": "Work+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400",
    "Karla": "Karla:ital,wght@0,400;0,500;0,700;1,400",
    "Rubik": "Rubik:ital,wght@0,300;0,400;0,500;0,700;1,300;1,400",
    "Source Serif 4": "Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400",
    "Lora": "Lora:ital,wght@0,400;0,500;0,600;0,700;1,400",
    "Merriweather": "Merriweather:ital,wght@0,300;0,400;0,700;1,300;1,400",
    "Crimson Pro": "Crimson+Pro:ital,wght@0,400;0,600;0,700;1,400",
    "Literata": "Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;0,7..72,700;1,7..72,400",
    "JetBrains Mono": "JetBrains+Mono:ital,wght@0,400;0,500;0,700;1,400",
    "Space Mono": "Space+Mono:ital,wght@0,400;0,700;1,400",
    "IBM Plex Mono": "IBM+Plex+Mono:ital,wght@0,400;0,500;0,700;1,400",
    "Fira Code": "Fira+Code:wght@400;500;600;700",
    "Bebas Neue": "Bebas+Neue",
    "Oswald": "Oswald:wght@400;500;600;700",
    "Raleway": "Raleway:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400",
    "Poppins": "Poppins:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400",
    "Archivo": "Archivo:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400",
    "Noto Serif Display": "Noto+Serif+Display:ital,wght@0,400;0,600;0,700;1,400",
}


# ═══════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════

def md_inline(text: str) -> str:
    """Convert basic inline markdown to HTML."""
    if not text:
        return ""
    t = html_lib.escape(str(text))
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'\*(.+?)\*', r'<em>\1</em>', t)
    t = re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    def _safe_link(m):
        text, url = m.group(1), m.group(2)
        if url.lower().strip().startswith('javascript:'):
            return text
        return f'<a href="{url}" target="_blank">{text}</a>'
    t = re.sub(r'\[(.+?)\]\((.+?)\)', _safe_link, t)
    t = t.replace('\\n', '<br>')
    return t


def embed_image(src: str) -> str:
    """Return image src — embed as base64 if local file."""
    if not src:
        return ""
    if src.startswith(('http://', 'https://', 'data:')):
        return src
    path = Path(src)
    if path.exists():
        suffix = path.suffix.lower()
        mime_map = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
        }
        mime = mime_map.get(suffix, 'image/png')
        data = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{data}"
    return src


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert hex color to rgba string."""
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) < 6:
        return f"rgba(0,0,0,{alpha})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def embed_yt_vimeo(url: str) -> str:
    """Convert YouTube/Vimeo URLs to embed format."""
    yt = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)', url)
    if yt:
        return f"https://www.youtube.com/embed/{yt.group(1)}?rel=0&modestbranding=1"
    vm = re.search(r'vimeo\.com/(\d+)', url)
    if vm:
        return f"https://player.vimeo.com/video/{vm.group(1)}"
    return url


def _caption_html(caption: str) -> str:
    """Render a section caption label."""
    if not caption:
        return ""
    return (
        f'<p style="font-family: var(--font-heading); font-size: 0.65rem; '
        f'letter-spacing: 0.2em; text-transform: uppercase; color: var(--accent); '
        f'text-align: center; margin-bottom: 2rem;">{md_inline(caption)}</p>'
    )


# ═══════════════════════════════════════════════
#  DURATION / PACING
# ═══════════════════════════════════════════════

def _calculate_duration(section: dict) -> float:
    """Calculate default auto-scroll hold duration for a section (seconds).

    Used when no explicit 'duration' is set in the section JSON.
    Returns how long the section should remain visible during video autoplay.
    """
    stype = section.get("type", "content")

    # Fixed durations for primarily visual sections
    FIXED = {
        "divider": 2.5,
        "quote": 4.0,
        "reveal": 4.0,
        "full_bleed": 3.5,
        "image": 3.0,
        "image_grid": 4.0,
        "stats": 3.5,
        "packshot": 5.0,
        "video": 6.0,
        "closing": 5.0,
        "hr": 1.0,
    }

    if stype in FIXED:
        return FIXED[stype]

    def _words(obj, keys):
        total = 0
        for k in keys:
            val = obj.get(k, "")
            if isinstance(val, list):
                total += sum(len(str(v).split()) for v in val)
            elif val:
                total += len(str(val).split())
        return total

    words = 0

    if stype == "note":
        words = _words(section, ["paragraphs"])

    elif stype == "concept":
        words = _words(section, ["heading", "desc", "description", "tagline"])

    elif stype in ("beats", "beat"):
        for beat in section.get("beats", [section]):
            words += _words(beat, ["text", "text2", "dialogue", "action"])

    elif stype == "table":
        rows = len(section.get("rows", []))
        return max(3.0, min(15.0, 2.0 + rows * 0.8))

    elif stype == "cards":
        for item in section.get("items", []):
            if isinstance(item, dict):
                words += _words(item, ["desc", "description"])
        items = len(section.get("items", []))
        return max(3.0, min(15.0, 2.0 + items * 0.5 + (words / 180) * 60))

    elif stype == "why_list":
        for item in section.get("items", []):
            if isinstance(item, dict):
                words += _words(item, ["title", "text"])

    elif stype == "specs":
        items = len(section.get("items", []))
        return max(3.0, 2.0 + items * 0.6)

    elif stype == "two_col":
        for col in section.get("columns", []):
            if isinstance(col, dict):
                words += _words(col, ["text"])

    elif stype == "content":
        words = _words(section, ["text", "texts", "heading"])

    if words > 0:
        return max(3.0, min(15.0, (words / 180) * 60))

    return 3.0


def _inject_data_attr(html: str, attr: str, value) -> str:
    """Inject a data attribute into the first section/div HTML element."""
    return re.sub(
        r'(<(?:section|div)\b)',
        f'\\1 {attr}="{value}"',
        html,
        count=1,
    )


# ═══════════════════════════════════════════════
#  SECTION RENDERERS
# ═══════════════════════════════════════════════

def render_cover(data: dict) -> str:
    """Render the cover section from top-level 'cover' object."""
    cover = data.get("cover", {})
    if not cover:
        return ""

    brand = html_lib.escape(cover.get("brand", ""))
    brand_url = cover.get("brand_url", "")
    logo = cover.get("logo", "")
    logo_filter = cover.get("logo_filter", "")
    title = html_lib.escape(cover.get("title", ""))
    doc_type = html_lib.escape(cover.get("type", ""))
    meta = cover.get("meta", [])
    bg = cover.get("background", "")

    duration = float(cover.get("duration", 4.0))

    bg_style = ""
    if bg:
        bg_src = embed_image(bg)
        bg_style = f' style="background: url(\'{bg_src}\') center/cover no-repeat;"'

    parts = [f'<section class="cover" data-duration="{duration:.1f}"{bg_style}>']

    if brand:
        if brand_url:
            parts.append(f'    <div class="cover-brand"><a href="{html_lib.escape(brand_url)}" target="_blank" style="color: inherit; text-decoration: none;">{brand}</a></div>')
        else:
            parts.append(f'    <div class="cover-brand">{brand}</div>')

    if logo:
        logo_src = embed_image(logo)
        filter_attr = f' style="filter: {html_lib.escape(logo_filter)};"' if logo_filter else ""
        parts.append(f'    <img src="{logo_src}" alt="{brand}" class="cover-logo"{filter_attr}>')

    parts.append('    <div class="cover-divider"></div>')

    if title:
        parts.append(f'    <div class="cover-subtitle">{title}</div>')
    if doc_type:
        parts.append(f'    <div class="cover-tagline">{doc_type}</div>')

    parts.append('    <div class="cover-scroll"><span>Scroll</span><div class="scroll-line"></div></div>')

    if meta:
        meta_html = " ".join(f'<span>{html_lib.escape(str(m))}</span>' for m in meta)
        parts.append(f'    <div class="cover-meta">{meta_html}</div>')

    parts.append('</section>')
    return '\n'.join(parts)


def render_divider(s: dict) -> str:
    number = html_lib.escape(str(s.get("number", "")))
    title = html_lib.escape(s.get("title", ""))
    raw_num = str(s.get("number", "")).strip().replace(" ", "-").lower()
    div_id = f' id="nav-{raw_num}"' if raw_num else ""
    parts = [f'<div class="section-divider"{div_id}>', '    <div class="container">']
    if number:
        parts.append(f'        <div class="section-number">{number}</div>')
    parts.append(f'        <h2 class="section-title">{title}</h2>')
    parts.append('        <div class="section-line"></div>')
    parts.append('    </div>')
    parts.append('</div>')
    return '\n'.join(parts)


def render_note(s: dict) -> str:
    paragraphs = s.get("paragraphs", [])
    signature = s.get("signature", "")
    parts = ['<section class="section">', '    <div class="container">', '        <div class="note-text">']
    for p in paragraphs:
        parts.append(f'            <p>{md_inline(p)}</p>')
    if signature:
        parts.append(f'            <p class="note-signature">{html_lib.escape(signature)}</p>')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_quote(s: dict) -> str:
    text = md_inline(s.get("text", ""))
    sub = s.get("sub", "")
    parts = ['<section class="big-quote">', '    <div class="container">']
    parts.append(f'        <div class="big-quote-text">{text}</div>')
    if sub:
        parts.append(f'        <p class="big-quote-sub">{md_inline(sub)}</p>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_table(s: dict) -> str:
    caption = s.get("caption", "")
    headers = s.get("headers", [])
    rows = s.get("rows", [])
    parts = ['<section class="section">', '    <div class="container">']
    if caption:
        parts.append(f'        <p style="font-size: 0.8rem; color: var(--text-dim); text-align: center; margin-bottom: 3rem; letter-spacing: 0.05em;">{md_inline(caption)}</p>')
    parts.append('        <table class="comp-table">')
    if headers:
        parts.append('            <thead><tr>')
        for h in headers:
            parts.append(f'                <th>{html_lib.escape(str(h))}</th>')
        parts.append('            </tr></thead>')
    parts.append('            <tbody>')
    for row in rows:
        parts.append('                <tr>')
        for cell in row:
            parts.append(f'                    <td>{md_inline(str(cell))}</td>')
        parts.append('                </tr>')
    parts.append('            </tbody>')
    parts.append('        </table>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_cards(s: dict) -> str:
    caption = s.get("caption", "")
    items = s.get("items", [])
    columns = min(4, max(2, s.get("columns", 2)))

    if columns >= 4:
        grid_class, card_class = "ref-grid", "ref-card"
        name_class, desc_class = "ref-card-name", "ref-card-desc"
        container = "container-wide"
    else:
        grid_class, card_class = "gap-grid", "gap-card"
        name_class, desc_class = "gap-card-name", "gap-card-desc"
        container = "container"

    col_style = ""
    if columns == 3:
        col_style = ' style="grid-template-columns: repeat(3, 1fr);"'

    parts = ['<section class="section">', f'    <div class="{container}">']
    if caption:
        parts.append(f'        {_caption_html(caption)}')
    parts.append(f'        <div class="{grid_class}"{col_style}>')
    for item in items:
        name = html_lib.escape(item.get("name", ""))
        desc = md_inline(item.get("desc", item.get("description", "")))
        parts.append(f'            <div class="{card_class}">')
        parts.append(f'                <div class="{name_class}">{name}</div>')
        parts.append(f'                <div class="{desc_class}">{desc}</div>')
        parts.append(f'            </div>')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_reveal(s: dict) -> str:
    label = html_lib.escape(s.get("label", ""))
    text = s.get("text", "")
    highlight = s.get("highlight", "")
    sub = s.get("sub", "")

    if highlight and highlight in text:
        text_html = html_lib.escape(text).replace(
            html_lib.escape(highlight),
            f'<span class="gap-highlight">{html_lib.escape(highlight)}</span>'
        )
    else:
        text_html = md_inline(text)

    parts = ['<section class="gap-reveal">', '    <div class="container">']
    if label:
        parts.append(f'        <div class="gap-reveal-label">{label}</div>')
    parts.append(f'        <h2 class="gap-reveal-text">{text_html}</h2>')
    if sub:
        parts.append(f'        <p class="gap-reveal-sub">{md_inline(sub)}</p>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_full_bleed(s: dict) -> str:
    src = s.get("src", s.get("image", ""))
    alt = html_lib.escape(s.get("alt", ""))
    img_src = embed_image(src)
    return f'<div class="full-bleed">\n    <img src="{img_src}" alt="{alt}">\n</div>'


def render_concept(s: dict) -> str:
    heading = s.get("heading", "")
    desc = s.get("desc", s.get("description", []))
    if isinstance(desc, str):
        desc = [desc]
    tagline = s.get("tagline", "")

    parts = ['<section class="concept-hero">', '    <div class="container">']
    if heading:
        parts.append(f'        <h2>{md_inline(heading)}</h2>')
    if desc:
        parts.append('        <div class="concept-desc">')
        for p in desc:
            parts.append(f'            <p>{md_inline(p)}</p>')
        parts.append('        </div>')
    if tagline:
        parts.append(
            f'        <p style="font-family: var(--font-heading); font-size: 1rem; '
            f'font-weight: 600; color: var(--accent); margin-top: 2rem; '
            f'letter-spacing: 0.05em; opacity: 0;" class="anim-fade">{md_inline(tagline)}</p>'
        )
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_why_list(s: dict) -> str:
    caption = s.get("caption", "")
    items = s.get("items", [])

    parts = ['<section class="section">', '    <div class="container">']
    if caption:
        parts.append(f'        {_caption_html(caption)}')
    parts.append('        <div class="why-list">')
    for i, item in enumerate(items, 1):
        title = html_lib.escape(item.get("title", ""))
        text = md_inline(item.get("text", ""))
        num = str(i).zfill(2)
        parts.append('            <div class="why-item">')
        parts.append(f'                <div class="why-number">{num}</div>')
        parts.append('                <div class="why-content">')
        if title:
            parts.append(f'                    <h4>{title}</h4>')
        if text:
            parts.append(f'                    <p>{text}</p>')
        parts.append('                </div>')
        parts.append('            </div>')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_beats(s: dict) -> str:
    beats = s.get("beats", [s])

    parts = ['<section class="section">', '    <div class="container">']
    for beat in beats:
        label = html_lib.escape(beat.get("label", ""))
        title = html_lib.escape(beat.get("title", ""))
        text = md_inline(beat.get("text", ""))
        text2 = md_inline(beat.get("text2", ""))
        dialogue = md_inline(beat.get("dialogue", ""))
        action = md_inline(beat.get("action", ""))
        dlg_style = beat.get("dialogue_style", "")

        parts.append('        <div class="beat">')
        if label:
            parts.append(f'            <div class="beat-label">{label}</div>')
        if title:
            parts.append(f'            <div class="beat-title">{title}</div>')
        if text:
            parts.append(f'            <div class="beat-text">{text}</div>')
        if text2:
            parts.append(f'            <div class="beat-text" style="margin-top: 1rem;">{text2}</div>')
        if dialogue:
            style_attr = f' style="{html_lib.escape(dlg_style)}"' if dlg_style else ""
            parts.append(f'            <div class="beat-dialogue"{style_attr}>{dialogue}</div>')
        if action:
            parts.append(f'            <div class="beat-action">{action}</div>')
        parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_two_col(s: dict) -> str:
    columns = s.get("columns", [])
    style = s.get("style", "")

    section_style = f' style="{html_lib.escape(style)}"' if style else ""
    parts = [f'<section class="section"{section_style}>', '    <div class="container">', '        <div class="two-col">']
    for col in columns:
        label = html_lib.escape(col.get("label", ""))
        texts = col.get("text", [])
        if isinstance(texts, str):
            texts = [texts]

        parts.append('            <div class="col">')
        if label:
            parts.append(f'                <div class="col-label">{label}</div>')
        for t in texts:
            parts.append(f'                <div class="col-text">{md_inline(t)}</div>')
        parts.append('            </div>')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_specs(s: dict) -> str:
    items = s.get("items", [])

    parts = ['<section class="section">', '    <div class="container">', '        <div class="specs-list">']
    for item in items:
        label = html_lib.escape(item.get("label", ""))
        value = md_inline(item.get("value", ""))
        parts.append('            <div class="spec-item">')
        parts.append(f'                <div class="spec-label">{label}</div>')
        parts.append(f'                <div class="spec-value">{value}</div>')
        parts.append('            </div>')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_stats(s: dict) -> str:
    items = s.get("items", [])
    text = s.get("text", "")

    parts = ['<section class="section">', '    <div class="container">', '        <div class="stats-row">']
    for item in items:
        number = html_lib.escape(str(item.get("number", "")))
        label = html_lib.escape(item.get("label", ""))
        parts.append('            <div class="stat">')
        parts.append(f'                <div class="stat-number">{number}</div>')
        if label:
            parts.append(f'                <div class="stat-label">{label}</div>')
        parts.append('            </div>')
    parts.append('        </div>')
    if text:
        parts.append(
            f'        <div style="text-align: center; margin-top: 2rem;">'
            f'<p style="font-size: 0.82rem; color: var(--text-dim); line-height: 1.8; '
            f'max-width: 600px; margin: 0 auto; opacity: 0;" class="anim-fade">'
            f'{md_inline(text)}</p></div>'
        )
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_packshot(s: dict) -> str:
    image = s.get("image", s.get("src", ""))
    taglines = s.get("taglines", [])
    logo = s.get("logo", "")

    parts = ['<section class="packshot">', '    <div class="packshot-inner">']
    if image:
        img_src = embed_image(image)
        parts.append(f'        <img src="{img_src}" alt="Product" class="packshot-bottle">')
    parts.append('        <div class="packshot-text">')
    for tl in taglines:
        parts.append(f'            <div class="packshot-tagline">{md_inline(tl)}</div>')
    if logo:
        logo_src = embed_image(logo)
        parts.append(f'            <img src="{logo_src}" alt="Brand" class="packshot-logo">')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_video(s: dict) -> str:
    label = html_lib.escape(s.get("label", s.get("title", "")))
    url = s.get("url", "")
    caption = s.get("caption", "")

    embed_url = embed_yt_vimeo(url) if url else ""

    parts = ['<section class="video-section">', '    <div class="container">']
    if label:
        parts.append(f'        <div class="video-label">{label}</div>')
    if embed_url:
        parts.append('        <div class="video-container">')
        parts.append(f'            <iframe src="{html_lib.escape(embed_url)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>')
        parts.append('        </div>')
    if caption:
        parts.append(f'        <p class="video-caption">{md_inline(caption)}</p>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_closing(s: dict) -> str:
    role = html_lib.escape(s.get("role", ""))
    name = html_lib.escape(s.get("name", ""))
    links = html_lib.escape(s.get("links", ""))
    brand_logo = s.get("brand_logo", "")
    company = html_lib.escape(s.get("company", ""))
    company_url = s.get("company_url", "")

    parts = ['<section class="closing">']
    if role:
        parts.append(f'    <div class="closing-role">{role}</div>')
    if name:
        parts.append(f'    <div class="closing-name">{name}</div>')
    if links:
        parts.append(f'    <div class="closing-links">{links}</div>')
    if brand_logo or company:
        parts.append('    <div class="closing-brand">')
        if brand_logo:
            logo_src = embed_image(brand_logo)
            parts.append(f'        <img src="{logo_src}" alt="Brand">')
        if brand_logo and company:
            parts.append('        <div class="closing-brand-divider"></div>')
        if company:
            if company_url:
                parts.append(f'        <a href="{html_lib.escape(company_url)}" target="_blank" class="closing-company">{company}</a>')
            else:
                parts.append(f'        <span class="closing-company">{company}</span>')
        parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_content(s: dict) -> str:
    heading = s.get("heading", "")
    text = s.get("text", "")
    texts = s.get("texts", [])
    if text and not texts:
        texts = [text]
    align = s.get("align", "left")
    caption = s.get("caption", "")

    center_margin = " margin: 0 auto;" if align == "center" else ""
    style = f' style="text-align: {align};"' if align != "left" else ""

    parts = ['<section class="section">', f'    <div class="container"{style}>']
    if caption:
        parts.append(f'        {_caption_html(caption)}')
    if heading:
        parts.append(
            f'        <h3 style="font-family: var(--font-heading); font-size: 1.3rem; '
            f'font-weight: 600; color: #fff; margin-bottom: 1.5rem;">{md_inline(heading)}</h3>'
        )
    for t in texts:
        parts.append(
            f'        <p style="font-size: 0.9rem; color: var(--text-mid); line-height: 1.8; '
            f'max-width: 800px;{center_margin} opacity: 0;" class="anim-fade">{md_inline(t)}</p>'
        )
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_image(s: dict) -> str:
    src = s.get("src", s.get("image", ""))
    caption = s.get("caption", "")
    alt = html_lib.escape(s.get("alt", caption or ""))
    img_src = embed_image(src)

    parts = ['<section class="section" style="text-align: center;">', '    <div class="container">']
    parts.append(
        f'        <img src="{img_src}" alt="{alt}" '
        f'style="max-width: 100%; border-radius: 12px; opacity: 0;" class="anim-fade">'
    )
    if caption:
        parts.append(
            f'        <p style="font-size: 0.75rem; color: var(--text-dim); '
            f'margin-top: 1.5rem; opacity: 0;" class="anim-fade">{md_inline(caption)}</p>'
        )
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_image_grid(s: dict) -> str:
    caption = s.get("caption", "")
    images = s.get("images", [])
    columns = min(4, max(2, s.get("columns", 2)))

    grid_class = "ref-grid" if columns >= 4 else "gap-grid"
    col_style = ""
    if columns == 3:
        col_style = ' style="grid-template-columns: repeat(3, 1fr);"'

    parts = ['<section class="section">', '    <div class="container-wide">']
    if caption:
        parts.append(f'        {_caption_html(caption)}')
    parts.append(f'        <div class="{grid_class}"{col_style}>')
    for img in images:
        if isinstance(img, str):
            src, cap = img, ""
        else:
            src = img.get("src", "")
            cap = img.get("caption", "")
        img_src = embed_image(src)
        card_class = "ref-card" if columns >= 4 else "gap-card"
        parts.append(f'            <div class="{card_class}" style="padding: 0; overflow: hidden;">')
        parts.append(
            f'                <img src="{img_src}" alt="{html_lib.escape(cap)}" '
            f'style="width: 100%; height: 200px; object-fit: cover; '
            f'border-radius: {"10px 10px 0 0" if cap else "10px"};">'
        )
        if cap:
            parts.append(
                f'                <p style="font-size: 0.72rem; color: var(--text-dim); '
                f'padding: 1rem; text-align: center;">{html_lib.escape(cap)}</p>'
            )
        parts.append('            </div>')
    parts.append('        </div>')
    parts.append('    </div>')
    parts.append('</section>')
    return '\n'.join(parts)


def render_hr(_s: dict) -> str:
    return '<div class="hr" style="margin: 4rem auto;"></div>'


# ═══════════════════════════════════════════════
#  DISPATCHER
# ═══════════════════════════════════════════════

RENDERERS = {
    'divider': render_divider,
    'note': render_note,
    'quote': render_quote,
    'table': render_table,
    'cards': render_cards,
    'reveal': render_reveal,
    'full_bleed': render_full_bleed,
    'concept': render_concept,
    'why_list': render_why_list,
    'beats': render_beats,
    'beat': render_beats,
    'two_col': render_two_col,
    'specs': render_specs,
    'stats': render_stats,
    'packshot': render_packshot,
    'video': render_video,
    'closing': render_closing,
    'content': render_content,
    'image': render_image,
    'image_grid': render_image_grid,
    'hr': render_hr,
}


def render_section(s: dict) -> str:
    """Render a section by dispatching to the appropriate renderer."""
    section_type = s.get("type", "content")
    renderer = RENDERERS.get(section_type)
    if renderer:
        out = renderer(s)
        duration = float(s.get("duration", _calculate_duration(s)))
        out = _inject_data_attr(out, "data-duration", f"{duration:.1f}")
        return out
    return f'<!-- Unknown section type: {html_lib.escape(section_type)} -->'


# ═══════════════════════════════════════════════
#  SCHEME & FONT HELPERS
# ═══════════════════════════════════════════════

def build_scheme_css(scheme: dict) -> str:
    """Build CSS variable overrides from color scheme."""
    if not scheme:
        return ""

    mappings = {
        "bg": "--bg",
        "text": "--text",
        "text_dim": "--text-dim",
        "text_mid": "--text-mid",
        "accent": "--accent",
        "accent_dim": "--accent-dim",
        "accent_light": "--accent-light",
        "brand_color": "--brand-color",
        "brand_light": "--brand-light",
        "cream": "--cream",
    }

    lines = []
    for key, prop in mappings.items():
        val = scheme.get(key, "")
        if val:
            lines.append(f"    {prop}: {val};")

    # Auto-generate derived values
    accent = scheme.get("accent", "")
    if accent and COLOR_RE.match(accent):
        if not scheme.get("accent_dim"):
            lines.append(f"    --accent-dim: {hex_to_rgba(accent, 0.3)};")
        if not scheme.get("accent_light"):
            lines.append(f"    --accent-light: {hex_to_rgba(accent, 0.6)};")

    brand_color = scheme.get("brand_color", "")
    if brand_color and COLOR_RE.match(brand_color):
        lines.append(f"    --brand-glow: {hex_to_rgba(brand_color, 0.15)};")
        lines.append(f"    --brand-glow-soft: {hex_to_rgba(brand_color, 0.08)};")

    if not lines:
        return ""
    return ":root {\n" + "\n".join(lines) + "\n}"


def build_font_link(fonts: dict) -> str:
    """Build Google Fonts link URL. Accepts any Google Font name."""
    families = set()
    for key in ("heading", "body", "serif"):
        name = fonts.get(key, "")
        if not name:
            continue
        if name in FONT_CATALOG:
            families.add(FONT_CATALOG[name])
        else:
            encoded = name.replace(" ", "+")
            families.add(f"{encoded}:wght@400;500;600;700")

    if not families:
        families = {
            FONT_CATALOG["Space Grotesk"],
            FONT_CATALOG["Outfit"],
            FONT_CATALOG["Playfair Display"],
        }

    return (
        "https://fonts.googleapis.com/css2?"
        + "&".join("family=" + f for f in sorted(families))
        + "&display=swap"
    )


def build_font_css(fonts: dict) -> str:
    """Build CSS font-family overrides."""
    if not fonts:
        return ""
    lines = []
    if fonts.get("heading"):
        lines.append(f"    --font-heading: '{fonts['heading']}', sans-serif;")
    if fonts.get("body"):
        lines.append(f"    --font-body: '{fonts['body']}', sans-serif;")
    if fonts.get("serif"):
        lines.append(f"    --font-serif: '{fonts['serif']}', serif;")
    if not lines:
        return ""
    return ":root {\n" + "\n".join(lines) + "\n}"


# ═══════════════════════════════════════════════
#  MODE SYSTEM
# ═══════════════════════════════════════════════

_LIGHT_OVERRIDES = """
html { scrollbar-color: rgba(0,0,0,0.15) transparent; }
::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.15); }
.cover-brand { color: rgba(0,0,0,0.4); }
.cover-meta { color: rgba(0,0,0,0.3); }
.cover-scroll span { color: rgba(0,0,0,0.3); }
.scroll-line { background: linear-gradient(to bottom, rgba(0,0,0,0.3), transparent); }
.section-title { color: var(--text); }
.note-text { color: rgba(0,0,0,0.7); }
.note-text em { color: var(--cream); }
.comp-table th { border-bottom-color: rgba(0,0,0,0.12); }
.comp-table td { border-bottom-color: rgba(0,0,0,0.06); color: var(--text-mid); }
.comp-table tbody tr:last-child td { color: var(--brand-light); border-bottom-color: rgba(0,0,0,0.12); }
.gap-card, .ref-card { background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.08); }
.gap-card:hover, .ref-card:hover { border-color: var(--accent-dim); background: rgba(0,0,0,0.04); }
.gap-card-name, .ref-card-name { color: var(--cream); }
.beat-title { color: var(--text); }
.beat + .beat { border-top-color: rgba(0,0,0,0.06); }
.why-item { border-bottom-color: rgba(0,0,0,0.06); }
.why-number { color: rgba(0,0,0,0.08); }
.spec-item { border-bottom-color: rgba(0,0,0,0.06); }
.hr { background: rgba(0,0,0,0.1); }
.particle { background: rgba(0,0,0,0.03); }
.closing-role { color: rgba(0,0,0,0.3); }
.closing-name { color: var(--text); }
.closing-links { color: rgba(0,0,0,0.3); }
.closing-brand-divider { background: rgba(0,0,0,0.1); }
.closing-company { color: rgba(0,0,0,0.3); }
.closing-brand img { filter: brightness(0.6); opacity: 0.5; }
.concept-hero h2 { color: var(--text); }
.concept-hero h2 em { color: var(--accent); }
.gap-reveal-text { color: var(--text); }
.video-container { border-color: rgba(0,0,0,0.08); }
.full-bleed::after { background: linear-gradient(to bottom, var(--bg) 0%, transparent 20%, transparent 80%, var(--bg) 100%); }
.packshot-bottle { filter: drop-shadow(0 0 60px rgba(0,0,0,0.1)); }
"""

MODE_PRESETS = {
    "dark": {"vars": {}, "overrides": ""},
    "light": {
        "vars": {
            "--bg": "#faf8f5",
            "--text": "#1a1a1a",
            "--text-dim": "rgba(0,0,0,0.45)",
            "--text-mid": "rgba(0,0,0,0.7)",
            "--cream": "#2a2520",
            "--accent": "#8b6914",
            "--accent-dim": "rgba(139,105,20,0.2)",
            "--accent-light": "#b8860b",
            "--brand-light": "#1b2d82",
        },
        "overrides": _LIGHT_OVERRIDES,
    },
    "editorial": {
        "vars": {
            "--bg": "#f8f5f0",
            "--text": "#1c1917",
            "--text-dim": "rgba(0,0,0,0.4)",
            "--text-mid": "rgba(0,0,0,0.65)",
            "--cream": "#2d2926",
            "--accent": "#c0392b",
            "--accent-dim": "rgba(192,57,43,0.15)",
            "--accent-light": "#922b21",
            "--brand-light": "#1b2d82",
        },
        "overrides": _LIGHT_OVERRIDES,
    },
    "minimal": {
        "vars": {
            "--bg": "#ffffff",
            "--text": "#000000",
            "--text-dim": "rgba(0,0,0,0.35)",
            "--text-mid": "rgba(0,0,0,0.6)",
            "--cream": "#111111",
            "--accent": "#000000",
            "--accent-dim": "rgba(0,0,0,0.1)",
            "--accent-light": "#333333",
            "--brand-light": "#000000",
        },
        "overrides": _LIGHT_OVERRIDES,
    },
}


def build_mode_css(mode: str) -> str:
    """Build CSS overrides for the display mode."""
    if mode == "dark" or mode not in MODE_PRESETS:
        return ""
    preset = MODE_PRESETS[mode]
    var_lines = [f"    {k}: {v};" for k, v in preset.get("vars", {}).items()]
    css = ""
    if var_lines:
        css = ":root {\n" + "\n".join(var_lines) + "\n}\n"
    css += preset.get("overrides", "")
    return css


# ═══════════════════════════════════════════════
#  ANIMATION SYSTEM
# ═══════════════════════════════════════════════

_PARTICLES_JS = """
(function() {
    var container = document.getElementById('particles');
    if (!container) return;
    for (var i = 0; i < 30; i++) {
        var p = document.createElement('div');
        p.className = 'particle';
        p.style.left = Math.random() * 100 + '%';
        p.style.top = Math.random() * 100 + '%';
        p.style.animationDuration = (15 + Math.random() * 25) + 's';
        p.style.animationDelay = Math.random() * 10 + 's';
        p.style.width = (1 + Math.random() * 2) + 'px';
        p.style.height = p.style.width;
        container.appendChild(p);
    }
})();
"""

_COVER_JS = """
gsap.registerPlugin(ScrollTrigger);

var cover = document.querySelector('.cover');
if (cover) {
    var tl = gsap.timeline({ delay: 0.3 });
    var els = {
        brand: cover.querySelector('.cover-brand'),
        logo: cover.querySelector('.cover-logo'),
        divider: cover.querySelector('.cover-divider'),
        subtitle: cover.querySelector('.cover-subtitle'),
        tagline: cover.querySelector('.cover-tagline'),
        meta: cover.querySelector('.cover-meta'),
        scroll: cover.querySelector('.cover-scroll')
    };
    if (els.brand) tl.to(els.brand, { opacity: 1, y: 0, duration: 0.8, ease: 'power3.out' });
    if (els.logo) tl.to(els.logo, { opacity: 1, scale: 1, duration: 1.2, ease: 'power3.out' }, '-=0.4');
    if (els.divider) tl.to(els.divider, { opacity: 1, scaleX: 1, duration: 0.6, ease: 'power3.out' }, '-=0.6');
    if (els.subtitle) tl.to(els.subtitle, { opacity: 1, y: 0, duration: 0.8, ease: 'power3.out' }, '-=0.3');
    if (els.tagline) tl.to(els.tagline, { opacity: 1, duration: 0.6 }, '-=0.3');
    if (els.meta) tl.to(els.meta, { opacity: 1, duration: 0.6 }, '-=0.3');
    if (els.scroll) tl.to(els.scroll, { opacity: 1, duration: 0.6 }, '-=0.2');

    gsap.to(cover, {
        scrollTrigger: { trigger: cover, start: 'top top', end: '80% top', scrub: true },
        opacity: 0, y: -50
    });
}
"""

_SECTION_ANIM_TEMPLATE = """
// Section dividers
document.querySelectorAll('.section-divider').forEach(function(div) {
    var stl = gsap.timeline({
        scrollTrigger: { trigger: div, start: 'top 80%', toggleActions: 'play none none none' }
    });
    var num = div.querySelector('.section-number');
    var title = div.querySelector('.section-title');
    var line = div.querySelector('.section-line');
    if (num) stl.to(num, {opacity: 1, duration: 0.5});
    if (title) stl.to(title, {__ENT__, duration: __DUR__, ease: '__EASE__'}, '-=0.2');
    if (line) stl.to(line, {opacity: 1, scaleX: 1, duration: 0.5, ease: 'power3.out'}, '-=0.3');
});

document.querySelectorAll('.note-text p').forEach(function(p, i) {
    gsap.to(p, {
        scrollTrigger: { trigger: p, start: 'top 85%' },
        __ENT__, duration: __DUR__, delay: i * 0.1, ease: '__EASE__'
    });
});
document.querySelectorAll('.note-signature').forEach(function(el) {
    gsap.to(el, { scrollTrigger: { trigger: el, start: 'top 85%' }, opacity: 1, duration: 0.6 });
});

document.querySelectorAll('.big-quote-text').forEach(function(el) {
    gsap.to(el, {
        scrollTrigger: { trigger: el, start: 'top 80%' },
        __POP__, duration: __POP_DUR__, ease: '__EASE__'
    });
});
document.querySelectorAll('.big-quote-sub').forEach(function(el) {
    gsap.to(el, { scrollTrigger: { trigger: el, start: 'top 85%' }, opacity: 1, duration: 0.7, delay: 0.3 });
});

document.querySelectorAll('.comp-table tbody tr').forEach(function(tr, i) {
    gsap.to(tr, {
        scrollTrigger: { trigger: tr, start: 'top 90%' },
        __DATA__, duration: 0.5, delay: i * 0.08, ease: '__EASE__'
    });
});

document.querySelectorAll('.gap-card').forEach(function(card, i) {
    gsap.to(card, {
        scrollTrigger: { trigger: card, start: 'top 85%' },
        __ENT__, duration: 0.6, delay: i * 0.1, ease: '__EASE__'
    });
});

document.querySelectorAll('.gap-reveal-label').forEach(function(el) {
    gsap.to(el, { scrollTrigger: { trigger: el, start: 'top 80%' }, opacity: 1, duration: 0.5 });
});
document.querySelectorAll('.gap-reveal-text').forEach(function(el) {
    gsap.to(el, {
        scrollTrigger: { trigger: el, start: 'top 80%' },
        __POP__, duration: __POP_DUR__, ease: '__EASE__'
    });
});
document.querySelectorAll('.gap-reveal-sub').forEach(function(el) {
    gsap.to(el, { scrollTrigger: { trigger: el, start: 'top 85%' }, opacity: 1, duration: 0.7, delay: 0.2 });
});

document.querySelectorAll('.full-bleed img').forEach(function(img) {
    gsap.to(img, {
        scrollTrigger: { trigger: img, start: 'top 90%' },
        opacity: 1, scale: 1, duration: 1.5, ease: 'power3.out'
    });
    gsap.to(img, {
        scrollTrigger: { trigger: img.parentElement, start: 'top bottom', end: 'bottom top', scrub: true },
        y: -60
    });
});

document.querySelectorAll('.concept-hero h2').forEach(function(el) {
    gsap.to(el, {
        scrollTrigger: { trigger: el, start: 'top 80%' },
        __POP__, duration: __POP_DUR__, ease: '__EASE__'
    });
});
document.querySelectorAll('.concept-desc p').forEach(function(p, i) {
    gsap.to(p, {
        scrollTrigger: { trigger: p, start: 'top 85%' },
        __ENT__, duration: 0.6, delay: i * 0.15, ease: '__EASE__'
    });
});

document.querySelectorAll('.why-item').forEach(function(item, i) {
    gsap.to(item, {
        scrollTrigger: { trigger: item, start: 'top 87%' },
        __DATA__, duration: 0.6, delay: i * 0.08, ease: '__EASE__'
    });
});

document.querySelectorAll('.beat').forEach(function(beat) {
    var children = beat.querySelectorAll('.beat-label, .beat-title, .beat-text, .beat-dialogue, .beat-action');
    children.forEach(function(el, i) {
        gsap.to(el, {
            scrollTrigger: { trigger: el, start: 'top 88%' },
            __ENT__, duration: 0.6, delay: i * 0.08, ease: '__EASE__'
        });
    });
});

document.querySelectorAll('.two-col .col').forEach(function(col, i) {
    gsap.to(col, {
        scrollTrigger: { trigger: col, start: 'top 85%' },
        __ENT__, duration: 0.7, delay: i * 0.15, ease: '__EASE__'
    });
});

document.querySelectorAll('.ref-card').forEach(function(card, i) {
    gsap.to(card, {
        scrollTrigger: { trigger: card, start: 'top 87%' },
        __ENT__, duration: 0.6, delay: i * 0.1, ease: '__EASE__'
    });
});

document.querySelectorAll('.spec-item').forEach(function(item, i) {
    gsap.to(item, {
        scrollTrigger: { trigger: item, start: 'top 88%' },
        __ENT__, duration: 0.5, delay: i * 0.08, ease: '__EASE__'
    });
});

document.querySelectorAll('.stat').forEach(function(stat, i) {
    gsap.to(stat, {
        scrollTrigger: { trigger: stat, start: 'top 85%' },
        __ENT__, duration: 0.6, delay: i * 0.15, ease: '__EASE__'
    });
});

var packshotBottle = document.querySelector('.packshot-bottle');
if (packshotBottle) {
    gsap.to(packshotBottle, {
        scrollTrigger: { trigger: '.packshot', start: 'top 70%' },
        __ENT__, duration: 1, ease: '__EASE__'
    });
}
document.querySelectorAll('.packshot-tagline').forEach(function(el, i) {
    gsap.to(el, {
        scrollTrigger: { trigger: '.packshot', start: 'top 60%' },
        __ENT__, duration: 0.8, delay: 0.3 + i * 0.2, ease: '__EASE__'
    });
});
var packshotLogo = document.querySelector('.packshot-logo');
if (packshotLogo) {
    gsap.to(packshotLogo, {
        scrollTrigger: { trigger: '.packshot', start: 'top 60%' },
        __ENT__, duration: 0.7, delay: 0.8, ease: '__EASE__'
    });
}

document.querySelectorAll('.video-label').forEach(function(el) {
    gsap.to(el, { scrollTrigger: { trigger: el, start: 'top 85%' }, opacity: 1, duration: 0.5 });
});
document.querySelectorAll('.video-container').forEach(function(el) {
    gsap.to(el, {
        scrollTrigger: { trigger: el, start: 'top 85%' },
        __ENT__, duration: 0.8, ease: '__EASE__'
    });
});
document.querySelectorAll('.video-caption').forEach(function(el) {
    gsap.to(el, { scrollTrigger: { trigger: el, start: 'top 90%' }, opacity: 1, duration: 0.6, delay: 0.3 });
});

var closingEl = document.querySelector('.closing');
if (closingEl) {
    var cr = closingEl.querySelector('.closing-role');
    var cn = closingEl.querySelector('.closing-name');
    var cl = closingEl.querySelector('.closing-links');
    var cb = closingEl.querySelector('.closing-brand');
    if (cr) gsap.to(cr, { scrollTrigger: { trigger: closingEl, start: 'top 70%' }, opacity: 1, duration: 0.5 });
    if (cn) gsap.to(cn, { scrollTrigger: { trigger: closingEl, start: 'top 70%' }, __ENT__, duration: 0.8, delay: 0.2, ease: '__EASE__' });
    if (cl) gsap.to(cl, { scrollTrigger: { trigger: closingEl, start: 'top 70%' }, opacity: 1, duration: 0.6, delay: 0.5 });
    if (cb) gsap.to(cb, { scrollTrigger: { trigger: closingEl, start: 'top 60%' }, opacity: 1, duration: 0.6, delay: 0.7 });
}

document.querySelectorAll('.anim-fade').forEach(function(el) {
    gsap.to(el, {
        scrollTrigger: { trigger: el, start: 'top 85%' },
        opacity: 1, duration: 0.7
    });
});
"""

_ANIM_PRESETS = {
    "fade": {
        "__ENT__": "opacity: 1, y: 0",
        "__DATA__": "opacity: 1, x: 0",
        "__POP__": "opacity: 1, scale: 1",
        "__DUR__": "0.7",
        "__POP_DUR__": "0.9",
        "__EASE__": "power3.out",
        "init": "",
    },
    "slide": {
        "__ENT__": "opacity: 1, x: 0, y: 0",
        "__DATA__": "opacity: 1, x: 0",
        "__POP__": "opacity: 1, x: 0, scale: 1",
        "__DUR__": "0.8",
        "__POP_DUR__": "1.0",
        "__EASE__": "power2.out",
        "init": """
(function() {
    var lefts = '.note-text p, .concept-hero h2, .concept-desc p, .beat-label, .beat-title, .beat-text, .beat-dialogue, .col:nth-child(odd), .section-title, .big-quote-text, .gap-reveal-text';
    var rights = '.gap-card:nth-child(even), .ref-card:nth-child(even), .stat:nth-child(even), .col:nth-child(even)';
    gsap.set(lefts, {x: -50, y: 0});
    gsap.set(rights, {x: 50, y: 0});
    gsap.set('.gap-card:nth-child(odd), .ref-card:nth-child(odd), .stat:nth-child(odd)', {x: -50, y: 0});
    gsap.set('.comp-table tbody tr, .why-item', {x: -30});
    gsap.set('.spec-item', {x: -20, y: 0});
})();
""",
    },
    "scale": {
        "__ENT__": "opacity: 1, scale: 1, y: 0",
        "__DATA__": "opacity: 1, scale: 1, x: 0",
        "__POP__": "opacity: 1, scale: 1",
        "__DUR__": "0.7",
        "__POP_DUR__": "1.0",
        "__EASE__": "back.out(1.4)",
        "init": """
(function() {
    var els = '.note-text p, .concept-hero h2, .concept-desc p, .gap-card, .ref-card, .stat, .spec-item, .col, .beat-title, .beat-text, .why-item, .section-title, .big-quote-text, .gap-reveal-text';
    gsap.set(els, {scale: 0.85, y: 0, transformOrigin: 'center center'});
    gsap.set('.comp-table tbody tr', {scale: 0.9, x: 0, transformOrigin: 'left center'});
})();
""",
    },
    "blur": {
        "__ENT__": "opacity: 1, filter: 'blur(0px)', y: 0",
        "__DATA__": "opacity: 1, filter: 'blur(0px)', x: 0",
        "__POP__": "opacity: 1, filter: 'blur(0px)', scale: 1",
        "__DUR__": "0.8",
        "__POP_DUR__": "1.2",
        "__EASE__": "power2.out",
        "init": """
(function() {
    var els = '.note-text p, .concept-hero h2, .concept-desc p, .gap-card, .ref-card, .stat, .spec-item, .col, .beat-title, .beat-text, .why-item, .section-title, .big-quote-text, .gap-reveal-text, .comp-table tbody tr';
    gsap.set(els, {filter: 'blur(12px)', y: 0, x: 0, scale: 1});
})();
""",
    },
    "clip": {
        "__ENT__": "opacity: 1, clipPath: 'inset(0 0 0 0)', y: 0",
        "__DATA__": "opacity: 1, clipPath: 'inset(0 0 0 0)', x: 0",
        "__POP__": "opacity: 1, clipPath: 'inset(0 0 0 0)', scale: 1",
        "__DUR__": "0.8",
        "__POP_DUR__": "1.0",
        "__EASE__": "power3.inOut",
        "init": """
(function() {
    var horiz = '.note-text p, .concept-desc p, .gap-card, .ref-card, .stat, .spec-item, .col, .beat-text, .beat-title, .why-item, .comp-table tbody tr';
    gsap.set(horiz, {clipPath: 'inset(0 100% 0 0)', y: 0, x: 0});
    gsap.set('.section-title, .big-quote-text, .gap-reveal-text, .concept-hero h2', {clipPath: 'inset(100% 0 0 0)', y: 0, scale: 1});
})();
""",
    },
}


def build_animation_js(preset: str = "fade") -> str:
    """Build complete GSAP animation JS for the given preset."""
    p = _ANIM_PRESETS.get(preset, _ANIM_PRESETS["fade"])
    section_js = _SECTION_ANIM_TEMPLATE
    for marker, value in p.items():
        if marker.startswith("__"):
            section_js = section_js.replace(marker, value)
    return _PARTICLES_JS + _COVER_JS + p.get("init", "") + section_js


# ═══════════════════════════════════════════════
#  NAVIGATION SYSTEM
# ═══════════════════════════════════════════════

def _extract_nav_items(data: dict) -> list:
    """Extract navigation items from section dividers."""
    items = []
    for section in data.get("sections", []):
        if section.get("type") == "divider":
            raw = str(section.get("number", "")).strip().replace(" ", "-").lower()
            items.append({
                "id": f"nav-{raw}" if raw else "",
                "number": section.get("number", ""),
                "title": section.get("title", ""),
            })
    return [item for item in items if item["id"]]


def build_nav_html(data: dict, nav_type: str) -> str:
    """Build navigation HTML."""
    if not nav_type or nav_type == "none":
        return ""
    items = _extract_nav_items(data)
    if not items:
        return ""

    if nav_type == "sidebar":
        links = []
        for i, item in enumerate(items):
            num = html_lib.escape(item["number"])
            title = html_lib.escape(item["title"])
            links.append(
                f'        <a href="#{item["id"]}" class="sidebar-nav-item" data-nav-index="{i}">'
                f'<span class="sidebar-nav-num">{num}</span> {title}</a>'
            )
        return (
            '<nav class="sidebar-nav" id="sidebar-nav">\n'
            '    <div class="sidebar-nav-inner">\n'
            + '\n'.join(links) + '\n'
            '    </div>\n'
            '</nav>'
        )

    if nav_type == "topbar":
        links = []
        for i, item in enumerate(items):
            label = html_lib.escape(f"{item['number']} {item['title']}")
            links.append(
                f'    <a href="#{item["id"]}" class="topbar-nav-item" data-nav-index="{i}">{label}</a>'
            )
        return (
            '<nav class="topbar-nav" id="topbar-nav">\n'
            + '\n'.join(links) + '\n'
            '    <div class="topbar-progress"></div>\n'
            '</nav>'
        )

    if nav_type == "dots":
        dots = []
        for i, item in enumerate(items):
            title = html_lib.escape(item["title"])
            dots.append(
                f'    <a href="#{item["id"]}" class="dots-nav-item" data-nav-index="{i}" title="{title}"></a>'
            )
        return '<nav class="dots-nav" id="dots-nav">\n' + '\n'.join(dots) + '\n</nav>'

    if nav_type == "progress":
        return '<div class="progress-bar" id="progress-bar"></div>'

    return ""


def build_nav_css(nav_type: str, mode: str = "dark") -> str:
    """Build CSS for navigation elements."""
    if not nav_type or nav_type == "none":
        return ""
    is_light = mode in ("light", "editorial", "minimal")
    bg = "rgba(255,255,255,0.92)" if is_light else "rgba(0,0,0,0.85)"
    border = "rgba(0,0,0,0.08)" if is_light else "rgba(255,255,255,0.06)"
    item_color = "rgba(0,0,0,0.35)" if is_light else "rgba(255,255,255,0.35)"
    item_hover = "rgba(0,0,0,0.7)" if is_light else "rgba(255,255,255,0.7)"
    dot_bg = "rgba(0,0,0,0.15)" if is_light else "rgba(255,255,255,0.15)"
    dot_hover = "rgba(0,0,0,0.4)" if is_light else "rgba(255,255,255,0.4)"

    if nav_type == "sidebar":
        return f"""
.sidebar-nav {{
    position: fixed; left: 0; top: 0; height: 100vh; width: 220px;
    background: {bg}; backdrop-filter: blur(20px);
    border-right: 1px solid {border}; z-index: 100;
    display: flex; flex-direction: column; justify-content: center; padding: 2rem 0;
}}
.sidebar-nav-inner {{ padding: 0 1.5rem; }}
.sidebar-nav-item {{
    display: block; padding: 0.5rem 0.8rem; font-family: var(--font-heading);
    font-size: 0.65rem; letter-spacing: 0.08em; color: {item_color};
    text-decoration: none; border-left: 2px solid transparent;
    transition: all 0.3s ease; margin-bottom: 0.2rem;
}}
.sidebar-nav-item:hover {{ color: {item_hover}; }}
.sidebar-nav-item.active {{ color: var(--accent); border-left-color: var(--accent); }}
.sidebar-nav-num {{ display: inline-block; width: 1.8em; opacity: 0.5; }}
body {{ padding-left: 220px; }}
.cover {{ width: calc(100% + 220px); margin-left: -220px; padding-left: 220px; }}
@media (max-width: 900px) {{
    .sidebar-nav {{ display: none; }}
    body {{ padding-left: 0; }}
    .cover {{ width: 100%; margin-left: 0; padding-left: 0; }}
}}
"""

    if nav_type == "topbar":
        return f"""
.topbar-nav {{
    position: fixed; top: 0; left: 0; right: 0; height: 48px;
    background: {bg}; backdrop-filter: blur(20px);
    border-bottom: 1px solid {border}; z-index: 100;
    display: flex; align-items: center; padding: 0 2rem; gap: 1.5rem;
    overflow-x: auto; scrollbar-width: none;
}}
.topbar-nav::-webkit-scrollbar {{ display: none; }}
.topbar-nav-item {{
    font-family: var(--font-heading); font-size: 0.55rem;
    letter-spacing: 0.1em; text-transform: uppercase; color: {item_color};
    text-decoration: none; padding: 0 0.3rem; position: relative;
    transition: color 0.3s ease; white-space: nowrap;
}}
.topbar-nav-item:hover {{ color: {item_hover}; }}
.topbar-nav-item.active {{ color: var(--accent); }}
.topbar-nav-item.active::after {{
    content: ''; position: absolute; bottom: -14px; left: 0; right: 0;
    height: 2px; background: var(--accent);
}}
.topbar-progress {{
    position: absolute; bottom: 0; left: 0; height: 2px; width: 100%;
    background: var(--accent); transform-origin: left; transform: scaleX(0);
}}
body {{ padding-top: 48px; }}
"""

    if nav_type == "dots":
        return f"""
.dots-nav {{
    position: fixed; right: 2rem; top: 50%; transform: translateY(-50%);
    z-index: 100; display: flex; flex-direction: column; gap: 10px;
}}
.dots-nav-item {{
    width: 8px; height: 8px; border-radius: 50%; background: {dot_bg};
    transition: all 0.3s ease; cursor: pointer; display: block;
}}
.dots-nav-item:hover {{ background: {dot_hover}; transform: scale(1.3); }}
.dots-nav-item.active {{ background: var(--accent); transform: scale(1.3); }}
@media (max-width: 768px) {{ .dots-nav {{ display: none; }} }}
"""

    if nav_type == "progress":
        return """
.progress-bar {
    position: fixed; top: 0; left: 0; height: 3px; width: 100%;
    background: var(--accent); transform-origin: left; transform: scaleX(0); z-index: 200;
}
"""

    return ""


def build_nav_js(nav_type: str) -> str:
    """Build navigation scroll-tracking JavaScript."""
    if not nav_type or nav_type == "none":
        return ""

    js = """
(function() {
    var dividers = document.querySelectorAll('.section-divider[id]');
    var navItems = document.querySelectorAll('[data-nav-index]');

    function setActive(idx) {
        navItems.forEach(function(item) { item.classList.remove('active'); });
        navItems.forEach(function(item) {
            if (parseInt(item.dataset.navIndex) === idx) item.classList.add('active');
        });
    }

    dividers.forEach(function(div, i) {
        ScrollTrigger.create({
            trigger: div,
            start: 'top 50%',
            onEnter: function() { setActive(i); },
            onEnterBack: function() { setActive(i); },
        });
    });

    navItems.forEach(function(item) {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            var target = document.querySelector(this.getAttribute('href'));
            if (target) gsap.to(window, { scrollTo: { y: target, offsetY: 80 }, duration: 1, ease: 'power2.inOut' });
        });
    });
"""

    if nav_type in ("topbar", "progress"):
        js += """
    var progressBar = document.querySelector('.topbar-progress, .progress-bar');
    if (progressBar) {
        gsap.to(progressBar, {
            scaleX: 1, ease: 'none',
            scrollTrigger: { trigger: document.documentElement, start: 'top top', end: 'bottom bottom', scrub: 0.3 }
        });
    }
"""

    js += "})();\n"
    return js


AUTOPLAY_JS = """
// Autoplay controller — activated by ?autoplay=1 URL parameter.
// Smoothly scrolls through the presentation for video recording.
(function() {
    var params = new URLSearchParams(window.location.search);
    if (!params.has('autoplay')) return;

    window.__autoplayDone = false;
    var startDelay = parseFloat(params.get('delay') || '5');

    // Hide scroll indicator and scrollbar during autoplay
    var scrollEl = document.querySelector('.cover-scroll');
    if (scrollEl) scrollEl.style.display = 'none';
    document.documentElement.style.scrollbarWidth = 'none';
    var hideScrollbar = document.createElement('style');
    hideScrollbar.textContent = '::-webkit-scrollbar { display: none !important; }';
    document.head.appendChild(hideScrollbar);

    gsap.registerPlugin(ScrollToPlugin);

    function buildTimeline() {
        var els = document.querySelectorAll('[data-duration]');
        if (!els.length) {
            window.__autoplayDone = true;
            return;
        }

        var tl = gsap.timeline({
            delay: startDelay,
            onComplete: function() {
                window.__autoplayDone = true;
                document.title += ' [DONE]';
            }
        });

        var vh = window.innerHeight;

        els.forEach(function(el, i) {
            var dur = parseFloat(el.dataset.duration || '3');

            if (i === 0) {
                // Hold on cover/first section (entrance animation plays during delay)
                tl.to({}, { duration: dur });
                return;
            }

            // Scroll to top of section
            tl.to(window, {
                scrollTo: { y: el, offsetY: 80 },
                duration: 1.5,
                ease: 'power2.inOut'
            });

            // If section is taller than viewport, pan down to reveal the rest
            var overflow = el.scrollHeight - vh + 80;
            if (overflow > 50) {
                // Hold briefly at top, then slowly scroll to bottom of section
                tl.to({}, { duration: dur * 0.3 });
                tl.to(window, {
                    scrollTo: { y: el, offsetY: -(overflow) },
                    duration: Math.min(dur * 0.4, 3),
                    ease: 'power1.inOut'
                });
                tl.to({}, { duration: dur * 0.3 });
            } else {
                // Section fits in viewport — just hold
                tl.to({}, { duration: dur });
            }
        });
    }

    // Wait for fonts/images to load before measuring layout
    window.addEventListener('load', function() {
        setTimeout(buildTimeline, 500);
    });
})();
"""


# ═══════════════════════════════════════════════
#  HTML BUILDER
# ═══════════════════════════════════════════════

def build_html(data: dict, theme_css: str) -> str:
    """Build the complete scroll-based HTML document."""
    title = html_lib.escape(data.get("title", "Treatment"))
    lang = html_lib.escape(data.get("lang", "en"))
    particles = data.get("particles", True)
    mode = data.get("mode", "dark")
    animation = data.get("animation", "fade")
    nav_type = data.get("nav", "none")

    fonts = data.get("fonts", {})
    font_url = build_font_link(fonts)
    mode_css = build_mode_css(mode)
    scheme_css = build_scheme_css(data.get("scheme", {}))
    font_css = build_font_css(fonts)
    nav_css = build_nav_css(nav_type, mode)
    overrides = "\n".join(filter(None, [mode_css, scheme_css, font_css, nav_css]))

    cover_html = render_cover(data)
    sections_html = "\n\n".join(render_section(s) for s in data.get("sections", []))
    particles_html = '<div class="particles" id="particles"></div>' if particles else ""
    nav_html = build_nav_html(data, nav_type)
    animation_js = build_animation_js(animation)
    nav_js = build_nav_js(nav_type)

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link href="{font_url}" rel="stylesheet">
    <script src="{GSAP_CDN}/gsap.min.js"></script>
    <script src="{GSAP_CDN}/ScrollTrigger.min.js"></script>
    <script src="{GSAP_CDN}/ScrollToPlugin.min.js"></script>
    <style>
{theme_css}
{overrides}
    </style>
</head>
<body>

{particles_html}
{nav_html}

{cover_html}

{sections_html}

<script>
{animation_js}
{nav_js}
{AUTOPLAY_JS}
</script>

</body>
</html>"""


# ═══════════════════════════════════════════════
#  PDF EXPORT
# ═══════════════════════════════════════════════

def export_pdf(html_path: str, pdf_path: str) -> bool:
    """Export treatment to PDF using Playwright/Chromium."""
    html_abs = Path(html_path).resolve()
    pdf_abs = Path(pdf_path).resolve()

    script = """
import asyncio, sys
from playwright.async_api import async_playwright

async def main():
    html_path, pdf_path = sys.argv[1], sys.argv[2]
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1920, 'height': 1080})
        await page.goto(f'file://{html_path}', wait_until='networkidle')
        await page.wait_for_timeout(3000)
        await page.pdf(
            path=pdf_path,
            width='1920px',
            height='1080px',
            print_background=True,
            margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
        )
        await browser.close()
        print(f'PDF exported to {pdf_path}')

asyncio.run(main())
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(html_abs), str(pdf_abs)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
            return True
        else:
            print(f"PDF export failed: {result.stderr}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("PDF export timed out after 30s", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate scroll-based GSAP treatments from JSON"
    )
    parser.add_argument("--json", required=True, help="Path to JSON definition")
    parser.add_argument("--output", required=True, help="Output HTML file path")
    parser.add_argument(
        "--theme", default=str(DEFAULT_THEME), help="Path to theme CSS file"
    )
    parser.add_argument("--pdf", default=None, help="Also export to PDF at this path")
    parser.add_argument(
        "--brand-url",
        default=None,
        help="Scrape this URL via Firecrawl and seed scheme/fonts/logo from the response",
    )
    parser.add_argument(
        "--aesthetic",
        default=None,
        help="Apply an aesthetic reference by name (e.g. a24, aesop). See references/ dir.",
    )
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"Error: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(json_path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {json_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Precedence (highest wins): treatment JSON > --aesthetic > --brand-url (cover only)
    # --brand-url only seeds cover.logo and cover.brand_url. Firecrawl is reliable
    # for logos but unreliable for colors/fonts, so scheme and fonts come
    # exclusively from the aesthetic reference or explicit treatment values.
    if args.aesthetic:
        try:
            from references import load_reference, apply_reference  # noqa: WPS433
            ref = load_reference(args.aesthetic)
            data = apply_reference(data, ref)
            print(f"Applied aesthetic reference: {args.aesthetic}")
        except Exception as e:
            print(f"Error loading aesthetic {args.aesthetic!r}: {e}", file=sys.stderr)
            sys.exit(1)

    if args.brand_url:
        try:
            from ingest_brand import ingest_brand, merge_into, BrandIngestError  # noqa: WPS433
            partial = ingest_brand(args.brand_url)
            cover_only = {}
            if "cover" in partial:
                cover_only["cover"] = partial["cover"]
            data = merge_into(data, cover_only)
            print(f"Ingested brand logo from: {args.brand_url}")
        except BrandIngestError as e:
            print(f"Brand ingestion failed: {e}", file=sys.stderr)
            sys.exit(1)

    theme_path = Path(args.theme)
    if not theme_path.exists():
        print(f"Error: Theme CSS not found: {theme_path}", file=sys.stderr)
        sys.exit(1)

    theme_css = theme_path.read_text()
    html_content = build_html(data, theme_css)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content)
    print(f"Treatment written to {output_path}")

    if args.pdf:
        export_pdf(str(output_path), args.pdf)


if __name__ == "__main__":
    main()
