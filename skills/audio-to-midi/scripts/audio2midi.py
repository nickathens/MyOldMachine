#!/usr/bin/env python3
"""
Audio to MIDI Transcription using Spotify's Basic Pitch.

Usage:
    python audio2midi.py input.mp3 output.mid
    python audio2midi.py input.wav  # outputs input_basic_pitch.mid

Supports: mp3, wav, flac, ogg, m4a
"""

import argparse
import sys
from pathlib import Path


def transcribe_audio(input_path: str, output_path: str = None) -> dict:
    """Transcribe audio to MIDI using basic-pitch."""
    try:
        from basic_pitch.inference import predict_and_save
    except ImportError:
        return {"error": "basic-pitch not installed. Run: pip install basic-pitch"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    # Determine output path
    if output_path:
        output_path = Path(output_path)
    else:
        output_path = input_path.parent

    output_dir = output_path if output_path.is_dir() else output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Run prediction and save
        predict_and_save(
            [str(input_path)],
            str(output_dir),
            save_midi=True,
            save_notes=False,
            save_model_outputs=False,
            sonify_midi=False,
        )

        # Find the generated MIDI file
        midi_file = output_dir / f"{input_path.stem}_basic_pitch.mid"

        if midi_file.exists():
            return {
                "success": True,
                "input": str(input_path),
                "output": str(midi_file),
                "message": f"Transcribed to {midi_file}"
            }
        else:
            return {"error": "MIDI file was not generated"}

    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio to MIDI")
    parser.add_argument("input", help="Input audio file (mp3, wav, flac, etc.)")
    parser.add_argument("output", nargs="?", help="Output MIDI file or directory")
    args = parser.parse_args()

    print(f"Transcribing: {args.input}")
    print("This may take a moment...")

    result = transcribe_audio(args.input, args.output)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    print(f"Success! MIDI saved to: {result['output']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
