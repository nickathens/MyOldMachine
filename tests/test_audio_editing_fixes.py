"""audio-editing fixes, Linux bot review 2026-09-27. Each fails on the code before them."""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import shutil as _shutil  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('numpy', 'pydub', 'scipy') if _ilu.find_spec(m) is None]
_NEEDS += [t for t in ('ffmpeg', 'ffprobe') if _shutil.which(t) is None]
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

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "audio-editing" / "scripts" / "edit.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=300)


def lufs(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "loudnorm=print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    return float(json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])["input_i"])


def kicks(path, sr=44100):
    x = np.zeros(sr * 4)
    for k in range(8):
        n = int(0.15 * sr)
        tt = np.arange(n) / sr
        s = int(k * 0.5 * sr)
        x[s:s + n] += np.sin(2 * np.pi * (60 + 120 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 18)
    wavfile.write(path, sr, (x * 0.25 * 32767).astype(np.int16))


class AudioEditing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalize_never_clips(self):
        kicks(self.d / "k.wav")
        r = run("normalize", self.d / "k.wav", self.d / "n.wav", "--target", "-14")
        self.assertEqual(r.returncode, 0, r.stderr)
        rate, a = wavfile.read(self.d / "n.wav")
        self.assertEqual(int((np.abs(a.astype(np.int64)) >= np.iinfo(a.dtype).max).sum()), 0, "hard clipping")

    def test_normalize_means_lufs(self):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=8",
                        "-af", "volume=0.1", str(self.d / "tone.wav")], check=True)
        r = run("normalize", self.d / "tone.wav", self.d / "t.wav", "--target", "-16")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAlmostEqual(lufs(self.d / "t.wav"), -16.0, delta=0.3)

    def test_an_mp3_edit_keeps_a_high_bitrate(self):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=5",
                        "-c:a", "libmp3lame", "-b:a", "320k", str(self.d / "s.mp3")], check=True)
        r = run("cut", self.d / "s.mp3", self.d / "c.mp3", "--start", "1", "--end", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=bit_rate", "-of", "csv=p=0",
                              str(self.d / "c.mp3")], capture_output=True, text=True).stdout
        self.assertGreaterEqual(int(out.strip()), 256000, "re-encoded at ffmpeg's 128k default")


if __name__ == "__main__":
    unittest.main()
