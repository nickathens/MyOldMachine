#!/usr/bin/env python3
"""
Inkscape vector graphics automation
"""
import argparse
import functools
import os
import shutil
import subprocess
import sys
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

# SVG namespace
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace('', SVG_NS)
ET.register_namespace('inkscape', 'http://www.inkscape.org/namespaces/inkscape')

def create_svg(width=1080, height=1080, bg_color=None):
    """Create base SVG element"""
    svg = ET.Element('svg', {
        'width': str(width),
        'height': str(height),
        'viewBox': f'0 0 {width} {height}',
        'xmlns': SVG_NS
    })

    if bg_color:
        ET.SubElement(svg, 'rect', {
            'width': '100%',
            'height': '100%',
            'fill': bg_color
        })

    return svg

def add_circle(svg, cx, cy, r, fill='#ffffff', stroke=None, stroke_width=2):
    """Add circle to SVG"""
    attrs = {'cx': str(cx), 'cy': str(cy), 'r': str(r), 'fill': fill}
    if stroke:
        attrs['stroke'] = stroke
        attrs['stroke-width'] = str(stroke_width)
    return ET.SubElement(svg, 'circle', attrs)

def add_rect(svg, x, y, width, height, fill='#ffffff', rx=0):
    """Add rectangle to SVG"""
    attrs = {
        'x': str(x), 'y': str(y),
        'width': str(width), 'height': str(height),
        'fill': fill
    }
    if rx:
        attrs['rx'] = str(rx)
    return ET.SubElement(svg, 'rect', attrs)

def add_text(svg, x, y, text, font_size=48, fill='#ffffff',
             font_family='sans-serif', anchor='middle'):
    """Add text to SVG"""
    elem = ET.SubElement(svg, 'text', {
        'x': str(x), 'y': str(y),
        'font-size': str(font_size),
        'fill': fill,
        'font-family': font_family,
        'text-anchor': anchor,
        'dominant-baseline': 'middle'
    })
    elem.text = text
    return elem

@functools.lru_cache(maxsize=None)
def _font(family, size):
    """The font file fontconfig (and so Inkscape) uses for family, at size."""
    from PIL import ImageFont
    path = None
    if shutil.which('fc-match'):
        path = subprocess.run(['fc-match', '-f', '%{file}', family], capture_output=True, text=True).stdout.strip()
    try:
        return ImageFont.truetype(path or 'DejaVuSans.ttf', size)
    except OSError:
        return None


def _width(text, size, family):
    font = _font(family, int(size))
    return font.getlength(text) if font else 0.6 * size * len(text)


def _wrap(text, size, family, max_width):
    lines, line = [], ''
    for word in text.split():
        trial = f'{line} {word}'.strip()
        if line and _width(trial, size, family) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def add_text_fit(svg, x, y, text, font_size, max_width, max_lines=3, fill='#ffffff',
                 font_family='sans-serif', line_height=1.15):
    """Centred text that stays inside max_width: wrapped, then shrunk if it must be.

    The templates used to set a title at one size on one line, so a long one
    ran off both edges of the canvas (Linux bot review 2026-09-27). Widths are
    measured with the font fontconfig resolves, which is the one Inkscape
    draws with. Returns the height of the block.
    """
    size = font_size
    while True:
        lines = _wrap(text, size, font_family, max_width)
        fits = all(_width(line, size, font_family) <= max_width for line in lines)
        if (fits and len(lines) <= max_lines) or size <= 8:
            break
        size = max(8, int(size * 0.92))
    step = size * line_height
    top = y - step * (len(lines) - 1) / 2
    elem = ET.SubElement(svg, 'text', {
        'x': str(x), 'y': str(round(top, 2)), 'font-size': str(size), 'fill': fill,
        'font-family': font_family, 'text-anchor': 'middle', 'dominant-baseline': 'middle',
    })
    for i, line in enumerate(lines):
        span = ET.SubElement(elem, 'tspan', {'x': str(x), 'dy': '0' if i == 0 else str(round(step, 2))})
        span.text = line
    return step * len(lines)


def upper(text):
    """Capitals the way Greek sets them: no tonos, and a tonos that kept two
    vowels apart (τσάι, ρολόι, άυλος) becomes a diaeresis on the second
    (ΤΣΑΪ, ΡΟΛΟΪ, ΑΫΛΟΣ). str.upper() kept the tonos ("ΚΑΛΗΜΈΡΑ"), an error
    any Greek reader sees. Other scripts upper-case as usual (CAFÉ)."""
    out, split = [], False
    for i, ch in enumerate(text):
        parts = unicodedata.normalize("NFD", ch)
        base, marks = parts[0], parts[1:]
        if split and base.lower() in "ιυ" and "\u0308" not in marks:
            marks += "\u0308"
        split = False
        if ("\u0370" <= base <= "\u03ff" or "\u1f00" <= base <= "\u1fff") and "\u0301" in marks:
            marks = marks.replace("\u0301", "")
            split = base.lower() in "αεηου" and text[i + 1:i + 2].lower() in ("ι", "υ")
        out.append(unicodedata.normalize("NFC", base + marks).upper())
    return "".join(out)


def add_line(svg, x1, y1, x2, y2, stroke='#ffffff', stroke_width=2):
    """Add line to SVG"""
    return ET.SubElement(svg, 'line', {
        'x1': str(x1), 'y1': str(y1),
        'x2': str(x2), 'y2': str(y2),
        'stroke': stroke,
        'stroke-width': str(stroke_width)
    })

def add_path(svg, d, fill='none', stroke='#ffffff', stroke_width=2):
    """Add path to SVG"""
    return ET.SubElement(svg, 'path', {
        'd': d,
        'fill': fill,
        'stroke': stroke,
        'stroke-width': str(stroke_width)
    })

def add_gradient(svg, id, colors, direction='vertical'):
    """Add linear gradient definition"""
    defs = svg.find('defs')
    if defs is None:
        defs = ET.SubElement(svg, 'defs')

    if direction == 'vertical':
        grad = ET.SubElement(defs, 'linearGradient', {
            'id': id, 'x1': '0%', 'y1': '0%', 'x2': '0%', 'y2': '100%'
        })
    else:
        grad = ET.SubElement(defs, 'linearGradient', {
            'id': id, 'x1': '0%', 'y1': '0%', 'x2': '100%', 'y2': '0%'
        })

    for i, color in enumerate(colors):
        offset = f'{int(100 * i / (len(colors) - 1))}%' if len(colors) > 1 else '0%'
        ET.SubElement(grad, 'stop', {'offset': offset, 'stop-color': color})

    return f'url(#{id})'

def save_svg(svg, output_path):
    """Save SVG to file"""
    tree = ET.ElementTree(svg)
    tree.write(output_path, encoding='unicode', xml_declaration=True)
    print(f"Saved: {output_path}")

def _inkscape(cmd, output):
    """Export into `output` through a hidden name beside it.

    A stale output from an earlier run never passes for a success, and a failed
    export leaves the file that was there alone. Deleting the output up front
    did the first but deleted the input when the two were the same file.
    """
    out = Path(output)
    part = out.with_name(f".{out.stem}.part{out.suffix}")
    part.unlink(missing_ok=True)
    try:
        try:
            r = subprocess.run(cmd + [f'--export-filename={part}'], capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            sys.exit("Error: inkscape is not installed")
        except subprocess.TimeoutExpired:
            sys.exit("Error: inkscape took longer than 10 minutes")
        if r.returncode != 0 or not part.exists():
            sys.exit(f"Error: inkscape wrote nothing: {r.stderr.strip()[-600:]}")
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    print(f"Converted: {output}")


def svg_to_png(input_svg, output_png, dpi=300, width=None, height=None):
    """Convert SVG to PNG using Inkscape"""
    cmd = ['inkscape', str(input_svg), '--export-type=png']

    if width:
        cmd.append(f'--export-width={width}')
    elif height:
        cmd.append(f'--export-height={height}')
    else:
        cmd.append(f'--export-dpi={dpi}')
    _inkscape(cmd, output_png)

def svg_to_pdf(input_svg, output_pdf):
    """Convert SVG to PDF"""
    _inkscape(['inkscape', str(input_svg), '--export-type=pdf'], output_pdf)

# Template generators

def template_social_media(title, subtitle=None, bg_colors=None, size=1080):
    """Create social media post template"""
    bg_colors = bg_colors or ['#1a1a2e', '#16213e']

    svg = create_svg(size, size)
    grad = add_gradient(svg, 'bg', bg_colors)
    add_rect(svg, 0, 0, size, size, fill=grad)

    # Title, wrapped or shrunk to 88 percent of the width; the subtitle sits under it
    block = add_text_fit(svg, size // 2, size // 2 - 30, title, 72, size * 0.88)

    if subtitle:
        add_text_fit(svg, size // 2, size // 2 - 30 + block / 2 + 44, subtitle, 36, size * 0.88,
                     max_lines=2, fill='#aaaaaa')

    return svg

def template_album_cover(title, artist, bg_color='#0f0f0f', accent='#ff6b6b'):
    """Create album cover template"""
    svg = create_svg(1400, 1400, bg_color)

    # Abstract geometric elements
    add_circle(svg, 700, 700, 400, fill='none', stroke=accent, stroke_width=3)
    add_circle(svg, 700, 700, 300, fill='none', stroke=accent, stroke_width=2)
    add_circle(svg, 700, 700, 200, fill='none', stroke=accent, stroke_width=1)

    # Title at bottom
    add_text_fit(svg, 700, 1200, upper(title), 64, 1200, max_lines=1)
    add_text_fit(svg, 700, 1280, artist, 32, 1200, max_lines=1, fill='#888888')

    return svg

def template_logo_minimal(text, bg_color='#000000', text_color='#ffffff'):
    """Create minimal text logo"""
    svg = create_svg(800, 400, bg_color)
    add_text_fit(svg, 400, 200, upper(text), 96, 720, max_lines=2, fill=text_color)
    return svg

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vector graphics generator')
    parser.add_argument('action', choices=['social', 'album', 'logo', 'convert'])
    parser.add_argument('--title', default='Title')
    parser.add_argument('--subtitle', default=None)
    parser.add_argument('--artist', default='Artist')
    parser.add_argument('--output', '-o',
                        help='Output file (default: /tmp/vector_output_*.svg, or the input name with the new format for convert)')
    parser.add_argument('--input', '-i', help='Input file for conversion')
    parser.add_argument('--format', choices=['png', 'pdf'],
                        help='Conversion format; taken from --output when that has .png or .pdf (default png)')
    parser.add_argument('--dpi', type=int, default=300)

    args = parser.parse_args()
    if args.action != 'convert' and not args.output:
        args.output = f'/tmp/vector_output_{uuid.uuid4().hex[:8]}.svg'

    if args.action == 'social':
        svg = template_social_media(args.title, args.subtitle)
        save_svg(svg, args.output)

    elif args.action == 'album':
        svg = template_album_cover(args.title, args.artist)
        save_svg(svg, args.output)

    elif args.action == 'logo':
        svg = template_logo_minimal(args.title)
        save_svg(svg, args.output)

    elif args.action == 'convert':
        if not args.input:
            sys.exit("Error: --input required for conversion")
        # The output used to default to a .svg name, and Inkscape exports by
        # extension, so convert --format png wrote an SVG
        suffix = Path(args.output).suffix.lower().lstrip('.') if args.output else ''
        if args.format and suffix in ('png', 'pdf') and suffix != args.format:
            sys.exit(f"Error: --format {args.format} but the output is .{suffix}")
        fmt = args.format or (suffix if suffix in ('png', 'pdf') else 'png')
        output = args.output or str(Path(args.input).with_suffix(f'.{fmt}'))
        if Path(output).suffix.lower() != f'.{fmt}':
            sys.exit(f"Error: the output {output} does not end in .{fmt}")
        if Path(output).resolve() == Path(args.input).resolve():
            sys.exit(f"Error: the output {output} is the input itself; give -o a different name")
        if fmt == 'png':
            svg_to_png(args.input, output, args.dpi)
        else:
            svg_to_pdf(args.input, output)
