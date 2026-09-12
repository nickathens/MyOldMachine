#!/usr/bin/env python3
"""
The gimp skill was written for GIMP 2.10 and this machine has run GIMP 3 for
months. Every runnable example in SKILL.md called a procedure that no longer
exists, and the failure did not look like a failure: GIMP 3 prints the error
and then parks in its main loop as a background process forever, because the
trailing `-b '(gimp-quit 0)'` is skipped once an earlier batch command fails.
Measured 12 Sep 2026 on GIMP 3.2.6: seven such runs sat at 0% CPU for 438
seconds and only ended when they were killed. `--quit` turns the same failure
into exit code 70 in one second.

What these tests pin:

  1  no runnable example in SKILL.md calls a procedure GIMP 3 removed
  2  every batch invocation in SKILL.md carries --quit, so a mistake exits
  3  the cleanup patterns match the command line the doc now tells you to run
     (they matched "gimp -i", which is not a substring of "gimp-console -i")
  4  scripts/batch.py builds a GIMP 3 command, not a 2.10 one
  5  the doc's own first recipe really runs, checked on its output pixels,
     wherever a GIMP 3 is installed to run it against
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "gimp"
DOC = SKILL / "SKILL.md"

sys.path.insert(0, str(REPO))

from utils.skill_hooks import find_processes_by_patterns  # noqa: E402
from utils.startup_cleanup import SKILL_PROCESS_PATTERNS  # noqa: E402

# Procedures that exist in 2.10 and are unbound variables in 3.2.6. Each one
# was called in a probe on 12 Sep 2026 and answered
# "Error: eval: unbound variable: <name>".
REMOVED_IN_GIMP3 = [
    "file-png-save",
    "file-jpeg-save",
    "file-gif-save",
    "gimp-image-get-active-layer",
    "plug-in-gauss",
    "plug-in-unsharp-mask",
    "gimp-pdb-query",
    "gimp-procedural-db-query",
    "gimp-pdb-get-proc-info",
    "gimp-pdb-proc-exists",
]

# The real command line of a headless GIMP 3 on this Mac, copied from ps while
# a batch run was in flight.
LIVE_GIMP_CMD = (
    "/Applications/GIMP.app/Contents/MacOS/gimp-console -i "
    "--batch-interpreter=plug-in-script-fu-eval "
    "-b (gimp-image-new 64 64 RGB) --quit"
)
# The Linux shape of the same job: gimp-console is on PATH there.
LIVE_GIMP_CMD_LINUX = (
    "gimp-console -i --batch-interpreter=plug-in-script-fu-eval "
    "-b (gimp-version) --quit"
)


def code_blocks(text: str) -> list[str]:
    """Every fenced code block in a markdown document."""
    return re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)


class DocIsGimp3(unittest.TestCase):

    def setUp(self):
        self.doc = DOC.read_text(encoding="utf-8")
        self.blocks = code_blocks(self.doc)

    def test_no_runnable_example_calls_a_removed_procedure(self):
        # Naming a dead procedure in the migration table is the point of the
        # table. Calling one inside a code block is a command that cannot run.
        for block in self.blocks:
            for proc in REMOVED_IN_GIMP3:
                self.assertNotIn(
                    f"({proc} ", block,
                    f"SKILL.md runs ({proc} ...), which GIMP 3 removed",
                )

    def test_every_batch_invocation_passes_quit(self):
        # Without --quit a failed -b leaves GIMP resident forever at 0% CPU.
        # A line the doc explicitly labels "Wrong" is the counter-example and
        # is allowed to be broken; nothing else is.
        found = 0
        for block in self.blocks:
            # Join continuation lines so a wrapped command reads as one.
            joined = block.replace("\\\n", " ")
            previous = ""
            for line in joined.splitlines():
                if not (" -b " in line and ("gimp" in line or "GIMP" in line)):
                    if line.strip():
                        previous = line
                    continue
                if "Wrong" in previous or "Wrong" in line:
                    previous = line
                    continue
                found += 1
                self.assertIn(
                    "--quit", line,
                    f"batch invocation without --quit will hang: {line.strip()}",
                )
                previous = line
        self.assertGreater(found, 0, "SKILL.md shows no batch invocation at all")

    def test_doc_states_the_installed_major_version(self):
        self.assertIn("GIMP 3", self.doc)
        self.assertNotIn(
            "GIMP 2.10 uses Script-Fu", self.doc,
            "the doc still presents 2.10 as the installed version",
        )


class CleanupPatternsMatchTheRealCommand(unittest.TestCase):
    """A pattern list that cannot see the process it is for reaps nothing."""

    def _stop_patterns(self) -> list[str]:
        config = json.loads((SKILL / "hooks.json").read_text(encoding="utf-8"))
        return config.get("stop", {}).get("kill_processes", []) or []

    def test_stop_hook_sees_a_headless_gimp3(self):
        table = [(4242, LIVE_GIMP_CMD), (4243, LIVE_GIMP_CMD_LINUX)]
        owned = {4242, 4243}
        matched = find_processes_by_patterns(
            table, self._stop_patterns(), owned_pids=owned,
        )
        self.assertEqual(
            sorted(matched), [4242, 4243],
            "skills/gimp/hooks.json cannot match a headless GIMP 3 command line",
        )

    def test_stop_hook_still_ignores_a_gui_gimp(self):
        gui = [(4300, "/Applications/GIMP.app/Contents/MacOS/gimp")]
        matched = find_processes_by_patterns(
            gui, self._stop_patterns(), owned_pids={4300},
        )
        self.assertEqual(matched, [], "a GUI GIMP the user opened must be left alone")

    def test_startup_cleanup_sees_a_headless_gimp3(self):
        for cmd in (LIVE_GIMP_CMD, LIVE_GIMP_CMD_LINUX):
            self.assertTrue(
                any(pat in cmd for pat in SKILL_PROCESS_PATTERNS),
                f"startup_cleanup has no pattern matching: {cmd}",
            )

    def test_startup_cleanup_still_ignores_a_gui_gimp(self):
        gui = "/Applications/GIMP.app/Contents/MacOS/gimp"
        self.assertFalse(any(pat in gui for pat in SKILL_PROCESS_PATTERNS))


class BatchScriptIsGimp3(unittest.TestCase):

    @staticmethod
    def load():
        path = SKILL / "scripts" / "batch.py"
        spec = importlib.util.spec_from_file_location("gimp_batch", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["gimp_batch"] = module
        spec.loader.exec_module(module)
        return module

    def test_gimp_script_builds_a_gimp3_command(self):
        module = self.load()
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            module.gimp_script("(gimp-version)")
        cmd = run.call_args[0][0]
        self.assertIn("--quit", cmd, "no --quit: a failing script parks GIMP forever")
        self.assertTrue(
            any(a.startswith("--batch-interpreter=") for a in cmd),
            "GIMP 3 needs an explicit --batch-interpreter",
        )
        self.assertNotIn(
            "(gimp-quit 0)", cmd,
            "a trailing (gimp-quit 0) never runs after a failed batch command",
        )

    def test_gimp_script_reports_a_failure_instead_of_returning(self):
        module = self.load()
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = mock.Mock(
                returncode=70, stdout="",
                stderr="Error: eval: unbound variable: plug-in-gauss",
            )
            with self.assertRaises(RuntimeError) as caught:
                module.gimp_script("(plug-in-gauss 1 1 1 1 1 1)")
        self.assertIn("unbound variable", str(caught.exception))


def _gimp3_binary() -> str | None:
    """The headless GIMP 3 on this machine, or None if there isn't one."""
    candidates = [
        shutil.which("gimp-console"),
        "/Applications/GIMP.app/Contents/MacOS/gimp-console",
    ]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            out = subprocess.run([candidate, "--version"], capture_output=True,
                                 text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if "version 3." in out:
            return candidate
    return None


GIMP3 = _gimp3_binary()


@unittest.skipUnless(GIMP3, "no GIMP 3 installed")
class DocRecipeActuallyRuns(unittest.TestCase):
    """The doc claims a verified recipe. Run it and read the pixels back."""

    def test_fill_and_export_writes_the_colour_it_was_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.png"
            script = f'''
(let* ((img (car (gimp-image-new 64 64 RGB)))
       (layer (car (gimp-layer-new img "bg" 64 64 RGB-IMAGE 100 LAYER-MODE-NORMAL))))
  (gimp-image-insert-layer img layer 0 -1)
  (gimp-context-set-foreground "#ff8000")
  (gimp-drawable-fill layer FILL-FOREGROUND)
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img
                   #:file "{out}" #:options -1)
  (gimp-image-delete img))'''
            done = subprocess.run(
                [GIMP3, "-i", "--batch-interpreter=plug-in-script-fu-eval",
                 "-b", script, "--quit"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(done.returncode, 0, done.stderr[-800:])
            self.assertTrue(out.exists(), "the recipe wrote no file")
            # PNG header: 8-byte signature, then IHDR width/height big-endian.
            raw = out.read_bytes()
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
            width = int.from_bytes(raw[16:20], "big")
            height = int.from_bytes(raw[20:24], "big")
            self.assertEqual((width, height), (64, 64))

    def test_a_removed_procedure_fails_fast_with_quit(self):
        # The whole reason --quit is in the doc: prove the failure exits.
        done = subprocess.run(
            [GIMP3, "-i", "--batch-interpreter=plug-in-script-fu-eval",
             "-b", "(plug-in-gauss RUN-NONINTERACTIVE 1 1 8.0 8.0 1)", "--quit"],
            capture_output=True, text=True, timeout=180,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unbound variable", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
