# Stem Separation

Separate audio into individual stems (vocals, drums, bass, other) using Meta's Demucs AI.

## Usage

```bash
# Separate into stems (creates folder with 4 wav files)
python skills/stems/scripts/separate.py input.mp3

# Specify output directory
python skills/stems/scripts/separate.py input.mp3 --output ./my_stems

# Use different model (htdemucs_ft is more accurate but slower)
python skills/stems/scripts/separate.py input.mp3 --model htdemucs_ft
```

## Output

Creates a folder with:
- `vocals.wav` - Isolated vocals
- `drums.wav` - Drums and percussion
- `bass.wav` - Bass instruments
- `other.wav` - Everything else (guitars, synths, etc.)

## Models

| Model | Quality | Speed |
|-------|---------|-------|
| htdemucs | Good | Faster |
| htdemucs_ft | Better | Slower |
| mdx_extra | Best | Slowest |

## Notes

- Processing takes 5-15 minutes per song (CPU)
- First run downloads model (~1GB)
- Works best with studio-quality recordings
- Output is always WAV format (high quality)
