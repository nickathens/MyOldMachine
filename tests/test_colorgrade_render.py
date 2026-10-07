"""Guards on the colorgrade render filter graph.

The graph shape is a memory decision, not a style one. The first build split the
input into one trim branch per shot and fed them to concat. concat consumes
branches in order, so every frame a later branch would eventually need sat in
the filter graph while branch zero was still encoding: peak memory tracked the
whole decoded video instead of staying flat. Measured on a 45s 2560x720 piece
with 30 shots, that peaked at 3.53 GB (3,701,412 KB RSS). The straight chain of
timeline-enabled lut3d filters that replaced it peaked at 0.63 GB (659,552 KB)
in the same wall-clock time and produced a byte-identical file: same md5 on the
container and on the decoded stream, 1084 frames both.

That matters beyond one machine. This bot is normally installed as a service
with a memory cap, and on Linux a systemd unit with OOMPolicy=stop takes the
whole service down when one child breaches the cap, so a render that balloons
is not a failed render, it is a dead bot mid-answer. Hence these tests pin the
shape rather than the output.

Standard library only. cgvideo imports numpy, which a bare CI runner does not
have, so the graph tests skip there and SourceTextTest still catches a revert.
"""
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "colorgrade" / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    import cgvideo as V
except ImportError:  # numpy absent (bare CI runner)
    V = None

ENABLE_RE = re.compile(r"enable='between\(n,(\d+),(\d+)\)'")


def shots(*bounds):
    """Shot list from (start, end) frame pairs, end exclusive, as the detector
    and the cut parser both produce."""
    return [V.Shot(i, a, b, a / 25.0, b / 25.0) for i, (a, b) in enumerate(bounds)]


def luts(n, prefix="/tmp/luts/shot"):
    return {i: f"{prefix}_{i:03d}.cube" for i in range(n)}


@unittest.skipIf(V is None, "cgvideo needs numpy")
class GraphShapeTest(unittest.TestCase):
    def test_never_uses_trim_or_concat(self):
        """The shape that buffered the whole video. If it comes back, a long
        render stops being a render and becomes an OOM kill."""
        g = V.build_graph(shots((0, 50), (50, 120), (120, 300)), luts(3))
        self.assertNotIn("trim=", g)
        self.assertNotIn("concat=", g)
        self.assertNotIn("setpts=", g)

    def test_single_chain_from_one_input(self):
        g = V.build_graph(shots((0, 50), (50, 120)), luts(2))
        self.assertEqual(g.count("[0:v]"), 1)
        self.assertEqual(g.count("[vout]"), 1)
        self.assertNotIn(";", g)  # one chain, no parallel branches

    def test_one_lut3d_per_shot(self):
        g = V.build_graph(shots((0, 50), (50, 120), (120, 300)), luts(3))
        self.assertEqual(g.count("lut3d="), 3)

    def test_enable_end_is_inclusive_of_the_last_frame(self):
        """Shot.end_frame is exclusive, ffmpeg's between() is inclusive at both
        ends. Off by one here either drops a frame from the grade or bleeds the
        previous shot's LUT one frame over the cut."""
        g = V.build_graph(shots((0, 50), (50, 120)), luts(2))
        self.assertEqual(ENABLE_RE.findall(g), [("0", "49"), ("50", "119")])

    def test_every_frame_graded_exactly_once(self):
        bounds = ((0, 50), (50, 120), (120, 300))
        g = V.build_graph(shots(*bounds), luts(3))
        ranges = [(int(a), int(b)) for a, b in ENABLE_RE.findall(g)]
        for frame in range(300):
            hits = [r for r in ranges if r[0] <= frame <= r[1]]
            self.assertEqual(len(hits), 1, f"frame {frame} matched {hits}")

    def test_single_shot_whole_video(self):
        g = V.build_graph(shots((0, 1084)), luts(1))
        self.assertEqual(ENABLE_RE.findall(g), [("0", "1083")])

    def test_one_frame_shot_is_a_valid_range(self):
        g = V.build_graph(shots((7, 8)), luts(1))
        self.assertEqual(ENABLE_RE.findall(g), [("7", "7")])


@unittest.skipIf(V is None, "cgvideo needs numpy")
class GraphEdgeCaseTest(unittest.TestCase):
    def test_no_shots_raises(self):
        with self.assertRaises(ValueError):
            V.build_graph([], {})

    def test_shot_without_a_lut_is_passed_through_not_dropped(self):
        """Under trim/concat a shot with no LUT still had a branch, so it
        survived into the output. With a chain, emitting nothing for it is
        correct precisely because nothing is being cut."""
        g = V.build_graph(shots((0, 50), (50, 120)), {0: "/tmp/a.cube"})
        self.assertEqual(g.count("lut3d="), 1)
        self.assertEqual(ENABLE_RE.findall(g), [("0", "49")])

    def test_zero_length_shot_raises_instead_of_grading_nothing(self):
        """between(n,0,-1) is never true, so the shot would render ungraded with
        no error anywhere. Reachable when ffprobe reports neither nb_frames nor
        a duration and nb_frames lands at 0."""
        with self.assertRaises(ValueError) as ctx:
            V.build_graph(shots((0, 0)), luts(1))
        self.assertIn("spans no frames", str(ctx.exception))

    def test_no_luts_at_all_is_a_valid_graph(self):
        """An empty chain is a filtergraph syntax error, so it has to be an
        explicit passthrough. Reachable via --normalize off with no look."""
        g = V.build_graph(shots((0, 50), (50, 120)), {})
        # Since the Linux bot sweep (2026-10-07) every graph ends by naming the BT.709 matrix
        # its output is tagged with, so even no LUT is a real filter, not null.
        self.assertEqual(g, f"[0:v]{V.ENCODE_MATRIX}[vout]")

    def test_extra_vf_is_appended_once_not_per_shot(self):
        g = V.build_graph(shots((0, 50), (50, 120), (120, 300)), luts(3),
                          extra_vf="scale=1920:-2")
        self.assertEqual(g.count("scale=1920:-2"), 1)
        # The extra filter is the last one the grade owns; the encode matrix
        # follows it.
        self.assertTrue(g.endswith(f"scale=1920:-2,{V.ENCODE_MATRIX}[vout]"))

    def test_lut_path_special_characters_are_escaped(self):
        """A colon separates filter options and a comma separates filters. An
        unescaped one in a path silently rewrites the graph."""
        g = V.build_graph(shots((0, 50)), {0: "/tmp/od d,ball:2/shot.cube"})
        self.assertIn(r"/tmp/od d\,ball\:2/shot.cube", g)
        self.assertEqual(g.count("lut3d="), 1)


def media(*audio, path="/x/in.mov"):
    # by keyword: MOM's Media carries color_primaries, which the Linux bot's has not
    return V.Media(path=path, width=640, height=360, fps=25.0, nb_frames=100, duration=4.0,
                   pix_fmt="yuv422p10le", color_space="bt709", color_transfer="bt709",
                   color_primaries="bt709", codec="prores", has_audio=bool(audio),
                   audio_codecs=tuple(audio))


@unittest.skipIf(V is None, "cgvideo needs numpy")
class AudioIntoMp4(unittest.TestCase):
    """Linux bot sweep 2026-10-07: the audio was
    always copied, and a ProRes .mov's PCM went into the .mp4 as an 'ipcm'
    entry that players largely cannot read (GStreamer has no mapping)."""

    def test_pcm_into_mp4_becomes_aac(self):
        self.assertEqual(V.audio_args(media("pcm_s24le"), "/o/out.mp4"),
                         ["-map", "0:a", "-c:a", "aac", "-b:a", "320k"])

    def test_safe_audio_and_other_containers_are_copied(self):
        self.assertEqual(V.audio_args(media("aac"), "/o/out.mp4"), ["-map", "0:a", "-c:a", "copy"])
        self.assertEqual(V.audio_args(media("pcm_s24le"), "/o/out.mov"), ["-map", "0:a", "-c:a", "copy"])
        self.assertEqual(V.audio_args(media(), "/o/out.mp4"), [])

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "needs ffmpeg")
    def test_a_rendered_mp4_carries_aac(self):
        import json
        import subprocess
        import tempfile
        import cgcore as C
        with tempfile.TemporaryDirectory() as d:
            src, out, lut = Path(d, "in.mov"), Path(d, "out.mp4"), Path(d, "id.cube")
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=1",
                            "-f", "lavfi", "-i", "sine=d=1", "-c:v", "prores_ks", "-c:a", "pcm_s24le",
                            "-shortest", str(src)], check=True)
            C.write_cube(str(lut), C.identity_lattice(2), 2)
            m = V.probe(str(src))
            self.assertEqual(m.audio_codecs, ("pcm_s24le",))
            V.render(m, [V.Shot(0, 0, m.nb_frames, 0.0, m.duration)], {0: str(lut)}, str(out), preset="ultrafast")
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                    "stream=codec_name", "-of", "json", str(out)],
                                   capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(probe.stdout)["streams"][0]["codec_name"], "aac")


class SourceTextTest(unittest.TestCase):
    """Runs even without numpy, so a bare runner still catches a revert."""

    def test_source_carries_no_trim_concat_builder(self):
        src = (SCRIPTS / "cgvideo.py").read_text(encoding="utf-8")
        build = src[src.index("def build_graph"):src.index("def render(")]
        self.assertNotIn("trim=", build)
        self.assertNotIn("concat=", build)
        self.assertIn("enable=", build)


if __name__ == "__main__":
    unittest.main()
