#!/usr/bin/env python3
"""Background removal wrapper using rembg."""
import argparse
from pathlib import Path
from rembg import remove
from PIL import Image


def remove_background(input_path, output_path):
    """Remove background from single image."""
    with open(input_path, 'rb') as f:
        input_data = f.read()

    output_data = remove(input_data)

    with open(output_path, 'wb') as f:
        f.write(output_data)

    print(f"Processed: {input_path} -> {output_path}")


def batch_remove(input_dir, output_dir):
    """Remove background from all images in directory."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}

    for img_file in input_path.iterdir():
        if img_file.suffix.lower() in extensions:
            out_file = output_path / f"{img_file.stem}.png"
            remove_background(str(img_file), str(out_file))


def get_mask(input_path, output_path):
    """Get alpha mask only."""
    img = Image.open(input_path)
    result = remove(img, only_mask=True)
    result.save(output_path)
    print(f"Mask saved: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Background Removal')
    parser.add_argument('command', choices=['remove', 'batch', 'mask'])
    parser.add_argument('input', help='Input image or directory')
    parser.add_argument('output', help='Output image or directory')

    args = parser.parse_args()

    if args.command == 'remove':
        remove_background(args.input, args.output)
    elif args.command == 'batch':
        batch_remove(args.input, args.output)
    elif args.command == 'mask':
        get_mask(args.input, args.output)
