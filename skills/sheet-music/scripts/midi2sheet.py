#!/usr/bin/env python3
"""
MIDI to Sheet Music - Convert MIDI files to PDF sheet music using LilyPond.

Usage:
    python midi2sheet.py input.mid                # Generate PDF
    python midi2sheet.py input.mid --png          # Generate PNG instead
    python midi2sheet.py input.mid --title "My Song"
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def midi_to_lilypond(midi_path: str) -> str:
    """Convert MIDI to LilyPond format using midi2ly."""
    result = subprocess.run(
        ["midi2ly", str(midi_path), "--output=-"],
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode != 0:
        raise Exception(f"midi2ly failed: {result.stderr}")

    return result.stdout


def render_lilypond(ly_content: str, output_path: str, title: str = None, png: bool = False) -> dict:
    """Render LilyPond content to PDF or PNG."""
    output_path = Path(output_path)

    # Modify content to add title if specified
    if title:
        header = f'''
\\header {{
  title = "{title}"
}}
'''
        # Insert header after \version line
        lines = ly_content.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('\\version'):
                lines.insert(i + 1, header)
                break
        ly_content = '\n'.join(lines)

    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ly', delete=False) as f:
        f.write(ly_content)
        ly_file = f.name

    try:
        # Determine output format
        if png:
            fmt_args = ["--png", "-dresolution=150"]
            expected_ext = ".png"
        else:
            fmt_args = ["--pdf"]
            expected_ext = ".pdf"

        # Run lilypond
        cmd = [
            "lilypond",
            *fmt_args,
            f"--output={output_path.with_suffix('')}",
            ly_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            return {"error": f"LilyPond failed: {result.stderr}"}

        output_file = output_path.with_suffix(expected_ext)
        if not output_file.exists():
            # LilyPond might add page numbers
            candidates = list(output_path.parent.glob(f"{output_path.stem}*{expected_ext}"))
            if candidates:
                output_file = candidates[0]

        if output_file.exists():
            return {"success": True, "output": str(output_file)}
        else:
            return {"error": "Output file not generated"}

    finally:
        Path(ly_file).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Convert MIDI to sheet music")
    parser.add_argument("input", help="Input MIDI file")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--title", "-t", help="Sheet music title")
    parser.add_argument("--png", action="store_true", help="Output as PNG instead of PDF")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        ext = ".png" if args.png else ".pdf"
        output_path = input_path.with_suffix(ext)

    print(f"Converting: {input_path}")
    print("Step 1: Converting MIDI to LilyPond notation...")

    try:
        ly_content = midi_to_lilypond(str(input_path))
    except Exception as e:
        print(f"Error: {e}")
        return 1

    print("Step 2: Rendering sheet music...")

    result = render_lilypond(ly_content, str(output_path), args.title, args.png)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    print(f"Success! Sheet music saved to: {result['output']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
