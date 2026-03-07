#!/usr/bin/env python3
"""Color palette extraction and generation."""
import argparse
import colorsys
from colorthief import ColorThief


def rgb_to_hex(r, g, b):
    """Convert RGB to HEX."""
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(hex_color):
    """Convert HEX to RGB."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hsl(r, g, b):
    """Convert RGB to HSL."""
    r, g, b = r/255, g/255, b/255
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return (int(h*360), int(s*100), int(l*100))


def hsl_to_rgb(h, s, l):
    """Convert HSL to RGB."""
    h, s, l = h/360, s/100, l/100
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return (int(r*255), int(g*255), int(b*255))


def extract_palette(image_path, num_colors=5):
    """Extract dominant colors from image."""
    ct = ColorThief(image_path)
    palette = ct.get_palette(color_count=num_colors, quality=1)

    result = []
    for rgb in palette:
        hex_color = rgb_to_hex(*rgb)
        result.append({
            'hex': hex_color,
            'rgb': rgb,
            'hsl': rgb_to_hsl(*rgb)
        })

    return result


def complementary(hex_color):
    """Get complementary color."""
    rgb = hex_to_rgb(hex_color)
    h, s, l = rgb_to_hsl(*rgb)
    comp_h = (h + 180) % 360
    comp_rgb = hsl_to_rgb(comp_h, s, l)
    return rgb_to_hex(*comp_rgb)


def analogous(hex_color, angle=30):
    """Get analogous colors."""
    rgb = hex_to_rgb(hex_color)
    h, s, l = rgb_to_hsl(*rgb)

    colors = []
    for offset in [-angle, 0, angle]:
        new_h = (h + offset) % 360
        new_rgb = hsl_to_rgb(new_h, s, l)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


def triadic(hex_color):
    """Get triadic colors."""
    rgb = hex_to_rgb(hex_color)
    h, s, l = rgb_to_hsl(*rgb)

    colors = []
    for offset in [0, 120, 240]:
        new_h = (h + offset) % 360
        new_rgb = hsl_to_rgb(new_h, s, l)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


def split_complementary(hex_color):
    """Get split-complementary colors."""
    rgb = hex_to_rgb(hex_color)
    h, s, l = rgb_to_hsl(*rgb)

    colors = [hex_color]
    for offset in [150, 210]:
        new_h = (h + offset) % 360
        new_rgb = hsl_to_rgb(new_h, s, l)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


def monochromatic(hex_color, variations=5):
    """Get monochromatic variations."""
    rgb = hex_to_rgb(hex_color)
    h, s, l = rgb_to_hsl(*rgb)

    colors = []
    for i in range(variations):
        new_l = int(20 + (60 / (variations - 1)) * i) if variations > 1 else l
        new_rgb = hsl_to_rgb(h, s, new_l)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Color Palette Tools')
    parser.add_argument('command', choices=['extract', 'complement', 'analogous', 'triadic', 'split', 'mono', 'convert'])
    parser.add_argument('input', help='Image path or hex color')
    parser.add_argument('--colors', '-n', type=int, default=5, help='Number of colors')
    parser.add_argument('--to', choices=['hex', 'rgb', 'hsl'], default='hex', help='Convert to format')

    args = parser.parse_args()

    if args.command == 'extract':
        palette = extract_palette(args.input, args.colors)
        print(f"Extracted {len(palette)} colors:")
        for i, color in enumerate(palette, 1):
            print(f"  {i}. {color['hex']} - RGB{color['rgb']} - HSL{color['hsl']}")

    elif args.command == 'complement':
        comp = complementary(args.input)
        print(f"Base: {args.input}")
        print(f"Complement: {comp}")

    elif args.command == 'analogous':
        colors = analogous(args.input)
        print(f"Analogous colors: {', '.join(colors)}")

    elif args.command == 'triadic':
        colors = triadic(args.input)
        print(f"Triadic colors: {', '.join(colors)}")

    elif args.command == 'split':
        colors = split_complementary(args.input)
        print(f"Split-complementary: {', '.join(colors)}")

    elif args.command == 'mono':
        colors = monochromatic(args.input, args.colors)
        print(f"Monochromatic: {', '.join(colors)}")

    elif args.command == 'convert':
        rgb = hex_to_rgb(args.input)
        hsl = rgb_to_hsl(*rgb)
        print(f"HEX: {args.input}")
        print(f"RGB: {rgb}")
        print(f"HSL: {hsl}")
