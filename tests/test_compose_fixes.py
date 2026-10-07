"""algorithmic-composition fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('pretty_midi',) if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pretty_midi

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "algorithmic-composition" / "scripts" / "compose.py"
NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']


def load():
    spec = importlib.util.spec_from_file_location("compose_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chord_roots(midi, tempo=120):
    """{beat: lowest note name} for each chord onset."""
    chords = {}
    for n in midi.instruments[0].notes:
        beat = round(n.start / (60 / tempo), 2)
        chords[beat] = min(chords.get(beat, 128), n.pitch)
    return {beat: NAMES[p % 12] for beat, p in sorted(chords.items())}


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=120)


class Compose(unittest.TestCase):
    def test_minor_progressions_use_minor_scale_roots(self):
        compose = load()
        self.assertEqual(list(chord_roots(compose.generate_chord_progression('C', 'sad', bars=4)).values()),
                         ['C', 'Ab', 'Eb', 'Bb'])
        self.assertEqual(list(chord_roots(compose.generate_chord_progression('C', 'epic', bars=4)).values()),
                         ['C', 'Bb', 'Ab', 'Bb'])

    def test_every_chord_starts_on_a_bar_line(self):
        compose = load()
        beats = chord_roots(compose.generate_chord_progression('C', 'jazz_251', bars=8)).keys()
        self.assertTrue(all(b % 4 == 0 for b in beats), list(beats))

    def test_unknown_names_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("chords", "--progression", "jazz", "-o", Path(d) / "j.mid")
            self.assertNotEqual(r.returncode, 0, "jazz silently became pop")
            self.assertFalse((Path(d) / "j.mid").exists())

    def test_full_uses_the_style_it_is_given(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "f.mid"
            r = run("full", "--style", "jazz", "--bars", "4", "-o", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            drums = [i for i in pretty_midi.PrettyMIDI(str(out)).instruments if i.is_drum][0]
            self.assertIn(51, {n.pitch for n in drums.notes}, "the jazz ride never played")


class MelodyFollowsTheProgression(unittest.TestCase):
    """Linux bot sweep 2026-10-07: `full` took the melody's scale from --scale,
    whose default is major, whatever the progression: `full --progression
    sad` in C wrote a C major melody (E, A, B naturals) over Cm Ab Eb Bb."""

    def _melody_pcs(self, *extra):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "full.mid")
            r = run("full", "--root", "C", "--bars", "8", "--seed", "7", "-o", str(out), *extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            midi = pretty_midi.PrettyMIDI(str(out))
        melody = [i for i in midi.instruments if not i.is_drum][1]
        return {n.pitch % 12 for n in melody.notes}

    def test_a_minor_progression_gets_a_minor_melody(self):
        pcs = self._melody_pcs("--progression", "sad")
        self.assertFalse(pcs & {4, 9, 11}, sorted(pcs))      # no E, A or B natural over C minor

    def test_an_explicit_scale_is_kept(self):
        pcs = self._melody_pcs("--progression", "sad", "--scale", "dorian")
        self.assertIn(9, pcs)                                # dorian keeps its A natural


if __name__ == "__main__":
    unittest.main()
