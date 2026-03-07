#!/usr/bin/env python3
"""
Image editing operations using Pillow.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageEnhance, ImageOps


def cmd_info(args):
    """Get image info."""
    img = Image.open(args.input)

    info = {
        "format": img.format,
        "mode": img.mode,
        "width": img.size[0],
        "height": img.size[1],
        "size": list(img.size),
    }

    if hasattr(img, 'n_frames'):
        info["frames"] = img.n_frames

    print(json.dumps(info, indent=2))


def cmd_resize(args):
    """Resize image."""
    img = Image.open(args.input)

    if args.width and args.height:
        new_size = (args.width, args.height)
    elif args.width:
        ratio = args.width / img.size[0]
        new_size = (args.width, int(img.size[1] * ratio))
    elif args.height:
        ratio = args.height / img.size[1]
        new_size = (int(img.size[0] * ratio), args.height)
    else:
        print("Error: Specify --width and/or --height", file=sys.stderr)
        sys.exit(1)

    resized = img.resize(new_size, Image.Resampling.LANCZOS)
    resized.save(args.output)
    print(f"Resized to {new_size[0]}x{new_size[1]} -> {args.output}")


def cmd_crop(args):
    """Crop image."""
    img = Image.open(args.input)

    if args.square:
        # Crop to center square
        min_dim = min(img.size)
        left = (img.size[0] - min_dim) // 2
        top = (img.size[1] - min_dim) // 2
        box = (left, top, left + min_dim, top + min_dim)
    elif args.box:
        box = tuple(args.box)
    else:
        print("Error: Specify --box L T R B or --square", file=sys.stderr)
        sys.exit(1)

    cropped = img.crop(box)
    cropped.save(args.output)
    print(f"Cropped to {cropped.size[0]}x{cropped.size[1]} -> {args.output}")


def cmd_rotate(args):
    """Rotate image."""
    img = Image.open(args.input)
    rotated = img.rotate(args.angle, expand=True, resample=Image.Resampling.BICUBIC)
    rotated.save(args.output)
    print(f"Rotated {args.angle} degrees -> {args.output}")


def cmd_convert(args):
    """Convert image format."""
    img = Image.open(args.input)

    # Handle transparency for formats that don't support it
    output_ext = Path(args.output).suffix.lower()
    if output_ext in ('.jpg', '.jpeg') and img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    save_args = {}
    if output_ext in ('.jpg', '.jpeg'):
        save_args['quality'] = args.quality

    img.save(args.output, **save_args)
    print(f"Converted to {output_ext} -> {args.output}")


def cmd_filter(args):
    """Apply filter to image."""
    img = Image.open(args.input)

    filters = {
        'blur': ImageFilter.BLUR,
        'sharpen': ImageFilter.SHARPEN,
        'contour': ImageFilter.CONTOUR,
        'detail': ImageFilter.DETAIL,
        'edge_enhance': ImageFilter.EDGE_ENHANCE,
        'emboss': ImageFilter.EMBOSS,
        'smooth': ImageFilter.SMOOTH,
    }

    if args.filter == 'grayscale':
        result = ImageOps.grayscale(img)
    elif args.filter == 'sepia':
        gray = ImageOps.grayscale(img)
        result = ImageOps.colorize(gray, '#704214', '#C0A080')
    elif args.filter == 'invert':
        if img.mode == 'RGBA':
            r, g, b, a = img.split()
            rgb = Image.merge('RGB', (r, g, b))
            inverted = ImageOps.invert(rgb)
            r, g, b = inverted.split()
            result = Image.merge('RGBA', (r, g, b, a))
        else:
            result = ImageOps.invert(img.convert('RGB'))
    elif args.filter in filters:
        result = img.filter(filters[args.filter])
    else:
        print(f"Unknown filter: {args.filter}", file=sys.stderr)
        print(f"Available: {', '.join(list(filters.keys()) + ['grayscale', 'sepia', 'invert'])}", file=sys.stderr)
        sys.exit(1)

    result.save(args.output)
    print(f"Applied {args.filter} filter -> {args.output}")


def cmd_adjust(args):
    """Adjust brightness/contrast."""
    img = Image.open(args.input)

    if args.brightness:
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(args.brightness)

    if args.contrast:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(args.contrast)

    if args.saturation:
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(args.saturation)

    img.save(args.output)
    print(f"Adjusted (b={args.brightness}, c={args.contrast}, s={args.saturation}) -> {args.output}")


def cmd_thumbnail(args):
    """Create thumbnail."""
    img = Image.open(args.input)
    img.thumbnail((args.size, args.size), Image.Resampling.LANCZOS)
    img.save(args.output)
    print(f"Created {img.size[0]}x{img.size[1]} thumbnail -> {args.output}")


def cmd_composite(args):
    """Overlay one image on another."""
    base = Image.open(args.base).convert('RGBA')
    overlay = Image.open(args.overlay).convert('RGBA')

    position = tuple(args.position) if args.position else (0, 0)

    # Create a copy to paste onto
    result = base.copy()
    result.paste(overlay, position, overlay)

    result.save(args.output)
    print(f"Composited at {position} -> {args.output}")


def cmd_border(args):
    """Add border to image."""
    img = Image.open(args.input)

    bordered = ImageOps.expand(img, border=args.width, fill=args.color)
    bordered.save(args.output)
    print(f"Added {args.width}px {args.color} border -> {args.output}")


def main():
    parser = argparse.ArgumentParser(description="Image editing tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Info
    p = subparsers.add_parser("info", help="Get image info")
    p.add_argument("input", help="Input file")
    p.set_defaults(func=cmd_info)

    # Resize
    p = subparsers.add_parser("resize", help="Resize image")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--width", type=int, help="Width")
    p.add_argument("--height", type=int, help="Height")
    p.set_defaults(func=cmd_resize)

    # Crop
    p = subparsers.add_parser("crop", help="Crop image")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--box", type=int, nargs=4, metavar=('L', 'T', 'R', 'B'), help="Crop box")
    p.add_argument("--square", action="store_true", help="Crop to center square")
    p.set_defaults(func=cmd_crop)

    # Rotate
    p = subparsers.add_parser("rotate", help="Rotate image")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--angle", type=float, required=True, help="Rotation angle (degrees)")
    p.set_defaults(func=cmd_rotate)

    # Convert
    p = subparsers.add_parser("convert", help="Convert format")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--quality", type=int, default=85, help="JPG quality")
    p.set_defaults(func=cmd_convert)

    # Filter
    p = subparsers.add_parser("filter", help="Apply filter")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--filter", "-f", required=True, help="Filter name")
    p.set_defaults(func=cmd_filter)

    # Adjust
    p = subparsers.add_parser("adjust", help="Adjust brightness/contrast")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--brightness", type=float, default=1.0, help="Brightness (1.0=normal)")
    p.add_argument("--contrast", type=float, default=1.0, help="Contrast (1.0=normal)")
    p.add_argument("--saturation", type=float, default=1.0, help="Saturation (1.0=normal)")
    p.set_defaults(func=cmd_adjust)

    # Thumbnail
    p = subparsers.add_parser("thumbnail", help="Create thumbnail")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--size", type=int, default=200, help="Max dimension")
    p.set_defaults(func=cmd_thumbnail)

    # Composite
    p = subparsers.add_parser("composite", help="Overlay images")
    p.add_argument("base", help="Base image")
    p.add_argument("overlay", help="Overlay image")
    p.add_argument("output", help="Output file")
    p.add_argument("--position", type=int, nargs=2, metavar=('X', 'Y'), help="Position")
    p.set_defaults(func=cmd_composite)

    # Border
    p = subparsers.add_parser("border", help="Add border")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--width", type=int, default=10, help="Border width")
    p.add_argument("--color", default="black", help="Border color")
    p.set_defaults(func=cmd_border)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
