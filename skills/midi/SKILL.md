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
- Transpose affects all note events
- Tempo changes adjust tempo map events
- Merge layers tracks from multiple files
- Track numbers are 0-indexed
