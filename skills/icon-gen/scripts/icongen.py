#!/usr/bin/env python3
"""Generate favicons and app icons from source image.

Linux bot review 2026-09-27: non-square art was stretched into the square (it is
padded now, centred, transparent), iOS icons kept an alpha channel that the
App Store rejects (flattened onto --background), SVG input could not be
opened although SKILL.md offered it (rendered with Inkscape now), and a
phone JPEG's EXIF rotation was ignored.
"""
import argparse
import functools
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageOps

BACKGROUND = "#ffffff"

FAVICON_SIZES = [16, 32, 48, 64, 128, 256]
IOS_SIZES = [180, 167, 152, 120, 87, 80, 76, 60, 58, 40, 29, 20]
ANDROID_SIZES = [512, 192, 144, 96, 72, 48, 36]
PWA_SIZES = [512, 384, 256, 192, 144, 128, 96, 72, 48]


@functools.lru_cache(maxsize=None)
def source_square(input_path):
    """The source as a square RGBA image: SVG rendered, EXIF applied, padded not stretched."""
    if input_path.lower().endswith(".svg"):
        if not shutil.which("inkscape"):
            sys.exit("Error: SVG input needs Inkscape, which is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            png = os.path.join(tmp, "render.png")
            r = subprocess.run(["inkscape", input_path, "--export-type=png", f"--export-filename={png}",
                                "--export-width=1024"], capture_output=True, text=True, timeout=300)
            if r.returncode != 0 or not os.path.exists(png):
                sys.exit(f"Error: Inkscape could not render {input_path}: {r.stderr.strip()[-300:]}")
            img = Image.open(png).convert("RGBA")
            img.load()
    else:
        img = ImageOps.exif_transpose(Image.open(input_path)).convert("RGBA")
    if img.width != img.height:
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = square
    return img


def resize_image(input_path, output_path, size, background=None):
    """Resize image to a square icon; with a background, flattened with no alpha."""
    img = source_square(input_path).resize((size, size), Image.Resampling.LANCZOS)
    if background:
        flat = Image.new("RGBA", img.size, background)
        img = Image.alpha_composite(flat, img).convert("RGB")
    img.save(output_path, 'PNG')
    print(f"Created: {output_path} ({size}x{size})")


def generate_favicon_set(input_path, output_dir):
    """Generate all favicon sizes."""
    os.makedirs(output_dir, exist_ok=True)

    for size in FAVICON_SIZES:
        output_path = os.path.join(output_dir, f"favicon-{size}x{size}.png")
        resize_image(input_path, output_path, size)

    # Generate ICO file with multiple sizes
    generate_ico(input_path, os.path.join(output_dir, "favicon.ico"))


def generate_ico(input_path, output_path):
    """Generate multi-resolution ICO file."""
    img = source_square(input_path)

    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    # Pillow's ICO writer downsizes from the image it is given and drops any
    # requested size larger than it, so saving from the 16px copy produced a
    # 16px-only icon (audit F29, 2026-09-06). Save from the largest.
    largest = img.resize(sizes[-1], Image.Resampling.LANCZOS)
    largest.save(output_path, format='ICO', sizes=sizes)
    embedded = sorted(Image.open(output_path).ico.sizes())
    print(f"Created: {output_path} (sizes: {', '.join(f'{w}x{h}' for w, h in embedded)})")


def generate_ios_icons(input_path, output_dir, background=BACKGROUND):
    """Generate iOS app icons: opaque, since the App Store rejects an icon with alpha."""
    os.makedirs(output_dir, exist_ok=True)

    for size in IOS_SIZES:
        output_path = os.path.join(output_dir, f"ios-{size}x{size}.png")
        resize_image(input_path, output_path, size, background=background)


def generate_android_icons(input_path, output_dir):
    """Generate Android app icons."""
    os.makedirs(output_dir, exist_ok=True)

    for size in ANDROID_SIZES:
        output_path = os.path.join(output_dir, f"android-{size}x{size}.png")
        resize_image(input_path, output_path, size)


def generate_pwa_icons(input_path, output_dir):
    """Generate PWA icons."""
    os.makedirs(output_dir, exist_ok=True)

    for size in PWA_SIZES:
        output_path = os.path.join(output_dir, f"pwa-{size}x{size}.png")
        resize_image(input_path, output_path, size)


def generate_all_icons(input_path, output_dir, background=BACKGROUND):
    """Generate all icon types."""
    generate_favicon_set(input_path, os.path.join(output_dir, "favicon"))
    generate_ios_icons(input_path, os.path.join(output_dir, "ios"), background)
    generate_android_icons(input_path, os.path.join(output_dir, "android"))
    generate_pwa_icons(input_path, os.path.join(output_dir, "pwa"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Icon Generator')
    parser.add_argument('command', choices=['favicon', 'ios', 'android', 'pwa', 'all', 'resize', 'ico'])
    parser.add_argument('input', help='Input image')
    parser.add_argument('output', help='Output directory or file')
    parser.add_argument('size', nargs='?', type=int, help='Size for resize command')
    parser.add_argument('--background', default=BACKGROUND,
                        help='Fill for iOS icons, which must be opaque (default white)')

    args = parser.parse_args()
    if not os.path.isfile(args.input):
        sys.exit(f"Error: no such file: {args.input}")

    if args.command == 'favicon':
        generate_favicon_set(args.input, args.output)
    elif args.command == 'ios':
        generate_ios_icons(args.input, args.output, args.background)
    elif args.command == 'android':
        generate_android_icons(args.input, args.output)
    elif args.command == 'pwa':
        generate_pwa_icons(args.input, args.output)
    elif args.command == 'all':
        generate_all_icons(args.input, args.output, args.background)
    elif args.command == 'resize':
        if not args.size:
            sys.exit("Error: size required for resize command")
        resize_image(args.input, args.output, args.size)
    elif args.command == 'ico':
        generate_ico(args.input, args.output)
