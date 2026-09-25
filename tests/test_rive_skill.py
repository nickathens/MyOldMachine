"""Tests for the rive skill (skills/rive).

Two halves. The offline half needs nothing but Python: the timeline compiler
that turns gestures and data into per-frame CLI arguments, the SVG converter,
the render plans and ffmpeg commands, the lint, the fonts, audio and recipe
maths, the templates' structure, the manifests and the docs. The live half
builds, captures and renders with the real Rive CLI and skips wherever
`rive` is not on PATH (CI has none; this Mac has 1.1.1), so the costs and
behaviours the scripts depend on are re-proved against the binary itself.

Numbers asserted here were measured on Rive CLI 1.1.1 on 25 Sep 2026 and are
documented in skills/rive/references/rendering.md.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "rive"
SCRIPTS = SKILL / "scripts"
TEMPLATES = SKILL / "templates"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L = load("rivelib")
T = load("rivetimeline")
S = load("rive_svg")
R = load("rive_render")
C = load("rive_check")
F = load("rive_fonts")
A = load("rive_audio")
RC = load("rive_recipes")
N = load("rive_new")
V = load("rive_versions")
W = load("riveweb")
WEB = load("rive_web")
D = load("rive_doctor")

RIVE = shutil.which("rive")
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
LIVE = unittest.skipUnless(RIVE and FFMPEG, "needs the Rive CLI and ffmpeg on PATH")
NEEDS_FFMPEG = unittest.skipUnless(FFMPEG, "needs ffmpeg")


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


class TempDir(unittest.TestCase):
    def setUp(self):
        # resolve: on macOS mkdtemp gives /var/... while resolved paths read /private/var/...
        self.tmp = Path(tempfile.mkdtemp(prefix="rive_skill_test_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)


def write_png(path: Path, width: int, height: int, pixel_at) -> None:
    """A tiny RGBA PNG writer, so test images are exact and need no imaging library."""
    import zlib
    rows = b"".join(b"\x00" + b"".join(bytes(pixel_at(x, y)) for x in range(width)) for y in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
                     + chunk(b"IEND", b""))


def scene_time(args: list[str]) -> float:
    """Scene time a list of frame arguments consumes, by the measured costs."""
    total = 0.0
    for a in args:
        if a.startswith("--advance="):
            v = a.split("=", 1)[1]
            total += float(v[:-2]) / 1000 if v.endswith("ms") else int(v) / 60
        elif a.startswith("--pointer=click@"):
            total += 3 / 60
        elif a.startswith("--pointer=") or a.startswith("--key=") or a.startswith("--gamepad="):
            total += 1 / 60
        elif a.startswith("--semantic-action="):
            total += 2 / 60
    return total


# ==========================================================================
# the timeline compiler
# ==========================================================================


class AdvanceArgTests(unittest.TestCase):
    def test_whole_frames_go_as_a_frame_count(self):
        self.assertEqual(L.advance_arg(1.0), "--advance=60")
        self.assertEqual(L.advance_arg(1 / 30), "--advance=2")
        self.assertEqual(L.advance_arg(0.5), "--advance=30")

    def test_anything_else_goes_as_fixed_point_milliseconds(self):
        self.assertEqual(L.advance_arg(1 / 24), "--advance=41.6667ms")
        self.assertEqual(L.advance_arg(1e-5), "--advance=0.01ms")
        self.assertNotIn("e-", L.advance_arg(1e-5))

    def test_negative_is_refused(self):
        with self.assertRaises(ValueError):
            L.advance_arg(-0.1)

    def test_frame_zero_is_never_captured_at_zero(self):
        # --advance=0 and no advance are the authored rest pose (measured: a text
        # keyed invisible on frame 0 still shows); frame 0 must be a real advance.
        self.assertGreater(L.FIRST_FRAME_EPSILON, 0)
        args = T.compile_frame(T.Timeline(), 0.0).args
        self.assertEqual(args, [L.advance_arg(L.FIRST_FRAME_EPSILON)])
        self.assertNotIn("--advance=0ms", args)


class CompileFrameTests(unittest.TestCase):
    def test_measured_gesture_costs(self):
        def cost(event):
            return sum(s.frames for s in T.expand_event(dict(event, at=0)))
        self.assertEqual(cost({"click": [1, 2]}), 3)
        self.assertEqual(cost({"drag": [0, 0, 10, 10], "steps": 8}), 11)
        self.assertEqual(cost({"drag": [0, 0, 10, 10], "steps": 1}), 4)
        self.assertEqual(cost({"key": "down"}), 2)
        self.assertEqual(cost({"key": "a", "phase": "down"}), 1)
        self.assertEqual(cost({"gamepad": "button@south:down"}), 1)
        self.assertEqual(cost({"semantic": "tap@Play"}), 2)
        self.assertEqual(cost({"move": [5, 5]}), 1)

    def test_a_click_is_move_press_release(self):
        flags = [s.flag for s in T.expand_event({"at": 1, "click": [560, 560]})]
        self.assertEqual(flags, ["--pointer=move@560,560", "--pointer=down@560,560", "--pointer=up@560,560"])

    def test_every_frame_consumes_exactly_its_own_time(self):
        tl = T.build_timeline([{"at": 0.5, "click": [10, 10]}, {"at": 1.0, "drag": [1, 2, 3, 4], "steps": 5},
                               {"at": 2.0, "semantic": "tap@X"}, {"at": 2.5, "key": "down"}])
        for t in [0.02, 0.25, 0.5, 0.51, 0.55, 0.99, 1.05, 1.2, 1.99, 2.0, 2.04, 2.6, 3.0, 5.0]:
            fa = tl.frame_args(t)
            self.assertAlmostEqual(scene_time(fa.args), t, places=5, msg=f"t={t}: {fa.args}")
            self.assertEqual(fa.late_by, 0.0)

    def test_a_frame_inside_a_click_shows_the_finished_steps_only(self):
        tl = T.build_timeline([{"at": 1.0, "click": [5, 5]}])
        mid = tl.frame_args(1.0 + 2 / 60).args
        self.assertIn("--pointer=down@5,5", mid)
        self.assertNotIn("--pointer=up@5,5", mid)
        self.assertIn("--pointer=up@5,5", tl.frame_args(1.0 + 3 / 60).args)

    def test_overlapping_gestures_report_a_late_frame(self):
        tl = T.build_timeline([{"at": 1.0, "click": [1, 1]}, {"at": 1.01, "click": [2, 2]}])
        fa = tl.frame_args(1.0 + 6 / 60 - 1e-4)
        self.assertGreater(fa.late_by, 0)

    def test_data_constants_and_curves_come_first(self):
        curve = T.Curve(keys=[(0.0, 0.0), (1.0, 100.0)])
        tl = T.build_timeline([], {"title": "Hi", "on": True}, {"level": curve})
        args = tl.frame_args(0.5).args
        self.assertEqual(args[:3], ["--data=title=Hi", "--data=on=true", "--data=level=50"])

    def test_web_schedule_is_pointer_only(self):
        tl = T.build_timeline([{"at": 1, "click": [3, 4]}])
        sched = T.web_schedule(tl)
        self.assertEqual([s["kind"] for s in sched], ["move", "down", "up"])
        with self.assertRaises(L.RiveError):
            T.web_schedule(T.build_timeline([{"at": 1, "key": "enter"}]))

    def test_bad_events_are_refused_readably(self):
        for bad in ({"click": [1, 2]}, {"at": 1}, {"at": 1, "drag": [1, 2]}, {"at": -1, "click": [1, 1]},
                    {"at": 1, "key": "x", "phase": "sideways"}):
            with self.assertRaises(L.RiveError):
                T.expand_event(bad)


class CurveTests(TempDir):
    def test_sampled_numbers_interpolate_and_strings_hold(self):
        c = T.Curve(samples=[0, 10, 20], fps=10)
        self.assertEqual(c.at(0.05), 5)
        self.assertEqual(c.at(5.0), 20)
        s = T.Curve(samples=["a", "ab", "abc"], fps=10)
        self.assertEqual(s.at(0.15), "ab")

    def test_keys_with_hold(self):
        c = T.Curve(keys=[(0, ""), (1.0, "h"), (1.2, "hi")], interpolation="hold")
        self.assertEqual(c.at(0.5), "")
        self.assertEqual(c.at(1.1), "h")
        self.assertEqual(c.at(9), "hi")

    def test_range_mapping(self):
        self.assertEqual(T.Curve(samples=[0.5], fps=1, lo=0, hi=200).at(0), 100)

    def test_curve_spec_parsing(self):
        doc = {"fps": 25, "curves": {"low": [0, 1], "b1": [1, 0]}}
        path = self.tmp / "c.json"
        path.write_text(json.dumps(doc))
        name, curve = T.load_curve(f"bass={path}:low@0:10")
        self.assertEqual(name, "bass")
        self.assertAlmostEqual(curve.at(0.02), 5.0)
        with self.assertRaises(L.RiveError):
            T.load_curve(f"bass={path}")      # several curves: must name one
        with self.assertRaises(L.RiveError):
            T.load_curve(f"bass={path}:nope")

    def test_format_value(self):
        self.assertEqual(T.format_value(True), "true")
        self.assertEqual(T.format_value(2.50), "2.5")
        self.assertEqual(T.format_value(-0.0), "0")
        self.assertEqual(T.format_value("A=B, C"), "A=B, C")
        with self.assertRaises(L.RiveError):
            T.format_value(float("nan"))

    def test_timeline_file(self):
        (self.tmp / "curves.json").write_text(json.dumps({"fps": 30, "curves": {"level": [0.0, 1.0]}}))
        doc = {"events": [{"at": 0.5, "click": [1, 1]}], "data": {"title": "X"},
               "curves": {"query": {"keys": [[0, ""], [1, "a"]], "interpolation": "hold"},
                          "level": {"file": "curves.json", "key": "level", "range": [0, 100]},
                          "bass": "curves.json:level"}}
        (self.tmp / "tl.json").write_text(json.dumps(doc))
        tl = T.load_timeline_file(self.tmp / "tl.json")
        self.assertEqual(len(tl.steps), 3)
        self.assertEqual(sorted(tl.curves), ["bass", "level", "query"])
        self.assertEqual(tl.curves["level"].at(1 / 30), 100)


# ==========================================================================
# rivelib
# ==========================================================================


class LibTests(TempDir):
    def test_yaml_scalar(self):
        y = self.tmp / "rive.yaml"
        y.write_text('name: "my proj"  # comment\nmain: Main\n')
        self.assertEqual(L.yaml_scalar(y, "name"), "my proj")
        self.assertEqual(L.yaml_scalar(y, "main"), "Main")
        self.assertIsNone(L.yaml_scalar(y, "missing"))

    def test_environment(self):
        with mock.patch.dict(os.environ, {"JARVIS_USER_DIR": str(self.tmp)}, clear=False):
            env = L.rive_env()
            self.assertEqual(env["XDG_CONFIG_HOME"], str(self.tmp / "rive" / "config"))
            self.assertFalse((self.tmp / "rive").exists(), "nothing is written into a user folder by a read")
        self.assertEqual(env["RIVE_ANALYTICS"], "off")
        self.assertEqual(env["RIVE_NO_TUI"], "1")
        self.assertEqual(env["RIVE_NO_AUDIO_DEVICE"], "1")
        self.assertNotIn("RIVE_NO_AUDIO_DEVICE", L.rive_env(headless=False))

    def test_no_user_dir_means_no_config_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_USER_DIR", None)
            self.assertIsNone(L.account_config_home())

    def test_version_note(self):
        self.assertIsNone(L.version_note(L.TESTED_CLI_VERSIONS[0]))
        self.assertIn("not a version", L.version_note("9.9.9"))
        self.assertIsNotNone(L.version_note(None))

    def test_a_crash_is_named_as_a_crash(self):
        # subprocess reports a signal as a negative code; CLI 1.1.1 on Linux
        # segfaults in one known case (references/rendering.md, Linux)
        self.assertIn("SIGSEGV", L.explain_exit(-11))
        self.assertIn("crashed", L.explain_exit(-11))
        self.assertEqual(L.explain_exit(6), L.EXIT_MEANINGS[6])

    def make_project(self, rml: str, yaml: str = "name: p\n") -> Path:
        proj = self.tmp / "proj"
        proj.mkdir()
        (proj / "rive.yaml").write_text(yaml)
        (proj / "scene.rml").write_text(rml)
        return proj

    def test_snapshot_rewrites_paths_that_leave_the_project(self):
        (self.tmp / "fonts").mkdir()
        (self.tmp / "fonts" / "F.ttf").write_bytes(b"x")
        proj = self.make_project('<FontAsset file="../fonts/F.ttf" name="F"/><ImageAsset file="img/a.png"/>',
                                 "name: p\nlibraries:\n  - ../lib\n")
        (proj / "build").mkdir()
        (proj / "build" / "old.riv").write_bytes(b"old")
        snap = L.snapshot_project(proj, self.tmp)
        self.addCleanup(L.remove_tree, snap.parent)
        text = (snap / "scene.rml").read_text()
        self.assertIn(f'file="{(self.tmp / "fonts" / "F.ttf").resolve()}"', text)
        self.assertIn('file="img/a.png"', text)
        self.assertIn(str((self.tmp / "lib").resolve()), (snap / "rive.yaml").read_text())
        self.assertFalse((snap / "build").exists())
        self.assertIn("rive_skill_snap_", str(snap))

    def test_background_goes_in_as_the_artboards_first_child(self):
        proj = self.make_project('<Rive version="1" kind="fragment">\n<Artboard width="10" height="10" '
                                 'name="B" id="0:9"><Shape/></Artboard>\n<Artboard name="A" id="0:2"/>\n</Rive>')
        board = L.ArtboardInfo(name="A", id="0:2", width=10, height=10)
        L.add_artboard_background(proj, board, "FF000000")
        text = (proj / "scene.rml").read_text()
        self.assertIn('<Artboard name="A" id="0:2"><Fill name="__rive_skill_bg"><SolidColor colorValue="FF000000"',
                      text)
        self.assertIn("</Fill></Artboard>", text)
        self.assertIn('id="0:9"><Shape/>', text, "the other artboard is untouched")

    def test_opaque_background(self):
        self.assertTrue(L.ArtboardInfo("a", None, 1, 1, background=["FF000000"]).opaque_background())
        self.assertTrue(L.ArtboardInfo("a", None, 1, 1, background=["gradient"]).opaque_background())
        self.assertFalse(L.ArtboardInfo("a", None, 1, 1, background=["80000000"]).opaque_background())
        self.assertFalse(L.ArtboardInfo("a", None, 1, 1).opaque_background())

    def test_artboards_from_inspect(self):
        inspect = {"defaultArtboard": {"name": "Main"},
                   "artboards": [{"type": "Artboard", "id": "0:2", "name": "Main", "width": 100, "height": 50,
                                  "defaultStateMachineId": "0:7", "viewModelId": "0:40",
                                  "children": [{"type": "Fill", "children": [{"type": "SolidColor",
                                                                              "colorValue": "ff101010"}]},
                                               {"type": "StateMachine", "id": "0:7", "name": "SM"}]}],
                   "roots": [{"type": "ViewModel", "id": "0:40", "name": "VM",
                              "children": [{"type": "ViewModelPropertyString", "name": "title"},
                                           {"type": "ViewModelPropertyNumber", "name": "level"},
                                           {"type": "ViewModelInstance", "name": "Default"}]}]}
        board = L.pick_artboard(inspect)
        self.assertEqual((board.name, board.size, board.state_machine), ("Main", (100, 50), "SM"))
        self.assertEqual(board.view_model_props, {"title": "string", "level": "number"})
        self.assertTrue(board.opaque_background())
        with self.assertRaises(L.RiveError):
            L.pick_artboard(inspect, "Nope")

    @NEEDS_FFMPEG
    def test_blank_detection(self):
        flat = self.tmp / "flat.png"
        write_png(flat, 32, 16, lambda x, y: (0x1D, 0x1D, 0x1D, 255))
        busy = self.tmp / "busy.png"
        write_png(busy, 32, 16, lambda x, y: (x * 8, y * 16, 0, 255))
        other = self.tmp / "other.png"
        write_png(other, 8, 8, lambda x, y: (0x33, 0x66, 0x99, 255))
        self.assertIn("nothing was drawn", L.blank_reason(flat))
        self.assertIsNone(L.blank_reason(busy))
        self.assertIn("#336699: only a flat background", L.blank_reason(other))


# ==========================================================================
# rive_render: plans and ffmpeg commands
# ==========================================================================


class RenderPlanTests(unittest.TestCase):
    def args(self, *argv):
        return R.build_parser().parse_args(list(argv))

    def test_output_planning(self):
        times, still = R.plan_frames(self.args("p", "-o", "x.png", "--at", "1.5"))
        self.assertEqual((times, still), ([1.5], True))
        times, still = R.plan_frames(self.args("p", "-o", "x.mp4", "--duration", "2", "--fps", "25"))
        self.assertEqual((len(times), still), (50, False))
        self.assertAlmostEqual(times[1], 0.04)
        with self.assertRaises(L.RiveError):
            R.plan_frames(self.args("p", "-o", "x.png", "--duration", "2"))
        with self.assertRaises(L.RiveError):
            R.plan_frames(self.args("p", "-o", "x.mp4"))
        times, _ = R.plan_frames(self.args("p", "-o", "f_%04d.png", "--frames", "3", "--start", "10"))
        self.assertEqual(times[0], 10.0)

    def test_cli_gesture_flags(self):
        a = self.args("p", "-o", "x.mp4", "--duration", "1", "--click", "0.5@10,20",
                      "--drag", "0.1@1,2>3,4:6", "--key", "0.2@enter")
        events = R.events_from_args(a)
        self.assertIn({"at": 0.5, "click": [10.0, 20.0]}, events)
        self.assertIn({"at": 0.1, "drag": [1.0, 2.0, 3.0, 4.0], "steps": 6}, events)
        self.assertIn({"at": 0.2, "key": "enter"}, events)
        with self.assertRaises(L.RiveError):
            R.events_from_args(self.args("p", "-o", "x.mp4", "--click", "10,20"))

    def test_view_args(self):
        board = L.ArtboardInfo("A", "0:2", 390, 844)
        rep = {"warnings": []}
        self.assertEqual(R.view_args(board, self.args("p", "-o", "x.png", "--size", "1170x2532"), rep),
                         ["--viewport=1170x2532", "--fit=contain"])
        rep = {"warnings": []}
        self.assertEqual(R.view_args(board, self.args("p", "-o", "x.png", "--size", "1080x1080"), rep),
                         ["--viewport=1080x1080", "--fit=layout"])
        self.assertTrue(rep["warnings"])
        with self.assertRaises(L.RiveError):
            R.view_args(board, self.args("p", "-o", "x.mov", "--size", "1080x1080", "--fit", "contain",
                                         "--alpha"), {"warnings": []})
        rep = {"warnings": []}
        self.assertEqual(R.view_args(board, self.args("p", "-o", "x.png"), rep), [])
        self.assertEqual(rep["size"], [390, 844])

    def test_alpha_graph_divides_with_blend_not_unpremultiply(self):
        # unpremultiply=inplace=1 after alphamerge left colour premultiplied on
        # ffmpeg 9 (errors up to 65 codes, measured); the division is a blend.
        self.assertNotIn("unpremultiply", R.ALPHA_GRAPH)
        self.assertIn("blend=all_expr='255-(A-B)'", R.ALPHA_GRAPH)
        self.assertIn("(A*255+B/2)/B", R.ALPHA_GRAPH)
        self.assertIn("alphamerge", R.ALPHA_GRAPH)

    def encode_cmd(self, out: str, *extra, alpha=False, size=(1920, 1080), frames=10):
        args = self.args("p", "-o", out, "--duration", "1", *extra)
        report = {"frames": frames, "size": list(size), "warnings": []}
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(R.subprocess, "run", fake_run), \
                mock.patch.object(R, "probe_output", lambda p: {}), \
                mock.patch.object(R.L, "ffmpeg_bin", lambda: "ffmpeg"):
            R.encode(Path("/tmp/frames"), 5, 30.0, Path(out), args, report, alpha)
        return " ".join(captured["cmd"]), report

    def test_h264_is_tagged_on_the_source_and_converted_accurately(self):
        cmd, _ = self.encode_cmd("x.mp4")
        self.assertIn("setparams=color_primaries=bt709:color_trc=bt709", cmd)
        self.assertIn("out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int", cmd)
        self.assertIn("-pix_fmt yuv420p", cmd)
        self.assertIn("-crf 14", cmd)
        self.assertIn("-t 0.333333", cmd)

    def test_prores_profiles(self):
        cmd, _ = self.encode_cmd("x.mov")
        self.assertIn("-profile:v 3", cmd)
        self.assertIn("yuv422p10le", cmd)
        cmd, _ = self.encode_cmd("x.mov", alpha=True)
        self.assertIn("-profile:v 4", cmd)
        self.assertIn("yuva444p10le", cmd)
        self.assertIn("-alpha_bits 16", cmd)
        with self.assertRaises(L.RiveError):
            self.encode_cmd("x.mov", "--prores", "422hq", alpha=True)

    def test_webm_and_gif_alpha(self):
        cmd, _ = self.encode_cmd("x.webm", alpha=True)
        self.assertIn("yuva420p", cmd)
        cmd, _ = self.encode_cmd("x.gif", alpha=True)
        self.assertIn("palettegen=reserve_transparent=1", cmd)

    def test_audio_is_trimmed_with_t_never_shortest(self):
        cmd, _ = self.encode_cmd("x.mov", "--audio", "a.wav", "--audio-offset", "12")
        self.assertIn("-ss 12.0 -i a.wav", cmd)
        self.assertIn("pcm_s24le", cmd)
        self.assertNotIn("-shortest", cmd)
        cmd, _ = self.encode_cmd("x.mp4", "--audio", "a.wav")
        self.assertIn("aac", cmd)

    def test_odd_sizes_are_padded_and_reported(self):
        cmd, report = self.encode_cmd("x.mp4", size=(1081, 1081))
        self.assertIn("pad=ceil(iw/2)*2:ceil(ih/2)*2", cmd)
        self.assertTrue(any("odd" in w for w in report["warnings"]))

    def test_curves_bind_only_matching_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"fps": 25, "curves": {"low": [0.1], "zzz": [1]}}))
            tl, rep = T.Timeline(), {}
            R.attach_curves(tl, path, {"low", "title"}, rep)
            self.assertEqual(sorted(tl.curves), ["low"])
            self.assertEqual(rep["curves_unused"], ["zzz"])
            with self.assertRaises(L.RiveError):
                R.attach_curves(T.Timeline(), path, {"title"}, {})


class RenderCleanupTests(TempDir):
    def project(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        (proj / "rive.yaml").write_text("name: proj\n")
        return proj

    def test_a_failed_capture_cancels_the_queue(self):
        import threading
        import time
        calls = []
        lock = threading.Lock()
        closed = []

        class FailingEngine:
            def __init__(self, project, args, report):
                self.passes = {"main": None}
                self.board = mock.Mock(view_model_props={})

            def capture(self, pass_name, t, timeline, out_png):
                with lock:
                    calls.append(out_png.name)
                if out_png.name.startswith("f00002"):
                    raise L.RiveError("capture at frame 2 failed")
                time.sleep(0.005)
                return T.FrameArgs(args=[], capture_time=t)

            def close(self):
                closed.append(True)

        err = io.StringIO()
        with mock.patch.object(R, "CliEngine", FailingEngine), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = R.main([str(self.project()), "-o", str(self.tmp / "x.mp4"), "--frames", "400",
                           "--workers", "2", "--work-dir", str(self.tmp)])
        self.assertEqual(code, 1)
        self.assertIn("frame 2 failed", err.getvalue())
        # a guard, not a fix: Executor.map already cancels the queue when a
        # result raises; a rewrite that collects errors instead would run
        # all 400 captures before reporting
        self.assertLess(len(calls), 60, f"{len(calls)} captures ran after the failure")
        self.assertEqual(closed, [True])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["proj"])

    def test_a_refused_project_leaves_no_work_folder(self):
        args = R.build_parser().parse_args([str(self.tmp), "-o", "x.mp4", "--work-dir", str(self.tmp)])
        with mock.patch.object(R.L, "require_rive", return_value="rive"), \
                mock.patch.object(R.L, "cli_version", return_value="1.1.1"), \
                mock.patch.object(R.L, "hash_project", return_value="0"), \
                mock.patch.object(R.L, "snapshot_project", side_effect=L.RiveError("refused")):
            with self.assertRaises(L.RiveError):
                R.CliEngine(self.tmp, args, {"warnings": []})
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith("rive_skill_render_")], [])


class AlphaCheckTests(TempDir):
    """A failed recomposite check used to warn and exit 0 with the file written
    (measured: a difference blend over a 50% fill, 143 codes off). It stops the
    render now, like a blank one, unless --allow-bad-alpha says otherwise."""

    CLEAN = {"max": 1.0, "mean": 0.1}

    def check(self, errors: list, allow_bad: bool = False) -> dict:
        report = {"warnings": []}
        engine = mock.Mock(source=self.tmp, workdir=self.tmp, passes={})
        results = iter(errors)
        with mock.patch.object(R.L, "snapshot_project", return_value=self.tmp), \
                mock.patch.object(R, "recomposite_error", lambda rgba, ref: dict(next(results))):
            # the option only when used, so the clean case runs unchanged on the old signature
            R.check_alpha(engine, T.Timeline(), [0.0, 0.5, 1.0], self.tmp, self.tmp, 5, report,
                          **({"allow_bad": True} if allow_bad else {}))
        return report

    def test_a_clean_solve_passes_quietly(self):
        report = self.check([self.CLEAN] * 3)
        self.assertEqual(report["warnings"], [])
        self.assertEqual([r["frame"] for r in report["alpha_check"]], [0, 1, 2])

    def test_a_failed_recomposite_stops_the_render(self):
        for bad, shown in (({"max": 143.3, "mean": 14.6}, "143 codes"), ({"psnr_db": 31.24}, "31.2 dB")):
            with self.subTest(shown):
                with self.assertRaises(L.RiveError) as caught:
                    self.check([self.CLEAN, bad, self.CLEAN])
                self.assertIn(f"frame 1 off by up to {shown}" if "codes" in shown else f"frame 1 at {shown}",
                              str(caught.exception))
                self.assertIn("--allow-bad-alpha", caught.exception.hint)

    def test_allow_bad_alpha_writes_it_with_the_numbers_in_a_warning(self):
        report = self.check([{"max": 143.3, "mean": 14.6}] + [self.CLEAN] * 2, allow_bad=True)
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("frame 0 off by up to 143 codes", report["warnings"][0])
        self.assertIn("written anyway because of --allow-bad-alpha", report["warnings"][0])

    def main_with(self, *extra: str) -> tuple[int, str, Path]:
        """R.main end to end with the CLI, ffmpeg and the recomposite faked:
        every capture is the same red and clear frame, the solve is 143 codes off."""
        proj = self.tmp / "proj"
        proj.mkdir(exist_ok=True)
        (proj / "rive.yaml").write_text("name: proj\n")
        out = self.tmp / "still.png"

        class Engine:
            def __init__(self, project, args, report):
                self.passes = {"black": None, "white": None}
                self.board = mock.Mock(view_model_props={})
                self.source, self.workdir = project, project

            def capture(self, pass_name, t, timeline, out_png):
                write_png(out_png, 4, 4, lambda x, y: (200, 0, 0, 255) if x < 2 else (0, 0, 0, 0))
                return T.FrameArgs(args=[], capture_time=t)

            def close(self):
                pass

        def solve(black, white, out_dir, fps, pad):
            for p in black.iterdir():
                shutil.copy2(p, out_dir / p.name)

        err = io.StringIO()
        # the blank check reads frames with ffprobe, which CI does not have
        with mock.patch.object(R, "CliEngine", Engine), mock.patch.object(R, "solve_alpha", solve), \
                mock.patch.object(R, "recomposite_error", lambda rgba, ref: {"max": 143.0, "mean": 14.0}), \
                mock.patch.object(R, "check_blank", lambda *a: None), \
                mock.patch.object(R.L, "snapshot_project", return_value=proj), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = R.main([str(proj), "-o", str(out), "--alpha", "--work-dir", str(self.tmp), *extra])
        return code, err.getvalue(), out

    def test_the_refusal_reaches_the_exit_code_and_writes_nothing(self):
        code, err, out = self.main_with()
        self.assertEqual(code, 1)
        self.assertIn("does not recomposite", err)
        self.assertFalse(out.exists())
        self.assertFalse(Path(str(out) + ".render.json").exists())

    def test_the_flag_reaches_the_check(self):
        code, err, out = self.main_with("--allow-bad-alpha")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.is_file())
        report = json.loads(Path(str(out) + ".render.json").read_text())
        self.assertTrue(any("--allow-bad-alpha" in w for w in report["warnings"]), report["warnings"])


# ==========================================================================
# rive_svg
# ==========================================================================


class SvgPathTests(unittest.TestCase):
    def ends(self, d):
        return [(round(s.end()[0], 4), round(s.end()[1], 4)) for s in S.parse_path(d)]

    def test_every_command_absolute_and_relative(self):
        subs = S.parse_path("M10 10 L20 10 H30 V20 h-10 v5 l-5 5 C 0 0 1 1 2 2 S 5 5 6 6 Q 7 8 9 9 T 12 12 Z")
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0].closed)
        kinds = [s[0] for s in subs[0].segments]
        self.assertEqual(kinds.count("C"), 4)  # C, S, Q, T all become cubics
        self.assertEqual(subs[0].segments[-1], ("L", (10.0, 10.0)))  # Z closes with a line

    def test_implicit_lineto_after_moveto(self):
        segs = S.parse_path("M0 0 10 0 10 10")[0].segments
        self.assertEqual(segs, [("L", (10.0, 0.0)), ("L", (10.0, 10.0))])

    def test_quadratic_is_elevated_exactly(self):
        (seg,) = S.parse_path("M0 0 Q 10 20 20 0")[0].segments
        _, c1, c2, p = seg
        self.assertAlmostEqual(c1[1], 40 / 3)
        self.assertAlmostEqual(c2[0], 20 + 2 / 3 * (10 - 20))

    def test_arc_lands_on_its_endpoint_on_the_ellipse(self):
        segs = S.parse_path("M 0 0 A 50 50 0 0 1 100 0")[0].segments
        self.assertTrue(all(s[0] == "C" for s in segs))
        self.assertEqual(segs[-1][-1], (100.0, 0.0))
        # the mid-arc point of a semicircle of radius 50 from (0,0) to (100,0), sweep 1, is (50, -50)
        mids = [s[-1] for s in segs[:-1]]
        self.assertTrue(any(abs(x - 50) < 1e-6 and abs(abs(y) - 50) < 1e-6 for x, y in mids), mids)

    def test_missing_numbers_are_an_error(self):
        with self.assertRaises(ValueError):
            S.parse_path("M 0 0 L 10")

    def test_transforms_compose_left_to_right(self):
        m = S.parse_transform("translate(10 0) scale(2)")
        self.assertEqual(S.apply(m, (1, 1)), (12.0, 2.0))
        r = S.parse_transform("rotate(90 5 5)")
        x, y = S.apply(r, (10, 5))
        self.assertAlmostEqual(x, 5)
        self.assertAlmostEqual(y, 10)

    def test_ellipse_approximation_stays_on_the_circle(self):
        e = S.ellipse_path(0, 0, 100, 100)
        worst = 0.0
        pts = [e.start] + [s[-1] for s in e.segments]
        for (x0, y0), (_, c1, c2, p) in zip(pts, e.segments):
            for t in (0.25, 0.5, 0.75):
                x = (1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t * t * c2[0] + t ** 3 * p[0]
                y = (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t * t * c2[1] + t ** 3 * p[1]
                worst = max(worst, abs(math.hypot(x, y) - 100))
        self.assertLess(worst, 0.03)


class SvgVertexTests(unittest.TestCase):
    def test_lines_are_straight_vertices_and_a_closed_duplicate_is_dropped(self):
        sq = S.rect_path(0, 0, 10, 10)
        vs = S.vertices(sq)
        self.assertEqual(len(vs), 4)
        self.assertTrue(all(v.startswith("<StraightVertex") for v in vs))

    def test_smooth_circle_gets_mirrored_vertices_with_the_out_handle_angle(self):
        vs = S.vertices(S.ellipse_path(0, 0, 100, 100))
        self.assertEqual(len(vs), 4)
        self.assertTrue(all(v.startswith("<CubicMirroredVertex") for v in vs))
        # at (100, 0) the path runs downward (+y), so the out handle points at +90 degrees
        first = vs[0]
        rot = float(re.search(r'rotation="([^"]+)"', first).group(1))
        self.assertAlmostEqual(rot, math.pi / 2, places=3)

    def test_a_corner_between_curves_is_detached(self):
        sub = S.parse_path("M0 0 C 0 -10 10 -10 10 0 C 20 10 30 10 30 0")[0]
        vs = S.vertices(sub)
        self.assertTrue(vs[1].startswith("<CubicDetachedVertex"), vs[1])


class SvgConvertTests(unittest.TestCase):
    SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="200" height="200">
      <defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/><stop offset="1" stop-color="#00f"/></linearGradient>
      <style>.c{fill:#1abc9c}</style></defs>
      <rect id="first" x="0" y="0" width="10" height="10" fill="url(#g)"/>
      <circle id="second" class="c" cx="50" cy="50" r="10" fill-opacity="0.5"/>
      <path id="hole" fill-rule="evenodd" d="M0 0h20v20h-20z M5 5h10v10h-10z"/>
      <line x1="0" y1="0" x2="10" y2="10" stroke="black" stroke-dasharray="4 2"/>
      <g display="none"><rect width="5" height="5"/></g>
      <text x="0" y="0">words</text>
      <use href="#second" x="10"/>
    </svg>"""

    def test_order_is_reversed_for_rives_first_on_top(self):
        rml, info = S.convert(self.SVG)
        first = rml.index('name="first"')
        second = rml.index('name="second"')
        self.assertGreater(first, second, "the SVG's first element paints under, so it must come last")

    def test_paint_details(self):
        rml, info = S.convert(self.SVG)
        self.assertIn('<LinearGradient', rml)
        self.assertIn('colorValue="801ABC9C"', rml)  # 50% fill-opacity folded into ARGB
        self.assertIn('fillRule="evenOdd"', rml)
        self.assertIn("<DashPath", rml)
        self.assertEqual(info["shapes"], 5)  # rect, circle, path, line, use; hidden group and text skipped
        self.assertTrue(any("text" in w for w in info["warnings"]))

    def test_viewbox_scale_and_fit(self):
        rml, info = S.convert(self.SVG)
        # viewBox 100 -> 200 px (x2); the <use> repeats the circle 10 units right: (60 + 10) * 2
        self.assertAlmostEqual(info["bbox"][2], 140.0)
        _, fitted = S.convert(self.SVG, fit=100, centre=(0, 0))
        x0, y0, x1, y1 = fitted["bbox"]
        self.assertAlmostEqual(max(x1 - x0, y1 - y0), 100, places=4)
        self.assertAlmostEqual((x0 + x1) / 2, 0, places=4)

    def test_ids_are_unique_and_valid(self):
        rml, _ = S.convert(self.SVG, base=5000)
        ids = re.findall(r'id="(\d+:\d+)"', rml)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(int(i.split(":")[1]) > 5000 for i in ids))

    def test_reveal_scene_wiring(self):
        rml, info = S.reveal_scene(self.SVG, 1920, 1080, None)
        ET.fromstring(rml)  # well-formed
        self.assertIn('<GroupEffect name="Draw" id="0:81">', rml)
        self.assertGreaterEqual(rml.count('<TargetEffect targetId="0:81"'), 2 * info["shapes"])
        self.assertIn('<KeyedObject objectId="0:82">\n                <KeyedProperty propertyKey="115">', rml)
        self.assertIn('defaultStateMachineId="0:7"', rml)
        ids = re.findall(r'\bid="(\d+:\d+)"', rml)
        self.assertEqual(len(ids), len(set(ids)))


# ==========================================================================
# rive_check lint
# ==========================================================================


class SvgUseCycleTests(unittest.TestCase):
    """A <use> that points back into its own ancestry is an error in SVG; it
    used to recurse until Python's stack ran out (RecursionError)."""

    CYCLES = {
        "ancestor": '<g id="a"><rect width="4" height="4"/><use href="#a" x="1"/></g>',
        "mutual": '<g id="a"><rect width="4" height="4"/><use href="#b"/></g>'
                  '<g id="b"><circle r="2"/><use href="#a"/></g>',
        "symbol": '<defs><symbol id="s"><rect width="3" height="3"/><use href="#s"/></symbol></defs>'
                  '<use href="#s"/>',
    }

    def test_a_cycle_is_cut_with_a_warning_and_the_rest_still_converts(self):
        for name, body in self.CYCLES.items():
            with self.subTest(name):
                svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{body}</svg>'
                rml, info = S.convert(svg)
                self.assertTrue(any("refers back" in w for w in info["warnings"]), info["warnings"])
                self.assertGreaterEqual(info["shapes"], 1)
                ET.fromstring(f"<r>{rml}</r>")

    def test_a_plain_use_is_still_expanded(self):
        # the fixture's <use href="#second"> draws the circle a second time
        rml, info = S.convert(SvgConvertTests.SVG)
        self.assertFalse(any("refers back" in w for w in info["warnings"]))
        self.assertEqual(rml.count('name="second"'), 2)
        self.assertEqual(info["shapes"], 5)


class LintTests(TempDir):
    def project(self, rml: str, luau: str | None = None) -> Path:
        proj = self.tmp / "p"
        proj.mkdir(exist_ok=True)
        (proj / "rive.yaml").write_text("name: p\n")
        (proj / "scene.rml").write_text(rml)
        if luau is not None:
            (proj / "main.luau").write_text(luau)
        return proj

    def kinds(self, rml, luau=None):
        return {f["kind"] for f in C.lint_markup(self.project(rml, luau))}

    def test_markup_rules(self):
        rml = """<Rive version="1" kind="fragment">
<LayoutComponentStyle paddingLeft="16" gapVertical="4" gapVerticalUnitsValue="points"/>
<TextStylePaint fontSize="12" fontAssetId="0:1"/>
<FontAsset name="NoFile"/>
<FontAsset file="/Library/Fonts/Arial.ttf" name="Sys"/>
<KeyboardInput keyType="down" keyPhase="7"/>
<KeyboardInput keyType="up"/>
<Shape childOrder="1"/>
<Fill name="F"><SolidColor colorValue="FFFFFFFF"/><Feather strength="4"/></Fill>
<DataBindContext nameBased="true"/>
<NestedSimpleAnimation animationId="0:3"/>
<TransitionValueIdComparator/>
<ScriptInputNumber name="speed"/>
<!-- render with rive_render.py --alpha -->
</Rive>"""
        kinds = self.kinds(rml, "type T = { velocity: Input<number> }")
        for k in ("units-undefined", "text-style-unlabelled", "font-without-file", "system-font-embedded",
                  "key-phase-seven", "key-phase-zero", "fractional-index", "feather-in-fill", "name-based-bind",
                  "nested-animation-paused", "abstract-comparator", "script-input-unmatched",
                  "comment-double-dash"):
            self.assertIn(k, kinds)

    def test_system_fonts_are_matched_by_folder_not_by_prefix(self):
        # ~/.local/share/fonts is where a Linux desktop installs a user's fonts
        user_linux = Path.home() / ".local" / "share" / "fonts" / "Brand.ttf"
        for ref, flagged in ((str(user_linux), True), ("/usr/share/fonts/truetype/x.ttf", True),
                             ("/usr/share/fontsnot/x.ttf", False), ("fonts/Inter.ttf", False)):
            with self.subTest(ref):
                kinds = self.kinds(f'<Rive version="1" kind="fragment"><FontAsset file="{ref}" name="F"/></Rive>')
                self.assertEqual("system-font-embedded" in kinds, flagged)

    def test_clean_markup_is_quiet(self):
        rml = """<Rive version="1" kind="fragment">
<LayoutComponentStyle paddingLeft="16" paddingLeftUnitsValue="points"/>
<TextStylePaint fontSize="12" fontAssetId="0:1" familyName="Inter" styleName="Bold"/>
<FontAsset file="Inter.ttf" name="Inter"/>
<KeyboardInput keyType="down" keyPhase="1"/>
<Shape childOrder="1/2"/>
<Stroke><SolidColor colorValue="FFFFFFFF"/><Feather strength="4"/></Stroke>
<NestedSimpleAnimation animationId="0:3" isPlaying="true"/>
<ScriptInputNumber name="speed"/>
</Rive>"""
        self.assertEqual(self.kinds(rml, "type T = { speed: Input<number> }"), set())

    def test_tree_rules(self):
        inspect = {"artboards": [{"type": "Artboard", "children": [
            {"type": "KeyFrameDouble", "frame": 3, "enums": {"interpolationType": "cubic"}, "children": []},
            {"type": "KeyFrameDouble", "frame": 4, "enums": {"interpolationType": "cubic"},
             "children": [{"type": "CubicEaseInterpolator"}]},
            {"type": "TextModifierGroup", "modifierFlags": 0},
            {"type": "LayoutComponent", "name": "Box"},
            {"type": "LayoutComponent", "name": "Ok", "styleId": "0:5"}]}],
            "roots": [{"type": "ViewModel", "children": [{"type": "ViewModelPropertyString", "name": "type"},
                                                         {"type": "ViewModelPropertyString", "name": "title"},
                                                         {"type": "ViewModelPropertyNumber", "name": "2x"}]}]}
        found = [f["kind"] for f in C.lint_tree(inspect)]
        self.assertEqual(found.count("curve-missing"), 1)
        self.assertEqual(found.count("layout-style-unlinked"), 1)
        self.assertEqual(found.count("vm-name"), 2)
        self.assertIn("modifier-flags-zero", found)

    def test_probe_values_never_equal_the_current_value(self):
        self.assertEqual(C.probe_value("boolean", True, "on"), "false")
        self.assertEqual(C.probe_value("boolean", False, "on"), "true")
        self.assertNotEqual(C.probe_value("number", 73.0, "n"), "73.0")
        self.assertEqual(C.probe_value("color", "FFFF00FF", "c"), "FF00FF00")
        self.assertTrue(C.probe_value("string", "x", "title").startswith("PROBE-"))
        self.assertIsNone(C.probe_value("trigger", None, "t"))


class CheckTestRunnerTests(TempDir):
    """rive_check runs `rive <dir> --test` whenever a project has Luau.

    The envelopes are the ones CLI 1.1.1 printed on 25 Sep 2026.
    """

    PASS = {"success": True, "command": "test", "data": {"passed": 2, "failed": 0, "noTestsFound": False,
                                                          "failures": []}, "errors": [], "warnings": []}
    FAIL = {"success": False, "command": "test",
            "data": {"passed": 1, "failed": 1, "noTestsFound": False,
                     "failures": [{"test": "clamp > keeps inside", "line": 10, "message": "3 is not equal to 4"}]},
            "errors": ["clamp > keeps inside: 3 is not equal to 4"], "warnings": []}
    NONE = {"success": True, "command": "test", "data": {"passed": 0, "failed": 0, "noTestsFound": True,
                                                          "failures": []}, "errors": [], "warnings": []}

    def run_with(self, envelope, code, luau=True):
        (self.tmp / "rive.yaml").write_text("name: t\n")
        if luau:
            (self.tmp / "clamp_tests.luau").write_text("return function(): Tests return function(test) end end\n")
        done = subprocess.CompletedProcess([], code, stdout=json.dumps(envelope), stderr="")
        report = {"errors": [], "warnings": []}
        with mock.patch.object(C.L, "run_rive", return_value=done) as run:
            C.run_tests(self.tmp, report)
        return report, run

    def test_passing_tests_are_counted(self):
        report, run = self.run_with(self.PASS, 0)
        self.assertEqual(run.call_args.args[0][1:], ["--test", "--format=json"])
        self.assertEqual((report["tests"]["passed"], report["tests"]["failed"]), (2, 0))
        self.assertEqual(report["errors"], [])

    def test_a_failing_case_is_an_error_naming_the_test(self):
        report, _ = self.run_with(self.FAIL, 6)
        self.assertEqual(report["errors"], ["test clamp > keeps inside (line 10): 3 is not equal to 4"])

    def test_no_tests_and_no_luau_stay_quiet(self):
        report, _ = self.run_with(self.NONE, 0)
        self.assertNotIn("tests", report)
        with tempfile.TemporaryDirectory() as other:
            self.tmp = Path(other)
            report, run = self.run_with(self.PASS, 0, luau=False)
        run.assert_not_called()
        self.assertNotIn("tests", report)

    def test_tests_that_cannot_run_are_an_error(self):
        broken = {"success": False, "command": "test", "data": {}, "errors": ["x:4 Expected identifier"],
                  "warnings": []}
        report, _ = self.run_with(broken, 1)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("could not run", report["errors"][0])
        self.assertIn("Expected identifier", report["errors"][0])


# ==========================================================================
# fonts, audio, recipes, versions, templates' starter
# ==========================================================================


class InteractionAndProbeArgsTests(unittest.TestCase):
    def test_the_pointer_leaves_after_every_click(self):
        # left over the control, a hover style reads as the click working:
        # measured on the button template with its click listener removed
        shots = C.interaction_shots(1.0, "210", "70", 0.5)
        for name in ("on", "off"):
            args = shots[name]
            clicks = [i for i, a in enumerate(args) if a.startswith("--pointer=click@")]
            self.assertTrue(clicks, name)
            for i in clicks:
                self.assertEqual(args[i + 1], C.AWAY, (name, args))

    def test_each_capture_is_compared_with_rest_at_the_same_scene_time(self):
        for at, settle in ((1.0, 0.5), (0.0, 0.5), (2.0, 0.25), (1.03, 0.4)):
            with self.subTest(at=at, settle=settle):
                shots = C.interaction_shots(at, "1", "2", settle)
                self.assertAlmostEqual(scene_time(shots["on"]), scene_time(shots["rest_on"]), places=6)
                self.assertAlmostEqual(scene_time(shots["off"]), scene_time(shots["rest_off"]), places=6)

    def test_a_probe_keeps_the_users_data_and_sets_its_own_value_last(self):
        # the CLI keeps the last --data for a path (measured), so the probe wins
        # for its own property while everything else stays as the user set it
        args = C.probe_args(["--artboard=A"], ["--data=name=Grace"], "unused", "PROBE", "--advance=60")
        self.assertEqual(args, ["--artboard=A", "--data=name=Grace", "--data=unused=PROBE", "--advance=60"])


class FontTests(unittest.TestCase):
    FONT = TEMPLATES / "_fonts" / "SpaceGrotesk-Variable.ttf"

    def test_packed_tags(self):
        self.assertEqual(F.pack_tag("wght"), 2003265652)
        self.assertEqual(F.pack_tag("tnum"), 1953396077)
        self.assertEqual(F.pack_tag("smcp"), 1936548720)

    def test_bundled_font_is_read_correctly(self):
        info = F.read_font(self.FONT)
        self.assertEqual(info["family"], "Space Grotesk")
        wght = info["axes"][0]
        self.assertEqual((wght["tag"], wght["min"], wght["max"], wght["default"]), ("wght", 300, 700, 300))
        self.assertIn("Bold", [i["name"] for i in info["instances"]])
        self.assertEqual(F.pick_instance(info, 700, None)[0], "Bold")
        self.assertEqual(F.pick_instance(info, 650, None)[0], "Custom")
        snip = F.snippet(info, file_name="x.ttf", weight=700)
        self.assertIn('familyName="Space Grotesk" styleName="Bold"', snip)
        self.assertIn('tag="2003265652" axisValue="700"', snip)


class FontDownloadTests(TempDir):
    def test_file_names_from_the_metadata_stay_inside_the_project(self):
        meta = ('fonts { name: "Evil" style: "normal" filename: "../../escape[wght].ttf" }\n'
                'fonts { name: "Evil" style: "normal" filename: "readme.txt" }\n')
        project = self.tmp / "a" / "b" / "proj"
        with mock.patch.object(F, "find_family", return_value=("ofl", "evil", meta)), \
                mock.patch.object(F, "_get", return_value=b"FONT"):
            written = F.add_family("Evil", project)
        self.assertEqual([p.name for p in written], ["escape-Variable.ttf"])
        self.assertEqual(sorted(p.name for p in project.iterdir()), ["escape-Variable.ttf", "evil-OFL.txt"])
        self.assertFalse((self.tmp / "a" / "escape-Variable.ttf").exists())
        self.assertFalse((self.tmp / "a" / "escape[wght].ttf").exists())


class AudioMathTests(TempDir):
    def test_smoothing_rises_fast_and_falls_slow(self):
        out = A.smooth([0, 1, 1, 1, 0, 0, 0], fps=25, attack=0.01, release=0.25)
        self.assertGreater(out[1], 0.9)
        self.assertGreater(out[5], 0.5)

    def test_normalise_and_onsets(self):
        self.assertEqual(max(A.normalise([0, 1, 2, 3, 100])), 1.0)
        self.assertEqual(A.onsets([0, 0, 1, 1])[2], 1.0)

    def test_log_bands(self):
        bands = A.log_bands(12)
        self.assertEqual(len(bands), 12)
        self.assertEqual(bands[0][1], 40.0)
        self.assertEqual(bands[-1][2], 12000.0)
        ratios = [b[2] / b[1] for b in bands]
        self.assertLess(max(ratios) - min(ratios), 0.01)

    @NEEDS_FFMPEG
    def test_a_kick_every_second_shows_in_the_low_band(self):
        path = self.tmp / "kick.wav"
        rate = 48000
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            frames = bytearray()
            for i in range(rate * 3):
                t = i / rate
                amp = 0.9 if (t % 1.0) < 0.1 else 0.02
                frames += struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * 70 * t)))
            w.writeframes(bytes(frames))
        doc = A.analyse(str(path), fps=25)
        low = doc["curves"]["low"]
        self.assertEqual(doc["frames"], 75)
        self.assertGreater(low[26], 0.8)   # just after the kick at 1.0 s
        self.assertLess(low[20], 0.4)      # before it, released


class RecipeTests(unittest.TestCase):
    def test_typing_is_seeded_monotonic_and_complete(self):
        a = RC.typing_times("hello world", 1.0, 10, 0.3, 7, 0.1)
        b = RC.typing_times("hello world", 1.0, 10, 0.3, 7, 0.1)
        self.assertEqual(a, b)
        self.assertEqual(a[-1][1], "hello world")
        times = [t for t, _ in a]
        self.assertEqual(times, sorted(times))
        self.assertEqual(a[0], (1.0, "h"))
        gap_after_space = a[6][0] - a[5][0]
        typical = a[2][0] - a[1][0]
        self.assertGreater(gap_after_space, typical * 0.9)

    def test_count_reaches_its_target_with_an_ease(self):
        keys = RC.count_keys(0, 1000, 0.5, 1.0, "expo-out", 0)
        self.assertEqual(keys[0], (0.5, 0))
        self.assertEqual(keys[-1][1], 1000)
        self.assertGreater(keys[len(keys) // 4][1], 500, "expo out covers most of the distance early")

    def test_string_keys_are_valid_rml(self):
        rml = RC.string_keys_rml([(1.0, 'a<"&')], "0:34")
        ET.fromstring(rml)
        self.assertIn('frame="60"', rml)


class VersionsTests(TempDir):
    def test_tables(self):
        (self.tmp / "t.csv").write_text("slug,title\nOne,Hello, World\n".replace("Hello, World", '"Hello, World"'))
        self.assertEqual(V.load_rows(self.tmp / "t.csv"), [{"slug": "One", "title": "Hello, World"}])
        (self.tmp / "t.json").write_text(json.dumps({"rows": [{"name": "A B"}]}))
        self.assertEqual(V.load_rows(self.tmp / "t.json"), [{"name": "A B"}])
        self.assertEqual(V.slugify("Ada Lovelace / 1843"), "Ada_Lovelace_1843")


class NewProjectTests(TempDir):
    def test_every_template_is_listed(self):
        self.assertEqual(N.available(), ["button", "counter", "logo_reveal", "lower_third", "ui_screen",
                                         "visualizer"])

    def test_create_copies_the_font_and_sets_defaults(self):
        dest = self.tmp / "My Supers"
        N.create("lower_third", dest, ['name=Ada "The" Lovelace', "title=Analyst & more"])
        rml = (dest / "scene.rml").read_text()
        self.assertIn('propertyValue="Ada &quot;The&quot; Lovelace"', rml)
        self.assertIn('propertyValue="Analyst &amp; more"', rml)
        self.assertIn('file="SpaceGrotesk-Variable.ttf"', rml)
        self.assertNotIn("../_fonts", rml)
        self.assertTrue((dest / "SpaceGrotesk-Variable.ttf").is_file())
        self.assertTrue((dest / "SpaceGrotesk-OFL.txt").is_file())
        self.assertIn("name: My_Supers", (dest / "rive.yaml").read_text())
        ET.fromstring(rml)
        with self.assertRaises(L.RiveError):
            N.create("lower_third", self.tmp / "x", ["nope=1"])


# ==========================================================================
# the web side, offline
# ==========================================================================


class WebOfflineTests(TempDir):
    def test_pins(self):
        pins = json.loads((SCRIPTS / "web_runtime.json").read_text())
        for name, pin in pins["packages"].items():
            self.assertIn(f"-{pin['version']}.tgz", pin["tarball"])
            self.assertTrue(pin["integrity"].startswith("sha512-"))
            self.assertIn("rive.wasm", pin["files"])
        self.assertIn(pins["default"], pins["packages"])

    def test_integrity_check(self):
        import base64
        import hashlib
        blob = b"rive"
        good = "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()
        self.assertTrue(W._verify_integrity(blob, good))
        self.assertFalse(W._verify_integrity(blob + b"!", good))
        self.assertFalse(W._verify_integrity(blob, "md5-xyz"))

    def fake_runtime(self):
        rt = self.tmp / "runtime"
        rt.mkdir(exist_ok=True)
        (rt / "rive.js").write_text("var rive={}; // </script> must be escaped")
        (rt / "rive.wasm").write_bytes(b"\0asm")
        return rt

    def test_folder_and_single_file_pages(self):
        riv = self.tmp / "a.riv"
        riv.write_bytes(b"RIVE")
        with mock.patch.object(WEB.W, "ensure_runtime", lambda flavour=None: self.fake_runtime()):
            page = WEB.build_page(riv, self.tmp / "site", title="T <x>", state_machine="Main", controls=True)
            text = page.read_text()
            self.assertTrue((self.tmp / "site" / "rive.wasm").is_file())
            self.assertIn('"stateMachine": "Main"', text)
            self.assertIn("T &lt;x&gt;", text)
            self.assertIn("setWasmFallbackUrl(null)", text)
            single = WEB.build_page(riv, self.tmp / "one.html", title="T", single=True)
            body = single.read_text()
            self.assertNotIn("// </script> must", body)
            self.assertIn("setWasmBinary", body)
            self.assertIn('id="riv"', body)

    def test_page_values_cannot_close_the_script(self):
        riv = self.tmp / "a.riv"
        riv.write_bytes(b"RIVE")
        hostile = "</script><script>alert(1)</script>"
        with mock.patch.object(WEB.W, "ensure_runtime", lambda flavour=None: self.fake_runtime()):
            page = WEB.build_page(riv, self.tmp / "site", title="T", data={"title": hostile}).read_text()
            self.assertNotIn("<script>alert", page)
            config = re.search(r"const CONFIG = (.*);", page).group(1)
            self.assertEqual(json.loads(config)["data"]["title"], hostile)
            for bad in ("red;}</style><script>x()</script>", "#12", "url(x)"):
                with self.subTest(bad), self.assertRaises(L.RiveError):
                    WEB.build_page(riv, self.tmp / "bad", title="T", background=bad)
            for good in ("transparent", "#fff", "#112233", "#11223344"):
                self.assertTrue(WEB.build_page(riv, self.tmp / "ok", title="T", background=good).is_file())

    def test_a_page_plays_a_state_machine_even_when_none_is_named(self):
        # with no name, rive.js 2.43.1 plays the first TIMELINE: listeners and
        # binds are dead (measured: the button page ignored a click). The page
        # reads the artboard's state machines first and names one itself, with
        # the singular `stateMachine` option (the plural one is deprecated).
        riv = self.tmp / "a.riv"
        riv.write_bytes(b"RIVE")
        with mock.patch.object(WEB.W, "ensure_runtime", lambda flavour=None: self.fake_runtime()):
            for single in (False, True):
                with self.subTest(single=single):
                    out = self.tmp / ("one.html" if single else "site")
                    page = WEB.build_page(riv, out, title="T", single=single).read_text()
                    self.assertIn("new rive.RiveFile(", page)
                    self.assertIn("stateMachineByIndex(", page)
                    self.assertIn("stateMachine: sm", page)
                    self.assertNotIn("stateMachines: CONFIG", page)

    def test_a_page_binds_only_a_file_that_has_a_view_model(self):
        # autoBind on a file with no view model logs a console error, which
        # made verify fail a page that plays fine (Rive's own rml_triangle)
        riv = self.tmp / "a.riv"
        riv.write_bytes(b"RIVE")
        with mock.patch.object(WEB.W, "ensure_runtime", lambda flavour=None: self.fake_runtime()):
            page = WEB.build_page(riv, self.tmp / "site", title="T").read_text()
        self.assertNotIn("autoBind: true", page)
        self.assertIn("autoBind: f.viewModelCount() > 0", page)

    def test_the_panel_starts_from_the_file_and_follows_it(self):
        # measured before: every colour input read #000000 whatever the file
        # held and followed no change, a pick wrote alpha FF over an 85% plate,
        # and an enum select kept its first value (LiveWebTests has the numbers)
        riv = self.tmp / "a.riv"
        riv.write_bytes(b"RIVE")
        with mock.patch.object(WEB.W, "ensure_runtime", lambda flavour=None: self.fake_runtime()):
            page = WEB.build_page(riv, self.tmp / "site", title="T", controls=True).read_text()
        colour = page[page.index('p.type === "color"'):page.index('p.type === "trigger"')]
        self.assertIn("input.value = hex()", colour)
        self.assertIn("c.rgb(", colour)
        self.assertIn("c.on(", colour)
        self.assertNotIn('"FF" +', colour)
        self.assertIn("e.on(", page[page.index('p.type === "enumType"'):])

    def test_a_failed_session_start_stops_what_it_started(self):
        import types
        riv = self.tmp / "a.riv"
        riv.write_bytes(b"RIVE")
        state = {"browser_closed": False, "pw_stopped": False, "tmp": None}

        class Browser:
            def new_context(self, **kw):
                raise RuntimeError("no display")

            def close(self):
                state["browser_closed"] = True

        class Playwright:
            chromium = types.SimpleNamespace(launch=lambda args: Browser())

            def stop(self):
                state["pw_stopped"] = True

        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = lambda: types.SimpleNamespace(start=lambda: Playwright())
        real_mkdtemp = W.tempfile.mkdtemp

        def spy(*a, **kw):
            state["tmp"] = real_mkdtemp(*a, dir=str(self.tmp), **{k: v for k, v in kw.items() if k != "dir"})
            return state["tmp"]

        with mock.patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                           "playwright.sync_api": fake}), \
                mock.patch.object(W, "ensure_runtime", lambda flavour=None: self.fake_runtime()), \
                mock.patch.object(W.tempfile, "mkdtemp", spy):
            session = W.WebSession(riv)
            with self.assertRaises(RuntimeError):
                session.__enter__()
        self.assertTrue(state["browser_closed"])
        self.assertTrue(state["pw_stopped"])
        self.assertIsNotNone(state["tmp"])
        self.assertFalse(Path(state["tmp"]).exists())
        self.assertIsNone(session._server)


@NEEDS_FFMPEG
class WebVerifyClickTests(TempDir):
    """verify --click compares screenshots taken with the real clock, so a page
    that moves by itself (Rive's spinning rml_triangle) read as a click that
    worked, even on an empty corner (measured). It now looks twice first."""

    def verify(self, colours: list) -> dict:
        import types
        page_file = self.tmp / "index.html"
        page_file.write_text("<html></html>")
        shots = iter(colours)

        class Page:
            mouse = types.SimpleNamespace(click=lambda x, y: None)

            def on(self, *a):
                pass

            def goto(self, url):
                pass

            def evaluate(self, script):
                if "getBoundingClientRect" in script:
                    return [0, 0, 100, 100]
                return {"loaded": True, "artboard": {"width": 100, "height": 100}, "stateMachines": ["SM"]}

            def screenshot(self, path):
                colour = next(shots)
                write_png(Path(path), 4, 4, lambda x, y: (*colour, 255) if (x, y) != (0, 0) else (9, 9, 9, 255))

        class Browser:
            def new_context(self, **kw):
                return types.SimpleNamespace(new_page=lambda: Page())

            def close(self):
                pass

        @contextlib.contextmanager
        def sync_playwright():
            yield types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda args: Browser()))

        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = sync_playwright
        with mock.patch.dict(sys.modules, {"playwright": types.ModuleType("playwright"),
                                           "playwright.sync_api": fake}):
            return WEB.verify_page(page_file, click=(5, 5), wait=0, shots=self.tmp / "shots")

    def test_a_page_that_moves_by_itself_cannot_credit_the_click(self):
        report = self.verify([(200, 0, 0), (0, 200, 0), (0, 0, 200)])
        self.assertIsNone(report["click_changed_picture"])
        self.assertIn("changes on its own", report["click_note"])

    def test_a_still_page_credits_a_click_that_changes_it(self):
        self.assertTrue(self.verify([(200, 0, 0), (200, 0, 0), (0, 200, 0)])["click_changed_picture"])

    def test_a_still_page_reports_a_dead_click(self):
        self.assertFalse(self.verify([(200, 0, 0)] * 3)["click_changed_picture"])


# ==========================================================================
# templates, manifests and docs
# ==========================================================================


class TemplateStaticTests(unittest.TestCase):
    def each(self):
        for name in N.available():
            yield name, TEMPLATES / name, (TEMPLATES / name / "scene.rml").read_text(encoding="utf-8")

    def test_every_template_is_well_formed_with_unique_ids(self):
        for name, _, rml in self.each():
            with self.subTest(name):
                ET.fromstring(rml)
                ids = re.findall(r'\bid="(\d+:\d+)"', rml)
                self.assertEqual(len(ids), len(set(ids)), f"{name}: duplicate ids")

    def test_every_artboard_has_a_default_state_machine_and_a_style(self):
        for name, _, rml in self.each():
            for tag in re.findall(r"<Artboard\b[^>]*>", rml, flags=re.S):
                with self.subTest(name, tag=tag[:60]):
                    self.assertIn("defaultStateMachineId=", tag)
                    self.assertIn("styleId=", tag)

    def test_asset_files_resolve_and_fonts_are_licensed(self):
        for name, folder, rml in self.each():
            for ref in re.findall(r'\bfile="([^"]+)"', rml):
                with self.subTest(name, ref=ref):
                    self.assertTrue((folder / ref).resolve().is_file())
                    self.assertFalse(ref.startswith("/"))
        self.assertTrue((TEMPLATES / "_fonts" / "SpaceGrotesk-OFL.txt").read_text().startswith("Copyright"))

    def test_text_styles_are_labelled_for_the_editor(self):
        for name, folder, _ in self.each():
            with self.subTest(name):
                self.assertEqual([f for f in C.lint_markup(folder) if f["severity"] == "error"], [])
                self.assertFalse([f for f in C.lint_markup(folder) if f["kind"] == "text-style-unlabelled"])


class DoctorInstallTests(TempDir):
    """The Linux install, laid out the way Rive's own install.sh lays it out.

    Measured on CLI 1.1.1, Linux x64: `rive docs` and `rive samples --path`
    look beside versions/<version>/rive only. With docs/ and samples/ in
    ~/.rive/ both commands fail ("not found beside the binary") and the
    doctor reports NOT READY straight after a clean install.
    """

    def tarball(self) -> bytes:
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            def add(name, data=None, kind=tarfile.REGTYPE, mode=0o644, link=""):
                info = tarfile.TarInfo(name)
                info.type, info.mode, info.linkname = kind, mode, link
                if data is not None:
                    info.size = len(data)
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
            add("rive", b"#!/bin/sh\necho rive\n", mode=0o755)
            add("docs", kind=tarfile.DIRTYPE, mode=0o755)
            add("docs/README.md", b"# docs\n")
            add("docs/escape", kind=tarfile.SYMTYPE, link="/etc/passwd")
            add("samples", kind=tarfile.DIRTYPE, mode=0o755)
            add("samples/rml_triangle", kind=tarfile.DIRTYPE, mode=0o755)
            add("samples/rml_triangle/rive.yaml", b"name: triangle\n")
        return buf.getvalue()

    def install(self, manifest: dict, blob: bytes) -> tuple[int, Path]:
        import hashlib
        home = Path(tempfile.mkdtemp(prefix="home_", dir=self.tmp))
        for art in manifest.get("artifacts", {}).values():
            art.setdefault("sha256", hashlib.sha256(blob).hexdigest())

        def urlopen(url, timeout=None):
            return io.BytesIO(json.dumps(manifest).encode() if url.endswith("manifest.json") else blob)

        with mock.patch.object(D.urllib.request, "urlopen", urlopen), \
                mock.patch.object(D.platform, "system", lambda: "Linux"), \
                mock.patch.object(D.platform, "machine", lambda: "x86_64"), \
                mock.patch.object(D.Path, "home", classmethod(lambda cls: home)), quiet():
            code = D.install()
        return code, home / ".rive"

    def test_docs_and_samples_sit_beside_the_versioned_binary(self):
        manifest = {"version": "1.1.1", "artifacts": {"linux-x64": {"path": "v1.1.1/rive-linux-x64.tar.gz"}}}
        code, rive_home = self.install(manifest, self.tarball())
        self.assertEqual(code, 0)
        payload = rive_home / "versions" / "1.1.1"
        self.assertTrue(os.access(payload / "rive", os.X_OK))
        self.assertTrue((payload / "docs" / "README.md").is_file())
        self.assertTrue((payload / "samples" / "rml_triangle" / "rive.yaml").is_file())
        self.assertFalse((rive_home / "docs").exists())
        self.assertFalse((rive_home / "samples").exists())
        self.assertFalse(os.path.lexists(payload / "docs" / "escape"), "a symlink in the archive never lands")
        self.assertEqual((rive_home / "bin" / "rive").read_bytes(), (payload / "rive").read_bytes())
        self.assertTrue(os.access(rive_home / "bin" / "rive", os.X_OK))
        self.assertEqual((rive_home / "current").read_text(), "1.1.1\n")
        self.assertEqual((rive_home / "default").read_text(), "1.1.1\n")

    def test_a_manifest_that_would_climb_out_installs_nothing(self):
        for version, path in (("../../x", "v../../x/rive.tar.gz"), ("1.1.1", "v1.1.1/../../rive.tar.gz"),
                              ("1.1.1", "elsewhere/rive.tar.gz")):
            with self.subTest(version=version, path=path):
                manifest = {"version": version, "artifacts": {"linux-x64": {"path": path}}}
                code, rive_home = self.install(manifest, self.tarball())
                self.assertEqual(code, 1)
                self.assertFalse(rive_home.exists())


class DoctorFlagTests(unittest.TestCase):
    def help_text(self, drop: str | None) -> str:
        lines = [f"  {f.split('=')[0]}=<x>   a flag" for f in D.FLAGS_USED if f != drop]
        lines += ["  --data-dump-every=<N>   per frame; combines with --data, --once and --pointer"]
        lines += [f"  {c}   a command" for c in D.SUBCOMMANDS_USED]
        return "\n".join(lines) + "\n"

    def flags(self, text: str) -> dict:
        results: list = []
        with mock.patch.object(D.L, "run_rive", lambda *a, **k: subprocess.CompletedProcess(a, 0, text, "")):
            D.flags(results)
        return {r["check"]: r for r in results}

    def test_every_flag_in_use_is_found_in_a_full_help(self):
        self.assertTrue(self.flags(self.help_text(drop=None))["flags"]["ok"])

    def test_a_flag_counts_only_where_help_defines_it(self):
        # --data survives as a substring of --data-dump and in other flags' prose
        for gone in ("--data", "--once"):
            with self.subTest(gone):
                check = self.flags(self.help_text(drop=gone))["flags"]
                self.assertFalse(check["ok"])
                self.assertIn(f"{gone} --", check["detail"])

    def two_column_help(self, drop: str | None) -> str:
        """1.1.1's layout: definitions at 2 spaces, wrapped descriptions at 28,
        and a wrapped line may begin with a flag (two begin "--semantics or")."""
        lines = []
        for f in D.FLAGS_USED:
            name = f.split("=")[0]
            if f != drop:
                lines.append(f"  {name + '=<x>':<26}what it does, which wraps")
            lines.append(" " * 28 + f"{name} or --data-dump")
        lines += [f"  {c:<26}a command" for c in D.SUBCOMMANDS_USED]
        return "\n".join(lines) + "\n"

    def test_a_flag_that_begins_a_wrapped_line_is_not_defined_by_it(self):
        # measured on the real help: any indent read --semantics as present
        # with its definition removed (18 of 19 caught)
        self.assertTrue(self.flags(self.two_column_help(drop=None))["flags"]["ok"])
        for gone in D.FLAGS_USED:
            with self.subTest(gone):
                check = self.flags(self.two_column_help(drop=gone))["flags"]
                self.assertFalse(check["ok"])
                self.assertIn(f"{gone} --", check["detail"])

    @unittest.skipUnless(RIVE, "needs the Rive CLI")
    def test_the_real_help_loses_each_flag_with_its_definition(self):
        helped = L.run_rive(["--help"], timeout=30)
        text = helped.stdout + helped.stderr
        self.assertEqual([f for f in D.FLAGS_USED if not D.defines(text, f)], [])
        for f in D.FLAGS_USED:
            with self.subTest(f):
                pattern = re.compile(rf"^  {re.escape(f.split('=')[0])}(?=[=\[ \t]|$)")
                kept = [line for line in text.splitlines() if not pattern.match(line)]
                self.assertLess(len(kept), len(text.splitlines()), "no definition line at the 2 space column")
                self.assertFalse(D.defines("\n".join(kept) + "\n", f))


class ManifestTests(unittest.TestCase):
    def test_deps_manifest_is_valid_and_read(self):
        from core import self_install
        deps = json.loads((SKILL / "deps.json").read_text())
        self.assertTrue(self_install._validate_deps(deps, "rive"))
        consumed = set(self_install._PKG_KEYS) | {"check", "pip", "npm", "post_install", "weight", "min_ram_gb",
                                                  "min_disk_gb", "install_note"}
        self.assertEqual(set(deps) - consumed, set())
        self.assertIn("rive_doctor.py --install", deps["install_note"])
        # the check name is what the installer would `brew install`; `rive` is the EDITOR cask
        self.assertNotIn("rive", deps["check"])

    def test_hooks_never_reap_by_name(self):
        hooks = json.loads((SKILL / "hooks.json").read_text())
        self.assertNotIn("stop", hooks)
        self.assertEqual(hooks["pre"]["min_ram_mb"], 1024)

    def test_readme_row(self):
        readme = (REPO / "README.md").read_text()
        self.assertRegex(readme, r"(?m)^\| rive \|")
        hooks = len(list((REPO / "skills").glob("*/hooks.json")))
        self.assertIn(f"{hooks} skills carry a per skill `hooks.json`", readme)


class DocTests(unittest.TestCase):
    def setUp(self):
        self.doc = (SKILL / "SKILL.md").read_text()

    def test_every_script_and_reference_named_exists(self):
        for script in set(re.findall(r"scripts/(\w+\.py)", self.doc)):
            self.assertTrue((SCRIPTS / script).is_file(), script)
        for ref in set(re.findall(r"references/(\w+\.md)", self.doc)):
            self.assertTrue((SKILL / "references" / ref).is_file(), ref)
        for name in N.available():
            self.assertIn(f"`{name}`", self.doc)

    def test_the_numbers_in_the_doc_match_the_code(self):
        self.assertIn("0.01 ms", self.doc)
        self.assertAlmostEqual(L.FIRST_FRAME_EPSILON * 1000, 0.01)
        self.assertIn("a click is 3 frames", self.doc)
        self.assertEqual(sum(s.frames for s in T.expand_event({"at": 0, "click": [0, 0]})), 3)
        sources = (SKILL / "references" / "sources.md").read_text()
        for version in L.TESTED_CLI_VERSIONS:
            self.assertIn(version, sources)

    def test_no_blockquotes_in_the_docs(self):
        for path in [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]:
            with self.subTest(path.name):
                self.assertNotRegex(path.read_text(), r"(?m)^>")


# ==========================================================================
# live: the real CLI
# ==========================================================================


@LIVE
class LiveTemplateTests(unittest.TestCase):
    def test_every_template_builds_inspects_clean_and_draws(self):
        for name in N.available():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                folder = TEMPLATES / name
                verify = L.run_rive([str(folder), "--verify"], timeout=120)
                self.assertEqual(verify.returncode, 0, verify.stderr[-400:])
                inspect = L.inspect_project(folder)
                self.assertEqual([p for p in L.problems(inspect) if p.get("severity") == "error"], [])
                png = Path(tmp) / "t.png"
                snap = L.snapshot_project(folder, tmp)
                cap = L.run_rive([str(snap), "--quiet", f"--screenshot={png}", "--advance=2s"], timeout=120)
                self.assertEqual(cap.returncode, 0, cap.stderr[-400:])
                self.assertIsNone(L.blank_reason(png))
                self.assertFalse((folder / "build").exists(), "templates must not collect build output")

    def test_the_gate_runs_luau_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = N.create("counter", Path(tmp) / "p", [])
            (proj / "mathutil.luau").write_text(
                "local M = {}\nfunction M.clamp(x: number, lo: number, hi: number): number\n"
                "    if x < lo then return lo end\n    if x > hi then return hi end\n    return x\nend\nreturn M\n")
            case = ("local mathutil = require('mathutil')\n\nreturn function(): Tests\n"
                    "    return function(test: Tester)\n        test.group('clamp', function()\n"
                    "            test.case('keeps inside', function(expect)\n"
                    "                expect(mathutil.clamp(3, 0, 5)).is({want})\n"
                    "            end)\n        end)\n    end\nend\n")
            for want, code_expected in ((3, 0), (4, 1)):
                (proj / "clamp_tests.luau").write_text(case.replace("{want}", str(want)))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = C.main([str(proj), "--out", str(Path(tmp) / f"o{want}"), "--json"])
                report = json.loads(buf.getvalue())
                self.assertEqual(code, code_expected, report["errors"])
                self.assertEqual(report["tests"]["passed"] + report["tests"]["failed"], 1)
                if want == 4:
                    self.assertIn("test clamp > keeps inside (line 7): 3 is not equal to 4", report["errors"])

    def test_the_button_toggles_both_ways(self):
        with tempfile.TemporaryDirectory() as tmp, quiet():
            code = C.main([str(TEMPLATES / "button"), "--interaction", "click@210,70", "--out", tmp, "--json"])
        self.assertEqual(code, 0)

    def test_a_click_that_only_hovers_fails_the_gate(self):
        # the button with its click listener removed still scales on hover; with
        # the pointer left over it after the click, the gate passed this dead
        # toggle exactly as it passes the real one (measured on CLI 1.1.1)
        with tempfile.TemporaryDirectory() as tmp:
            proj = N.create("button", Path(tmp) / "dead", [])
            scene = proj / "scene.rml"
            text, removed = re.subn(r'\s*<StateMachineListenerSingle[^>]*listenerTypeValue="click"[^>]*>.*?'
                                    r'</StateMachineListenerSingle>', "", scene.read_text(), flags=re.S)
            self.assertEqual(removed, 1)
            scene.write_text(text)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = C.main([str(proj), "--interaction", "click@210,70", "--out", str(Path(tmp) / "o"), "--json"])
            report = json.loads(buf.getvalue())
            self.assertEqual(code, 1, report["errors"])
            self.assertFalse(report["interaction"]["responds"])

    def test_probing_with_data_set_keeps_an_unbound_property_inert(self):
        # the probes dropped the user's --data, so with --data set every
        # property, even one bound to nothing, read "drives the picture"
        with tempfile.TemporaryDirectory() as tmp:
            proj = N.create("lower_third", Path(tmp) / "p", [])
            scene = proj / "scene.rml"
            text = scene.read_text()
            prop = '<ViewModelPropertyColor name="ink" id="0:66"/>'
            value = '<ViewModelInstanceColor propertyValue="FFFFFFFF" viewModelPropertyId="0:66"/>'
            self.assertIn(prop, text)
            self.assertIn(value, text)
            text = text.replace(prop, prop + '<ViewModelPropertyString name="unused" id="0:900"/>')
            text = text.replace(value, value + '<ViewModelInstanceString propertyValue="x" viewModelPropertyId="0:900"/>')
            scene.write_text(text)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                C.main([str(proj), "--at", "2", "--probe-binds", "--data", "name=Grace Hopper",
                        "--out", str(Path(tmp) / "o"), "--json"])
            binds = json.loads(buf.getvalue())["binds"]
            self.assertTrue(binds["unused"].startswith("no visible effect"), binds)
            self.assertTrue(binds["title"].startswith("drives"), binds)

    def test_every_bound_property_in_the_lower_third_drives_the_picture(self):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
            code = C.main([str(TEMPLATES / "lower_third"), "--at", "2", "--probe-binds", "--out", tmp, "--json"])
        report = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(all(v.startswith("drives") for v in report["binds"].values()), report["binds"])


@LIVE
class LiveTimingTests(unittest.TestCase):
    def dump_last_frame(self, args):
        with tempfile.TemporaryDirectory() as tmp:
            snap = L.snapshot_project(TEMPLATES / "button", tmp)
            dump = Path(tmp) / "d.jsonl"
            L.run_rive([str(snap), "--quiet", f"--data-dump={dump}", "--data-dump-every=1", *args], timeout=120)
            lines = [json.loads(x) for x in dump.read_text().splitlines() if x.strip()]
            return lines[-1]["frame"], lines[-1]["time"]

    def test_the_compiled_timeline_lands_exactly_on_each_frame_time(self):
        tl = T.build_timeline([{"at": 0.2, "click": [210, 70]}, {"at": 0.5, "key": "enter"},
                               {"at": 0.7, "drag": [10, 10, 100, 100], "steps": 4}])
        # CLI 1.1.1 on Linux segfaults writing --data-dump-every once a key is in
        # the run (a handled key, or any key before a drag; also on Rive's own
        # keyboard_menu sample). The same arguments with --screenshot render
        # fine, and no script dumps per frame with keys, so only this
        # measurement is out of reach there (references/rendering.md, Linux).
        linux_dump_crash = sys.platform.startswith("linux") and L.cli_version() == "1.1.1"
        for t in (0.1, 0.25, 0.6, 1.0):
            args = tl.frame_args(t).args
            with self.subTest(t=t):
                if linux_dump_crash and any(a.startswith("--key=") for a in args):
                    self.skipTest("Rive CLI 1.1.1 on Linux crashes in --data-dump-every with a key in the run")
                frame, time_ = self.dump_last_frame(args)
                self.assertAlmostEqual(time_, t, places=3)
                self.assertEqual(frame, round(t * 60))

    def test_click_cost_is_three_frames(self):
        frame, _ = self.dump_last_frame(["--advance=6", "--pointer=click@210,70", "--advance=6"])
        self.assertEqual(frame, 15)

    def test_frame_zero_advance_is_not_the_rest_pose(self):
        # the lower third's accent bar is keyed to scaleY 0 at frame 0: visible
        # at rest, gone once the entry animation applies.
        with tempfile.TemporaryDirectory() as tmp:
            snap = L.snapshot_project(TEMPLATES / "lower_third", tmp)
            rest, first = Path(tmp) / "rest.png", Path(tmp) / "first.png"
            L.run_rive([str(snap), "--quiet", f"--screenshot={rest}", "--advance=0"], timeout=60)
            L.run_rive([str(snap), "--quiet", f"--screenshot={first}",
                        L.advance_arg(L.FIRST_FRAME_EPSILON)], timeout=60)
            self.assertNotEqual(L.sha256_file(rest), L.sha256_file(first))


@LIVE
class LiveRenderTests(unittest.TestCase):
    def render(self, argv):
        self.stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(self.stderr):
            code = R.main(argv)
        return code

    def test_counter_mp4_with_a_count_curve(self):
        with tempfile.TemporaryDirectory() as tmp:
            curve = Path(tmp) / "count.json"
            with quiet():
                RC.main(["count", "--path", "value", "--to", "900", "--duration", "0.3", "-o", str(curve)])
            out = Path(tmp) / "c.mp4"
            code = self.render([str(TEMPLATES / "counter"), "-o", str(out), "--duration", "0.5", "--fps", "20",
                                "--timeline", str(curve), "--workers", "4"])
            self.assertEqual(code, 0)
            report = json.loads(Path(str(out) + ".render.json").read_text())
            probe = report["probe"]
            self.assertEqual(int(probe["nb_read_frames"]), 10)
            self.assertEqual((probe["color_primaries"], probe["color_transfer"]), ("bt709", "bt709"))
            self.assertEqual(report["sample_args"]["0"][-1], "--advance=0.01ms")

    def test_alpha_solve_recomposites_within_a_few_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "lt.mov"
            code = self.render([str(TEMPLATES / "lower_third"), "-o", str(out), "--alpha", "--frames", "3",
                                "--start", "1.5", "--fps", "25", "--workers", "4"])
            self.assertEqual(code, 0)
            report = json.loads(Path(str(out) + ".render.json").read_text())
            self.assertEqual(report["probe"]["profile"], "4444")
            for check in report["alpha_check"]:
                if "max" in check:
                    self.assertLessEqual(check["max"], 4.0, check)
                else:
                    self.assertGreater(check["psnr_db"], 40, check)

    def test_an_alpha_solve_that_does_not_recomposite_is_refused(self):
        # a difference blend over a 50% fill is not plain source-over, so the
        # black and white passes cannot solve it: measured 143 codes off
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "blend"
            proj.mkdir()
            (proj / "rive.yaml").write_text("name: blend\n")
            (proj / "scene.rml").write_text(
                '<Rive version="1" kind="fragment"><Artboard defaultStateMachineId="0:7" styleId="0:3" '
                'width="64" height="64" name="A" id="0:2"><LayoutComponentStyle name="S" id="0:3"/>'
                '<Shape x="28" y="28" name="Under" id="0:20"><Rectangle width="36" height="36" name="P1"/>'
                '<Fill name="F1"><SolidColor colorValue="80E04040" name="C1"/></Fill></Shape>'
                '<Shape x="38" y="38" blendModeValue="difference" name="Over" id="0:21">'
                '<Ellipse width="36" height="36" name="P2"/>'
                '<Fill name="F2"><SolidColor colorValue="FF57A5E0" name="C2"/></Fill></Shape>'
                '<LinearAnimation duration="1" name="X" id="0:6"/><StateMachine name="SM" id="0:7">'
                '<StateMachineLayer name="L" id="0:8"><AnyState/><ExitState/><EntryState>'
                '<StateTransition stateToId="0:9"/></EntryState><AnimationState animationId="0:6" id="0:9"/>'
                '</StateMachineLayer></StateMachine></Artboard></Rive>')
            out = Path(tmp) / "x.mov"
            argv = [str(proj), "-o", str(out), "--alpha", "--frames", "2", "--fps", "25", "--workers", "2"]
            self.assertEqual(self.render(argv), 1)
            self.assertIn("does not recomposite", self.stderr.getvalue())
            self.assertIn("--allow-bad-alpha", self.stderr.getvalue())
            self.assertFalse(out.exists())
            self.assertEqual(self.render(argv + ["--allow-bad-alpha"]), 0, self.stderr.getvalue())
            report = json.loads(Path(str(out) + ".render.json").read_text())
            self.assertEqual(report["probe"]["profile"], "4444")
            self.assertTrue(any("written anyway" in w for w in report["warnings"]), report["warnings"])

    def test_opaque_background_refuses_alpha(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self.render([str(TEMPLATES / "counter"), "-o", str(Path(tmp) / "x.mov"), "--alpha",
                                "--frames", "2"])
            self.assertEqual(code, 1)
            self.assertIn("opaque background fill", self.stderr.getvalue())

    def test_a_blank_scene_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "empty"
            proj.mkdir()
            (proj / "rive.yaml").write_text("name: empty\n")
            (proj / "scene.rml").write_text(
                '<Rive version="1" kind="fragment"><Artboard defaultStateMachineId="0:7" styleId="0:3" '
                'width="64" height="64" name="A" id="0:2"><LayoutComponentStyle name="S" id="0:3"/>'
                '<LinearAnimation duration="1" name="X" id="0:6"/><StateMachine name="SM" id="0:7">'
                '<StateMachineLayer name="L" id="0:8"><AnyState/><ExitState/><EntryState>'
                '<StateTransition stateToId="0:9"/></EntryState><AnimationState animationId="0:6" id="0:9"/>'
                '</StateMachineLayer></StateMachine></Artboard></Rive>')
            code = self.render([str(proj), "-o", str(Path(tmp) / "x.mp4"), "--frames", "3", "--keep-backdrop"])
            self.assertEqual(code, 1)
            self.assertIn("every sampled frame is empty", self.stderr.getvalue())
            self.assertIn("nothing was drawn", self.stderr.getvalue())


@unittest.skipUnless(RIVE and FFMPEG and importlib.util.find_spec("playwright"),
                     "needs the Rive CLI, ffmpeg and Playwright with Chromium")
class LiveWebTests(unittest.TestCase):
    def test_a_page_built_without_a_state_machine_name_answers_a_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = N.create("button", Path(tmp) / "b", [])
            self.assertEqual(L.run_rive([str(proj), "--once", "--quiet"], timeout=120).returncode, 0)
            riv = next((proj / "build").glob("*.riv"))
            with quiet():
                page = WEB.build_page(riv, Path(tmp) / "site", title="Button", size=(420, 140))
            report = WEB.verify_page(page, click=(210, 70), shots=Path(tmp) / "shots")
            self.assertTrue(report["ok"], report)
            self.assertTrue(report["click_changed_picture"], report)
            self.assertFalse([m for m in report["console"] if "deprecat" in m or "default-state-machine" in m],
                             report["console"])

    @contextlib.contextmanager
    def served(self, page: Path):
        """The page open in headless Chromium over HTTP, as verify_page opens it."""
        import http.server
        import threading
        import time
        from playwright.sync_api import sync_playwright

        folder = page.parent

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(folder), **kw)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(args=W.CHROMIUM_ARGS)
                pg = browser.new_page()
                pg.goto(f"http://127.0.0.1:{server.server_address[1]}/{page.name}")
                deadline = time.time() + 30
                while not pg.evaluate("window.__rive || null") and time.time() < deadline:
                    time.sleep(0.1)
                self.assertTrue(pg.evaluate("window.__rive && window.__rive.loaded"))
                yield pg
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

    READ_CONTROLS = """() => { const vmi = window.__r.viewModelInstance, out = {controls: {}, colours: {}};
      for (const label of document.querySelectorAll("#controls label")) {
        const input = label.parentElement.querySelector("input, select");
        if (input) out.controls[label.textContent] = input.value; }
      for (const p of vmi.properties) if (p.type === "color")
        out.colours[p.name] = (vmi.color(p.name).value >>> 0).toString(16).toUpperCase();
      return out; }"""

    def settle(self, pg):
        # property callbacks run on the next advance of the player
        pg.wait_for_timeout(400)
        return pg.evaluate(self.READ_CONTROLS)

    def test_the_colour_controls_start_from_the_file_and_keep_its_alpha(self):
        # measured before the fix: every colour input read #000000, a colour
        # the scene set was not followed, and a pick on the 85% plate made it
        # opaque (D90A0A0A to FF112233)
        with tempfile.TemporaryDirectory() as tmp:
            proj = N.create("lower_third", Path(tmp) / "lt", [])
            self.assertEqual(L.run_rive([str(proj), "--once", "--quiet"], timeout=120).returncode, 0)
            riv = next((proj / "build").glob("*.riv"))
            with quiet():
                page = WEB.build_page(riv, Path(tmp) / "site", title="LT", size=(960, 540), controls=True)
            with self.served(page) as pg:
                state = self.settle(pg)
                self.assertEqual(state["colours"], {"accent": "FFC9A84C", "plate": "D90A0A0A", "ink": "FFFFFFFF"})
                self.assertEqual({k: state["controls"][k] for k in ("accent", "plate", "ink")},
                                 {"accent": "#c9a84c", "plate": "#0a0a0a", "ink": "#ffffff"})
                pg.evaluate("() => { window.__r.viewModelInstance.color('accent').value = 0xFF2266AA | 0; }")
                self.assertEqual(self.settle(pg)["controls"]["accent"], "#2266aa")
                pg.evaluate("""() => { for (const label of document.querySelectorAll("#controls label"))
                    if (label.textContent === "plate") { const input = label.parentElement.querySelector("input");
                      input.value = "#112233"; input.dispatchEvent(new Event("input")); } }""")
                state = self.settle(pg)
                self.assertEqual(state["colours"]["plate"], "D9112233")
                self.assertEqual(state["controls"]["plate"], "#112233")

    def test_an_enum_control_follows_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "mood"
            proj.mkdir()
            (proj / "rive.yaml").write_text("name: mood\n")
            (proj / "scene.rml").write_text(
                '<Rive version="1" kind="fragment"><DataEnumCustom name="Mood" id="0:70">'
                '<DataEnumValue key="calm" value="Calm" id="0:71"/><DataEnumValue key="loud" value="Loud" id="0:72"/>'
                '<DataEnumValue key="dark" value="Dark" id="0:73"/></DataEnumCustom>'
                '<Artboard defaultStateMachineId="0:7" viewModelId="0:60" viewModelInstanceId="0:61" styleId="0:3" '
                'width="64" height="64" name="A" id="0:2"><LayoutComponentStyle name="S" id="0:3"/>'
                '<Shape x="32" y="32" name="Dot" id="0:20"><Ellipse width="40" height="40" name="P"/>'
                '<Fill name="F"><SolidColor colorValue="FFE04040" name="C"/></Fill></Shape>'
                '<LinearAnimation duration="1" name="X" id="0:6"/><StateMachine name="SM" id="0:7">'
                '<StateMachineLayer name="L" id="0:8"><AnyState/><ExitState/><EntryState>'
                '<StateTransition stateToId="0:9"/></EntryState><AnimationState animationId="0:6" id="0:9"/>'
                '</StateMachineLayer></StateMachine></Artboard>'
                '<ViewModel defaultInstanceId="0:61" name="Probe" id="0:60">'
                '<ViewModelPropertyEnumCustom enumId="0:70" name="mood" id="0:63"/>'
                '<ViewModelInstance exports="true" name="Default" id="0:61">'
                '<ViewModelInstanceEnum propertyValue="0:72" viewModelPropertyId="0:63"/>'
                '</ViewModelInstance></ViewModel></Rive>')
            self.assertEqual(L.run_rive([str(proj), "--once", "--quiet"], timeout=120).returncode, 0)
            with quiet():
                page = WEB.build_page(next((proj / "build").glob("*.riv")), Path(tmp) / "site", title="Mood",
                                      controls=True)
            with self.served(page) as pg:
                self.assertEqual(self.settle(pg)["controls"]["mood"], "loud")
                pg.evaluate("() => { window.__r.viewModelInstance.enum('mood').value = 'dark'; }")
                self.assertEqual(self.settle(pg)["controls"]["mood"], "dark")


@LIVE
class LiveSvgTests(unittest.TestCase):
    def test_a_converted_svg_builds_and_draws(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg = Path(tmp) / "m.svg"
            svg.write_text(SvgConvertTests.SVG)
            proj = Path(tmp) / "proj"
            with quiet():
                self.assertEqual(S.main([str(svg), "--project", str(proj), "--size", "400x400",
                                         "--background", "FFFFFF", "--reveal"]), 0)
            self.assertEqual(L.run_rive([str(proj), "--verify"], timeout=60).returncode, 0)
            png = Path(tmp) / "m.png"
            L.run_rive([str(proj), "--quiet", f"--screenshot={png}", "--advance=3s"], timeout=60)
            self.assertIsNone(L.blank_reason(png))


if __name__ == "__main__":
    unittest.main()
