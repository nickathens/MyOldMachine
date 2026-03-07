#!/usr/bin/env python3
"""Generate favicons and app icons from source image."""
import argparse
import os
from PIL import Image

FAVICON_SIZES = [16, 32, 48, 64, 128, 256]
IOS_SIZES = [180, 167, 152, 120, 87, 80, 76, 60, 58, 40, 29, 20]
ANDROID_SIZES = [512, 192, 144, 96, 72, 48, 36]
PWA_SIZES = [512, 384, 256, 192, 144, 128, 96, 72, 48]


def resize_image(input_path, output_path, size):
    """Resize image to square size."""
    img = Image.open(input_path)
    img = img.convert('RGBA')
    img = img.resize((size, size), Image.Resampling.LANCZOS)
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
    img = Image.open(input_path).convert('RGBA')

    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = []

    for size in sizes:
        resized = img.resize(size, Image.Resampling.LANCZOS)
        icons.append(resized)

    icons[0].save(output_path, format='ICO', sizes=sizes)
    print(f"Created: {output_path} (multi-resolution)")


def generate_ios_icons(input_path, output_dir):
    """Generate iOS app icons."""
    os.makedirs(output_dir, exist_ok=True)

    for size in IOS_SIZES:
        output_path = os.path.join(output_dir, f"ios-{size}x{size}.png")
        resize_image(input_path, output_path, size)


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


def generate_all_icons(input_path, output_dir):
    """Generate all icon types."""
    generate_favicon_set(input_path, os.path.join(output_dir, "favicon"))
    generate_ios_icons(input_path, os.path.join(output_dir, "ios"))
    generate_android_icons(input_path, os.path.join(output_dir, "android"))
    generate_pwa_icons(input_path, os.path.join(output_dir, "pwa"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Icon Generator')
    parser.add_argument('command', choices=['favicon', 'ios', 'android', 'pwa', 'all', 'resize', 'ico'])
    parser.add_argument('input', help='Input image')
    parser.add_argument('output', help='Output directory or file')
    parser.add_argument('size', nargs='?', type=int, help='Size for resize command')

    args = parser.parse_args()

    if args.command == 'favicon':
        generate_favicon_set(args.input, args.output)
    elif args.command == 'ios':
        generate_ios_icons(args.input, args.output)
    elif args.command == 'android':
        generate_android_icons(args.input, args.output)
    elif args.command == 'pwa':
        generate_pwa_icons(args.input, args.output)
    elif args.command == 'all':
        generate_all_icons(args.input, args.output)
    elif args.command == 'resize':
        if not args.size:
            print("Error: size required for resize command")
        else:
            resize_image(args.input, args.output, args.size)
    elif args.command == 'ico':
        generate_ico(args.input, args.output)
