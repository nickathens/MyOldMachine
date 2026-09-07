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


DEFAULT_TEMPO = 500000  # microseconds per beat, 120 BPM, the MIDI default when no event is present


def cmd_tempo(args):
    """Change tempo of MIDI file."""
    mid = mido.MidiFile(args.input)
    if not args.bpm and not args.scale:
        print("Error: give --bpm or --scale", file=sys.stderr)
        sys.exit(1)

    found = False
    for track in mid.tracks:
        for i, msg in enumerate(track):
            if msg.type == 'set_tempo':
                found = True
                if args.bpm:
                    new_tempo = mido.bpm2tempo(args.bpm)
                else:
                    new_tempo = int(msg.tempo / args.scale)
                track[i] = msg.copy(tempo=new_tempo)

    # No tempo event: the file plays at the implicit 120 BPM default, so
    # scaling means writing that default scaled (audit F21, 2026-09-06: a
    # --scale on such a file changed nothing).
    if not found:
        tempo = mido.bpm2tempo(args.bpm) if args.bpm else int(DEFAULT_TEMPO / args.scale)
        mid.tracks[0].insert(0, mido.MetaMessage('set_tempo', tempo=tempo, time=0))

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
        # Delta times are in the OTHER file's ticks per beat. Appended as-is
        # into a file with a different resolution they play at the wrong
        # speed (audit F21, 2026-09-06: 960 tpb notes landed at double beat
        # positions in a 480 tpb file). Rescale on absolute ticks so rounding
        # cannot drift along the track.
        for track in other.tracks:
            if other.ticks_per_beat != base.ticks_per_beat:
                track = _rescale_track(track, other.ticks_per_beat, base.ticks_per_beat)
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
    track = mid.tracks[args.track]
    # A type 1 file keeps tempo (and time signature) on the conductor track.
    # Extracting another track alone dropped them, so the result played at
    # 120 BPM whatever the piece was (audit F21, 2026-09-06). Carry the
    # conductor's tempo map over, at its absolute times, unless the track
    # already has its own.
    if args.track != 0 and not any(m.type == 'set_tempo' for m in track):
        track = _merge_conductor(mid.tracks[0], track)
    new_mid.tracks.append(track)

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


def _to_absolute(track):
    """[(abs_tick, msg)] for a track's delta-time messages."""
    out, t = [], 0
    for msg in track:
        t += msg.time
        out.append((t, msg))
    return out


def _from_absolute(events):
    """A MidiTrack from [(abs_tick, msg)], sorted by time (stable), end_of_track last."""
    events = sorted(events, key=lambda e: (e[0], e[1].type == 'end_of_track'))
    track = mido.MidiTrack()
    prev = 0
    for t, msg in events:
        track.append(msg.copy(time=t - prev))
        prev = t
    return track


def _rescale_track(track, src_tpb, dst_tpb):
    return _from_absolute([(round(t * dst_tpb / src_tpb), msg) for t, msg in _to_absolute(track)])


def _merge_conductor(conductor, track):
    meta = [(t, m) for t, m in _to_absolute(conductor)
            if m.type in ('set_tempo', 'time_signature', 'key_signature')]
    body = [(t, m) for t, m in _to_absolute(track) if m.type != 'end_of_track']
    end = [(t, m) for t, m in _to_absolute(track) if m.type == 'end_of_track']
    return _from_absolute(meta + body + end)


def cmd_quantize(args):
    """Quantize note timing to grid."""
    mid = mido.MidiFile(args.input)
    # The grid is 1/args.grid of a whole note: --grid 4 is a quarter note
    # (one beat), --grid 8 an eighth. Quantise ABSOLUTE positions, not the
    # delta between events: rounding each delta moved a note at tick 230 to
    # 240 instead of 0 and let the error walk down the track (audit F21).
    grid = max(1, round(mid.ticks_per_beat * 4 / args.grid))

    # Only ONSETS snap. A note's release moves by the same amount as its
    # onset so it keeps its length: snapping both ends collapsed any note
    # shorter than half a grid step to zero length, silent (eight eighth
    # notes on a quarter grid lost four of them: review of #156).
    for i, track in enumerate(mid.tracks):
        events = []
        open_shift = {}  # (channel, note) -> [shift, ...] for sounding notes
        for t, msg in _to_absolute(track):
            is_on = msg.type == 'note_on' and msg.velocity > 0
            is_off = msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)
            if is_on:
                shift = round(t / grid) * grid - t
                open_shift.setdefault((msg.channel, msg.note), []).append(shift)
                t += shift
            elif is_off:
                stack = open_shift.get((msg.channel, msg.note))
                if stack:
                    t += stack.pop(0)
                else:  # a release with no onset in this track: snap it alone
                    t = round(t / grid) * grid
            events.append((t, msg))
        mid.tracks[i] = _from_absolute(events)

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
