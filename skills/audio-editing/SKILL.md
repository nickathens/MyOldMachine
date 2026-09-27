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

# Normalize loudness to a LUFS target (EBU R128); peaks stay under --true-peak (default -1 dBTP)
python scripts/edit.py normalize input.mp3 output.mp3 --target -14

# Convert format
python scripts/edit.py convert input.wav output.mp3 --bitrate 320k

# Get audio info (codec, bitrate, peak, LUFS, true peak, loudness range)
python scripts/edit.py info input.mp3
```

## Supported Formats

mp3, wav, flac, ogg, m4a, aac (requires ffmpeg)

## Examples

"Cut 10 to 30 seconds from this track" + audio
"Merge these two songs together" + 2 files
"Add a 2 second fade out" + audio
"Make this louder by 6dB" + audio
"Convert this to mp3" + audio file
"Normalize this to -14 LUFS" + audio

## Notes

- Uses pydub with ffmpeg backend; loudness work (normalize, info) is ffmpeg's EBU R128 measurement
- normalize measures integrated loudness, then applies a plain gain when the peaks allow it, or limits them with loudnorm when they would pass the ceiling (and says so, including when the target could not be reached). Before 2026-09-27 it added (target minus RMS dBFS): the result was not LUFS (a "-16" landed at -16.75, a "-14" at -11.55) and transient material hard-clipped (920 clipped samples on a drum loop). Common targets: -14 LUFS streaming, -16 podcasts, -23 broadcast (EBU)
- volume warns when the gain would clip, with the amount
- Lossy exports default to 320k MP3, 256k AAC/Vorbis, 192k Opus (they used ffmpeg's 128k default, 64k for mono); `--bitrate` on convert overrides. WAV and FLAC keep 24 bits when the source had more than 16
- Crossfade creates smooth transitions between merged tracks
