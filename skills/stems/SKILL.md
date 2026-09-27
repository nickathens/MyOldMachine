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

## Examples

"Separate the stems from this song" + audio file
"Extract the vocals from this track"
"Isolate the drums"
"Remove vocals from this song" (use the 'other' stem)

## Notes

- `--device auto` (the default) tries one CUDA operation first and uses the GPU only if it really runs: a torch build without kernels for an older card (a GTX 970) reports CUDA as available and then fails every kernel. Before 2026-09-27 every separation on such a machine crashed on that.
- Measured on a 4 core i5 (htdemucs, CPU): 5 minutes of stereo audio took 3.5 minutes and peaked at 2.3 GB of RAM. On Linux, run anything longer than a song in a capped scope so it can never take the bot down with it: `systemd-run --user --scope --quiet --collect -p MemoryMax=4G -p MemorySwapMax=0 -- python .../separate.py ...`
- The first run downloads the model (htdemucs is about 80 MB) into `~/.cache/torch/hub/checkpoints/`
- Stems are written as 16-bit WAV through soundfile: demucs 4.0.1 saves through torchaudio, and torchaudio 2.9+ needs a torchcodec package that is not installed, so the separation used to finish and then fail writing the files
- Works best with studio-quality recordings
