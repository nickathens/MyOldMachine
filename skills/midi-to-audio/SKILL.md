# MIDI to Audio

Render MIDI files to audio (WAV/MP3) using FluidSynth with real instrument soundfonts.

## Usage

```bash
# Convert MIDI to WAV
python skills/midi-to-audio/scripts/render.py input.mid

# Convert to MP3 (or flac, ogg, m4a)
python skills/midi-to-audio/scripts/render.py input.mid --format mp3

# Specify output file: its extension sets the format
python skills/midi-to-audio/scripts/render.py input.mid --output rendered.mp3

# Use custom soundfont
python skills/midi-to-audio/scripts/render.py input.mid --soundfont /path/to/soundfont.sf2

# Peak level after normalising (default -1 dBFS), or keep FluidSynth's own level
python skills/midi-to-audio/scripts/render.py input.mid --peak -3
python skills/midi-to-audio/scripts/render.py input.mid --no-normalize --gain 0.3
```

## Soundfonts

Default: FluidR3_GM (General MIDI) - installed at `/usr/share/sounds/sf2/FluidR3_GM.sf2`

For better quality, you can download additional soundfonts (.sf2 files).

## Full Pipeline

Combine with other skills for complete workflows:

```bash
# Audio -> MIDI -> Edit -> Audio
python skills/audio-to-midi/scripts/audio2midi.py recording.mp3
python skills/midi/scripts/midi_tool.py transpose recording_basic_pitch.mid transposed.mid --semitones 5
python skills/midi-to-audio/scripts/render.py transposed.mid --format mp3
```

## Notes

- Uses FluidSynth with General MIDI soundfont; output quality depends on the soundfont
- FluidSynth renders in 32-bit float, the result is peak-normalised to -1 dBFS and written as 24-bit WAV/FLAC or the lossy format asked for. Before 2026-09-27 the default gain 1.0 (five times FluidSynth's own) clipped dense chords, and `--output x.mp3` without `--format mp3` wrote a WAV file under the .mp3 name
- Release and reverb tails are kept (FluidSynth plays past the last note until the voices die)
- Rendering is fast (real-time or faster)
- MP3 conversion requires ffmpeg
