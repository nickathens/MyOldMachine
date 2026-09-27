#!/usr/bin/env python3
"""
MIDI to Sheet Music - Convert MIDI files to PDF sheet music using LilyPond.

Usage:
    python midi2sheet.py input.mid                # Generate PDF
    python midi2sheet.py input.mid --png          # Generate PNG instead
    python midi2sheet.py input.mid --title "My Song"
    python midi2sheet.py input.mid --grid 8       # coarser tidy for loose playing; --grid 0 for none

Played MIDI (and anything from audio-to-midi) sits a little off the grid.
Handed to midi2ly as it is, a plain scale came out as notes tied to 128ths
with slivers of rest between them (Linux bot review 2026-09-27). Before engraving,
onsets are snapped to the grid and a note runs on to the next onset when the
gap is shorter than one grid step, the way notation programs tidy a take.
"""

import argparse
import bisect
import subprocess
import sys
import tempfile
from pathlib import Path


def tidy_midi(midi_path: str, out_path: str, division: int) -> None:
    """Snap onsets to 1/division notes and close small gaps (legato), per track."""
    import mido

    mid = mido.MidiFile(midi_path)
    grid = max(1, round(mid.ticks_per_beat * 4 / division))
    for index, track in enumerate(mid.tracks):
        t, events, notes, sounding = 0, [], [], {}
        for msg in track:
            t += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                sounding.setdefault((msg.channel, msg.note), []).append((t, msg.velocity))
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                started = sounding.get((msg.channel, msg.note))
                if started:
                    start, velocity = started.pop(0)
                    notes.append([start, t, msg.channel, msg.note, velocity])
            elif msg.type != 'end_of_track':
                events.append((t, 1, msg))
        for note in notes:
            note[0] = round(note[0] / grid) * grid
        onsets = sorted({note[0] for note in notes})
        for note in notes:
            later = bisect.bisect_right(onsets, note[0])
            nxt = onsets[later] if later < len(onsets) else None
            end = round(note[1] / grid) * grid
            if nxt is not None and nxt - note[1] < grid:
                end = nxt
            note[1] = max(end, note[0] + grid)
            # offs sort before ons at the same tick, so repeated notes do not collide
            events.append((note[0], 2, mido.Message('note_on', channel=note[2], note=note[3], velocity=note[4])))
            events.append((note[1], 0, mido.Message('note_off', channel=note[2], note=note[3], velocity=0)))
        events.sort(key=lambda e: (e[0], e[1]))
        rebuilt, last = mido.MidiTrack(), 0
        for when, _, msg in events:
            rebuilt.append(msg.copy(time=when - last))
            last = when
        rebuilt.append(mido.MetaMessage('end_of_track', time=0))
        mid.tracks[index] = rebuilt
    mid.save(out_path)


def detect_key(midi_path: str):
    """midi2ly's --key value (sharps, :1 for minor) from music21's analysis, or None."""
    try:
        from music21 import converter
        key = converter.parse(midi_path).analyze('key')
        return f"{key.sharps}{':1' if key.mode == 'minor' else ''}"
    except Exception:
        return None


def midi_to_lilypond(midi_path: str, division: int = 16, key: str = None) -> str:
    """Convert MIDI to LilyPond format using midi2ly."""
    cmd = ["midi2ly", str(midi_path), "--output=-"]
    if division:
        cmd += [f"--start-quant={division}", f"--duration-quant={division}"]
    if key:
        cmd.append(f"--key={key}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode != 0:
        raise Exception(f"midi2ly failed: {result.stderr}")

    return result.stdout


def render_lilypond(ly_content: str, output_path: str, title: str = None, png: bool = False) -> dict:
    """Render LilyPond content to PDF or PNG."""
    output_path = Path(output_path)

    # Modify content to add title if specified
    if title:
        safe = title.replace('\\', '\\\\').replace('"', '\\"')
        header = f'''
\\header {{
  title = "{safe}"
}}
'''
        # Insert header after \version line
        lines = ly_content.split('\n')
        for i, line in enumerate(lines):
            if line.strip().startswith('\\version'):
                lines.insert(i + 1, header)
                break
        ly_content = '\n'.join(lines)

    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ly', delete=False) as f:
        f.write(ly_content)
        ly_file = f.name

    try:
        # Determine output format
        if png:
            fmt_args = ["--png", "-dresolution=150"]
            expected_ext = ".png"
        else:
            fmt_args = ["--pdf"]
            expected_ext = ".pdf"

        # Run lilypond
        cmd = [
            "lilypond",
            *fmt_args,
            f"--output={output_path.with_suffix('')}",
            ly_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            return {"error": f"LilyPond failed: {result.stderr}"}

        output_file = output_path.with_suffix(expected_ext)
        if output_file.exists():
            return {"success": True, "output": str(output_file)}
        # a PNG score longer than a page comes out as name-page1.png, name-page2.png ...
        pages = sorted(output_path.parent.glob(f"{output_path.stem}-page*{expected_ext}"),
                       key=lambda p: int(p.stem.rsplit("page", 1)[-1] or 0))
        if pages:
            return {"success": True, "output": ", ".join(str(p) for p in pages)}
        return {"error": "Output file not generated"}

    finally:
        Path(ly_file).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Convert MIDI to sheet music")
    parser.add_argument("input", help="Input MIDI file")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--title", "-t", help="Sheet music title")
    parser.add_argument("--png", action="store_true", help="Output as PNG instead of PDF")
    parser.add_argument("--grid", type=int, default=16,
                        help="Tidy the timing to 1/N notes before engraving (default 16; 8 for loose playing; 0 = off)")
    parser.add_argument("--key", help="Key for midi2ly: sharps (negative for flats), :1 for minor, e.g. 2 or -3:1 "
                                      "(default: detected)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        ext = ".png" if args.png else ".pdf"
        output_path = input_path.with_suffix(ext)

    print(f"Converting: {input_path}")
    print("Step 1: Converting MIDI to LilyPond notation...")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            source = str(input_path)
            if args.grid:
                source = str(Path(tmp) / "tidy.mid")
                tidy_midi(str(input_path), source, args.grid)
            key = args.key or detect_key(source)
            ly_content = midi_to_lilypond(source, args.grid, key)
    except Exception as e:
        print(f"Error: {e}")
        return 1

    print("Step 2: Rendering sheet music...")

    result = render_lilypond(ly_content, str(output_path), args.title, args.png)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    print(f"Success! Sheet music saved to: {result['output']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
