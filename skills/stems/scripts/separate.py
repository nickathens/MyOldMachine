#!/usr/bin/env python3
"""
Stem Separation using Demucs (Meta's AI model).

Usage:
    python separate.py input.mp3                    # Separate into 4 stems
    python separate.py input.mp3 --model htdemucs   # Use specific model
    python separate.py input.mp3 --output ./stems   # Custom output directory

Outputs: vocals.wav, drums.wav, bass.wav, other.wav
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def separate_stems(input_path: str, output_dir: str = None, model: str = "htdemucs") -> dict:
    """Separate audio into stems using Demucs."""
    input_path = Path(input_path)

    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    # Determine output directory
    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = input_path.parent / f"{input_path.stem}_stems"

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Run demucs
        cmd = [
            sys.executable, "-m", "demucs",
            "--out", str(output_dir),
            "--name", model,
            str(input_path)
        ]

        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 min timeout

        if result.returncode != 0:
            return {"error": f"Demucs failed: {result.stderr}"}

        # Find the output files
        stems_dir = output_dir / model / input_path.stem

        if not stems_dir.exists():
            # Try alternative path
            stems_dir = output_dir / input_path.stem

        stems = {}
        for stem_name in ["vocals", "drums", "bass", "other"]:
            stem_file = stems_dir / f"{stem_name}.wav"
            if stem_file.exists():
                stems[stem_name] = str(stem_file)

        if not stems:
            return {"error": f"No stems found in {stems_dir}"}

        return {
            "success": True,
            "input": str(input_path),
            "output_dir": str(stems_dir),
            "stems": stems,
            "message": f"Separated into {len(stems)} stems"
        }

    except subprocess.TimeoutExpired:
        return {"error": "Processing timed out (>30 minutes)"}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Separate audio into stems")
    parser.add_argument("input", help="Input audio file")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--model", "-m", default="htdemucs",
                       choices=["htdemucs", "htdemucs_ft", "mdx_extra", "mdx_extra_q"],
                       help="Model to use (default: htdemucs)")
    args = parser.parse_args()

    print(f"Separating: {args.input}")
    print(f"Model: {args.model}")
    print("This will take several minutes (runs on CPU)...")
    print("")

    result = separate_stems(args.input, args.output, args.model)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    print(f"Success! Stems saved to: {result['output_dir']}")
    print("Stems:")
    for name, path in result['stems'].items():
        print(f"  - {name}: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
