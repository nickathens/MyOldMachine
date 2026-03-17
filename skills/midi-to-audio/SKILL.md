# MIDI to Audio

Render MIDI files to audio (WAV/MP3) using FluidSynth with real instrument soundfonts.

## Usage

```bash
# Convert MIDI to WAV
python skills/midi-to-audio/scripts/render.py input.mid

# Convert to MP3
python skills/midi-to-audio/scripts/render.py input.mid --format mp3

# Specify output file
python skills/midi-to-audio/scripts/render.py input.mid --output rendered.wav

# Use custom soundfont
python skills/midi-to-audio/scripts/render.py input.mid --soundfont /path/to/soundfont.sf2

# Adjust gain (volume)
python skills/midi-to-audio/scripts/render.py input.mid --gain 1.5
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

- Uses FluidSynth with General MIDI soundfont
- Output quality depends on soundfont
- Rendering is fast (real-time or faster)
- MP3 conversion requires ffmpeg
