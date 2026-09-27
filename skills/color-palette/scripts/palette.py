#!/usr/bin/env python3
"""Color palette extraction and generation.

HSL is kept in floats between steps and rounded only for display: rounding
each step to integers made the untouched middle of an analogous set drift
(#3498db came back #3497d9), grey's complement turn #7f7f7f and #010203
collapse to black (Linux bot review 2026-09-27).
"""
import argparse
import colorsys
import re
import sys

from colorthief import ColorThief


def rgb_to_hex(r, g, b):
    """Convert RGB to HEX."""
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(hex_color):
    """Convert HEX (#rrggbb, #rgb, with or without #) to RGB."""
    text = hex_color.strip().lstrip('#')
    if re.fullmatch(r"[0-9a-fA-F]{3}", text):
        text = "".join(ch * 2 for ch in text)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        sys.exit(f"Error: not a hex colour: {hex_color!r} (use #rrggbb or #rgb)")
    return tuple(int(text[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hsl(r, g, b):
    """Convert RGB to HSL as floats: hue in degrees, saturation and lightness in percent."""
    h, lgt, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    return (h * 360, s * 100, lgt * 100)


def hsl_to_rgb(h, s, lgt):
    """Convert HSL to RGB, rounded."""
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, lgt / 100, s / 100)
    return (round(r * 255), round(g * 255), round(b * 255))


def hsl_text(hsl):
    return tuple(round(v) for v in hsl)


def extract_palette(image_path, num_colors=5):
    """Extract dominant colors from image, each refined to its pixels' true mean.

    ColorThief quantises to 5 bits a channel, so it reports approximations
    (28,204,28 for a flat 30,200,30). Every pixel is assigned to its nearest
    palette colour and the colour becomes the mean of those pixels; the list
    is ordered by how much of the image each colour covers.
    """
    import numpy as np
    from PIL import Image

    ct = ColorThief(image_path)
    palette = ct.get_palette(color_count=num_colors, quality=1)[:num_colors]

    with Image.open(image_path) as im:
        im = im.convert("RGBA")
        im.thumbnail((400, 400))
        px = np.asarray(im, dtype=np.float64).reshape(-1, 4)
    px = px[px[:, 3] >= 125][:, :3]      # ColorThief's own transparency cut
    centres = np.array(palette, dtype=np.float64)
    nearest = ((px[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)

    result = []
    for i, rgb in enumerate(palette):
        members = px[nearest == i]
        if len(members):
            rgb = tuple(int(v) for v in np.round(members.mean(axis=0)))
        result.append({
            'hex': rgb_to_hex(*rgb),
            'rgb': rgb,
            'hsl': hsl_text(rgb_to_hsl(*rgb)),
            'share': round(100 * len(members) / max(len(px), 1), 1),
        })
    result.sort(key=lambda c: -c['share'])
    return result


def complementary(hex_color):
    """Get complementary color."""
    rgb = hex_to_rgb(hex_color)
    h, s, lgt = rgb_to_hsl(*rgb)
    comp_h = (h + 180) % 360
    comp_rgb = hsl_to_rgb(comp_h, s, lgt)
    return rgb_to_hex(*comp_rgb)


def analogous(hex_color, angle=30):
    """Get analogous colors."""
    rgb = hex_to_rgb(hex_color)
    h, s, lgt = rgb_to_hsl(*rgb)

    colors = []
    for offset in [-angle, 0, angle]:
        new_h = (h + offset) % 360
        new_rgb = hsl_to_rgb(new_h, s, lgt)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


def triadic(hex_color):
    """Get triadic colors."""
    rgb = hex_to_rgb(hex_color)
    h, s, lgt = rgb_to_hsl(*rgb)

    colors = []
    for offset in [0, 120, 240]:
        new_h = (h + offset) % 360
        new_rgb = hsl_to_rgb(new_h, s, lgt)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


def split_complementary(hex_color):
    """Get split-complementary colors."""
    rgb = hex_to_rgb(hex_color)
    h, s, lgt = rgb_to_hsl(*rgb)

    colors = [hex_color]
    for offset in [150, 210]:
        new_h = (h + offset) % 360
        new_rgb = hsl_to_rgb(new_h, s, lgt)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


def monochromatic(hex_color, variations=5):
    """Get monochromatic variations."""
    rgb = hex_to_rgb(hex_color)
    h, s, lgt = rgb_to_hsl(*rgb)

    colors = []
    for i in range(variations):
        new_l = 20 + (60 / (variations - 1)) * i if variations > 1 else lgt
        new_rgb = hsl_to_rgb(h, s, new_l)
        colors.append(rgb_to_hex(*new_rgb))

    return colors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Color Palette Tools')
    parser.add_argument('command', choices=['extract', 'complement', 'analogous', 'triadic', 'split', 'mono', 'convert'])
    parser.add_argument('input', help='Image path or hex color')
    parser.add_argument('--colors', '-n', type=int, default=5, help='Number of colors')
    parser.add_argument('--to', choices=['hex', 'rgb', 'hsl'], help='Convert to one format (default: all three)')

    args = parser.parse_args()

    if args.command == 'extract':
        palette = extract_palette(args.input, args.colors)
        print(f"Extracted {len(palette)} colors (by share of the image):")
        for i, color in enumerate(palette, 1):
            print(f"  {i}. {color['hex']} - RGB{color['rgb']} - HSL{color['hsl']} - {color['share']}%")

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
        hsl = hsl_text(rgb_to_hsl(*rgb))
        lines = {'hex': f"HEX: {rgb_to_hex(*rgb)}", 'rgb': f"RGB: {rgb}", 'hsl': f"HSL: {hsl}"}
        print("\n".join([lines[args.to]] if args.to else lines.values()))
