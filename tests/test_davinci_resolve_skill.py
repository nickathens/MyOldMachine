"""Offline tests for the davinci-resolve skill scripts.

Everything here runs without Resolve installed. queue-job/check-job are
exercised through a COOCOO_RESOLVE_JOB override so no real job file is
touched; the fcpxml builder tests generate tiny clips with ffmpeg and are
skipped when ffmpeg/ffprobe are absent.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "davinci-resolve" / "scripts"
RESOLVE_API = SCRIPTS / "resolve_api.py"
BUILD_FCPXML = SCRIPTS / "build_fcpxml.py"

HAVE_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def run_api(job_file, *argv):
    env = dict(os.environ, COOCOO_RESOLVE_JOB=str(job_file))
    return subprocess.run([sys.executable, str(RESOLVE_API), *argv],
                          capture_output=True, text=True, env=env)


class QueueJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coocoo_resolve_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.job_file = self.tmp / "resolve_job.json"
        self.clip = self.tmp / "clip.mp4"
        self.clip.write_bytes(b"\x00")

    def queue(self, *extra):
        return run_api(self.job_file, "queue-job", str(self.clip), *extra)

    def read_job(self):
        return json.loads(self.job_file.read_text())

    def test_preset_only_writes_job(self):
        # Regression: --render-preset without --render-out used to crash on
        # Path(None) before any job file was written.
        r = self.queue("--render-preset", "H.264 Master")
        self.assertEqual(r.returncode, 0, r.stderr)
        job = self.read_job()
        self.assertEqual(job["render"]["preset"], "H.264 Master")
        self.assertIsNone(job["render"]["out"])

    def test_out_only_writes_job(self):
        r = self.queue("--render-out", str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        job = self.read_job()
        self.assertIsNone(job["render"]["preset"])
        self.assertEqual(job["render"]["out"], str(self.tmp.resolve()))

    def test_preset_and_out(self):
        r = self.queue("--render-preset", "H.264 Master", "--render-out", str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        job = self.read_job()
        self.assertEqual(job["render"]["preset"], "H.264 Master")
        self.assertEqual(job["render"]["out"], str(self.tmp.resolve()))

    def test_no_render_flags(self):
        r = self.queue("--name", "My Cut", "--fps", "24")
        self.assertEqual(r.returncode, 0, r.stderr)
        job = self.read_job()
        self.assertIsNone(job["render"])
        self.assertEqual(job["timeline"], "My Cut")
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["media"], [str(self.clip.resolve())])

    def test_missing_media_fails_before_writing(self):
        r = run_api(self.job_file, "queue-job", str(self.tmp / "nope.mp4"))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.job_file.exists())

    def test_check_job_reads_back(self):
        self.queue("--name", "Round Trip")
        r = run_api(self.job_file, "check-job")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Round Trip", r.stdout)
        self.assertIn("pending", r.stdout)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not available")
class BuildFcpxmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="coocoo_fcpxml_test_"))
        cls.clips = []
        for name, secs in ((u"κλιπ ένα & 'δύο'.mp4", 1.0), ("clip b.mp4", 2.0)):
            path = cls.tmp / name
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                            "-i", f"color=c=black:s=64x36:r=25:d={secs}",
                            "-pix_fmt", "yuv420p", str(path)], check=True)
            cls.clips.append(path)
        cls.music = cls.tmp / "music.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=10",
                        str(cls.music)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def seconds(rational):
        value = rational.rstrip("s")
        if "/" in value:
            num, den = value.split("/")
            return Fraction(int(num), int(den))
        return Fraction(int(value))

    def build(self, out, *extra):
        return subprocess.run([sys.executable, str(BUILD_FCPXML), str(out),
                               *[str(c) for c in self.clips], *extra],
                              capture_output=True, text=True)

    def test_semantics_with_music(self):
        out = self.tmp / "cut.fcpxml"
        r = self.build(out, "--fps", "25", "--music", str(self.music))
        self.assertEqual(r.returncode, 0, r.stderr)

        root = ET.parse(out).getroot()  # well-formed or this raises
        spine = root.find("./library/event/project/sequence/spine")
        top_clips = spine.findall("asset-clip")
        self.assertEqual(len(top_clips), len(self.clips))

        # offsets are sequential and the sequence spans the clip sum
        offset = Fraction(0)
        for clip in top_clips:
            self.assertEqual(self.seconds(clip.get("offset")), offset)
            offset += self.seconds(clip.get("duration"))
        sequence = root.find("./library/event/project/sequence")
        self.assertEqual(self.seconds(sequence.get("duration")), offset)

        # music sits on a connected lane, truncated to the timeline length
        music_clip = top_clips[0].find("asset-clip")
        self.assertEqual(music_clip.get("lane"), "-1")
        self.assertEqual(self.seconds(music_clip.get("duration")), offset)

        # awkward filename survives quoting end to end
        assets = root.findall("./resources/asset")
        names = {a.get("name") for a in assets}
        self.assertIn(u"κλιπ ένα & 'δύο'", names)
        greek = next(a for a in assets if a.get("name") == u"κλιπ ένα & 'δύο'")
        src = greek.find("media-rep").get("src")
        self.assertTrue(src.startswith("file:///"))
        self.assertNotIn(" ", src)

    def test_music_shorter_than_timeline_keeps_own_length(self):
        short_music = self.tmp / "short.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1",
                        str(short_music)], check=True)
        out = self.tmp / "cut_short_music.fcpxml"
        r = self.build(out, "--fps", "25", "--music", str(short_music))
        self.assertEqual(r.returncode, 0, r.stderr)
        root = ET.parse(out).getroot()
        spine = root.find("./library/event/project/sequence/spine")
        music_clip = spine.findall("asset-clip")[0].find("asset-clip")
        self.assertEqual(self.seconds(music_clip.get("duration")), Fraction(1))


if __name__ == "__main__":
    unittest.main()
