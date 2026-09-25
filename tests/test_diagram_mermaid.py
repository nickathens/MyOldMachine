#!/usr/bin/env python3
"""Guards for the Mermaid renderer in the diagram skill (scripts/diagram.py).

Mermaid CLI 12.0.0 (published 2026-09-24) removed -w/--width and exits on it
with "error: unknown option '-w'". The skill passed -w on every render, so
installing 12 broke every diagram on the machine. 12's --size is not a
rename: it scales a PNG until its LONG side equals the size, so a small
flowchart is blown up to 1600 px and a long sequence diagram is squeezed to
1600 px tall. With neither flag the page is Puppeteer's default 800 px and a
wide diagram shrinks. The script reads the installed version, passes -w to 11
and older, and on 12 sets the page width through Puppeteer's defaultViewport
in a per-render launch config, which is what -w did.

Mermaid 12 also changed the default look (drop shadows), widened short
flowchart and state labels to 120 px, and moved five diagram types to the ELK
layout. scripts/mermaid.json keeps 11's flat look, natural node widths and
dagre layout unless a diagram's own front matter asks for something else.

The live tests render for real with whatever mmdc is on PATH, and skip when
there is none, so they skip on CI.
"""

import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "diagram" / "scripts" / "diagram.py"

# The flowchart SKILL.md opens with.
SOURCE = """graph TD
    A[Start] --> B{Decision}
    B -->|Yes| C[Do thing]
    B -->|No| D[Skip]
"""
GANTT = """gantt
    title Timeline
    dateFormat YYYY-MM-DD
    section Build
    Spec      :a1, 2026-05-01, 3d
    Implement :after a1, 7d
"""
SMALL_FLOW = "graph LR\n    A[Input] --> B[Save]\n"
# The look a node was drawn with, as an attribute on the element itself. The
# stylesheet inside the same SVG also names every look in [data-look="..."]
# selectors, which the leading character class skips.
LOOK_ON_ELEMENT = re.compile(r'[^\[]data-look="([A-Za-z]+)"')


def load_script():
    spec = importlib.util.spec_from_file_location("diagram_mermaid", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_mmdc(version_output: str, returncode: int = 0):
    """A subprocess.run stand-in: answers --version, records every render.

    The source and the launch config sit in a temporary directory that is
    gone once render() returns, so each render reads them while in flight.
    """
    renders = []

    def run(cmd, *args, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, version_output, "")
        renders.append({
            "cmd": cmd,
            "source": Path(cmd[cmd.index("-i") + 1]).read_text(encoding="utf-8"),
            "config": json.loads(Path(cmd[cmd.index("-p") + 1]).read_text(encoding="utf-8")),
        })
        return subprocess.CompletedProcess(cmd, returncode, "", "boom" if returncode else "")

    return run, renders


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.d = load_script()

    def test_version_parsing(self):
        cases = {
            "12.0.0\n": 12,
            "11.17.0": 11,
            "v12.1.3\n": 12,
            "  13.0.0-beta.1\n": 13,
            "": 0,
            "not a version": 0,
        }
        for output, major in cases.items():
            with self.subTest(output=output), \
                    mock.patch.object(self.d.subprocess, "run",
                                      side_effect=fake_mmdc(output)[0]):
                self.assertEqual(self.d.mmdc_major(), major)

    def test_a_missing_or_hung_mmdc_reads_as_unknown(self):
        for error in (FileNotFoundError("mmdc"),
                      subprocess.TimeoutExpired(["mmdc", "--version"], 30)):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(self.d.subprocess, "run", side_effect=error):
                self.assertEqual(self.d.mmdc_major(), 0)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.d = load_script()

    def render_with(self, version_output: str, width: int = 1600) -> dict:
        run, renders = fake_mmdc(version_output)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.d.shutil, "which", return_value="/usr/bin/mmdc"), \
                mock.patch.object(self.d.subprocess, "run", side_effect=run):
            self.d.render(SOURCE, Path(tmp) / "out.png", width=width)
        self.assertEqual(len(renders), 1)
        return renders[0]

    def test_mermaid_cli_12_gets_the_page_width_in_the_launch_config(self):
        render = self.render_with("12.0.0\n")
        cmd = render["cmd"]
        # 12 refuses -w, and its --size scales a PNG by the long side.
        for flag in ("-w", "--width", "--size"):
            self.assertNotIn(flag, cmd)
        self.assertEqual(render["config"]["defaultViewport"]["width"], 1600)
        self.assertEqual(Path(cmd[cmd.index("-c") + 1]), self.d.MERMAID_CONFIG)

    def test_mermaid_cli_11_keeps_the_width_flag_it_understands(self):
        cmd = self.render_with("11.17.0\n", width=1800)["cmd"]
        self.assertEqual(cmd[cmd.index("-w") + 1], "1800")
        self.assertNotIn("--size", cmd)

    def test_an_unreadable_version_is_treated_as_current(self):
        # Guessing 11 on a 12 install fails every render; guessing 12 on an 11
        # install only narrows the page to 800 px. So unknown means current.
        for output in ("", "mmdc: command crashed\n"):
            with self.subTest(output=output):
                render = self.render_with(output, width=1300)
                self.assertNotIn("-w", render["cmd"])
                self.assertNotIn("--size", render["cmd"])
                self.assertEqual(render["config"]["defaultViewport"]["width"], 1300)

    def test_size_args_by_major(self):
        self.assertEqual(self.d.size_args(1600, 12), [])
        self.assertEqual(self.d.size_args(1600, 13), [])
        self.assertEqual(self.d.size_args(1600, 11), ["-w", "1600"])
        self.assertEqual(self.d.size_args(1600, 10), ["-w", "1600"])
        self.assertEqual(self.d.size_args(1600, 0), [])

    def test_the_launch_config_keeps_the_file_settings(self):
        config = self.d.puppeteer_config(1234)
        self.assertIn("--no-sandbox", config["args"])
        self.assertEqual(config["defaultViewport"]["width"], 1234)

    def test_the_mermaid_config_pins_the_old_look_and_never_the_theme(self):
        config = json.loads(self.d.MERMAID_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(config["look"], "classic")
        # The five diagram types Mermaid 12 moved to ELK get dagre back, each
        # in its own section.
        for kind in ("flowchart", "state", "class", "er", "requirement"):
            self.assertEqual(config[kind]["layout"], "dagre", kind)
        # 12 widens every shorter flowchart and state label to 120 px.
        for kind in ("flowchart", "state"):
            self.assertEqual(config[kind]["minNodeWidth"], 0, kind)
        # A global "layout" outranks every type's own default and flattened
        # the radial mindmap into a tree; a "theme" would silently override
        # the --theme flag, because mmdc merges the file over {theme} from -t.
        self.assertNotIn("layout", config)
        self.assertNotIn("theme", config)

    def test_render_hands_mmdc_the_source_then_cleans_up(self):
        run, renders = fake_mmdc("12.0.0\n", returncode=1)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.d.shutil, "which", return_value="/usr/bin/mmdc"), \
                mock.patch.object(self.d.subprocess, "run", side_effect=run):
            with self.assertRaises(RuntimeError) as caught:
                self.d.render(SOURCE, Path(tmp) / "never.png")
        self.assertIn("boom", str(caught.exception))
        self.assertEqual(renders[0]["source"], SOURCE)
        cmd = renders[0]["cmd"]
        self.assertFalse(Path(cmd[cmd.index("-i") + 1]).exists())
        self.assertFalse(Path(cmd[cmd.index("-p") + 1]).exists())


@unittest.skipUnless(shutil.which("mmdc"), "mmdc is not installed; nothing real to render with")
class LiveRenderTests(unittest.TestCase):
    """Real renders through the script, with the mmdc this machine has."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def render(self, source: str, name: str, *args: str) -> Path:
        out = self.tmp / name
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-o", str(out), *args],
            input=source, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return out

    def test_the_skill_example_renders_at_its_natural_size(self):
        # 11 drew it at 275x362; --size 1600 on 12 blew it up to 1340x1600.
        width, height = png_size(self.render(SOURCE, "example.png"))
        self.assertLess(max(width, height), 600)

    def test_the_page_width_is_honoured(self):
        # A gantt always fills the page, so its PNG is the page width less the
        # 8 px body margin on each side: 1584 at 1600 under mermaid-cli 11 with
        # -w and under 12 with defaultViewport; 784 under 12 with neither.
        for width in (1600, 1000):
            out = self.render(GANTT, f"gantt{width}.png", "--width", str(width))
            self.assertEqual(png_size(out)[0], width - 16)

    def test_a_long_sequence_keeps_its_natural_height(self):
        # --size 1600 would squeeze this to 1600 px tall and shrink the text.
        lines = ["sequenceDiagram"] + [f"    A->>B: message {i}" for i in range(60)]
        width, height = png_size(self.render("\n".join(lines) + "\n", "tall.png"))
        self.assertGreater(height, 1800)

    def test_the_flat_look_is_the_default_and_front_matter_still_wins(self):
        svg = self.render(SMALL_FLOW, "flat.svg").read_text(encoding="utf-8")
        self.assertEqual(set(LOOK_ON_ELEMENT.findall(svg)), {"classic"})
        own = "---\nconfig:\n  look: handDrawn\n---\n" + SMALL_FLOW
        svg = self.render(own, "own.svg").read_text(encoding="utf-8")
        self.assertEqual(set(LOOK_ON_ELEMENT.findall(svg)), {"handDrawn"})

    def test_short_labels_keep_their_natural_width(self):
        # Mermaid 12's minimum label width drew these one letter boxes 180 px
        # (flowchart) and 136 px (state) wide; 11 drew them 68 and 24.
        for name, source in (("tiny_flow.svg", "graph LR\n    A[x] --> B[y]\n"),
                             ("tiny_state.svg", "stateDiagram-v2\n    [*] --> x\n    x --> y\n")):
            with self.subTest(name):
                svg = self.render(source, name).read_text(encoding="utf-8")
                widths = [float(w) for w in re.findall(
                    r'<rect class="basic label-container"[^>]*width="([\d.]+)"', svg)]
                self.assertEqual(len(widths), 2, "node boxes not found in the SVG")
                self.assertLess(max(widths), 100)

    def test_a_mindmap_still_fans_out_around_its_root(self):
        # Under a global `layout: dagre` every node hung below the root (0 of
        # 8 above it); with the layout scoped per type, 4 of 8 sit above.
        source = "mindmap\n  root((Bot))\n    Skills\n      diagram\n      media\n" \
                 "    Memory\n      projects\n    Engines\n      Claude\n      Codex\n"
        svg = self.render(source, "mind.svg").read_text(encoding="utf-8")
        nodes = re.findall(
            r'<g class="node mindmap-node ([^"]*)"[^>]*transform="translate\(([-\d.]+),\s*([-\d.]+)\)"', svg)
        roots = [float(y) for cls, _, y in nodes if "section-root" in cls]
        self.assertEqual(len(roots), 1, "mindmap root not found in the SVG")
        above = [cls for cls, _, y in nodes if "section-root" not in cls and float(y) < roots[0]]
        self.assertTrue(above, "every node hangs below the root: the mindmap lost its own layout")

    def test_front_matter_can_still_ask_for_elk(self):
        # dagre draws flowchart edges as cubic curves ("C" in the path);
        # ELK routes them orthogonally with rounded corners and no "C".
        graph = "graph LR\n    A --> B --> C\n    A --> C\n"

        def edge_commands(name, source):
            svg = self.render(source, name).read_text(encoding="utf-8")
            paths = re.findall(r'<path d="([^"]+)" id="[^"]*L_[A-Z]_[A-Z]', svg)
            self.assertTrue(paths, "no flowchart edges found in the SVG")
            return set("".join(re.findall(r"[A-Za-z]", "".join(paths))))

        self.assertIn("C", edge_commands("dagre.svg", graph))
        own = "---\nconfig:\n  layout: elk\n---\n" + graph
        self.assertNotIn("C", edge_commands("elk.svg", own))

    def test_svg_and_pdf_render(self):
        svg = self.render(SMALL_FLOW, "flow.svg").read_text(encoding="utf-8")
        self.assertIn("<svg", svg[:400])
        self.assertTrue(self.render(SMALL_FLOW, "flow.pdf").read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
