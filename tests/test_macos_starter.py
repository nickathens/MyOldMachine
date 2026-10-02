"""The starter launchd runs in front of the bot's Python on macOS.

What is locked down here, and why each one matters:

  1  the starter hands back the command's exit status, so launchd sees the
     bot's own exit and KeepAlive behaves exactly as it did before
  2  it passes on every signal launchd and /restart send; a SIGTERM that
     stopped at the starter would leave the bot to be SIGKILLed after
     launchd's exit timeout, mid-reply
  3  the bot stays in the starter's process group, so when launchd ends the
     job, the group goes with it and no second poller can survive
  4  it is built once and never rebuilt: macOS keys the grant to the hash of
     its bytes, so a rebuild would void it exactly as a Python update does
  5  a failed build leaves nothing behind for the LaunchAgent to run
  6  the LaunchAgent falls back to Python whenever the starter is missing or
     cannot be executed, so the starter can never keep the bot from starting

The C program is compiled for real wherever a compiler exists, Linux CI
included, since nothing in it is macOS specific. Signing is checked on macOS.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from install import macos_starter  # noqa: E402
from install import service  # noqa: E402

CC = macos_starter.find_compiler()
TEMPLATE = REPO / "install" / "templates" / "com.myoldmachine.bot.plist"

FORWARDED = ("SIGHUP", "SIGINT", "SIGQUIT", "SIGTERM", "SIGUSR1", "SIGUSR2")


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.01)


@unittest.skipIf(CC is None, "no C compiler")
class StarterProgramTests(unittest.TestCase):
    """The C program itself, built from the source the installer builds."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._td.name).resolve()
        cls.starter = cls.dir / "starter"
        subprocess.run([CC, "-O2", "-Wall", "-o", str(cls.starter),
                        str(macos_starter.SOURCE)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _start(self, *cmd: str) -> subprocess.Popen:
        # Its own session, as launchd gives it: the starter leads the group.
        proc = subprocess.Popen([str(self.starter), *cmd], start_new_session=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def reap():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()
        self.addCleanup(reap)
        return proc

    def _run(self, *cmd: str, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.starter), *cmd], capture_output=True,
                              text=True, timeout=30, env=env)

    def test_it_compiles_without_a_warning(self):
        out = subprocess.run([CC, "-O2", "-Wall", "-Wextra", "-o",
                              str(self.dir / "warned"), str(macos_starter.SOURCE)],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr.strip(), "")

    def test_the_exit_status_is_the_commands(self):
        for code in (0, 1, 7, 255):
            with self.subTest(code=code):
                self.assertEqual(self._run("/bin/sh", "-c", f"exit {code}").returncode, code)

    def test_a_command_killed_by_a_signal_exits_128_plus_the_signal(self):
        self.assertEqual(self._run("/bin/sh", "-c", "kill -KILL $$").returncode,
                         128 + signal.SIGKILL)

    def test_no_command_is_a_usage_error(self):
        out = self._run()
        self.assertEqual(out.returncode, 64)
        self.assertIn("usage", out.stderr)

    def test_a_command_that_cannot_start_says_which(self):
        out = self._run("/no/such/program")
        self.assertEqual(out.returncode, 127)
        self.assertIn("/no/such/program", out.stderr)

    def test_arguments_and_environment_pass_through_unchanged(self):
        env = dict(os.environ, STARTER_TEST="kept as is")
        out = self._run("/bin/sh", "-c", 'printf "%s|%s|%s" "$1" "$2" "$STARTER_TEST"',
                        "sh", "two words", "", env=env)
        self.assertEqual(out.stdout, "two words||kept as is")

    def test_every_signal_reaches_the_bot_and_its_exit_comes_back(self):
        for name in FORWARDED:
            with self.subTest(signal=name):
                sig = getattr(signal, name)
                ready = self.dir / f"ready.{name}"
                code = 40 + int(sig)
                proc = self._start(
                    "/bin/sh", "-c",
                    f'trap "exit {code}" {name[3:]}; : > "{ready}"; '
                    f"while :; do sleep 0.05; done")
                _wait_for(ready)
                os.kill(proc.pid, sig)
                self.assertEqual(proc.wait(timeout=10), code,
                                 f"{name} did not reach the child")

    def test_the_bot_is_the_starters_child_in_the_starters_group(self):
        ready = self.dir / "ready.group"
        proc = self._start("/bin/sh", "-c", f'echo $$ > "{ready}.pid"; : > "{ready}"; '
                                            "while :; do sleep 0.05; done")
        _wait_for(ready)
        child = int((self.dir / "ready.group.pid").read_text())
        self.assertNotEqual(child, proc.pid)
        parent = subprocess.run(["ps", "-o", "ppid=", "-p", str(child)],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(int(parent), proc.pid)
        # launchd ends a job by its process group. A child in a group of its
        # own would outlive the starter, and KeepAlive would then start a
        # second bot beside it.
        self.assertEqual(os.getpgid(child), proc.pid)


@unittest.skipIf(CC is None, "no C compiler")
class BuildOnceTests(unittest.TestCase):
    """Trap 4 and 5: built once, proved before it is moved in, never rebuilt."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name).resolve()
        self.path = macos_starter.starter_path(self.home)

    def test_the_starter_lives_outside_the_repo(self):
        path = macos_starter.starter_path(Path("/Users/someone"))
        self.assertEqual(path, Path("/Users/someone/Library/Application Support/"
                                    "MyOldMachine/MyOldMachine"))
        self.assertNotIn(str(REPO), str(macos_starter.starter_path()))

    def test_a_missing_starter_is_built_proved_and_usable(self):
        path, note = macos_starter.ensure_starter(self.home)
        self.assertEqual(path, self.path)
        self.assertTrue(macos_starter.usable(self.path), note)
        self.assertIn("Built", note)
        out = subprocess.run([str(self.path), "/bin/sh", "-c", "exit 3"])
        self.assertEqual(out.returncode, 3)
        # Nothing of the build is left beside it.
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()),
                         [self.path.name])

    @unittest.skipUnless(platform.system() == "Darwin", "codesign is macOS")
    def test_on_macos_it_is_signed_with_a_stable_identifier(self):
        macos_starter.ensure_starter(self.home)
        shown = subprocess.run(["codesign", "-dv", str(self.path)],
                               capture_output=True, text=True).stderr
        self.assertIn(f"Identifier={macos_starter.SIGNING_IDENTIFIER}", shown)
        self.assertEqual(subprocess.run(["codesign", "--verify", "--strict",
                                         str(self.path)]).returncode, 0)

    def test_an_existing_starter_is_never_rebuilt(self):
        # The grant is keyed to the hash of these bytes. Whatever is there,
        # the source in the repo included, must not change them.
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"#!/bin/sh\nexec \"$@\"\n")
        self.path.chmod(0o755)
        before = self.path.stat()
        with mock.patch.object(macos_starter, "find_compiler",
                               side_effect=AssertionError("tried to build")), \
             mock.patch.object(macos_starter, "build",
                               side_effect=AssertionError("tried to build")):
            path, note = macos_starter.ensure_starter(self.home)
        self.assertEqual(path, self.path)
        self.assertIn("kept", note)
        after = self.path.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_size),
                         (after.st_ino, after.st_mtime_ns, after.st_size))
        self.assertEqual(self.path.read_bytes(), b"#!/bin/sh\nexec \"$@\"\n")

    def test_a_second_run_keeps_the_first_build_byte_for_byte(self):
        macos_starter.ensure_starter(self.home)
        first = self.path.read_bytes()
        inode = self.path.stat().st_ino
        macos_starter.ensure_starter(self.home)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(self.path.stat().st_ino, inode)

    def test_no_compiler_means_no_starter_and_nothing_written(self):
        with mock.patch.object(macos_starter, "find_compiler", return_value=None):
            path, note = macos_starter.ensure_starter(self.home)
        self.assertIsNone(path)
        self.assertIn("xcode-select --install", note)
        self.assertFalse(self.path.exists())

    def test_something_else_at_the_path_is_left_alone(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not a program")
        self.path.chmod(0o644)
        with mock.patch.object(macos_starter, "build",
                               side_effect=AssertionError("replaced it")):
            path, _ = macos_starter.ensure_starter(self.home)
        self.assertIsNone(path)
        self.assertEqual(self.path.read_text(), "not a program")

    def test_a_build_that_fails_its_proof_is_not_moved_in(self):
        wrong = Path(self._td.name) / "wrong.c"
        wrong.write_text("int main(void) { return 0; }\n")
        with self.assertRaises(macos_starter.StarterError) as caught:
            macos_starter.build(self.path, CC, source=wrong)
        self.assertIn("exits 7", str(caught.exception))
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_a_build_that_does_not_compile_is_reported_not_raised(self):
        broken = Path(self._td.name) / "broken.c"
        broken.write_text("this is not C\n")
        with mock.patch.object(macos_starter, "SOURCE", broken):
            path, note = macos_starter.ensure_starter(self.home)
        self.assertIsNone(path)
        self.assertIn("Could not build", note)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])


@unittest.skipIf(CC is None, "no C compiler")
class LaunchAgentTests(unittest.TestCase):
    """The installer's real output, run the way launchd runs it.

    The plist is rendered by `_setup_macos_launch_agent` from the real template
    under a scratch home, which also builds a real starter there. Its
    ProgramArguments are then executed with /bin/bash, and a stand-in Python
    reports who its parent is. bash execs whatever comes next, so the stand-in's
    parent is bash's own pid exactly when the starter sat in between.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name).resolve()
        self.home = root / "home"
        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)
        self.repo = root / "repo"
        (self.repo / ".venv" / "bin").mkdir(parents=True)
        (self.repo / "install" / "templates").mkdir(parents=True)
        shutil.copy(TEMPLATE, self.repo / "install" / "templates" / TEMPLATE.name)
        (self.repo / ".env").write_text("STARTER_TEST_ENV=from-dotenv\n")
        self.report = root / "report"
        python = self.repo / ".venv" / "bin" / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'printf "%s %s %s %s" "$$" "$PPID" "$STARTER_TEST_ENV" "$1" > "{self.report}"\n')
        python.chmod(0o755)

    def _install(self, load: bool = False):
        with mock.patch.object(Path, "home", return_value=self.home), \
             mock.patch.object(service, "get_sudo_password", return_value=None), \
             mock.patch.object(service, "sudo_run",
                               side_effect=AssertionError("ran sudo")), \
             mock.patch.object(service, "_remove_stale_telegram_bot_api_plist_macos"), \
             mock.patch.object(service, "_launchctl_load") as load_call:
            self.assertTrue(service._setup_macos_launch_agent(self.repo, load=load))
        agent = self.home / "Library" / "LaunchAgents" / "com.myoldmachine.bot.plist"
        return plistlib.loads(agent.read_bytes()), load_call

    def _launch(self, program_arguments):
        """Run the job's command line; return (bash pid, python pid, its ppid, env, argv1)."""
        if self.report.exists():
            self.report.unlink()
        proc = subprocess.Popen(program_arguments, cwd=self.repo)
        self.assertEqual(proc.wait(timeout=30), 0)
        pid, ppid, env, arg = self.report.read_text().split(" ", 3)
        return proc.pid, int(pid), int(ppid), env, arg

    @property
    def starter(self) -> Path:
        return macos_starter.starter_path(self.home)

    def test_the_plist_names_the_starter_and_is_written_without_a_reload(self):
        plist, load_call = self._install(load=False)
        load_call.assert_not_called()
        args = plist["ProgramArguments"]
        self.assertEqual(args[:2], ["/bin/bash", "-c"])
        self.assertIn(f'"{self.starter}"', args[2])
        self.assertTrue(macos_starter.usable(self.starter))

    def test_a_normal_install_still_loads_it(self):
        _, load_call = self._install(load=True)
        load_call.assert_called_once()

    def test_the_bot_runs_as_the_starters_child(self):
        plist, _ = self._install()
        bash, python, parent, env, arg = self._launch(plist["ProgramArguments"])
        self.assertNotEqual(python, bash)
        self.assertEqual(parent, bash, "the starter was not in between")
        self.assertEqual(env, "from-dotenv")
        self.assertEqual(arg, str(self.repo / "bot.py"))

    def test_without_a_starter_the_bot_starts_as_before(self):
        plist, _ = self._install()
        self.starter.unlink()
        bash, python, _, env, arg = self._launch(plist["ProgramArguments"])
        self.assertEqual(python, bash, "bash should have exec'd Python directly")
        self.assertEqual((env, arg), ("from-dotenv", str(self.repo / "bot.py")))

    def test_a_starter_that_is_not_executable_is_skipped(self):
        plist, _ = self._install()
        self.starter.chmod(0o644)
        bash, python, _, _, _ = self._launch(plist["ProgramArguments"])
        self.assertEqual(python, bash)

    def test_a_starter_that_cannot_be_executed_falls_back_too(self):
        plist, _ = self._install()
        self.starter.write_bytes(b"\x00\x01\x02\x03 not a program")
        bash, python, _, _, _ = self._launch(plist["ProgramArguments"])
        self.assertEqual(python, bash)


class ServiceCommandLineTests(unittest.TestCase):
    def test_no_load_is_refused_off_macos_before_anything_is_touched(self):
        with mock.patch.object(sys, "argv", ["service.py", "--repo-dir", "/x",
                                             "--os", "linux", "--no-load"]), \
             mock.patch.object(service, "setup_linux_service",
                               side_effect=AssertionError("installed")), \
             mock.patch.object(service, "setup_macos_service",
                               side_effect=AssertionError("installed")), \
             mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as caught:
                service.main()
        self.assertEqual(caught.exception.code, 1)

    def test_no_load_reaches_the_macos_setup(self):
        seen = {}

        def fake(repo_dir, os_info=None, load=True):
            seen["load"] = load
            return True

        with mock.patch.object(sys, "argv", ["service.py", "--repo-dir", "/x",
                                             "--os", "macos", "--no-load"]), \
             mock.patch.object(service, "setup_macos_service", side_effect=fake), \
             mock.patch("builtins.print"):
            service.main()
        self.assertEqual(seen, {"load": False})


if __name__ == "__main__":
    unittest.main()
