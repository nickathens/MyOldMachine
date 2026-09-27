"""midi-to-audio fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('mido', 'numpy', 'soundfile') if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import mido
import numpy as np
import soundfile

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "midi-to-audio" / "scripts" / "render.py"
SOUNDFONT = Path("/usr/share/sounds/sf2/FluidR3_GM.sf2")


def chords(path):
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    for chord in ([48, 55, 60, 64, 67, 72], [50, 57, 62, 65, 69, 74]):
        for n in chord:
            track.append(mido.Message("note_on", note=n, velocity=120, time=0))
        for i, n in enumerate(chord):
            track.append(mido.Message("note_off", note=n, velocity=0, time=960 if i == 0 else 0))
    mid.save(path)


@unittest.skipUnless(shutil.which("fluidsynth") and SOUNDFONT.exists(), "needs FluidSynth and FluidR3_GM")
class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        chords(self.d / "c.mid")

    def tearDown(self):
        self.tmp.cleanup()

    def run_render(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                              timeout=300)

    def test_dense_chords_do_not_clip(self):
        r = self.run_render(self.d / "c.mid", "--output", self.d / "c.wav")
        self.assertEqual(r.returncode, 0, r.stderr)
        audio, _ = soundfile.read(self.d / "c.wav")
        self.assertEqual(int((np.abs(audio) >= 0.999).sum()), 0, "clipped at full scale")

    def test_an_mp3_name_gets_an_mp3(self):
        r = self.run_render(self.d / "c.mid", "--output", self.d / "c.mp3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.d / "c.mp3").read_bytes()[:3], b"ID3", "WAV bytes under an .mp3 name")


if __name__ == "__main__":
    unittest.main()
