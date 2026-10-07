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

# Images into a folder (default: beside the input), named <input>.waveform.png and <input>.spectrum.png
python scripts/analyze.py input.mp3 --waveform --spectrum --output /tmp/plots

# A section of a long file: from 5:00, four minutes
python scripts/analyze.py mix.mp3 --start 300 --duration 240
```

Memory grows with length (each feature computes a full-length STFT or CQT):
a 34 minute album needs more than 6 GB for the plain analysis and about
3 GB for a spectrogram. On Linux the script runs itself inside its own
memory-capped scope (6 GB, `AUDIO_ANALYSIS_MEM_MAX`), outside the bot's
service, so a file that is too long fails alone with "killed ... by the 6G
memory cap: analyse a section with --start and --duration". On Linux where no
scope can be made, files over ten minutes need `--duration`; macOS runs as
before.

## Output

Full analysis includes:
- **Duration** - Length in seconds and mm:ss format
- **BPM** - Beats per minute (tempo)
- **Key** - Musical key (e.g., "C", "Am", "F#")
- **Sample Rate** - Audio sample rate in Hz
- **Brightness** - Spectral centroid (higher = brighter sound)

## Notes

- BPM detection works best on rhythmic music
- Key detection correlates the average chroma with the 24 Krumhansl-Kessler key profiles; `key_confidence` in the JSON is that correlation. It is still approximate: a relative major and minor (C and Am) share every note, so modal or ambiguous material can land on either. Works best on tonal music
- Waveform/spectrum images are saved as PNG
