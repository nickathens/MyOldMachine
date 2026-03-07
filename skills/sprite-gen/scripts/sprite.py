#!/usr/bin/env python3
"""Sprite sheet generation and manipulation."""
import argparse
import os
import glob
from PIL import Image
import math


def create_sprite_sheet(image_paths, output_path, cols=4, padding=0):
    """Combine images into a sprite sheet."""
    images = [Image.open(p) for p in sorted(image_paths)]

    if not images:
        print("No images found")
        return

    # Get dimensions from first image
    width, height = images[0].size
    rows = math.ceil(len(images) / cols)

    # Create sheet
    sheet_width = cols * (width + padding) - padding
    sheet_height = rows * (height + padding) - padding
    sheet = Image.new('RGBA', (sheet_width, sheet_height), (0, 0, 0, 0))

    for i, img in enumerate(images):
        x = (i % cols) * (width + padding)
        y = (i // cols) * (height + padding)
        sheet.paste(img, (x, y))

    sheet.save(output_path)
    print(f"Created sprite sheet: {output_path} ({cols}x{rows}, {len(images)} frames)")


def split_sprite_sheet(sheet_path, output_dir, cols, rows):
    """Split sprite sheet into individual frames."""
    os.makedirs(output_dir, exist_ok=True)

    sheet = Image.open(sheet_path)
    width, height = sheet.size

    frame_width = width // cols
    frame_height = height // rows

    frame_num = 0
    for row in range(rows):
        for col in range(cols):
            x = col * frame_width
            y = row * frame_height
            frame = sheet.crop((x, y, x + frame_width, y + frame_height))

            # Skip empty frames
            if frame.getbbox():
                output_path = os.path.join(output_dir, f"frame_{frame_num:04d}.png")
                frame.save(output_path)
                frame_num += 1

    print(f"Extracted {frame_num} frames to {output_dir}")


def resize_sprite(input_path, output_path, scale=2):
    """Resize sprite using nearest-neighbor (pixel-perfect)."""
    img = Image.open(input_path)
    new_size = (img.width * scale, img.height * scale)
    resized = img.resize(new_size, Image.Resampling.NEAREST)
    resized.save(output_path)
    print(f"Resized: {input_path} -> {output_path} ({scale}x)")


def pixelate(input_path, output_path, pixel_size=8):
    """Pixelate an image."""
    img = Image.open(input_path)

    # Shrink
    small = img.resize(
        (img.width // pixel_size, img.height // pixel_size),
        Image.Resampling.NEAREST
    )

    # Enlarge back
    result = small.resize(img.size, Image.Resampling.NEAREST)
    result.save(output_path)
    print(f"Pixelated: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sprite Tools')
    parser.add_argument('command', choices=['sheet', 'split', 'resize', 'pixelate'])
    parser.add_argument('input', nargs='+', help='Input file(s) or pattern')
    parser.add_argument('--output', '-o', required=True, help='Output file or directory')
    parser.add_argument('--cols', type=int, default=4, help='Columns in sheet')
    parser.add_argument('--rows', type=int, default=4, help='Rows (for split)')
    parser.add_argument('--scale', type=int, default=2, help='Scale factor')
    parser.add_argument('--padding', type=int, default=0, help='Padding between sprites')
    parser.add_argument('--pixel-size', type=int, default=8, help='Pixel size for pixelate')

    args = parser.parse_args()

    # Expand globs
    files = []
    for pattern in args.input:
        files.extend(glob.glob(pattern))

    if args.command == 'sheet':
        create_sprite_sheet(files, args.output, args.cols, args.padding)
    elif args.command == 'split':
        split_sprite_sheet(files[0], args.output, args.cols, args.rows)
    elif args.command == 'resize':
        resize_sprite(files[0], args.output, args.scale)
    elif args.command == 'pixelate':
        pixelate(files[0], args.output, args.pixel_size)
