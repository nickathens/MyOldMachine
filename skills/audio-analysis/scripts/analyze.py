#!/usr/bin/env python3
"""
Audio Analysis - BPM, key, waveform, spectrum analysis.

Usage:
    python analyze.py input.mp3              # Full analysis
    python analyze.py input.mp3 --bpm        # Just BPM
    python analyze.py input.mp3 --key        # Just key detection
    python analyze.py input.mp3 --waveform   # Generate waveform image
    python analyze.py input.mp3 --spectrum   # Generate spectrum image
"""

import argparse
import json
import sys
from pathlib import Path


def analyze_audio(input_path: str, output_dir: str = None) -> dict:
    """Perform full audio analysis."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        return {"error": "librosa not installed. Run: pip install librosa"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = input_path.parent

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Load audio
        y, sr = librosa.load(str(input_path), sr=None)
        duration = librosa.get_duration(y=y, sr=sr)

        # BPM detection
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo) if isinstance(tempo, (int, float, np.floating)) else float(tempo[0])

        # Key detection using chroma features
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)

        # Map to key names
        key_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        estimated_key_idx = np.argmax(chroma_mean)
        estimated_key = key_names[estimated_key_idx]

        # Determine major/minor using simple heuristic
        # Check relative minor/major strength
        minor_idx = (estimated_key_idx + 9) % 12
        if chroma_mean[minor_idx] > chroma_mean[estimated_key_idx] * 0.9:
            mode = "minor"
            estimated_key = key_names[minor_idx] + "m"
        else:
            mode = "major"

        # Loudness (RMS)
        rms = librosa.feature.rms(y=y)
        avg_loudness = float(np.mean(rms))

        # Spectral centroid (brightness)
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
        avg_brightness = float(np.mean(spectral_centroid))

        return {
            "success": True,
            "file": str(input_path),
            "duration_seconds": round(duration, 2),
            "duration_formatted": f"{int(duration // 60)}:{int(duration % 60):02d}",
            "sample_rate": sr,
            "bpm": round(bpm, 1),
            "key": estimated_key,
            "avg_loudness_rms": round(avg_loudness, 4),
            "brightness_hz": round(avg_brightness, 1),
        }

    except Exception as e:
        return {"error": str(e)}


def generate_waveform(input_path: str, output_path: str = None) -> dict:
    """Generate waveform visualization."""
    try:
        import librosa
        import librosa.display
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"error": "Required packages not installed"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    if output_path:
        output_path = Path(output_path)
    else:
        output_path = input_path.with_suffix('.waveform.png')

    try:
        y, sr = librosa.load(str(input_path), sr=None)

        plt.figure(figsize=(14, 4))
        plt.subplot(1, 1, 1)
        librosa.display.waveshow(y, sr=sr, alpha=0.8)
        plt.title(f'Waveform: {input_path.name}')
        plt.xlabel('Time (s)')
        plt.ylabel('Amplitude')
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        return {"success": True, "output": str(output_path)}
    except Exception as e:
        return {"error": str(e)}


def generate_spectrum(input_path: str, output_path: str = None) -> dict:
    """Generate spectrogram visualization."""
    try:
        import librosa
        import librosa.display
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {"error": "Required packages not installed"}

    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}"}

    if output_path:
        output_path = Path(output_path)
    else:
        output_path = input_path.with_suffix('.spectrum.png')

    try:
        y, sr = librosa.load(str(input_path), sr=None)

        plt.figure(figsize=(14, 6))

        # Mel spectrogram
        S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128)
        S_dB = librosa.power_to_db(S, ref=np.max)

        librosa.display.specshow(S_dB, x_axis='time', y_axis='mel', sr=sr, fmax=8000)
        plt.colorbar(format='%+2.0f dB')
        plt.title(f'Mel Spectrogram: {input_path.name}')
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()

        return {"success": True, "output": str(output_path)}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Analyze audio files")
    parser.add_argument("input", help="Input audio file")
    parser.add_argument("--bpm", action="store_true", help="Show only BPM")
    parser.add_argument("--key", action="store_true", help="Show only key")
    parser.add_argument("--waveform", action="store_true", help="Generate waveform image")
    parser.add_argument("--spectrum", action="store_true", help="Generate spectrogram image")
    parser.add_argument("--output", "-o", help="Output directory for images")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    results = {}

    # Generate visualizations if requested
    if args.waveform:
        result = generate_waveform(args.input, args.output)
        if "error" in result:
            print(f"Waveform error: {result['error']}")
        else:
            print(f"Waveform saved: {result['output']}")
            results["waveform"] = result["output"]

    if args.spectrum:
        result = generate_spectrum(args.input, args.output)
        if "error" in result:
            print(f"Spectrum error: {result['error']}")
        else:
            print(f"Spectrum saved: {result['output']}")
            results["spectrum"] = result["output"]

    # Run analysis
    if not (args.waveform or args.spectrum) or args.bpm or args.key:
        analysis = analyze_audio(args.input)

        if "error" in analysis:
            print(f"Error: {analysis['error']}")
            return 1

        if args.json:
            print(json.dumps(analysis, indent=2))
        elif args.bpm:
            print(f"BPM: {analysis['bpm']}")
        elif args.key:
            print(f"Key: {analysis['key']}")
        else:
            print(f"File: {analysis['file']}")
            print(f"Duration: {analysis['duration_formatted']} ({analysis['duration_seconds']}s)")
            print(f"Sample Rate: {analysis['sample_rate']} Hz")
            print(f"BPM: {analysis['bpm']}")
            print(f"Key: {analysis['key']}")
            print(f"Brightness: {analysis['brightness_hz']} Hz")

        results.update(analysis)

    return 0


if __name__ == "__main__":
    sys.exit(main())
