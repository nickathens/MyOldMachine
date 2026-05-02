#!/usr/bin/env python3
"""
MIDI manipulation tool using mido.
"""

import argparse
import json
import sys

import mido


def cmd_info(args):
    """Get MIDI file info."""
    mid = mido.MidiFile(args.input)

    # Count notes and find pitch range
    note_count = 0
    min_note = 127
    max_note = 0
    tracks_info = []

    for i, track in enumerate(mid.tracks):
        track_notes = 0
        track_name = None
        for msg in track:
            if msg.type == 'track_name':
                track_name = msg.name
            if msg.type == 'note_on' and msg.velocity > 0:
                track_notes += 1
                note_count += 1
                min_note = min(min_note, msg.note)
                max_note = max(max_note, msg.note)
        tracks_info.append({
            "track": i,
            "name": track_name,
            "notes": track_notes,
            "events": len(track)
        })

    # Get tempo
    tempo = 500000  # default 120 BPM
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break

    bpm = round(mido.tempo2bpm(tempo), 1)

    def note_name(n):
        names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        return f"{names[n % 12]}{n // 12 - 1}"

    info = {
        "type": mid.type,
        "ticks_per_beat": mid.ticks_per_beat,
        "duration_seconds": round(mid.length, 2),
        "tempo_bpm": bpm,
        "total_notes": note_count,
        "pitch_range": f"{note_name(min_note)} - {note_name(max_note)}" if note_count else "N/A",
        "tracks": tracks_info
    }
    print(json.dumps(info, indent=2))


def cmd_transpose(args):
    """Transpose all notes by semitones."""
    mid = mido.MidiFile(args.input)

    for track in mid.tracks:
        for msg in track:
            if msg.type in ('note_on', 'note_off'):
                new_note = msg.note + args.semitones
                if 0 <= new_note <= 127:
                    msg.note = new_note

    mid.save(args.output)
    print(f"Transposed by {args.semitones:+d} semitones -> {args.output}")


def cmd_tempo(args):
    """Change tempo of MIDI file."""
    mid = mido.MidiFile(args.input)

    for track in mid.tracks:
        for i, msg in enumerate(track):
            if msg.type == 'set_tempo':
                if args.bpm:
                    new_tempo = mido.bpm2tempo(args.bpm)
                elif args.scale:
                    new_tempo = int(msg.tempo / args.scale)
                else:
                    continue
                track[i] = msg.copy(tempo=new_tempo)

    # If no tempo event found, add one
    if args.bpm and not any(msg.type == 'set_tempo' for track in mid.tracks for msg in track):
        mid.tracks[0].insert(0, mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(args.bpm)))

    mid.save(args.output)
    if args.bpm:
        print(f"Set tempo to {args.bpm} BPM -> {args.output}")
    else:
        print(f"Scaled tempo by {args.scale}x -> {args.output}")


def cmd_merge(args):
    """Merge multiple MIDI files."""
    if len(args.files) < 2:
        print("Error: Need at least 2 files", file=sys.stderr)
        sys.exit(1)

    base = mido.MidiFile(args.files[0])

    for path in args.files[1:]:
        other = mido.MidiFile(path)
        for track in other.tracks:
            base.tracks.append(track)

    base.save(args.output)
    print(f"Merged {len(args.files)} files -> {args.output}")


def cmd_extract(args):
    """Extract a specific track."""
    mid = mido.MidiFile(args.input)

    if args.track >= len(mid.tracks):
        print(f"Error: Track {args.track} not found (file has {len(mid.tracks)} tracks)", file=sys.stderr)
        sys.exit(1)

    new_mid = mido.MidiFile(type=0, ticks_per_beat=mid.ticks_per_beat)
    new_mid.tracks.append(mid.tracks[args.track])

    new_mid.save(args.output)
    print(f"Extracted track {args.track} -> {args.output}")


def cmd_notes(args):
    """List all notes in the file."""
    mid = mido.MidiFile(args.input)

    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

    notes = []

    for track_idx, track in enumerate(mid.tracks):
        time = 0
        for msg in track:
            time += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                note_name = f"{names[msg.note % 12]}{msg.note // 12 - 1}"
                notes.append({
                    "track": track_idx,
                    "time": time,
                    "note": msg.note,
                    "name": note_name,
                    "velocity": msg.velocity
                })

    # Sort by time
    notes.sort(key=lambda x: (x['time'], x['note']))

    if args.json:
        print(json.dumps(notes[:100], indent=2))  # Limit output
    else:
        print(f"{'Time':>8} {'Track':>5} {'Note':>6} {'Vel':>4}")
        print("-" * 28)
        for n in notes[:50]:
            print(f"{n['time']:>8} {n['track']:>5} {n['name']:>6} {n['velocity']:>4}")
        if len(notes) > 50:
            print(f"... and {len(notes) - 50} more notes")


def cmd_quantize(args):
    """Quantize note timing to grid."""
    mid = mido.MidiFile(args.input)
    grid = mid.ticks_per_beat // args.grid

    for track in mid.tracks:
        for msg in track:
            if msg.type in ('note_on', 'note_off'):
                msg.time = round(msg.time / grid) * grid

    mid.save(args.output)
    print(f"Quantized to 1/{args.grid} notes -> {args.output}")


def main():
    parser = argparse.ArgumentParser(description="MIDI editing tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Info
    p = subparsers.add_parser("info", help="Get MIDI info")
    p.add_argument("input", help="Input MIDI file")
    p.set_defaults(func=cmd_info)

    # Transpose
    p = subparsers.add_parser("transpose", help="Transpose notes")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--semitones", "-s", type=int, required=True, help="Semitones (+/-)")
    p.set_defaults(func=cmd_transpose)

    # Tempo
    p = subparsers.add_parser("tempo", help="Change tempo")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--bpm", type=float, help="Set BPM")
    p.add_argument("--scale", type=float, help="Scale tempo (0.5 = half speed)")
    p.set_defaults(func=cmd_tempo)

    # Merge
    p = subparsers.add_parser("merge", help="Merge MIDI files")
    p.add_argument("files", nargs="+", help="Files to merge")
    p.add_argument("-o", "--output", required=True, help="Output file")
    p.set_defaults(func=cmd_merge)

    # Extract
    p = subparsers.add_parser("extract", help="Extract track")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--track", "-t", type=int, required=True, help="Track number")
    p.set_defaults(func=cmd_extract)

    # Notes
    p = subparsers.add_parser("notes", help="List notes")
    p.add_argument("input", help="Input file")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.set_defaults(func=cmd_notes)

    # Quantize
    p = subparsers.add_parser("quantize", help="Quantize timing")
    p.add_argument("input", help="Input file")
    p.add_argument("output", help="Output file")
    p.add_argument("--grid", "-g", type=int, default=4, help="Grid division (4=quarter, 8=eighth, 16=sixteenth)")
    p.set_defaults(func=cmd_quantize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
