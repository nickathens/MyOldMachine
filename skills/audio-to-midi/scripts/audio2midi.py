#!/usr/bin/env python3
"""
Audio to MIDI Transcription using Spotify's Basic Pitch.

Usage:
    python audio2midi.py input.mp3 output.mid
    python audio2midi.py input.wav  # outputs input_basic_pitch.mid

Supports: mp3, wav, flac, ogg, m4a
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path


def _restore_scipy_gaussian() -> None:
    """basic-pitch 0.3.0 calls scipy.signal.gaussian, removed in SciPy 1.13.

    The function only moved to scipy.signal.windows and kept its signature, so
    alias it back instead of pinning the whole environment to an old SciPy.
    """
    import scipy.signal

    if not hasattr(scipy.signal, "gaussian") and hasattr(scipy.signal, "windows"):
        scipy.signal.gaussian = scipy.signal.windows.gaussian


def _model_candidates() -> list:
    """Bundled model builds to try, most likely to load first.

    basic-pitch defaults to its TensorFlow SavedModel whenever TensorFlow is
    importable, but TF 2.16 dropped support for the format that model was saved
    in, so on a current TensorFlow the default cannot be loaded at all. The ONNX
    build ships in the same wheel, so prefer it when onnxruntime is installed.
    """
    from basic_pitch import (ICASSP_2022_MODEL_PATH, FilenameSuffix,
                             build_icassp_2022_model_path)

    candidates = []
    try:
        import onnxruntime  # noqa: F401
        candidates.append(build_icassp_2022_model_path(FilenameSuffix.onnx))
    except ImportError:
        pass
    if ICASSP_2022_MODEL_PATH not in candidates:
        candidates.append(ICASSP_2022_MODEL_PATH)
    return candidates


def transcribe_audio(input_path: str, output_path: str = None) -> dict:
    """Transcribe audio to MIDI using basic-pitch."""
    try:
        from basic_pitch.inference import predict_and_save
    except ImportError:
        return {"error": "basic-pitch not installed. Run: pip install basic-pitch"}

    _restore_scipy_gaussian()

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    # Determine output path
    if output_path:
        output_path = Path(output_path)
    else:
        output_path = input_path.parent

    # basic-pitch only ever writes <stem>_basic_pitch.mid into a directory, so an
    # output given as a filename has to be honoured by moving the result after.
    # A name without .mid/.midi is a folder, made if it is missing.
    if output_path.suffix.lower() in (".mid", ".midi"):
        output_dir, requested_file = output_path.parent, output_path
    else:
        output_dir, requested_file = output_path, None
    output_dir.mkdir(parents=True, exist_ok=True)
    final = requested_file or output_dir / f"{input_path.stem}_basic_pitch.mid"

    # model_or_model_path has been required since basic-pitch 0.3.0. Omitting it
    # raises TypeError before a single note is read, which is how this script had
    # been failing on every call.
    # basic-pitch refuses to overwrite its own earlier output (it raises
    # "already exists"), which failed every rerun, and the loop below read
    # that as a model that would not load. It writes into a fresh folder
    # beside the destination instead, and the result replaces the old file.
    last_error = "no basic-pitch model could be loaded"
    for model_path in _model_candidates():
        with tempfile.TemporaryDirectory(dir=output_dir, prefix=".basic_pitch_") as scratch:
            try:
                predict_and_save(
                    [str(input_path)],
                    scratch,
                    save_midi=True,
                    save_notes=False,
                    save_model_outputs=False,
                    sonify_midi=False,
                    model_or_model_path=model_path,
                )
            except Exception as e:
                last_error = str(e)
                continue

            midi_file = Path(scratch) / f"{input_path.stem}_basic_pitch.mid"
            if not midi_file.exists():
                return {"error": "MIDI file was not generated"}
            os.replace(midi_file, final)
        return {
            "success": True,
            "input": str(input_path),
            "output": str(final),
            "message": f"Transcribed to {final}"
        }

    return {"error": last_error}


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
