#!/usr/bin/env python3
"""The size flag the diagram skill passes to the Mermaid CLI.

Mermaid CLI 12.0.0 (published 2026-09-24) removed -w/--width in favour of
--size, and exits on the old flag with "error: unknown option '-w'". The
skill passed -w on every render, so installing 12 broke every diagram on the
machine. 11 and older do not know --size, and the Linux side may still be on
11, so scripts/diagram.py reads the installed version and passes the flag
that version understands.

The last test renders for real with whatever mmdc is on PATH, and is the one
that fails on this Mac when 12 is installed under the old script.
"""

import importlib.util
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


def load_script():
    spec = importlib.util.spec_from_file_location("diagram_mermaid", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_mmdc(version_output: str):
    """A subprocess.run stand-in: answers --version, records every render."""
    renders = []

    def run(cmd, *args, **kwargs):
        if cmd[1:] == ["--version"]:
            return subprocess.CompletedProcess(cmd, 0, version_output, "")
        renders.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run, renders


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", header[16:24])


class SizeFlagTests(unittest.TestCase):
    def setUp(self):
        self.d = load_script()

    def render_with(self, version_output: str, width: int = 1600) -> list[str]:
        run, renders = fake_mmdc(version_output)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.d.shutil, "which", return_value="/usr/bin/mmdc"), \
                mock.patch.object(self.d.subprocess, "run", side_effect=run):
            self.d.render(SOURCE, Path(tmp) / "out.png", width=width)
        self.assertEqual(len(renders), 1)
        return renders[0]

    def test_mermaid_cli_12_gets_size_and_never_the_removed_width_flag(self):
        cmd = self.render_with("12.0.0\n")
        self.assertIn("--size", cmd)
        self.assertEqual(cmd[cmd.index("--size") + 1], "1600")
        self.assertNotIn("-w", cmd)
        self.assertNotIn("--width", cmd)

    def test_mermaid_cli_11_keeps_the_width_flag_it_understands(self):
        cmd = self.render_with("11.17.0\n", width=1800)
        self.assertEqual(cmd[cmd.index("-w") + 1], "1800")
        self.assertNotIn("--size", cmd)

    def test_an_unreadable_version_gets_the_current_flag(self):
        self.assertIn("--size", self.render_with(""))
        self.assertIn("--size", self.render_with("mmdc: command crashed\n"))

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

    def test_size_args_by_major(self):
        self.assertEqual(self.d.size_args(1600, 12), ["--size", "1600"])
        self.assertEqual(self.d.size_args(1600, 13), ["--size", "1600"])
        self.assertEqual(self.d.size_args(1600, 11), ["-w", "1600"])
        self.assertEqual(self.d.size_args(1600, 10), ["-w", "1600"])
        self.assertEqual(self.d.size_args(1600, 0), ["--size", "1600"])


class RealRenderTests(unittest.TestCase):
    """One real render through the script, with the mmdc this machine has."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("mmdc") is None:
            raise unittest.SkipTest("mmdc is not installed; nothing real to render with")
        # Read here rather than through the script, so this test still renders
        # (and fails for the real reason) against a script without the probe.
        out = subprocess.run(["mmdc", "--version"], capture_output=True,
                             text=True, timeout=30).stdout
        match = re.match(r"\s*v?(\d+)\.", out)
        cls.major = int(match.group(1)) if match else 0

    def test_the_skill_example_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.png"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "-o", str(out)],
                input=SOURCE, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            width, height = png_size(out)
        if self.major >= 12:
            # --size makes the longest side 1600; measured 1340x1600 here.
            self.assertAlmostEqual(max(width, height), 1600, delta=8)
        else:
            self.assertLessEqual(width, 1600)


if __name__ == "__main__":
    unittest.main()
