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
                    "reference/05_picture.md", "reference/06_series.md"):
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


if __name__ == "__main__":
    unittest.main()
