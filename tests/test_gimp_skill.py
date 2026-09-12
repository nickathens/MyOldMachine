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

  1  GIMP 3 recipes do not call procedures removed in that version
  2  GIMP 3 batches carry --quit, while GIMP 2 uses its legacy quit command
  3  startup cleanup spares GIMP jobs; Stop hooks select only owned jobs
  4  scripts/batch.py detects the version and reports errors on either path
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

try:
    from PIL import Image
except ImportError:
    Image = None

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
        self.blocks = code_blocks(self.doc.split("## GIMP 2.10 compatibility")[0])

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

    def test_doc_requires_an_installed_version_check(self):
        self.assertIn("GIMP 3", self.doc)
        self.assertIn("gimp-console --version", self.doc)
        self.assertIn("## GIMP 2.10 compatibility", self.doc)
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

    def test_startup_cleanup_leaves_unowned_gimp_jobs_alone(self):
        from utils import startup_cleanup as cleanup

        commands = [LIVE_GIMP_CMD, LIVE_GIMP_CMD_LINUX,
                    "gimp -i -b (work)", "gimp --no-interface -b (work)",
                    "gimp-console --no-interface -b (work)"]
        with mock.patch.object(cleanup, "_get_all_pids", return_value=list(
                enumerate(commands, start=4242))), \
                mock.patch.object(cleanup, "_kill_pids") as kill, \
                mock.patch.object(cleanup, "_clean_temp_files", return_value=0), \
                mock.patch.object(cleanup, "BROWSER_STATE_FILES", []):
            result = cleanup.run_startup_cleanup()
        kill.assert_not_called()
        self.assertEqual(result["skill_processes_killed"], 0)

    def test_stop_hook_never_selects_another_sessions_gimp(self):
        table = [(4242, LIVE_GIMP_CMD), (4243, LIVE_GIMP_CMD_LINUX)]
        self.assertEqual(find_processes_by_patterns(
            table, self._stop_patterns(), owned_pids={4242}), [4242])

    def test_stop_handler_checks_ownership_before_reaping_gimp(self):
        from utils import skill_hooks as hooks

        for root_command, expected in [("claude -p", [1100]), ("python worker.py", [])]:
            with self.subTest(root=root_command):
                table = [(1000, 1, root_command), (1100, 1000, LIVE_GIMP_CMD),
                         (1200, 1, LIVE_GIMP_CMD_LINUX), (1300, 1000, "python hook.py")]
                with mock.patch.object(hooks, "get_process_table", return_value=table), \
                        mock.patch.object(hooks.os, "getpid", return_value=1300), \
                        mock.patch.object(hooks, "get_daemon_pid", return_value=None), \
                        mock.patch.object(hooks, "get_last_used_age", return_value=0), \
                        mock.patch.object(hooks, "BROWSER_STATE_FILES", []), \
                        mock.patch.object(hooks, "load_all_stop_hooks", return_value={
                            "gimp": {"kill_processes": self._stop_patterns()}}), \
                        mock.patch.object(hooks, "kill_pids_batch") as kill, \
                        mock.patch.object(hooks, "clean_old_temp_files", return_value=0), \
                        mock.patch.object(hooks, "db_cleanup_old"), \
                        mock.patch.object(hooks, "log_action"):
                    hooks.handle_stop({})
                if expected:
                    kill.assert_called_once_with(expected)
                else:
                    kill.assert_not_called()

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
            run.side_effect = [
                mock.Mock(returncode=0, stdout="GIMP version 3.2.6", stderr=""),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]
            module.gimp_script("(gimp-version)")
        cmd = run.call_args[0][0]
        self.assertIn("--quit", cmd, "no --quit: a failing script parks GIMP forever")
        self.assertIn("--new-instance", cmd)
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
            run.side_effect = [
                mock.Mock(returncode=0, stdout="GIMP version 3.2.6", stderr=""),
                mock.Mock(returncode=70, stdout="",
                          stderr="Error: eval: unbound variable: plug-in-gauss"),
            ]
            with self.assertRaises(RuntimeError) as caught:
                module.gimp_script("(plug-in-gauss 1 1 1 1 1 1)")
        self.assertIn("unbound variable", str(caught.exception))

    def test_gimp2_uses_legacy_quit_and_returns_output(self):
        module = self.load()
        with mock.patch.object(subprocess, "run") as run:
            run.side_effect = [
                mock.Mock(returncode=0, stdout="GIMP version 2.10.36", stderr=""),
                mock.Mock(returncode=0, stdout="VERSION=2.10.36", stderr=""),
            ]
            result = module.gimp_script("(gimp-version)", timeout=7)
        cmd = run.call_args.args[0]
        self.assertNotIn("--quit", cmd)
        self.assertIn("--new-instance", cmd)
        self.assertEqual(cmd[-2:], ["-b", "(gimp-quit 0)"])
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertLessEqual(run.call_args_list[0].kwargs["timeout"], 10)
        self.assertEqual(result, "VERSION=2.10.36")

    def test_gimp2_batch_error_is_failure_even_with_zero_exit(self):
        module = self.load()
        with mock.patch.object(subprocess, "run") as run:
            run.side_effect = [
                mock.Mock(returncode=0, stdout="GIMP version 2.10.36", stderr=""),
                mock.Mock(returncode=0, stdout="", stderr=(
                    "batch command experienced an execution error:\n"
                    "Error: eval: unbound variable: bad-procedure")),
            ]
            with self.assertRaisesRegex(RuntimeError, "bad-procedure"):
                module.gimp_script("(bad-procedure)")

    def test_unknown_or_failed_version_probe_does_not_run_a_script(self):
        for code, version in [(0, "unknown"), (1, "GIMP version 3.2.6"),
                              (0, "GIMP version 4.0.0")]:
            with self.subTest(code=code, version=version):
                module = self.load()
                with mock.patch.object(subprocess, "run", return_value=mock.Mock(
                        returncode=code, stdout=version, stderr="")) as run:
                    with self.assertRaises(RuntimeError):
                        module.gimp_script("(gimp-version)")
                self.assertEqual(run.call_count, 1)

    def test_batch_timeout_propagates(self):
        module = self.load()
        with mock.patch.object(subprocess, "run", side_effect=[
                mock.Mock(returncode=0, stdout="GIMP version 3.2.6", stderr=""),
                subprocess.TimeoutExpired("gimp", 7)]):
            with self.assertRaises(subprocess.TimeoutExpired):
                module.gimp_script("(gimp-version)", timeout=7)


def _gimp_binary_for_major(major: int) -> str | None:
    """Resolve a real installed binary; live tests skip on other versions."""
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
        if f"version {major}." in out:
            return candidate
    return None


GIMP3 = _gimp_binary_for_major(3)
GIMP2 = _gimp_binary_for_major(2)


@unittest.skipUnless(GIMP2, "no GIMP 2 installed")
class LegacyWrapperActuallyRuns(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = mock.patch.dict(os.environ, {"GIMP2_DIRECTORY": directory.name})
        config.start()
        self.addCleanup(config.stop)

    def test_real_version_expression(self):
        module = BatchScriptIsGimp3.load()
        with mock.patch.object(module, "gimp_binary", return_value=GIMP2):
            output = module.gimp_script('(gimp-message (car (gimp-version)))', timeout=30)
        self.assertIn("2.10.", output)

    def test_real_script_error_raises(self):
        module = BatchScriptIsGimp3.load()
        with mock.patch.object(module, "gimp_binary", return_value=GIMP2):
            with self.assertRaisesRegex(RuntimeError, "unbound variable"):
                module.gimp_script('(missing-mom-test-procedure)', timeout=30)

    @unittest.skipUnless(Image, "requires Pillow")
    def test_legacy_doc_fill_and_export(self):
        module = BatchScriptIsGimp3.load()
        legacy = DOC.read_text().split("## GIMP 2.10 compatibility", 1)[1]
        block = code_blocks(legacy)[0]
        script = block.split("-b '", 1)[1].split("' -b", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "orange.png"
            with mock.patch.object(module, "gimp_binary", return_value=GIMP2):
                module.gimp_script(script.replace("/tmp/out.png", str(output)), timeout=30)
            with Image.open(output) as image:
                self.assertEqual(image.size, (64, 64))
                self.assertEqual(image.convert("RGBA").getextrema(),
                                 ((255, 255), (128, 128), (0, 0), (255, 255)))


class DocRecipeActuallyRuns(unittest.TestCase):
    """The doc claims a verified recipe. Run it and read the pixels back."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = mock.patch.dict(os.environ, {"GIMP3_DIRECTORY": directory.name})
        config.start()
        self.addCleanup(config.stop)

    @unittest.skipUnless(GIMP3 and Image, "requires GIMP 3 and Pillow")
    def test_fill_and_export_writes_the_colour_it_was_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "probe.png"
            block = next(block for block in code_blocks(DOC.read_text())
                         if '(gimp-image-new 64 64 RGB)' in block)
            script = block.split("-b '", 1)[1].rsplit("'", 1)[0]
            script = script.replace("/tmp/out.png", str(out))
            module = BatchScriptIsGimp3.load()
            with mock.patch.object(module, "gimp_binary", return_value=GIMP3):
                module.gimp_script(script, timeout=180)
            self.assertTrue(out.exists(), "the recipe wrote no file")
            with Image.open(out) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (64, 64))
                self.assertEqual(image.convert("RGBA").getextrema(),
                                 ((255, 255), (128, 128), (0, 0), (255, 255)))

    @unittest.skipUnless(GIMP3, "no GIMP 3 installed")
    def test_a_removed_procedure_fails_fast_with_quit(self):
        # The whole reason --quit is in the doc: prove the failure exits.
        done = subprocess.run(
            [GIMP3, "-i", "--new-instance", "--batch-interpreter=plug-in-script-fu-eval",
             "-b", "(plug-in-gauss RUN-NONINTERACTIVE 1 1 8.0 8.0 1)", "--quit"],
            capture_output=True, text=True, timeout=180,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unbound variable", done.stdout + done.stderr)


@unittest.skipUnless(Image, "requires Pillow")
class ColourRecipeControls(unittest.TestCase):
    """Drive the real recipe assertion with a correct and an incorrect PNG."""

    def _run_recipe_with_colour(self, colour, wrong_pixel=False):
        def render(cmd, **kwargs):
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "GIMP version 3.2.6", "")
            script = cmd[cmd.index("-b") + 1]
            output = re.search(r'#:file "([^"]+)"', script).group(1)
            image = Image.new("RGBA", (64, 64), colour)
            if wrong_pixel:
                image.putpixel((0, 0), (0, 0, 0, 255))
            image.save(output)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        # Unwrap only the missing GIMP decorator; run the actual test body.
        recipe = DocRecipeActuallyRuns(
            "test_fill_and_export_writes_the_colour_it_was_asked_for")
        method = DocRecipeActuallyRuns.test_fill_and_export_writes_the_colour_it_was_asked_for
        body = getattr(method, "__wrapped__", method)
        with mock.patch.object(subprocess, "run", side_effect=render):
            body(recipe)

    def test_orange_passes(self):
        self._run_recipe_with_colour((255, 128, 0))

    def test_black_is_rejected(self):
        with self.assertRaises(AssertionError):
            self._run_recipe_with_colour((0, 0, 0))

    def test_one_wrong_pixel_is_rejected(self):
        with self.assertRaises(AssertionError):
            self._run_recipe_with_colour((255, 128, 0), wrong_pixel=True)

    def test_transparent_orange_is_rejected(self):
        with self.assertRaises(AssertionError):
            self._run_recipe_with_colour((255, 128, 0, 0))


if __name__ == "__main__":
    unittest.main()
