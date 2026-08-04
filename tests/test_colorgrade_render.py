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
        self.assertEqual(g, "[0:v]null[vout]")

    def test_extra_vf_is_appended_once_not_per_shot(self):
        g = V.build_graph(shots((0, 50), (50, 120), (120, 300)), luts(3),
                          extra_vf="scale=1920:-2")
        self.assertEqual(g.count("scale=1920:-2"), 1)
        self.assertTrue(g.endswith("scale=1920:-2[vout]"))

    def test_lut_path_special_characters_are_escaped(self):
        """A colon separates filter options and a comma separates filters. An
        unescaped one in a path silently rewrites the graph."""
        g = V.build_graph(shots((0, 50)), {0: "/tmp/od d,ball:2/shot.cube"})
        self.assertIn(r"/tmp/od d\,ball\:2/shot.cube", g)
        self.assertEqual(g.count("lut3d="), 1)


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
