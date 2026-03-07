#!/usr/bin/env python3
"""
Inkscape vector graphics automation
"""
import subprocess
import os
import sys
import argparse
import uuid
import xml.etree.ElementTree as ET

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
        rect = ET.SubElement(svg, 'rect', {
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

def svg_to_png(input_svg, output_png, dpi=300, width=None, height=None):
    """Convert SVG to PNG using Inkscape"""
    cmd = ['inkscape', input_svg, f'--export-filename={output_png}']

    if width:
        cmd.append(f'--export-width={width}')
    elif height:
        cmd.append(f'--export-height={height}')
    else:
        cmd.append(f'--export-dpi={dpi}')

    subprocess.run(cmd, check=True)
    print(f"Converted: {output_png}")

def svg_to_pdf(input_svg, output_pdf):
    """Convert SVG to PDF"""
    cmd = ['inkscape', input_svg, f'--export-filename={output_pdf}']
    subprocess.run(cmd, check=True)
    print(f"Converted: {output_pdf}")

# Template generators

def template_social_media(title, subtitle=None, bg_colors=None, size=1080):
    """Create social media post template"""
    bg_colors = bg_colors or ['#1a1a2e', '#16213e']

    svg = create_svg(size, size)
    grad = add_gradient(svg, 'bg', bg_colors)
    add_rect(svg, 0, 0, size, size, fill=grad)

    # Title
    add_text(svg, size//2, size//2 - 30, title, font_size=72, fill='#ffffff')

    if subtitle:
        add_text(svg, size//2, size//2 + 50, subtitle, font_size=36, fill='#aaaaaa')

    return svg

def template_album_cover(title, artist, bg_color='#0f0f0f', accent='#ff6b6b'):
    """Create album cover template"""
    svg = create_svg(1400, 1400, bg_color)

    # Abstract geometric elements
    add_circle(svg, 700, 700, 400, fill='none', stroke=accent, stroke_width=3)
    add_circle(svg, 700, 700, 300, fill='none', stroke=accent, stroke_width=2)
    add_circle(svg, 700, 700, 200, fill='none', stroke=accent, stroke_width=1)

    # Title at bottom
    add_text(svg, 700, 1200, title.upper(), font_size=64, fill='#ffffff')
    add_text(svg, 700, 1280, artist, font_size=32, fill='#888888')

    return svg

def template_logo_minimal(text, bg_color='#000000', text_color='#ffffff'):
    """Create minimal text logo"""
    svg = create_svg(800, 400, bg_color)
    add_text(svg, 400, 200, text.upper(), font_size=96, fill=text_color,
             font_family='sans-serif')
    return svg

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vector graphics generator')
    parser.add_argument('action', choices=['social', 'album', 'logo', 'convert'])
    parser.add_argument('--title', default='Title')
    parser.add_argument('--subtitle', default=None)
    parser.add_argument('--artist', default='Artist')
    parser.add_argument('--output', '-o', default=f'/tmp/vector_output_{uuid.uuid4().hex[:8]}.svg')
    parser.add_argument('--input', '-i', help='Input file for conversion')
    parser.add_argument('--format', choices=['png', 'pdf'], default='png')
    parser.add_argument('--dpi', type=int, default=300)

    args = parser.parse_args()

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
            print("Error: --input required for conversion")
            sys.exit(1)
        if args.format == 'png':
            svg_to_png(args.input, args.output, args.dpi)
        else:
            svg_to_pdf(args.input, args.output)
