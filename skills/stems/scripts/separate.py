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
import subprocess
import sys
from pathlib import Path


# demucs 4.0.1 writes stems with torchaudio.save, and torchaudio 2.9 and later
# hand saving to the separate torchcodec package, which is not installed: the
# separation finished and then died writing vocals.wav (Linux bot review
# 2026-09-27, torch/torchaudio 2.10). The runner writes through soundfile
# instead, which is already a dependency (librosa), then runs demucs's own CLI.
DEMUCS_RUNNER = """
import sys
import soundfile
import torchaudio

def _save(path, wav, sample_rate, encoding="PCM_S", bits_per_sample=16, **_):
    subtype = "FLOAT" if encoding == "PCM_F" else {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}[bits_per_sample]
    soundfile.write(str(path), wav.detach().cpu().numpy().T, sample_rate, subtype=subtype)

torchaudio.save = _save
from demucs.separate import main
main(sys.argv[1:])
"""


def pick_device(python: str = sys.executable) -> str:
    """'cuda' only when a CUDA kernel really runs in this interpreter.

    A torch build without kernels for an older card (measured on a GTX 970,
    compute 5.2) reports cuda.is_available() True and then fails every kernel
    with "no kernel image is available", so leaving the choice to demucs
    crashed every separation there (review 2026-09-27). A box whose torch can
    use its GPU still gets it.
    """
    probe = ("import sys, torch; sys.exit(0 if torch.cuda.is_available() and "
             "(torch.ones(1, device='cuda') * 2).item() == 2 else 1)")
    try:
        result = subprocess.run([python, "-c", probe], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return "cpu"
    return "cuda" if result.returncode == 0 else "cpu"


def separate_stems(input_path: str, output_dir: str = None, model: str = "htdemucs",
                   device: str = "auto") -> dict:
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
        if device == "auto":
            device = pick_device()
        cmd = [
            sys.executable, "-c", DEMUCS_RUNNER,
            "--out", str(output_dir),
            "--name", model,
            "--device", device,
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
    parser.add_argument("--device", "-d", default="auto", choices=["auto", "cpu", "cuda"],
                        help="auto (default) uses the GPU only when a CUDA kernel "
                             "actually runs, otherwise the CPU")
    args = parser.parse_args()

    print(f"Separating: {args.input}")
    print(f"Model: {args.model}")
    print("This will take several minutes (runs on CPU)...")
    print("")

    result = separate_stems(args.input, args.output, args.model, args.device)

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
