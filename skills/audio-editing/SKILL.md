# Audio Editing

Edit audio files: cut, merge, fade, convert formats, adjust volume.

## Script

`scripts/edit.py` in this skill directory. Run with `python <path-to-script> <command> [args]`.

## Commands

```bash
# Cut a segment (start and end in seconds)
python scripts/edit.py cut input.mp3 output.mp3 --start 10 --end 30

# Merge multiple files
python scripts/edit.py merge file1.mp3 file2.mp3 -o combined.mp3

# Crossfade merge (overlap in ms)
python scripts/edit.py merge file1.mp3 file2.mp3 -o combined.mp3 --crossfade 2000

# Add fade in/out (duration in ms)
python scripts/edit.py fade input.mp3 output.mp3 --fade-in 1000 --fade-out 2000

# Adjust volume (dB, positive=louder, negative=quieter)
python scripts/edit.py volume input.mp3 output.mp3 --db 6

# Normalize to target dBFS
python scripts/edit.py normalize input.mp3 output.mp3 --target -14

# Convert format
python scripts/edit.py convert input.wav output.mp3 --bitrate 320k

# Get audio info
python scripts/edit.py info input.mp3
```

## Supported Formats

mp3, wav, flac, ogg, m4a, aac (requires ffmpeg)

## Notes

- Uses pydub with ffmpeg backend
- Crossfade creates smooth transitions between merged tracks
- Normalize is useful for podcast/streaming loudness standards
