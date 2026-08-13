"""Structural tests for the colorgrade skill.

These run on a bare CI runner: standard library only, no numpy, scipy, Pillow,
ffmpeg or DaVinci Resolve. They deliberately do not check the colour maths --
`skills/colorgrade/scripts/selftest.py` does that and needs numpy, so it cannot
run here.

What is pinned here is the structure the maths sits on: the look library, the
DCTL parameter contract that dctlgen.py rewrites by text, and the two DCTL
constructs whose presence made DaVinci Resolve reject the first build of
LensIsolate.dctl with "Error Processing DaVinci CTL".
"""
import ast
import json
import py_compile
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "colorgrade"
SCRIPTS = SKILL / "scripts"
LOOKS = SKILL / "looks"
DCTL = SKILL / "dctl" / "LensIsolate.dctl"


def ui_params(text):
    """Parse DEFINE_UI_PARAMS lines the same way dctlgen.write_dctl does.

    dctlgen rewrites slider defaults by slicing on the prefix, taking
    `rindex(")")` and splitting on commas. If the DCTL is ever reformatted so
    that stops working, the generator silently emits the template's own
    defaults instead of the measured ones, which points the matte at whatever
    object the template was last aimed at. Hence parsing it here the same way.
    """
    out = []
    for line in text.splitlines():
        if not line.startswith("DEFINE_UI_PARAMS("):
            continue
        inner = line[len("DEFINE_UI_PARAMS("):line.rindex(")")]
        out.append([x.strip() for x in inner.split(",")])
    return out


def _assignment(path, name):
    """The module level assignment to `name`, read without importing.

    These scripts import numpy at module level, so a bare runner cannot import
    them to read a constant. Reading the source is the only way to pin one here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                return node.value
    raise AssertionError(f"{path.name} has no module level {name}")


def _module_constant(path, name):
    value = _assignment(path, name)
    if not isinstance(value, ast.Constant):
        raise AssertionError(f"{path.name}: {name} is no longer a plain constant")
    return value.value


def _env_default(path, name, var):
    """The fallback in `name = ...os.environ.get(var, DEFAULT)...`.

    The default is what every run without the variable set actually uses, so
    that literal is the thing worth pinning, not the assignment around it.
    """
    for node in ast.walk(_assignment(path, name)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == var):
            return node.args[1].value
    raise AssertionError(f"{path.name}: {name} no longer defaults an os.environ {var}")


class SkillLayoutTest(unittest.TestCase):
    def test_expected_files_present(self):
        for rel in ("SKILL.md", "deps.json", "dctl/LensIsolate.dctl",
                    "dctl/dctl_host.swift", "scripts/cg.py", "scripts/cgcore.py",
                    "scripts/cganalyze.py", "scripts/cgvideo.py",
                    "scripts/dctlgen.py", "scripts/selftest.py",
                    "scripts/groundtruth.py", "reference/01_method.md",
                    "reference/03_failures.md",
                    # track three, the picture itself, and track four, series
                    "scripts/cgpanel.py", "scripts/cgframes.py",
                    "scripts/cgfix.py", "scripts/cgseries.py",
                    "scripts/cgrife.py", "scripts/setup_rife.sh",
                    "scripts/selftest_tools.py",
                    "reference/05_picture.md", "reference/06_series.md",
                    # track three's frame repair pipeline
                    "scripts/cgyuv.py", "scripts/cgflow.py",
                    "scripts/selftest_frames.py", "reference/07_frames.md"):
            self.assertTrue((SKILL / rel).is_file(), f"missing {rel}")

    def test_picture_tools_are_optional_about_torch(self):
        """Only the frame REBUILD may need torch, and it must say so, not crash.

        Stall detection, the cadence test and every colour tool have to work on
        a machine with no torch. If cgframes or cgpanel ever import it at module
        level, the whole picture track becomes unusable behind a 2 GB install.
        """
        for name in ("cgpanel.py", "cgframes.py", "cgfix.py", "cgseries.py"):
            tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
            for node in tree.body:                      # module level only
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                self.assertNotIn("torch", [n.split(".")[0] for n in names],
                                 f"{name} imports torch at module level")

    def test_rife_model_path_is_overridable(self):
        """The weights live outside the repo, so the path must be settable."""
        text = (SCRIPTS / "cgrife.py").read_text(encoding="utf-8")
        self.assertIn("CG_RIFE_HOME", text)

    def test_rife_runs_at_full_scale(self):
        """The two faults this track was built to fix are numbers, so pin them.

        `selftest_frames.py` proves the behaviour, but it needs numpy and torch
        and so cannot run on a bare runner. That leaves the fault sites guarded
        by nothing automatic, which is how the value got to 0.5 in the first
        place: it is the usual recommendation for 4K, and correct for its own
        purpose, which is interpolating across large motion. Frame repair works
        between ADJACENT frames, where the coarse pyramid has nothing real to
        lock onto. Fed two identical frames, where the only right answer is that
        frame back, 0.5 invents 2.12 levels of movement in the middle of the
        picture against 0.0001 at 1.0.
        """
        self.assertEqual(1.0, float(_env_default(SCRIPTS / "cgrife.py",
                                                 "SCALE", "CG_RIFE_SCALE")),
                         "RIFE scale default must stay 1.0, see reference/07_frames.md")

    def test_a_long_freeze_is_treated_as_a_deliberate_hold(self):
        """A hold must never be rebuilt, and the cap that spares it is a number.

        No frame rate conversion produces long runs: 24 from 18 repeats one
        frame at a time, 24 from 12 every other frame, 24 from 8 in pairs, so
        three is the most a real retime can leave. What does produce a long run
        is a title settling, an end board, or an actor being still. Raising this
        cap hands those to the rebuilder, and a solid run scores BETTER on the
        cadence test than genuine damage, because every gap inside a run is 1
        and so the beat looks perfectly regular. Measured: a 20 frame hold
        reached `plan` as density 0.26, gap spread 0.00, and planned to move 48
        real frames to fix a fault that did not exist.
        """
        self.assertEqual(3, _module_constant(SCRIPTS / "cgframes.py", "MAX_STALL_RUN"),
                         "MAX_STALL_RUN guards deliberate holds, see reference/07_frames.md")

    def test_the_census_reads_both_motion_and_area(self):
        """A frozen plate under a moving graphic is invisible to the mean step.

        A corporate film generated its live action at 18 fps and conformed it to
        24 by repeating one frame in four, then laid a bright animated overlay
        over the top at the full rate. On a repeated frame the picture
        underneath is identical and only the overlay moves, so the MEAN step
        still reads 0.26 of the shot's typical value, nowhere near the 0.10 the
        census needs, and 36 repeated frames in one shot were reported as zero.
        The share of the frame that moved reads 0.089 on the same frames.

        So `census_frozen` has to take an area series and flag a frame that
        either reading calls frozen. Losing the second argument, or dropping the
        OR, restores a blindness that a whole delivery walked through.
        """
        src = (SCRIPTS / "cgframes.py").read_text()
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "census_frozen"), None)
        self.assertIsNotNone(fn, "census_frozen must exist")
        args = [a.arg for a in fn.args.args]
        self.assertIn("area", args,
                      "census_frozen must take the area series, see reference/07_frames.md")
        body = ast.get_source_segment(src, fn) or ""
        self.assertIn("by_mean | by_area", body,
                      "a frame frozen by EITHER reading is frozen; the OR is the fix")
        # every caller has to pass it, or the argument is decoration
        for call in [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == "census_frozen"]:
            self.assertGreaterEqual(
                len(call.args), 3,
                f"census_frozen called with {len(call.args)} arguments at line "
                f"{call.lineno}; the area series must be passed at every call site")

    def test_the_scan_returns_the_area_series(self):
        """cgyuv.Ruler.scan feeds the census, so the series has to come from it.

        Pinned here because the unpacking is positional at every call site: a
        scan that quietly goes back to three return values fails loudly, but a
        census that quietly stops being given the fourth does not.
        """
        src = (SCRIPTS / "cgyuv.py").read_text()
        fn = next((n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "scan"), None)
        self.assertIsNotNone(fn, "cgyuv.Ruler.scan must exist")
        ret = [n for n in ast.walk(fn) if isinstance(n, ast.Return)][-1]
        self.assertIsInstance(ret.value, ast.Tuple)
        self.assertEqual(4, len(ret.value.elts),
                         "scan must return lap, diff, span, area")
        self.assertEqual(
            4, len(ast.parse((SCRIPTS / "cgframes.py").read_text()).body and
                   [t for t in ast.walk(ast.parse((SCRIPTS / "cgframes.py").read_text()))
                    if isinstance(t, ast.Assign) and isinstance(t.value, ast.Call)
                    and getattr(t.value.func, "attr", None) == "scan"
                    and isinstance(t.targets[0], ast.Tuple)][0].targets[0].elts),
            "every ruler.scan() call must unpack four values")
        self.assertGreater(_module_constant(SCRIPTS / "cgyuv.py", "MOVED_LEVELS"), 2.0,
                           "MOVED_LEVELS below h264 ringing would count noise as motion")

    def test_the_render_tags_all_four_colour_fields(self):
        """A delivery that loses its transfer tag is a gamma the player guesses.

        ffmpeg drops an output -color_trc and -color_primaries when the raw
        video being piped in carries no transfer and no primaries of its own.
        `Spec.tags` sits on both sides of that pipe, so when it named only the
        range and the matrix, a render of a file tagged bt709/bt709/bt709 came
        out tagged bt709/unknown/unknown. Nothing failed and nothing warned.
        """
        src = (SCRIPTS / "cgyuv.py").read_text()
        spec = next((n for n in ast.walk(ast.parse(src))
                     if isinstance(n, ast.ClassDef) and n.name == "Spec"), None)
        self.assertIsNotNone(spec, "cgyuv.Spec must exist")
        fields = [n.target.id for n in spec.body if isinstance(n, ast.AnnAssign)]
        for f in ("color_range", "colorspace", "transfer", "primaries"):
            self.assertIn(f, fields, f"Spec must carry {f}")
        # Read the RETURNED LIST, not the source text. A first version of this
        # matched the function's source segment, which includes the docstring,
        # and the docstring names all four flags: deleting them from the code
        # left the test passing. Two of three mutations walked through it.
        tags = next((n for n in spec.body
                     if isinstance(n, ast.FunctionDef) and n.name == "tags"), None)
        self.assertIsNotNone(tags, "Spec.tags must exist")
        ret = [n for n in ast.walk(tags) if isinstance(n, ast.Return)][-1]
        self.assertIsInstance(ret.value, ast.List, "Spec.tags must return a list")
        emitted = {e.value for e in ret.value.elts
                   if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        for flag in ("-color_range", "-colorspace", "-color_trc", "-color_primaries"):
            self.assertIn(flag, emitted, f"Spec.tags must emit {flag}")

        # and the probe has to SUPPLY them, checked on the Media(...) call
        # rather than on the file text, for the same reason.
        vsrc = (SCRIPTS / "cgvideo.py").read_text()
        made = [n for n in ast.walk(ast.parse(vsrc))
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Media"]
        self.assertTrue(made, "cgvideo.probe must build a Media")
        passed = {k.arg for k in made[0].keywords}
        for f in ("color_space", "color_transfer", "color_primaries"):
            self.assertIn(f, passed, f"cgvideo.probe must read {f} off the source")

    def test_the_gate_judges_stillness_across_the_anchors(self):
        """Inside a 2 frame stall, span measures the fault, not the shot.

        `span[f]` is |next minus previous| in the ORIGINAL, and inside a stall
        two frames long the frames either side of the interior frame ARE the
        frozen copies the repair exists to replace. Judged on span alone, that
        frame reads as a shot that is not moving and its rebuild is reverted
        every time, by construction: five rebuilds thrown away per clip on the
        material this was found on. The gate must judge a fill across the two
        REAL anchors it was built from. See reference/03_failures.md entry 22.
        """
        src = (SCRIPTS / "cgframes.py").read_text()
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertIn("_still_by_anchors", names,
                      "the anchor based still test must exist")
        gate = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "cmd_gate")
        called = {getattr(c.func, "id", None) for c in ast.walk(gate)
                  if isinstance(c, ast.Call)}
        self.assertIn("_still_by_anchors", called,
                      "cmd_gate must judge stillness across the anchors, "
                      "not on span at the rebuilt frame")

    def test_plan_takes_an_explicit_mode_override(self):
        """Stalls in pairs score density 0.117 and regularity 1.09: both
        thresholds sit on the wrong side of a perfectly regular beat. The fix
        is an explicit per shot override, not a loosened threshold, so the
        flag has to exist and cmd_plan has to parse it into a per shot map.
        """
        src = (SCRIPTS / "cgframes.py").read_text()
        self.assertIn("--force-mode", src,
                      "plan must expose --force-mode, see 03_failures.md entry 22")
        tree = ast.parse(src)
        plan = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "plan_jobs")
        body = ast.get_source_segment(src, plan) or ""
        self.assertIn("force_mode", body,
                      "plan_jobs must consult the override")

    def test_the_track_names_its_output_after_its_input(self):
        """A plan must be built from a track OF THE SOURCE. An earlier tracker
        hardcoded its output path, so tracking a repaired clip silently
        overwrote the source's track and the replan was built from the
        repair's own output. The default output name must be derived from the
        measured file, never a fixed path.
        """
        src = (SCRIPTS / "cgtrack.py").read_text()
        tree = ast.parse(src)
        savez = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "savez"]
        self.assertTrue(savez, "cgtrack must save the track")
        self.assertNotIsInstance(
            savez[0].args[0], ast.Constant,
            "the track's output path must come from the input or an argument, "
            "not a hardcoded literal")
        self.assertIn(".track.npz", src,
                      "the default track name must say which file it came from")

    def test_every_script_compiles(self):
        for script in sorted(SCRIPTS.glob("*.py")):
            with self.subTest(script=script.name):
                with tempfile.NamedTemporaryFile(suffix=".pyc") as out:
                    py_compile.compile(str(script), cfile=out.name, doraise=True)

    def test_no_absolute_home_paths(self):
        # A path baked to this machine's home would break the skill on the
        # Linux side and on any other install.
        pattern = re.compile(r"/Users/[a-z]|/home/[a-z]")
        for f in SKILL.rglob("*"):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            with self.subTest(file=f.name):
                text = f.read_text(encoding="utf-8", errors="ignore")
                self.assertIsNone(pattern.search(text),
                                  f"{f.name} carries an absolute home path")

    def test_skill_md_description_is_extractable(self):
        # core.skill_loader takes the first paragraph after the H1 and caps it
        # at 200 chars. An empty first paragraph means an empty catalog entry.
        lines = (SKILL / "SKILL.md").read_text(encoding="utf-8").strip().split("\n")
        self.assertTrue(lines[0].startswith("# "))
        para = []
        for line in lines[1:]:
            if not line.strip():
                if para:
                    break
                continue
            para.append(line.strip())
        self.assertTrue(" ".join(para).strip(), "SKILL.md has no description paragraph")

    def test_reference_cross_links_resolve(self):
        # SKILL.md used to point at 04_failures.md, which does not exist.
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for name in re.findall(r"`(reference/)?(\d\d_[a-z_]+\.md)`", text):
            with self.subTest(doc=name[1]):
                self.assertTrue((SKILL / "reference" / name[1]).is_file(),
                                f"SKILL.md links {name[1]}, which does not exist")


class DepsTest(unittest.TestCase):
    def setUp(self):
        self.deps = json.loads((SKILL / "deps.json").read_text(encoding="utf-8"))

    def test_shape(self):
        self.assertIn(self.deps["weight"], {"light", "medium", "heavy"})
        self.assertIsInstance(self.deps["check"], dict)
        for name, cmd in self.deps["check"].items():
            self.assertIsInstance(cmd, str, f"check {name} is not a command string")
        for key in ("brew", "apt"):
            self.assertIsInstance(self.deps[key], list)

    def test_install_note_keeps_python_out_of_the_bot_venv(self):
        # The bot's own .venv must never receive numpy/scipy: recreating or
        # breaking it kills the running bot.
        note = self.deps["install_note"]
        self.assertIn("~/.venvs/colorgrade", note)
        self.assertIn("never in the bot", note.lower())


class LookLibraryTest(unittest.TestCase):
    def looks(self):
        return sorted(LOOKS.glob("*.json"))

    def test_every_look_parses_and_self_identifies(self):
        for f in self.looks():
            with self.subTest(look=f.stem):
                look = json.loads(f.read_text(encoding="utf-8"))
                self.assertEqual(look.get("name"), f.stem,
                                 "the name field must match the filename")
                self.assertTrue(look.get("description"))
                self.assertTrue(look.get("reference"),
                                "every look states where its numbers come from")

    def test_tone_fields_are_sane(self):
        for f in self.looks():
            look = json.loads(f.read_text(encoding="utf-8"))
            with self.subTest(look=f.stem):
                self.assertGreater(look["contrast"], 0.0)
                self.assertGreaterEqual(look["saturation"], 0.0)
                for key in ("shadow_tint", "highlight_tint"):
                    if key in look:
                        self.assertEqual(len(look[key]), 3, f"{key} must be RGB")
                for hs in look.get("hue_shifts", []):
                    self.assertLessEqual(0.0, hs["centre"])
                    self.assertLess(hs["centre"], 360.0)
                    self.assertGreater(hs["width"], 0.0)

    def test_neutral_look_is_actually_neutral(self):
        # `neutral` is the documented "balance and match only" default. If it
        # ever acquires a creative move, every unattended grade shifts.
        look = json.loads((LOOKS / "neutral.json").read_text(encoding="utf-8"))
        self.assertEqual(look["contrast"], 1.0)
        self.assertEqual(look["saturation"], 1.0)
        for key in ("shadow_tint", "highlight_tint", "hue_shifts", "crosstalk"):
            self.assertNotIn(key, look)

    def test_skill_md_table_matches_the_library(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| `([a-z0-9_]+)` \|", text, re.M))
        on_disk = {f.stem for f in self.looks()}
        self.assertEqual(documented, on_disk)


class DctlContractTest(unittest.TestCase):
    def setUp(self):
        self.text = DCTL.read_text(encoding="utf-8")

    def test_transform_has_a_documented_signature(self):
        # Blackmagic document four transform signatures. This is the plain
        # float3 one, the shape their own Gain.dctl uses.
        self.assertIn(
            "__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, "
            "int p_Y, float p_R, float p_G, float p_B)",
            self.text)

    def test_no_texture_passed_into_a_helper(self):
        # Not one of the thirteen sample .dctl files Blackmagic ship passes
        # __TEXTURE__ into a helper __DEVICE__ function, and doing it is one of
        # the two things that made Resolve 21 reject v1 of this file. The macOS
        # Metal harness compiles it happily, so only this rule catches it.
        self.assertNotIn("__TEXTURE__", self.text)

    def test_no_alpha_output(self):
        # Alpha out of a DCTL only works through the ResolveFX DCTL plugin
        # (DaVinciCTL README section 7). This file is applied that way, but the
        # v1 alpha version still failed, so it stays out.
        self.assertNotIn("DEFINE_DCTL_ALPHA_MODE", self.text)
        self.assertNotIn("float4 transform", self.text)

    def test_ui_params_parse_the_way_dctlgen_rewrites_them(self):
        params = ui_params(self.text)
        self.assertTrue(params)
        for parts in params:
            with self.subTest(param=parts[0]):
                if parts[2] == "DCTLUI_SLIDER_FLOAT":
                    # name, label, kind, default, min, max, step
                    self.assertEqual(len(parts), 7)
                    default, lo, hi, step = (float(x) for x in parts[3:7])
                    self.assertLess(lo, hi)
                    self.assertGreater(step, 0.0)
                    self.assertTrue(lo <= default <= hi,
                                    f"{parts[0]} default {default} is outside {lo}..{hi}")

    def test_every_slider_dctlgen_aims_exists_in_the_template(self):
        # dctlgen only rewrites a default when the name is in its dict AND the
        # line is a DCTLUI_SLIDER_FLOAT. A rename on either side is silent: the
        # generated file would still load, aimed at the wrong object.
        source = (SCRIPTS / "dctlgen.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        packed = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef) and node.name == "_pack"):
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "dict":
                        packed = {kw.arg for kw in call.keywords}
        self.assertTrue(packed, "could not read the slider names out of _pack")
        packed |= {"hueShift", "satGain"}  # write_dctl adds these two

        sliders = {p[0] for p in ui_params(self.text) if p[2] == "DCTLUI_SLIDER_FLOAT"}
        self.assertEqual(packed - sliders, set(),
                         "dctlgen aims a slider the DCTL does not define")


class LintTests(unittest.TestCase):
    """CI's ruff scope is `bot.py core/ utils/ install/ miniapp/ tests/`.

    `skills/` is not in it, so nothing in the pipeline can see a lint error in
    this tree. Eight arrived here in one branch, invisible behind a green run.
    Scoped to colorgrade rather than all of `skills/`, which already carries
    eleven of its own elsewhere and wants its own clean-up.
    """

    @unittest.skipUnless(shutil.which("ruff"), "ruff not installed")
    def test_the_skill_is_ruff_clean(self):
        proc = subprocess.run([shutil.which("ruff"), "check", "--output-format=concise",
                               str(SKILL)],
                              capture_output=True, text=True, cwd=SKILL.parent.parent)

        self.assertEqual(proc.returncode, 0, f"\n{proc.stdout}{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
