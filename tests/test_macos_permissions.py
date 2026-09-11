"""Offline tests for the macOS screen-control permission step.

Nothing here touches TCC, opens System Settings, or takes a screenshot. Every
probe in the module is a subprocess call, so the subprocess boundary is where
the tests cut: real command output, captured from this machine on 11 Sep 2026,
is fed back in and the three-state answer is checked against it.

The traps being locked down, all of them met while building this:

  1  three TCC services get treated as one. Automation was GRANTED on this
     machine while Accessibility was refused, so "the bot can talk to System
     Events" is not evidence that clicking works
  2  the grant names a Python.app inside the framework, never the
     bin/python3.12 that .venv points at. Adding the obvious one produces a
     grant that applies to nothing and reports no error
  3  the launch agent runs the bot as `/bin/bash -c "... exec python bot.py"`,
     so the process table offers /bin/bash as the thing to grant
  4  `pgrep -f bot.py` finds nothing from inside the bot, because on macOS it
     skips ancestors of the caller
  5  a marker on disk is not a state: recording that somebody said yes, rather
     than that a probe came back granted, is the failure this module exists to
     avoid
  6  the grant is keyed to an ad-hoc signature at a versioned Homebrew path, so
     a Python upgrade silently voids it while System Settings still shows the
     switch on
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def load_module():
    """Import by path so the test runs whether or not `install` is a package
    on the path, and so each test class gets a module it can patch freely."""
    path = REPO / "install" / "macos_permissions.py"
    spec = importlib.util.spec_from_file_location("macos_permissions_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


perms = load_module()


# Captured from this machine, verbatim.
AX_DENIED = ('44:91: execution error: System Events got an error: osascript is '
             'not allowed assistive access. (-25211)\n')
SR_DENIED_RECT = "could not create image from rect\n"
SR_DENIED_DISPLAY = "could not create image from display\n"
AUTOMATION_OK = "loginwindow\n"
AUTOMATION_DENIED = ("execution error: Not authorized to send Apple events to "
                     "System Events. (-1743)\n")


class ProbeTests(unittest.TestCase):
    """Each probe, driven by real captured output."""

    def _osa(self, replies):
        """Answer osascript by matching a fragment of the script it was given."""
        def fake(script, timeout=25):
            for fragment, reply in replies.items():
                if fragment in script:
                    return reply
            raise AssertionError(f"unexpected script: {script}")
        return fake

    def test_automation_granted(self):
        with mock.patch.object(perms, "_osascript",
                               self._osa({"name of first process": (0, AUTOMATION_OK)})):
            self.assertEqual(perms.probe_automation(), perms.GRANTED)

    def test_automation_denied(self):
        with mock.patch.object(perms, "_osascript",
                               self._osa({"name of first process": (1, AUTOMATION_DENIED)})):
            self.assertEqual(perms.probe_automation(), perms.DENIED)

    def test_automation_unknown_when_it_fails_some_other_way(self):
        with mock.patch.object(perms, "_osascript",
                               self._osa({"name of first process": (1, "timed out\n")})):
            self.assertEqual(perms.probe_automation(), perms.UNKNOWN)

    def test_accessibility_denied_is_the_state_measured_here(self):
        with mock.patch.object(perms, "_osascript", self._osa({
            "name of first process": (0, AUTOMATION_OK),
            "UI elements enabled": (0, "false\n"),
            "count of windows": (1, AX_DENIED),
        })):
            self.assertEqual(perms.probe_accessibility(), perms.DENIED)

    def test_accessibility_granted_needs_both_instruments(self):
        with mock.patch.object(perms, "_osascript", self._osa({
            "name of first process": (0, AUTOMATION_OK),
            "UI elements enabled": (0, "true\n"),
            "count of windows": (0, "3\n"),
        })):
            self.assertEqual(perms.probe_accessibility(), perms.GRANTED)

    def test_accessibility_is_denied_when_the_two_instruments_disagree(self):
        # The read is the capability; a flag that says otherwise does not
        # outvote it. Resolving a disagreement towards "yes" would report a
        # permission the bot does not have.
        with mock.patch.object(perms, "_osascript", self._osa({
            "name of first process": (0, AUTOMATION_OK),
            "UI elements enabled": (0, "false\n"),
            "count of windows": (0, "3\n"),
        })):
            self.assertEqual(perms.probe_accessibility(), perms.DENIED)

    def test_accessibility_is_unknown_when_apple_events_are_blocked(self):
        # This is trap 1. With Automation refused, every accessibility probe
        # fails for a reason that has nothing to do with Accessibility, and
        # reporting DENIED here would send somebody to the wrong pane.
        with mock.patch.object(perms, "_osascript", self._osa({
            "name of first process": (1, AUTOMATION_DENIED),
        })):
            self.assertEqual(perms.probe_accessibility(), perms.UNKNOWN)

    def test_screen_recording_denied_both_wordings(self):
        for text in (SR_DENIED_RECT, SR_DENIED_DISPLAY):
            with mock.patch.object(perms, "_run", return_value=(1, text)):
                self.assertEqual(perms.probe_screen_recording(), perms.DENIED, text)

    def test_screen_recording_granted_needs_real_bytes(self):
        # A zero-byte file is not a screenshot. screencapture can exit 0 and
        # leave nothing behind, and that must not read as granted.
        def write_nothing(cmd, timeout=25):
            Path(cmd[-1]).write_bytes(b"")
            return 0, ""

        def write_png(cmd, timeout=25):
            Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\n")
            return 0, ""

        with mock.patch.object(perms, "_run", write_nothing):
            self.assertEqual(perms.probe_screen_recording(), perms.UNKNOWN)
        with mock.patch.object(perms, "_run", write_png):
            self.assertEqual(perms.probe_screen_recording(), perms.GRANTED)

    def test_the_probe_captures_one_pixel_not_the_screen(self):
        seen = {}

        def capture(cmd, timeout=25):
            seen["cmd"] = cmd
            return 1, SR_DENIED_RECT

        with mock.patch.object(perms, "_run", capture):
            perms.probe_screen_recording()
        self.assertIn("-R", seen["cmd"])
        self.assertIn("0,0,1,1", seen["cmd"])

    def test_the_probe_leaves_no_image_behind(self):
        kept = {}

        def capture(cmd, timeout=25):
            kept["path"] = Path(cmd[-1])
            kept["path"].write_bytes(b"\x89PNG\r\n\x1a\n")
            return 0, ""

        with mock.patch.object(perms, "_run", capture):
            perms.probe_screen_recording()
        self.assertFalse(kept["path"].exists())


class GrantTargetTests(unittest.TestCase):
    """Trap 2, 3 and 4: which program the grant has to name."""

    def _framework(self, root: Path, version: str = "3.12") -> Path:
        base = root / "Frameworks" / "Python.framework" / "Versions" / version
        (base / "bin").mkdir(parents=True)
        (base / "Resources" / "Python.app" / "Contents" / "MacOS").mkdir(parents=True)
        binary = base / "bin" / f"python{version}"
        binary.write_text("")
        return binary

    def test_framework_app_is_found_from_the_bin_symlink_target(self):
        with tempfile.TemporaryDirectory() as td:
            binary = self._framework(Path(td))
            found = perms.framework_app(binary)
            self.assertIsNotNone(found)
            self.assertTrue(str(found).endswith("Resources/Python.app"))

    def test_a_plain_python_has_no_framework_app(self):
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "usr" / "bin" / "python3"
            binary.parent.mkdir(parents=True)
            binary.write_text("")
            self.assertIsNone(perms.framework_app(binary))

    def test_the_search_stops_at_versions(self):
        # Without the stop, a Resources/Python.app belonging to some unrelated
        # framework further up the tree would be offered as the answer.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binary = self._framework(root)
            (binary.parent.parent / "Resources" / "Python.app").rename(
                binary.parent.parent / "Resources" / "NotIt.app")
            decoy = root / "Resources" / "Python.app"
            decoy.mkdir(parents=True)
            self.assertIsNone(perms.framework_app(binary))

    def test_the_shell_wrapper_is_not_the_bot(self):
        # Trap 3. The launch agent's own line mentions bot.py and starts with
        # /bin/bash; granting that would be useless and frightening.
        ps = (
            "  501 /bin/bash -c set -a; source /repo/.env; set +a; exec "
            "/repo/.venv/bin/python /repo/bot.py\n"
            "  608 /opt/homebrew/Cellar/python@3.12/3.12.14/Frameworks/"
            "Python.framework/Versions/3.12/Resources/Python.app/Contents/"
            "MacOS/Python /repo/bot.py\n"
        )
        with mock.patch.object(perms, "_run", return_value=(0, ps)):
            exe = perms._live_bot_executable()
        self.assertIsNotNone(exe)
        self.assertNotIn("bash", str(exe))
        self.assertTrue(str(exe).endswith("MacOS/Python"))

    def test_a_grep_for_the_bot_is_not_the_bot(self):
        ps = "  999 /usr/bin/grep bot.py\n"
        with mock.patch.object(perms, "_run", return_value=(0, ps)):
            self.assertIsNone(perms._live_bot_executable())

    def test_the_live_process_gives_the_bundle_not_the_binary_inside_it(self):
        ps = ("  608 /opt/homebrew/Cellar/python@3.12/3.12.14/Frameworks/"
              "Python.framework/Versions/3.12/Resources/Python.app/Contents/"
              "MacOS/Python /repo/bot.py\n")
        with mock.patch.object(perms, "_run", return_value=(0, ps)):
            target = perms.grant_target()
        self.assertTrue(str(target).endswith("Resources/Python.app"))
        self.assertNotIn("Contents/MacOS", str(target))

    def test_falls_back_to_the_virtualenv_when_no_bot_is_running(self):
        # This is the install-time case: the wizard runs before any bot exists.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            binary = self._framework(repo)
            venv_bin = repo / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "python").symlink_to(binary)
            with mock.patch.object(perms, "_live_bot_executable", return_value=None):
                target = perms.grant_target(repo_dir=repo)
            self.assertIsNotNone(target)
            self.assertTrue(str(target).endswith("Resources/Python.app"))

    def test_no_venv_and_no_bot_is_none_rather_than_a_guess(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(perms, "_live_bot_executable", return_value=None):
                self.assertIsNone(perms.grant_target(repo_dir=Path(td)))


class StateTests(unittest.TestCase):
    """Trap 5 and 6: what gets written down, and what it is for."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.state = Path(self._td.name) / "macos_permissions.json"
        self.addCleanup(self._td.cleanup)

    def test_only_an_observed_grant_is_recorded(self):
        perms.record_grants(
            {"accessibility": perms.GRANTED,
             "screen_recording": perms.DENIED,
             "automation": perms.UNKNOWN},
            Path("/tmp/Python.app"), path=self.state)
        written = json.loads(self.state.read_text())["granted"]
        self.assertEqual(list(written), ["accessibility"])

    def test_nothing_recorded_means_not_configured(self):
        self.assertFalse(perms.is_configured(path=self.state))
        perms.record_grants({"accessibility": perms.GRANTED},
                            Path("/tmp/Python.app"), path=self.state)
        self.assertTrue(perms.is_configured(path=self.state))

    def test_is_configured_does_not_probe(self):
        # It decides whether the installer asks again on resume. If it probed,
        # a resume on a machine whose owner deliberately revoked the grant
        # would turn into the same question every time.
        perms.record_grants({"accessibility": perms.GRANTED},
                            Path("/tmp/Python.app"), path=self.state)
        with mock.patch.object(perms, "_run",
                               side_effect=AssertionError("probed")):
            self.assertTrue(perms.is_configured(path=self.state))

    def test_no_regression_when_nothing_was_ever_granted(self):
        with mock.patch.object(perms, "probe_all",
                               side_effect=AssertionError("probed")):
            self.assertEqual(perms.regressions(path=self.state), [])

    def test_a_revoked_grant_is_reported(self):
        perms.record_grants({"accessibility": perms.GRANTED},
                            Path("/tmp/Python.app"), path=self.state)
        with mock.patch.object(perms, "probe_all",
                               return_value={"accessibility": perms.DENIED}), \
             mock.patch.object(perms, "grant_target",
                               return_value=Path("/tmp/Python.app")), \
             mock.patch.object(perms, "code_identity", return_value=None), \
             mock.patch.object(perms.platform, "system", return_value="Darwin"):
            lines = perms.regressions(path=self.state)
        self.assertEqual(len(lines), 1)
        self.assertIn("Accessibility", lines[0])

    def test_an_upgraded_interpreter_is_reported_differently(self):
        # Trap 6, and the reason the identity is stored at all. Same symptom
        # as a revoke, completely different instruction, so the two must not
        # share a message.
        with mock.patch.object(perms, "code_identity", return_value="Python-AAA"):
            perms.record_grants({"accessibility": perms.GRANTED},
                                Path("/tmp/Python.app"), path=self.state)
        with mock.patch.object(perms, "probe_all",
                               return_value={"accessibility": perms.DENIED}), \
             mock.patch.object(perms, "grant_target",
                               return_value=Path("/tmp/New.app")), \
             mock.patch.object(perms, "code_identity", return_value="Python-BBB"), \
             mock.patch.object(perms.platform, "system", return_value="Darwin"):
            lines = perms.regressions(path=self.state)
        self.assertEqual(len(lines), 1)
        self.assertIn("replaced", lines[0])
        self.assertIn("/tmp/New.app", lines[0])

    def test_a_grant_that_still_works_is_not_a_regression(self):
        perms.record_grants({"accessibility": perms.GRANTED},
                            Path("/tmp/Python.app"), path=self.state)
        with mock.patch.object(perms, "probe_all",
                               return_value={"accessibility": perms.GRANTED}), \
             mock.patch.object(perms, "grant_target",
                               return_value=Path("/tmp/Python.app")), \
             mock.patch.object(perms, "code_identity", return_value="x"), \
             mock.patch.object(perms.platform, "system", return_value="Darwin"):
            self.assertEqual(perms.regressions(path=self.state), [])

    def test_unknown_is_not_a_regression(self):
        # UNKNOWN means the probe could not tell, usually because Automation
        # is off. Reporting that as a lost permission would be a false alarm
        # on exactly the machines least able to check.
        perms.record_grants({"accessibility": perms.GRANTED},
                            Path("/tmp/Python.app"), path=self.state)
        with mock.patch.object(perms, "probe_all",
                               return_value={"accessibility": perms.UNKNOWN}), \
             mock.patch.object(perms, "grant_target",
                               return_value=Path("/tmp/Python.app")), \
             mock.patch.object(perms, "code_identity", return_value="x"), \
             mock.patch.object(perms.platform, "system", return_value="Darwin"):
            self.assertEqual(perms.regressions(path=self.state), [])

    def test_an_unreadable_state_file_is_empty_not_a_crash(self):
        self.state.write_text("{ not json")
        self.assertEqual(perms.load_state(path=self.state), {})
        self.assertFalse(perms.is_configured(path=self.state))


class InteractiveTests(unittest.TestCase):
    """The step itself: it must never claim a grant it did not observe."""

    def _run_step(self, before, after, answer="y"):
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return answer if len(asked) == 1 else ""

        states = iter([before, after])
        with mock.patch.object(perms.platform, "system", return_value="Darwin"), \
             mock.patch.object(perms, "probe_all", lambda: next(states)), \
             mock.patch.object(perms, "grant_target",
                               return_value=Path("/tmp/Python.app")), \
             mock.patch.object(perms, "copy_to_clipboard", return_value=True), \
             mock.patch.object(perms, "open_pane", return_value=True) as pane, \
             mock.patch.object(perms, "record_grants") as record:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                perms.run_macos_permissions_step({}, ask=ask)
        return buf.getvalue(), pane, record, asked

    def test_saying_no_opens_nothing(self):
        all_denied = {p["key"]: perms.DENIED for p in perms.PERMISSIONS}
        out, pane, record, _ = self._run_step(all_denied, all_denied, answer="n")
        pane.assert_not_called()
        record.assert_not_called()
        self.assertIn("Skipped", out)

    def test_the_warning_is_printed_before_the_question(self):
        # The question is asked through `ask`, not printed, so the ordering
        # has to be checked across both channels: the standing-grant warning
        # must already be on screen when the prompt appears.
        all_denied = {p["key"]: perms.DENIED for p in perms.PERMISSIONS}
        out, _, _, asked = self._run_step(all_denied, all_denied, answer="n")
        self.assertIn("keylogger", out)
        self.assertIn("Set these up now?", asked[0])
        self.assertLess(out.index("keylogger"), out.index("Right now:"))

    def test_a_grant_that_did_not_take_is_reported_as_missing(self):
        all_denied = {p["key"]: perms.DENIED for p in perms.PERMISSIONS}
        out, _, _, _ = self._run_step(all_denied, all_denied, answer="y")
        self.assertIn("Still missing", out)
        self.assertNotIn("All set", out)

    def test_a_grant_that_took_says_so(self):
        before = {p["key"]: perms.DENIED for p in perms.PERMISSIONS}
        after = {p["key"]: perms.GRANTED for p in perms.PERMISSIONS}
        out, _, _, _ = self._run_step(before, after, answer="y")
        self.assertIn("All set", out)

    def test_the_step_is_a_no_op_off_darwin(self):
        with mock.patch.object(perms.platform, "system", return_value="Linux"), \
             mock.patch.object(perms, "probe_all",
                               side_effect=AssertionError("probed")):
            perms.run_macos_permissions_step({}, ask=lambda p: "y")


class CliTests(unittest.TestCase):
    def test_json_shape(self):
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(perms.platform, "system", return_value="Darwin"), \
             mock.patch.object(perms, "probe_all",
                               return_value={"accessibility": perms.DENIED}), \
             mock.patch.object(perms, "grant_target",
                               return_value=Path("/tmp/Python.app")), \
             mock.patch.object(perms, "regressions", return_value=[]):
            with contextlib.redirect_stdout(buf):
                rc = perms.main(["--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["states"]["accessibility"], "denied")
        self.assertEqual(payload["target"], "/tmp/Python.app")
        self.assertEqual(payload["regressions"], [])

    def test_off_darwin_it_refuses(self):
        import contextlib
        import io
        with mock.patch.object(perms.platform, "system", return_value="Linux"), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(perms.main(["--check"]), 1)

    def test_every_permission_has_a_pane_to_open(self):
        for perm in perms.PERMISSIONS:
            self.assertIn(perm["key"], perms.PANES, perm["key"])
            self.assertTrue(perms.PANES[perm["key"]].startswith(
                "x-apple.systempreferences:"))


class WiringTests(unittest.TestCase):
    """The step has to be reachable, and the regression has to be noticed."""

    def test_the_wizard_offers_it_on_macos_only(self):
        source = (REPO / "install" / "wizard.py").read_text()
        self.assertIn('"key": "macos_screen_control"', source)
        entry = source.split('"key": "macos_screen_control"', 1)[1].split("},", 1)[0]
        self.assertIn('platform.system() == "Darwin"', entry)
        self.assertIn("_is_macos_permissions_configured", entry)
        self.assertIn("_run_macos_permissions_step", entry)

    def test_the_nightly_report_can_raise_it(self):
        source = (REPO / "utils" / "nightly_report.py").read_text()
        self.assertIn("_permissions_section", source)
        self.assertIn("macos_permissions import regressions", source)

    def test_the_nightly_section_is_silent_with_nothing_to_say(self):
        import importlib
        report = importlib.import_module("utils.nightly_report")
        with mock.patch("install.macos_permissions.regressions", return_value=[]):
            self.assertEqual(report._permissions_section(), [])

    def test_the_nightly_section_speaks_when_a_grant_is_lost(self):
        import importlib
        report = importlib.import_module("utils.nightly_report")
        with mock.patch("install.macos_permissions.regressions",
                        return_value=["Accessibility was granted once and is refused now."]):
            lines = report._permissions_section()
        self.assertEqual(lines[0], "SCREEN CONTROL")
        self.assertIn("Accessibility", lines[1])

    def test_the_nightly_section_survives_a_broken_probe(self):
        # The nightly report is the machine's one scheduled voice. A probe
        # that throws must cost a section, never the whole report.
        import importlib
        report = importlib.import_module("utils.nightly_report")
        with mock.patch("install.macos_permissions.regressions",
                        side_effect=RuntimeError("osascript exploded")):
            self.assertEqual(report._permissions_section(), [])


class SpacedPathTests(unittest.TestCase):
    """A checkout whose path contains a space.

    `/Users/j/My Old Machine` is an ordinary place to put this on a Mac, and
    everything before the first space of a ps line is then `/Users/j/My`,
    which names nothing. The whole point of the module is handing System
    Settings an entry that exists, so naming one that does not is the worst
    answer available: it is the failure the module was written to prevent,
    wearing the module's own authority.
    """

    def _repo_with_a_space(self, td):
        repo = Path(td) / "My Old Machine"
        venv_bin = repo / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        real = venv_bin / "python3.12"
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        (venv_bin / "python").symlink_to(real)
        (repo / "bot.py").write_text("")
        return repo, real

    def test_a_spaced_executable_is_not_cut_at_the_first_space(self):
        with tempfile.TemporaryDirectory() as td:
            repo, real = self._repo_with_a_space(td)
            args = f"{real} {repo / 'bot.py'}"
            self.assertEqual(perms._executable_from(args), real)

    def test_the_live_bot_is_found_when_the_repo_path_has_a_space(self):
        with tempfile.TemporaryDirectory() as td:
            repo, real = self._repo_with_a_space(td)
            ps = (
                f"  501 /bin/bash -c set -a; source {repo / '.env'}; set +a; "
                f"exec {real} {repo / 'bot.py'}\n"
                f"  608 {real} {repo / 'bot.py'}\n"
            )
            with mock.patch.object(perms, "_run", return_value=(0, ps)):
                exe = perms._live_bot_executable()
                target = perms.grant_target(repo_dir=repo)
            self.assertEqual(exe, real)
            self.assertNotIn("bash", str(exe))
            # And the thing the account holder is told to paste has to exist.
            self.assertTrue(Path(target).exists(), f"{target} does not exist")

    def test_a_relative_command_is_not_offered_as_the_grant(self):
        # `python bot.py` typed in a terminal. A relative name resolves against
        # whatever directory this happens to be run from, and "python" is not
        # something anybody can add in System Settings.
        with mock.patch.object(perms, "_run",
                               return_value=(0, "  701 python3 ./repo/bot.py\n")):
            self.assertIsNone(perms._live_bot_executable())

    def test_an_unspaced_path_is_unchanged_when_nothing_is_on_disk(self):
        # The hermetic case every other test in this file relies on: no file
        # matches, so the first token stays the answer.
        args = "/opt/homebrew/nope/Python.app/Contents/MacOS/Python /repo/bot.py"
        self.assertEqual(
            perms._executable_from(args),
            Path("/opt/homebrew/nope/Python.app/Contents/MacOS/Python"))


class InstallerPromptTests(unittest.TestCase):
    """The step driven by the prompt function the installer really passes.

    Every other interactive test here hands the step a fake `ask` that returns
    a canned string, so none of them can see what happens when the account
    holder does the one thing the screen tells them to do and presses Return.
    This captures the callable `install/wizard.py` hands over and drives the
    real step with it.
    """

    def _installer_ask(self):
        import install.wizard as wizard
        captured = {}

        def spy(config, ask=None):
            captured["ask"] = ask

        with mock.patch("install.macos_permissions.run_macos_permissions_step", spy):
            wizard._run_macos_permissions_step({})
        self.assertIsNotNone(captured.get("ask"), "the wizard passed no prompt function")
        return captured["ask"]

    def _drive(self, askfn, typed):
        """Run the step with `typed` on stdin. Returns (outcome, printed)."""
        states = {"accessibility": perms.DENIED,
                  "screen_recording": perms.DENIED,
                  "automation": perms.GRANTED}
        buf = io.StringIO()
        real_stdin, real_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(typed)
        sys.stdout = buf
        outcome = "completed"
        try:
            with mock.patch.object(perms.platform, "system", return_value="Darwin"), \
                 mock.patch.object(perms, "probe_all", return_value=states), \
                 mock.patch.object(perms, "grant_target",
                                   return_value=Path("/tmp/Python.app")), \
                 mock.patch.object(perms, "copy_to_clipboard", return_value=True), \
                 mock.patch.object(perms, "open_pane", return_value=True), \
                 mock.patch.object(perms, "record_grants"):
                perms.run_macos_permissions_step({}, ask=askfn)
        # BaseException on purpose: the defect this guards is a SystemExit
        # raised out of the prompt, which `except Exception` would not see.
        except BaseException as exc:
            outcome = f"{type(exc).__name__}: {exc}"
        finally:
            sys.stdin, sys.stdout = real_stdin, real_stdout
        return outcome, buf.getvalue()

    def test_pressing_return_at_each_step_finishes_the_grant(self):
        # Say yes, then press Return at each "press Return when that is done".
        outcome, printed = self._drive(self._installer_ask(), "y\n" + "\n" * 6)
        self.assertEqual(outcome, "completed")
        self.assertIn("Checked again", printed)
        self.assertNotIn("This field is required", printed)

    def test_pressing_return_at_the_question_declines_rather_than_looping(self):
        outcome, printed = self._drive(self._installer_ask(), "\n")
        self.assertEqual(outcome, "completed")
        self.assertIn("Skipped", printed)
        self.assertNotIn("This field is required", printed)

    def test_the_prompt_is_printed_exactly_as_the_step_wrote_it(self):
        # wizard.ask adds its own indent and a second colon, which is how the
        # two contracts drifted apart in the first place.
        askfn = self._installer_ask()
        buf = io.StringIO()
        real_stdin, real_stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO("\n"), buf
        try:
            answer = askfn("    Press Return here when that is done: ")
        finally:
            sys.stdin, sys.stdout = real_stdin, real_stdout
        self.assertEqual(answer, "")
        self.assertEqual(buf.getvalue(), "    Press Return here when that is done: ")


class EndToEndCliTests(unittest.TestCase):
    """`--check` on the macOS path with nothing inside the module mocked.

    Every other CLI test replaces `grant_target` and `regressions` with canned
    answers, so the chain they stand in front of - `_live_bot_executable`,
    `_executable_from`, `code_identity`, the identity comparison - is never
    executed by anything. Only the shell commands are faked here, so a name
    that does not resolve or a signature that has drifted fails the build on
    Linux instead of waiting for somebody's Mac.
    """

    FRAMEWORK = ("/opt/homebrew/Cellar/python@3.12/3.12.14/Frameworks/"
                 "Python.framework/Versions/3.12/Resources/Python.app"
                 "/Contents/MacOS/Python")

    def _shell(self, cmd, timeout=25):
        """The machine measured on 11 Sep 2026: Apple events on, the rest off."""
        name = Path(cmd[0]).name
        if name == "osascript":
            script = cmd[-1]
            if "name of first process" in script:
                return 0, AUTOMATION_OK
            return 1, AX_DENIED
        if name == "screencapture":
            return 1, SR_DENIED_RECT
        if name == "ps":
            return 0, f"  608 {self.FRAMEWORK} /repo/bot.py\n"
        if name == "codesign":
            return 0, "Executable=/x\nIdentifier=org.python.python\nFormat=bundle\n"
        return 1, ""

    def test_check_runs_the_whole_chain_and_names_the_bundle(self):
        import contextlib
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "macos_permissions.json"
            state.write_text(json.dumps({"granted": {"accessibility": {
                "target": self.FRAMEWORK, "identity": "an-older-build"}}}))
            buf = io.StringIO()
            with mock.patch.object(perms.platform, "system", return_value="Darwin"), \
                 mock.patch.object(perms, "STATE_FILE", state), \
                 mock.patch.object(perms, "_run", side_effect=self._shell):
                with contextlib.redirect_stdout(buf):
                    rc = perms.main(["--check"])
            printed = buf.getvalue()

        self.assertEqual(rc, 0)
        # Three states, measured separately, not one answer for all three.
        self.assertIn("Accessibility", printed)
        # The bundle, not the binary inside it, and not /bin/bash.
        self.assertIn("Resources/Python.app", printed)
        self.assertNotIn("Contents/MacOS", printed)
        # The identity on record differs from the one codesign reports now, so
        # this is the replaced-interpreter message, not the revoked one.
        self.assertIn("was replaced", printed)
        self.assertNotIn("granted once and is refused now", printed)


class ModuleShapeTests(unittest.TestCase):
    def test_nothing_is_defined_after_the_entrypoint_guard(self):
        """A definition below `if __name__` imports fine and crashes as a script.

        Every test here imports the module, so the guard is false and the whole
        file executes; `python install/macos_permissions.py --check` on a Mac
        would hit a NameError instead. Nothing else in the suite can see that,
        and on Linux the CLI exits at the platform check before it gets there.
        """
        import ast
        tree = ast.parse((REPO / "install" / "macos_permissions.py").read_text())
        guards = [n for n in tree.body
                  if isinstance(n, ast.If) and "__name__" in ast.dump(n.test)]
        self.assertEqual(len(guards), 1, "expected exactly one __main__ guard")
        self.assertIs(tree.body[-1], guards[0],
                      "something is defined after `if __name__`; it will be "
                      "undefined when this file is run as a script")


if __name__ == "__main__":
    unittest.main()
