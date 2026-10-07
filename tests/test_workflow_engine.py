"""workflow.py fixes from the Linux bot review 2026-09-27.

- a timed out step killed only the shell, so the work under it ran on;
- --resume sorted random run ids by name and resumed an arbitrary run;
- duplicate step ids and dependencies on unknown steps were accepted.
"""

# The skill's own libraries and tools: CI installs neither, so this module
# skips there instead of failing on an import.
import importlib.util as _ilu  # noqa: E402
import unittest as _unittest  # noqa: E402

_NEEDS = [m for m in ('yaml',) if _ilu.find_spec(m) is None]
if _NEEDS:
    raise _unittest.SkipTest("needs " + ", ".join(_NEEDS))

import argparse
import importlib.util
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "workflow" / "scripts" / "workflow.py"
spec = importlib.util.spec_from_file_location("workflow_under_test", SCRIPT)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name, value in (("STATE_DIR", self.tmp / "state"),
                            ("HISTORY_FILE", self.tmp / "state" / "history.json"),
                            ("WORKFLOW_DIR", self.tmp / "workflows"),
                            ("RUN_ROOT", self.tmp)):
            patcher = mock.patch.object(wf, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        wf.ensure_dirs()

    def write(self, name, text):
        path = self.tmp / "workflows" / f"{name}.yaml"
        path.write_text(text, encoding="utf-8")
        return path


class TimeoutKillsTheWholeStep(Isolated):
    def test_children_of_the_shell_die_too(self):
        # The step backgrounds a sleep and records its pid, then waits; the
        # pid file identifies the child exactly (a pgrep pattern can match an
        # unrelated process whose command line carries the same text).
        self.write("slow", "name: slow\nsteps:\n  - id: s\n"
                           "    command: sleep 300 & echo $! > child.pid; wait\n"
                           "    timeout: 1\n")
        engine = wf.WorkflowEngine(verbose=False)
        began = time.monotonic()
        run = engine.run(engine.load_workflow("slow"))
        self.assertLess(time.monotonic() - began, 20)
        self.assertEqual(run.results["s"].status, "failed")
        pid = int((Path(run.variables["run_dir"]) / "child.pid").read_text())
        self.addCleanup(shutil.rmtree, run.variables["run_dir"], True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.1)
        if _alive(pid):
            os.kill(pid, 9)
            self.fail("the step's child outlived its timeout")

    @unittest.skipUnless(shutil.which("setsid"), "needs setsid (util-linux)")
    def test_a_child_that_left_the_group_does_not_hang_the_run(self):
        # audit pass 2: after the kill the runner waited for the output pipes to
        # close, and a setsid child still holding them kept the run waiting
        child = self.tmp / "child.sh"
        child.write_text("echo $$ > child.pid\nexec sleep 30\n", encoding="utf-8")
        self.write("escape", "name: escape\nsteps:\n  - id: s\n"
                             f"    command: setsid sh {child} & wait\n"
                             "    timeout: 1\n")
        engine = wf.WorkflowEngine(verbose=False)
        began = time.monotonic()
        run = engine.run(engine.load_workflow("escape"))
        took = time.monotonic() - began
        pid_file = Path(run.variables["run_dir"]) / "child.pid"
        self.addCleanup(shutil.rmtree, run.variables["run_dir"], True)
        if pid_file.exists() and _alive(int(pid_file.read_text())):
            os.kill(int(pid_file.read_text()), 9)
        self.assertEqual(run.results["s"].status, "failed")
        self.assertLess(took, 15, "the run waited on a pipe held by a child outside the step's group")


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, IndexError):
        return False
    return state != "Z"


class ResumePicksTheNewestRun(Isolated):
    def test_most_recent_unfinished_run_is_resumed(self):
        path = self.write("r", "name: r\nsteps:\n  - id: a\n    command: test \"{{go}}\" = yes\n")
        engine = wf.WorkflowEngine(verbose=False)
        definition = engine.load_workflow(path)
        runs = []
        for i in range(6):
            run = engine.run(definition, variables={"go": "no", "n": str(i)})
            self.assertEqual(run.status, "failed")
            os.utime(run.state_file, (1_000_000 + i, 1_000_000 + i))
            runs.append(run)
        newest = runs[-1]
        resumed = []
        real_load = wf.WorkflowRun.load_state
        with mock.patch.object(wf.WorkflowRun, "load_state",
                               side_effect=lambda rid: resumed.append(rid) or real_load(rid)):
            wf.cmd_run(argparse.Namespace(workflow=str(path), var=["go=yes"], resume=True))
        self.assertEqual(resumed, [newest.run_id])


class DefinitionsAreChecked(Isolated):
    def test_duplicate_ids_and_unknown_dependencies_are_refused(self):
        engine = wf.WorkflowEngine(verbose=False)
        cases = {
            "dup": "name: d\nsteps:\n  - id: a\n    command: 'true'\n  - id: a\n    command: 'true'\n",
            "later": "name: l\nsteps:\n  - id: a\n    command: 'true'\n    depends: [b]\n"
                     "  - id: b\n    command: 'true'\n",
            "typo": "name: t\nsteps:\n  - id: a\n    command: 'true'\n  - id: b\n    command: 'true'\n"
                    "    depends: [aa]\n",
            "empty": "",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    engine.load_workflow(self.write(name, text))

    def test_a_valid_chain_still_runs(self):
        engine = wf.WorkflowEngine(verbose=False)
        run = engine.run(engine.load_workflow(self.write(
            "ok", "name: ok\nsteps:\n  - id: a\n    command: echo hi\n"
                  "  - id: b\n    command: cat -\n    stdin: '{{a.stdout}}'\n    depends: [a]\n")))
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.results["b"].stdout.strip(), "hi")


if __name__ == "__main__":
    unittest.main()
