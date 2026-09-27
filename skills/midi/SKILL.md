# MIDI Editing

Read, analyze, and manipulate MIDI files: transpose, change tempo, merge, extract info.

## Usage

```bash
# Get MIDI file info
python skills/midi/scripts/midi_tool.py info input.mid

# Transpose (semitones, positive=up, negative=down)
python skills/midi/scripts/midi_tool.py transpose input.mid output.mid --semitones 5

# Change tempo (BPM)
python skills/midi/scripts/midi_tool.py tempo input.mid output.mid --bpm 120

# Scale tempo (multiply current tempo)
python skills/midi/scripts/midi_tool.py tempo input.mid output.mid --scale 0.5

# Merge multiple MIDI files (layered)
python skills/midi/scripts/midi_tool.py merge file1.mid file2.mid -o combined.mid

# Extract specific track
python skills/midi/scripts/midi_tool.py extract input.mid output.mid --track 0

# List all notes (for analysis)
python skills/midi/scripts/midi_tool.py notes input.mid

# Quantize to grid (ticks per beat division)
python skills/midi/scripts/midi_tool.py quantize input.mid output.mid --grid 4
```

## Examples

"transpose this MIDI up 5 semitones" + midi file
"what's in this MIDI file?" + midi file
"slow this MIDI down by half"
"merge these MIDI files" + 2 files

## Notes

- Uses mido library for MIDI operations
- Transpose moves every note except channel 10 (General MIDI drums, where a note number is an instrument; `--include-drums` moves them too). A note pushed outside 0..127 is dropped and counted, and the key signature follows
- `--bpm` sets the opening tempo and keeps later tempo changes in proportion (120 then 60, set to 90, becomes 90 then 45); `--scale` multiplies them all. Give one of the two
- `info` reports the opening tempo plus `tempo_changes` and `tempo_range_bpm`
- `notes` shows the tick, the seconds (through the tempo map) and the channel; the table stops at 50 notes and JSON carries all of them, unless `--limit N` (0 = all)
- Merge layers tracks from multiple files into a type 1 file (two single track files used to fail), rescales resolution, and notes when a later file brings its own tempo events
- Track numbers are 0-indexed
- Bad values and unreadable files print `Error:` and exit 1
