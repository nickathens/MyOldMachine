"""sound-design fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import shutil as _shutil  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('numpy', 'scipy') if _ilu.find_spec(m) is None]
_NEEDS += [t for t in ('ffprobe',) if _shutil.which(t) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io import wavfile

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "sound-design" / "scripts" / "synth.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=120)


def codec(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["streams"][0]["codec_name"]


class Synth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mp3_output_is_mp3(self):
        out = self.d / "k.mp3"
        r = run("kick", "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(codec(out), "mp3", "WAV bytes under an .mp3 name")

    def test_reverb_exists_and_keeps_its_tail(self):
        out = self.d / "r.wav"
        r = run("snare", "--duration", "0.5", "--reverb", "1.0", "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        rate, audio = wavfile.read(out)
        self.assertAlmostEqual(len(audio) / rate, 1.5, delta=0.01)

    def test_brown_noise_has_no_dc_drift(self):
        out = self.d / "b.wav"
        r = run("noise", "--noise-type", "brown", "--duration", "5", "--seed", "3", "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        rate, audio = wavfile.read(out)
        self.assertLess(abs(float(np.mean(audio / 32767))), 0.01)

    def test_zero_duration_is_an_error_not_a_traceback(self):
        r = run("tone", "--duration", "0", "-o", self.d / "z.wav")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
