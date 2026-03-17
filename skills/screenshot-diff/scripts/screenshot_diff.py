#!/usr/bin/env python3
"""Screenshot comparison tool."""
import argparse
import subprocess
import uuid
from PIL import Image
import os


def compare_images(image1_path, image2_path, output_path=None, threshold=0.1):
    """Compare two images and generate diff."""
    # Use ImageMagick compare
    diff_path = output_path or f'/tmp/diff_{uuid.uuid4().hex[:8]}.png'

    # Get difference metric
    result = subprocess.run(
        ['compare', '-metric', 'RMSE', image1_path, image2_path, 'null:'],
        capture_output=True,
        text=True
    )

    # Parse result (format: "12345 (0.123)")
    error_output = result.stderr.strip()
    try:
        # Extract percentage from output
        if '(' in error_output:
            diff_value = float(error_output.split('(')[1].rstrip(')'))
        else:
            diff_value = float(error_output.split()[0])
    except (ValueError, IndexError):
        diff_value = -1

    # Generate visual diff if images differ
    if diff_value > threshold:
        subprocess.run([
            'compare',
            image1_path, image2_path,
            '-compose', 'src',
            '-highlight-color', 'red',
            diff_path
        ], check=True)
        print(f"Difference: {diff_value:.4f} ({diff_value*100:.2f}%)")
        print(f"Diff image saved: {diff_path}")
        return False
    else:
        print(f"Images match within threshold ({threshold})")
        print(f"Actual difference: {diff_value:.4f}")
        return True


def composite_diff(image1_path, image2_path, output_path):
    """Create side-by-side comparison."""
    img1 = Image.open(image1_path)
    img2 = Image.open(image2_path)

    # Ensure same size
    width = max(img1.width, img2.width)
    height = max(img1.height, img2.height)

    # Create composite
    composite = Image.new('RGB', (width * 2 + 10, height), (128, 128, 128))
    composite.paste(img1, (0, 0))
    composite.paste(img2, (width + 10, 0))
    composite.save(output_path)

    print(f"Side-by-side comparison saved: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Screenshot Diff')
    parser.add_argument('command', choices=['compare', 'composite'])
    parser.add_argument('image1', help='First image')
    parser.add_argument('image2', help='Second image')
    parser.add_argument('--output', '-o', help='Output diff image')
    parser.add_argument('--threshold', '-t', type=float, default=0.1)

    args = parser.parse_args()

    if args.command == 'compare':
        compare_images(args.image1, args.image2, args.output, args.threshold)
    elif args.command == 'composite':
        composite_diff(args.image1, args.image2, args.output or f'/tmp/composite_{uuid.uuid4().hex[:8]}.png')
