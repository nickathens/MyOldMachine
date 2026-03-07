#!/usr/bin/env python3
"""Image upscaling using Real-ESRGAN."""
import argparse
import subprocess


def upscale_image(input_path, output_path, scale=4, face_enhance=False):
    """Upscale image using Real-ESRGAN."""
    cmd = [
        'python', '-m', 'realesrgan',
        '-i', input_path,
        '-o', output_path,
        '-s', str(scale)
    ]

    if face_enhance:
        cmd.append('--face_enhance')

    print(f"Upscaling {input_path} by {scale}x...")
    subprocess.run(cmd, check=True)
    print(f"Saved: {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Image Upscaler')
    parser.add_argument('input', help='Input image')
    parser.add_argument('output', help='Output image')
    parser.add_argument('--scale', '-s', type=int, default=4, choices=[2, 4])
    parser.add_argument('--face', '-f', action='store_true', help='Face enhancement')

    args = parser.parse_args()
    upscale_image(args.input, args.output, args.scale, args.face)
