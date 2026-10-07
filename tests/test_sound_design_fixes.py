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


def _load_synth():
    import importlib.util
    spec = importlib.util.spec_from_file_location("synth_sweep", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Tuning(unittest.TestCase):
    """Linux bot sweep 2026-10-07: the pad's detuned voices sat at 0.96, 0.98,
    1.00 and 1.02 of the pitch asked, centred 1 percent low, so a 220 Hz pad
    sounded about 17 cents flat; '--bitcrush 8' kept 513 levels (9 bits); a
    tone shorter than its envelope stopped mid-decay with a click."""

    def test_a_pad_is_centred_on_its_pitch(self):
        synth = _load_synth()
        seen = []
        real = synth.oscillator

        def spy(freq, duration, waveform="sine", sample_rate=synth.SAMPLE_RATE):
            seen.append(freq)
            return real(freq, duration, waveform, sample_rate)

        synth.oscillator = spy
        try:
            synth.synth_pad(220, 0.5)
        finally:
            synth.oscillator = real
        cents = 1200 * np.log2(np.mean(seen) / 220)
        self.assertLess(abs(cents), 1.0, seen)

    def test_bitcrush_keeps_the_bits_asked(self):
        synth = _load_synth()
        sine = np.sin(np.linspace(0, 2 * np.pi * 5, 44100))
        self.assertLessEqual(len(np.unique(synth.bitcrush(sine, 4))), 2 ** 4 + 1)

    def test_a_short_tone_ends_at_silence(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "t.wav")
            r = run("tone", "--duration", "0.1", "--freq", "440", "-o", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            _, x = wavfile.read(out)
        self.assertLess(np.max(np.abs(x[-20:])), 400)        # of 32767


if __name__ == "__main__":
    unittest.main()
