# Audio Analysis

Analyze audio files for BPM, key, loudness, and generate visualizations.

## Script

`scripts/analyze.py` in this skill directory.

## Commands

```bash
# Full analysis
python scripts/analyze.py input.mp3

# Just BPM
python scripts/analyze.py input.mp3 --bpm

# Just key
python scripts/analyze.py input.mp3 --key

# Generate waveform image
python scripts/analyze.py input.mp3 --waveform

# Generate spectrogram
python scripts/analyze.py input.mp3 --spectrum

# Output as JSON
python scripts/analyze.py input.mp3 --json
```

## Output

Full analysis includes:
- **Duration** - Length in seconds and mm:ss format
- **BPM** - Beats per minute (tempo)
- **Key** - Musical key (e.g., "C", "Am", "F#")
- **Sample Rate** - Audio sample rate in Hz
- **Brightness** - Spectral centroid (higher = brighter sound)

## Notes

- BPM detection works best on rhythmic music
- Key detection is approximate (works best on tonal music)
- Waveform/spectrum images are saved as PNG
