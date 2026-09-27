"""midi tool fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('mido',) if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import mido

TOOL = Path(__file__).resolve().parent.parent / "skills" / "midi" / "scripts" / "midi_tool.py"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True, timeout=60)


def single_track(path, notes, channel=0, key=None):
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    if key:
        tr.append(mido.MetaMessage("key_signature", key=key, time=0))
    for n in notes:
        tr.append(mido.Message("note_on", note=n, velocity=90, channel=channel, time=0))
        tr.append(mido.Message("note_off", note=n, velocity=0, channel=channel, time=480))
    mid.save(path)


def onsets(path):
    return [m.note for tr in mido.MidiFile(path).tracks for m in tr if m.type == "note_on" and m.velocity > 0]


class MidiTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merging_two_single_track_files_works(self):
        single_track(self.d / "a.mid", [60, 62])
        single_track(self.d / "b.mid", [67])
        r = run("merge", self.d / "a.mid", self.d / "b.mid", "-o", self.d / "ab.mid")
        self.assertEqual(r.returncode, 0, r.stderr)
        merged = mido.MidiFile(self.d / "ab.mid")
        self.assertEqual((merged.type, len(merged.tracks)), (1, 2))

    def test_transpose_leaves_the_drums_alone(self):
        single_track(self.d / "drums.mid", [36, 38, 42], channel=9)
        r = run("transpose", self.d / "drums.mid", self.d / "d.mid", "-s", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(onsets(self.d / "d.mid"), [36, 38, 42], "kick, snare and hat became toms")

    def test_a_note_pushed_out_of_range_is_dropped_not_left_behind(self):
        single_track(self.d / "hi.mid", [120, 60])
        r = run("transpose", self.d / "hi.mid", self.d / "h.mid", "-s", "12")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(onsets(self.d / "h.mid"), [72])
        self.assertIn("dropped", r.stdout)

    def test_the_key_signature_follows(self):
        single_track(self.d / "k.mid", [57], key="Am")
        run("transpose", self.d / "k.mid", self.d / "k2.mid", "-s", "2")
        keys = [m.key for m in mido.MidiFile(self.d / "k2.mid").tracks[0] if m.type == "key_signature"]
        self.assertEqual(keys, ["Bm"])

    def test_json_notes_are_all_there(self):
        single_track(self.d / "many.mid", [30 + i % 90 for i in range(124)])
        r = run("notes", self.d / "many.mid", "--json")
        self.assertEqual(len(json.loads(r.stdout)), 124, "JSON stopped at 100 without a word")

    def test_bpm_keeps_a_tempo_map_in_proportion(self):
        mid = mido.MidiFile(type=1, ticks_per_beat=480)
        conductor = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0),
                                    mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(60), time=1920)])
        mid.tracks.append(conductor)
        mid.save(self.d / "map.mid")
        r = run("tempo", self.d / "map.mid", self.d / "m.mid", "--bpm", "90")
        self.assertEqual(r.returncode, 0, r.stderr)
        bpms = [round(mido.tempo2bpm(m.tempo), 1) for m in mido.MidiFile(self.d / "m.mid").tracks[0]
                if m.type == "set_tempo"]
        self.assertEqual(bpms, [90.0, 45.0], "the ritardando was flattened")

    def test_bad_values_are_errors_not_tracebacks(self):
        single_track(self.d / "a.mid", [60])
        for args in (("quantize", self.d / "a.mid", self.d / "q.mid", "--grid", "0"),
                     ("tempo", self.d / "a.mid", self.d / "t.mid", "--scale", "-1"),
                     ("extract", self.d / "a.mid", self.d / "e.mid", "--track", "-1")):
            r = run(*args)
            self.assertEqual(r.returncode, 1, args)
            self.assertNotIn("Traceback", r.stderr, args)
            self.assertIn("Error:", r.stderr, args)


if __name__ == "__main__":
    unittest.main()
