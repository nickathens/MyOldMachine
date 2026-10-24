"""The 3 day release age in utils.app_updates.

A poisoned release published with a stolen upload token (LiteLLM on PyPI,
October 2026, live for under two hours) is usually found and pulled within
hours, so a nightly job that installs on the first night is the one that
catches it. These tests hold the rule on every path that installs: Claude
Code, the Codex CLI from npm or its cask, and the skill CLIs.

Everything is faked: npm's release dates, every install, the clock. Nothing
here reaches the network or installs anything: every way out of the module
is a tripwire for the whole file, and a test swaps in only what it needs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import app_updates as au  # noqa: E402

NOW = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)
# The two install helpers themselves, for the tests of their command lines;
# everywhere else they are tripwires.
REAL_LIVE = au._npm_install_live
REAL_TRIAL = au._trial_install
REAL_RUN = au._run
REAL_SUBPROCESS_RUN = subprocess.run


_TRIPWIRES = []


def setUpModule():
    def tripwire(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"a test reached the real {name}: {args!r}")
        return fail
    # au.subprocess is the one shared module, so this also covers
    # puppeteer_browsers.npm_global_root, which runs npm itself.
    for owner, name in ((au, "_run"), (au, "_npm_install_live"), (au, "_trial_install"),
                        (au.subprocess, "run"), (au, "_fetch")):
        p = patch.object(owner, name, tripwire(name))
        p.start()
        _TRIPWIRES.append(p)


def tearDownModule():
    # Not addModuleCleanup: pytest does not run those between files, and the
    # tripwires would then break every test file after this one.
    while _TRIPWIRES:
        _TRIPWIRES.pop().stop()


def ago(days=0, hours=0, minutes=0):
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def npm_view(times: dict, latest: str, served=None, extra=None) -> str:
    """What `npm view <pkg> time versions dist-tags --json` prints."""
    stamps = {"created": "2020-01-01T00:00:00.000Z", "modified": "2026-10-23T00:00:00.000Z"}
    stamps.update({v: w.strftime("%Y-%m-%dT%H:%M:%S.000Z") for v, w in times.items()})
    stamps.update(extra or {})
    return json.dumps({"time": stamps,
                       "versions": list(times) if served is None else served,
                       "dist-tags": {"latest": latest}})


class _Clock:
    """The module's clock pinned to NOW."""

    def setUp(self):
        super().setUp()
        p = patch.object(au, "_utcnow", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)


class SettingTests(unittest.TestCase):
    def test_three_days_and_an_hour_for_the_pieces(self):
        # Pinned: a change here changes what every machine installs tonight.
        self.assertEqual(au.MIN_RELEASE_AGE_DAYS, 3)
        self.assertEqual(au.RELEASE_PIECES_GRACE, timedelta(hours=1))

    def test_npm_is_given_exactly_the_window(self):
        self.assertEqual(au._npm_before(NOW), "2026-10-21T01:00:00Z")


class ParseTimeTests(unittest.TestCase):
    def test_npm_times_read_as_utc(self):
        self.assertEqual(au._parse_time("2026-10-23T04:52:29.240Z"),
                         datetime(2026, 10, 23, 4, 52, 29, 240000, tzinfo=timezone.utc))
        self.assertEqual(au._parse_time("2026-10-23T07:52:29+03:00"),
                         datetime(2026, 10, 23, 4, 52, 29, tzinfo=timezone.utc))

    def test_a_time_without_a_zone_is_unknown(self):
        # Up to 14 hours either way is a large slice of a three day window.
        self.assertIsNone(au._parse_time("2026-10-23T04:52:29"))

    def test_garbage_is_unknown(self):
        for bad in ("", "soon", None, 17, "2026-13-40T00:00:00Z"):
            with self.subTest(bad=bad):
                self.assertIsNone(au._parse_time(bad))


class NpmReleaseTimesTests(unittest.TestCase):
    def view(self, out, rc=0):
        with patch("utils.app_updates.shutil.which", return_value="/usr/bin/npm"), \
                patch("utils.app_updates._run", return_value=(rc, out)) as run:
            got = au.npm_release_times("@openai/codex")
        return got, run

    def test_asks_npm_for_the_dates_on_stdout_only(self):
        got, run = self.view(npm_view({"0.163.0": ago(8), "0.164.0": ago(1)}, "0.164.0"))
        self.assertEqual(run.call_args.args[0],
                         ["npm", "view", "@openai/codex", "time", "versions", "dist-tags", "--json"])
        self.assertIs(run.call_args.kwargs["merge_stderr"], False)
        times, latest = got
        self.assertEqual(latest, "0.164.0")
        self.assertEqual(times, {"0.163.0": ago(8), "0.164.0": ago(1)})

    def test_prereleases_and_the_platform_parts_are_dropped(self):
        # version_tuple orders 0.163.0-alpha.11.2 ABOVE 0.163.0, and Codex
        # files its platform builds as 0.163.0-linux-x64 in the same map, so
        # either could pass for a newer release. Both sit below the latest
        # tag here, where only the plain-version rule can drop them.
        out = npm_view({"0.163.0": ago(9), "0.164.0": ago(4)}, "0.164.0",
                       served=["0.163.0", "0.163.0-alpha.11.2", "0.163.0-linux-x64", "0.164.0"],
                       extra={"0.163.0-alpha.11.2": "2026-10-15T00:00:00.000Z",
                              "0.163.0-linux-x64": "2026-10-15T00:00:00.000Z"})
        times, _ = self.view(out)[0]
        self.assertEqual(sorted(times), ["0.163.0", "0.164.0"])

    def test_a_version_npm_no_longer_serves_is_dropped(self):
        # An unpublished release, the usual fate of a poisoned one, keeps its
        # entry in the time map.
        out = npm_view({"1.0.0": ago(30), "1.0.1": ago(5), "1.0.2": ago(4)}, "1.0.2",
                       served=["1.0.0", "1.0.2"])
        times, _ = self.view(out)[0]
        self.assertEqual(sorted(times), ["1.0.0", "1.0.2"])

    def test_versions_above_the_latest_tag_are_dropped(self):
        out = npm_view({"1.0.0": ago(30), "2.0.0": ago(9)}, "1.0.0")
        times, _ = self.view(out)[0]
        self.assertEqual(list(times), ["1.0.0"])

    def test_a_package_with_one_version(self):
        # npm prints a lone version as a string, not a list.
        out = json.dumps({"time": {"1.0.0": "2026-01-01T00:00:00.000Z"},
                          "versions": "1.0.0", "dist-tags": {"latest": "1.0.0"}})
        times, latest = self.view(out)[0]
        self.assertEqual((list(times), latest), (["1.0.0"], "1.0.0"))

    def test_anything_npm_cannot_say_is_none(self):
        for out, rc in (("", 0), ("npm error 404", 1), ("[]", 0), ("not json", 0),
                        (json.dumps({"time": {}, "versions": [], "dist-tags": {}}), 0),
                        (npm_view({"1.0.0": ago(9)}, "latest-is-a-word"), 0),
                        (npm_view({"1.0.0": ago(9)}, "1.0.1"), 0)):
            with self.subTest(out=out[:40], rc=rc):
                self.assertIsNone(self.view(out, rc)[0])

    def test_no_npm_is_none(self):
        with patch("utils.app_updates.shutil.which", return_value=None), \
                patch("utils.app_updates._run") as run:
            self.assertIsNone(au.npm_release_times("surge"))
        run.assert_not_called()


class ReleaseGateTests(_Clock, unittest.TestCase):
    def gate(self, times, installed, latest, pinnable=True, npm_latest=None):
        found = ({v: w for v, w in times.items()}, npm_latest or latest)
        with patch.object(au, "npm_release_times", return_value=found) as view:
            got = au.release_gate("pkg", installed, latest, pinnable=pinnable)
        self.assertEqual(view.call_args.args, ("pkg",))
        return got

    def test_the_newest_release_old_enough_is_the_target(self):
        # Claude Code ships about one a day, so waiting for the NEWEST to be
        # three days old would mean never updating.
        g = self.gate({"2.1.300": ago(9), "2.1.303": ago(6), "2.1.305": ago(4), "2.1.308": ago(2),
                       "2.1.310": ago(0, 4)}, "2.1.300", "2.1.310")
        self.assertEqual(g.target, "2.1.305")

    def test_the_hour_for_the_pieces_is_part_of_the_wait(self):
        # Codex 0.164.0's darwin-arm64 build reached npm 5.5 minutes after
        # its main package, and npm installs without a piece it may not
        # take. So three days is not enough on its own.
        for age, target in ((timedelta(days=3, minutes=30), ""),
                            (timedelta(days=3, minutes=59), ""),
                            (timedelta(days=3, minutes=61), "0.164.0")):
            with self.subTest(age=age):
                g = self.gate({"0.163.0": ago(9), "0.164.0": NOW - age}, "0.163.0", "0.164.0")
                self.assertEqual(g.target, target)

    def test_when_nothing_is_old_enough_the_next_in_line_is_named(self):
        # The oldest release above the installed one installs first; naming
        # the newest would put a new line in the report every night.
        g = self.gate({"2.1.300": ago(9), "2.1.301": ago(2), "2.1.302": ago(1)}, "2.1.300", "2.1.302")
        self.assertEqual((g.target, g.next), ("", "2.1.301"))
        self.assertEqual(g.published, ago(2))
        self.assertEqual(g.eligible, ago(2) + timedelta(days=3, hours=1))

    def test_a_latest_npm_has_not_listed_yet_is_simply_new(self):
        # Claude's release channel can lead npm by minutes.
        g = self.gate({"2.1.300": ago(9), "2.1.305": ago(4)}, "2.1.300", "2.1.310", npm_latest="2.1.305")
        self.assertEqual(g.target, "2.1.305")

    def test_nothing_above_latest_is_ever_a_target(self):
        g = self.gate({"1.0.0": ago(30), "1.1.0": ago(20), "1.2.0": ago(10)}, "1.0.0", "1.1.0",
                      npm_latest="1.2.0")
        self.assertEqual(g.target, "1.1.0")

    def test_an_installer_that_takes_only_the_newest_waits_for_the_newest(self):
        # The Codex cask: brew installs the version it lists, nothing older.
        g = self.gate({"0.163.0": ago(8), "0.164.0": ago(1)}, "0.162.0", "0.164.0", pinnable=False)
        self.assertEqual((g.target, g.next), ("", "0.164.0"))
        g = self.gate({"0.163.0": ago(8), "0.164.0": ago(5)}, "0.162.0", "0.164.0", pinnable=False)
        self.assertEqual(g.target, "0.164.0")

    def test_the_newest_missing_from_npm_cannot_be_dated(self):
        # Not taken as new: if npm never listed it, it would wait for ever
        # without a word. Undated, it is reported weekly as waiting on a person.
        for npm_latest in ("0.163.0", "0.165.0"):
            with self.subTest(npm_latest=npm_latest):
                self.assertIsNone(self.gate({"0.163.0": ago(8), "0.165.0": ago(4)}, "0.162.0", "0.164.0",
                                            pinnable=False, npm_latest=npm_latest))

    def test_no_dates_is_none(self):
        with patch.object(au, "npm_release_times", return_value=None):
            self.assertIsNone(au.release_gate("pkg", "1.0.0", "1.0.1"))


class _Installs:
    """Records every install and trial, so a test can say none ran."""

    def setUp(self):
        super().setUp()
        self.calls = []
        for name in ("_run", "_npm_install_live", "_trial_install"):
            p = patch.object(au, name, side_effect=self._record(name))
            p.start()
            self.addCleanup(p.stop)

    def _record(self, name):
        def fake(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "_trial_install":
                return True, ""
            return 0, ""
        return fake

    def installs(self):
        return [c for c in self.calls if c[0] in ("_npm_install_live", "_trial_install")
                or (c[0] == "_run" and c[1][0][:2] in (["claude", "install"], ["claude", "update"],
                                                       ["brew", "upgrade"]))]


class ClaudeCodeAgeTests(_Clock, _Installs, unittest.TestCase):
    def check(self, source, times, installed="2.1.300", latest="2.1.310", after=None):
        versions = [installed, after or "2.1.305"]
        with patch.object(au, "_claude_installed_version", side_effect=lambda: versions.pop(0)), \
                patch.object(au, "_claude_latest_version", return_value=latest), \
                patch.object(au, "_claude_source", return_value=source), \
                patch.object(au, "npm_release_times", return_value=(times, latest)):
            return au.check_claude_code(auto_update=True)

    TIMES = {"2.1.300": ago(9), "2.1.305": ago(4), "2.1.310": ago(0, 3)}

    def test_a_native_install_takes_the_newest_old_enough_release_exactly(self):
        (s,) = self.check("native", self.TIMES)
        self.assertEqual((s.state, s.installed, s.latest), ("updated", "2.1.305", "2.1.305"))
        self.assertEqual(self.installs()[0][1][0], ["claude", "install", "2.1.305"])
        self.assertIn("2.1.310", s.detail)

    def test_an_npm_install_is_tried_first_then_pinned_with_its_dependencies_held(self):
        (s,) = self.check("npm", self.TIMES)
        self.assertEqual(s.state, "updated")
        (trial, live) = self.installs()
        self.assertEqual(trial[0], "_trial_install")
        self.assertEqual(trial[1][:2], ("@anthropic-ai/claude-code", "2.1.305"))
        self.assertEqual(trial[2]["before"], "2026-10-21T01:00:00Z")
        self.assertEqual(live[1], ("@anthropic-ai/claude-code@2.1.305",))
        self.assertEqual(live[2]["before"], "2026-10-21T01:00:00Z")

    def test_a_failed_npm_trial_leaves_the_live_one(self):
        with patch.object(au, "_trial_install", return_value=(False, "it reports nothing")):
            (s,) = self.check("npm", self.TIMES)
        self.assertEqual((s.state, s.installed), ("failed", "2.1.300"))
        self.assertIn("2.1.300 was kept", s.detail)
        self.assertEqual([c for c in self.calls if c[0] == "_npm_install_live"], [])

    def test_an_install_it_cannot_pin_waits_for_the_newest(self):
        # `claude update` takes the channel's newest, so that one has to be
        # old enough itself.
        (s,) = self.check("", self.TIMES)
        self.assertEqual((s.state, s.latest), ("waiting", "2.1.310"))
        self.assertEqual(self.installs(), [])
        (s,) = self.check("", {"2.1.300": ago(9), "2.1.310": ago(5)}, after="2.1.310")
        self.assertEqual(s.state, "updated")
        self.assertEqual(self.installs()[0][1][0], ["claude", "update"])

    def test_nothing_old_enough_waits_and_says_until_when(self):
        (s,) = self.check("native", {"2.1.300": ago(9), "2.1.308": ago(2), "2.1.310": ago(0, 3)})
        self.assertEqual((s.state, s.installed, s.latest), ("waiting", "2.1.300", "2.1.308"))
        self.assertIn("out ", s.detail)
        self.assertIn("installs after", s.detail)
        self.assertEqual(self.installs(), [])

    def test_unreadable_dates_never_install(self):
        with patch.object(au, "npm_release_times", return_value=None), \
                patch.object(au, "_claude_installed_version", return_value="2.1.300"), \
                patch.object(au, "_claude_latest_version", return_value="2.1.310"), \
                patch.object(au, "_claude_source", return_value="native"):
            (s,) = au.check_claude_code(auto_update=True)
        self.assertEqual(s.state, "outdated")
        self.assertIn("could not read when", s.detail)
        self.assertEqual(self.installs(), [])

    def test_a_native_install_that_landed_elsewhere_is_a_failure(self):
        (s,) = self.check("native", self.TIMES, after="2.1.310")
        self.assertEqual(s.state, "failed")
        self.assertIn("2.1.310", s.detail)

    def test_a_version_that_is_not_plain_never_reaches_the_command_line(self):
        # `claude install` also reads words ("stable", "latest"), and with the
        # rule off the channel's text would go straight to it.
        with patch.object(au, "MIN_RELEASE_AGE_DAYS", 0), \
                patch.object(au, "_claude_installed_version", return_value="2.1.300"), \
                patch.object(au, "_claude_latest_version", return_value="2.1.310-rc1"), \
                patch.object(au, "_claude_source", return_value="native"):
            (s,) = au.check_claude_code(auto_update=True)
        self.assertEqual(s.state, "failed")
        self.assertIn("not a plain version", s.detail)
        self.assertEqual(self.installs(), [])

    def test_report_only_and_major_moves_never_ask_npm(self):
        with patch.object(au, "npm_release_times") as view, \
                patch.object(au, "_claude_installed_version", return_value="2.1.300"), \
                patch.object(au, "_claude_latest_version", return_value="3.0.0"):
            (major,) = au.check_claude_code(auto_update=True)
            (report,) = au.check_claude_code(auto_update=False)
        self.assertEqual((major.state, report.state), ("outdated", "outdated"))
        view.assert_not_called()


class ClaudeSourceTests(unittest.TestCase):
    def test_the_native_layout(self):
        with patch("utils.app_updates.shutil.which", return_value="/home/u/.local/bin/claude"), \
                patch("utils.app_updates.os.path.realpath",
                      side_effect=lambda p: "/home/u/.local/share/claude/versions/2.1.310"
                      if str(p).endswith("bin/claude") else str(p)):
            self.assertEqual(au._claude_source(), "native")

    def test_the_npm_layout(self):
        root = Path("/usr/lib/node_modules")
        real = "/usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        with patch("utils.app_updates.shutil.which", return_value="/usr/bin/claude"), \
                patch.object(au.puppeteer_browsers, "npm_global_root", return_value=root), \
                patch("utils.app_updates.os.path.realpath",
                      side_effect=lambda p: real if str(p) == "/usr/bin/claude" else str(p)):
            self.assertEqual(au._claude_source(), "npm")

    def test_anything_else(self):
        for real in ("/opt/homebrew/Caskroom/claude-code/2.1.310/claude",
                     "/home/u/versions/2.1.310", "/usr/local/bin/claude"):
            with self.subTest(real=real), \
                    patch("utils.app_updates.shutil.which", return_value="/usr/local/bin/claude"), \
                    patch.object(au.puppeteer_browsers, "npm_global_root",
                                 return_value=Path("/usr/lib/node_modules")), \
                    patch("utils.app_updates.os.path.realpath",
                          side_effect=lambda p, r=real: r if str(p) == "/usr/local/bin/claude" else str(p)):
                self.assertEqual(au._claude_source(), "")
        with patch("utils.app_updates.shutil.which", return_value=None):
            self.assertEqual(au._claude_source(), "")


class ClaudeBuildWorksTests(unittest.TestCase):
    """The trial check for an npm-built Claude Code, on stand-in binaries."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for owner, name, value in ((au, "_run", REAL_RUN), (au.subprocess, "run", REAL_SUBPROCESS_RUN)):
            p = patch.object(owner, name, value)
            p.start()
            self.addCleanup(p.stop)

    def binary(self, body):
        path = Path(self.tmp.name) / "claude"
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        os.chmod(path, 0o755)
        return path

    def test_the_version_meant(self):
        self.assertEqual(au._claude_build_works(self.binary('echo "2.1.305 (Claude Code)"'), "2.1.305"),
                         (True, ""))

    def test_another_version_is_refused(self):
        ok, why = au._claude_build_works(self.binary('echo "2.1.305 (Claude Code)"'), "2.1.306")
        self.assertFalse(ok)
        self.assertIn("reports 2.1.305, not 2.1.306", why)

    def test_a_build_missing_its_platform_piece_is_refused(self):
        # What npm leaves behind when it skipped the piece: a stub that fails.
        ok, why = au._claude_build_works(
            self.binary('echo "claude native binary not installed" >&2; exit 1'), "2.1.305")
        self.assertFalse(ok)
        self.assertIn("reports nothing", why)
        self.assertIn("rc=1", why)


class CodexAgeTests(_Clock, unittest.TestCase):
    """The age rule inside check_codex_cli, with every other step faked."""

    def check(self, source, times, installed="0.162.0", latest="0.164.0"):
        calls = []
        on_disk = {"v": installed}
        npm_latest = max(times, key=au.version_tuple)

        def version(binary, env=None):
            return on_disk["v"]

        def record(name, result):
            def fake(*args, **kwargs):
                calls.append((name, args, kwargs))
                if name == "npm-live":
                    on_disk["v"] = args[0].rsplit("@", 1)[1]
                if name == "run" and args[0][:2] == ["brew", "upgrade"]:
                    on_disk["v"] = latest
                return result
            return fake

        with patch("utils.app_updates.shutil.which", return_value="/opt/homebrew/bin/codex"), \
                patch.object(au, "_codex_version", side_effect=version), \
                patch.object(au, "_codex_source", return_value=source), \
                patch.object(au, "_codex_latest", return_value=latest), \
                patch.object(au, "_codex_running", return_value=False), \
                patch.object(au, "npm_release_times", return_value=(times, npm_latest)), \
                patch.object(au, "_codex_cask_trial", side_effect=record("cask-trial", (True, ""))), \
                patch.object(au, "_trial_install", side_effect=record("npm-trial", (True, ""))), \
                patch.object(au, "_codex_takes_the_bot", side_effect=record("live-check", (True, ""))), \
                patch.object(au, "_npm_install_live", side_effect=record("npm-live", (0, ""))), \
                patch.object(au, "_run", side_effect=record("run", (0, ""))):
            statuses = au.check_codex_cli(auto_update=True)
        return statuses, calls

    def test_npm_takes_the_newest_old_enough_release_with_its_dependencies_held(self):
        (s,), calls = self.check("npm", {"0.162.0": ago(20), "0.163.0": ago(8), "0.164.0": ago(1)})
        self.assertEqual((s.state, s.installed), ("updated", "0.163.0"))
        trial = next(c for c in calls if c[0] == "npm-trial")
        self.assertEqual(trial[1][:2], ("@openai/codex", "0.163.0"))
        self.assertEqual(trial[2]["before"], "2026-10-21T01:00:00Z")
        live = next(c for c in calls if c[0] == "npm-live")
        self.assertEqual(live[1], ("@openai/codex@0.163.0",))
        self.assertEqual(live[2]["before"], "2026-10-21T01:00:00Z")

    def test_the_cask_waits_until_its_own_release_is_old_enough(self):
        (s,), calls = self.check("cask", {"0.163.0": ago(8), "0.164.0": ago(1)})
        self.assertEqual((s.state, s.latest), ("waiting", "0.164.0"))
        self.assertEqual(calls, [], "something was fetched or installed for a release too new")

    def test_an_old_enough_cask_goes_through_its_trial(self):
        (s,), calls = self.check("cask", {"0.163.0": ago(8), "0.164.0": ago(4)})
        self.assertEqual(s.state, "updated")
        self.assertEqual([c[0] for c in calls], ["cask-trial", "run", "live-check"])

    def test_nothing_old_enough_on_npm_waits(self):
        (s,), calls = self.check("npm", {"0.162.0": ago(20), "0.163.0": ago(2), "0.164.0": ago(1)})
        self.assertEqual((s.state, s.latest), ("waiting", "0.163.0"))
        self.assertEqual(calls, [])

    def test_unreadable_dates_never_install(self):
        with patch.object(au, "npm_release_times", return_value=None):
            (s,), calls = self._unreadable()
        self.assertEqual(s.state, "outdated")
        self.assertIn("could not read when", s.detail)
        self.assertEqual(calls, [])

    def _unreadable(self):
        calls = []
        with patch("utils.app_updates.shutil.which", return_value="/opt/homebrew/bin/codex"), \
                patch.object(au, "_codex_version", return_value="0.162.0"), \
                patch.object(au, "_codex_source", return_value="cask"), \
                patch.object(au, "_codex_latest", return_value="0.164.0"), \
                patch.object(au, "_codex_running", return_value=False), \
                patch.object(au, "_codex_cask_trial", side_effect=lambda v: calls.append(v)), \
                patch.object(au, "_run", side_effect=lambda *a, **k: calls.append(a)):
            return au.check_codex_cli(auto_update=True), calls


class NpmCliAgeTests(_Clock, unittest.TestCase):
    def check(self, pkg, current, latest, times, proof=None):
        calls = []

        def live(spec, before=None):
            calls.append(("live", spec, before))
            return 0, ""

        def trial(p, version, proof_fn, before=None):
            calls.append(("trial", p, version, before))
            return True, ""

        with patch.object(au, "_npm_outdated_global",
                          return_value={pkg: {"current": current, "latest": latest}}), \
                patch.object(au, "npm_release_times",
                             return_value=(times, max(times, key=au.version_tuple))), \
                patch.object(au, "_npm_install_live", side_effect=live), \
                patch.object(au, "_trial_install", side_effect=trial), \
                patch.dict(au.NPM_PROOF, {pkg: proof} if proof else {}):
            statuses = au.check_npm_clis(auto_update=True)
        return statuses, calls

    def test_the_newest_old_enough_release_installs_pinned(self):
        (s,), calls = self.check("surge", "0.46.0", "0.46.2",
                                 {"0.46.0": ago(9), "0.46.1": ago(5), "0.46.2": ago(1)})
        self.assertEqual((s.state, s.installed, s.latest), ("updated", "0.46.1", "0.46.1"))
        self.assertEqual(calls, [("live", "surge@0.46.1", "2026-10-21T01:00:00Z")])

    def test_a_cli_with_a_proof_is_tried_with_the_same_hold(self):
        pkg = "@mermaid-js/mermaid-cli"
        (s,), calls = self.check(pkg, "12.0.0", "12.0.1", {"12.0.0": ago(30), "12.0.1": ago(4)},
                                 proof=lambda prefix: (True, ""))
        self.assertEqual(s.state, "updated")
        self.assertEqual(calls, [("trial", pkg, "12.0.1", "2026-10-21T01:00:00Z"),
                                 ("live", f"{pkg}@12.0.1", "2026-10-21T01:00:00Z")])

    def test_too_new_waits(self):
        (s,), calls = self.check("surge", "0.46.0", "0.46.2", {"0.46.0": ago(9), "0.46.2": ago(1)})
        self.assertEqual((s.state, s.latest), ("waiting", "0.46.2"))
        self.assertEqual(calls, [])

    def test_unreadable_dates_never_install(self):
        with patch.object(au, "_npm_outdated_global",
                          return_value={"surge": {"current": "0.46.0", "latest": "0.46.2"}}), \
                patch.object(au, "npm_release_times", return_value=None), \
                patch.object(au, "_npm_install_live") as live:
            (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "outdated")
        self.assertIn("could not read when", s.detail)
        live.assert_not_called()

    def test_a_hand_installed_package_is_never_dated(self):
        # Reported, never moved: no reason to ask npm anything.
        with patch.object(au, "_npm_outdated_global",
                          return_value={"cowsay": {"current": "1.0.0", "latest": "1.0.1"}}), \
                patch.object(au, "npm_release_times") as view:
            (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "outdated")
        view.assert_not_called()


class InstallArgvTests(unittest.TestCase):
    def test_the_live_install_carries_the_hold(self):
        with patch.object(au, "_npm_global_needs_sudo", return_value=False), \
                patch.object(au, "_run", return_value=(0, "")) as run:
            REAL_LIVE("surge@0.46.1", before="2026-10-21T01:00:00Z")
        self.assertEqual(run.call_args.args[0],
                         ["npm", "install", "-g", "--before=2026-10-21T01:00:00Z", "surge@0.46.1"])

    def test_under_sudo_too(self):
        import subprocess
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(au, "_npm_global_needs_sudo", return_value=True), \
                patch("install.sudo.get_sudo_password", return_value="pw"), \
                patch("utils.app_updates.subprocess.run", return_value=done) as sub:
            REAL_LIVE("surge@0.46.1", before="2026-10-21T01:00:00Z")
        self.assertEqual(sub.call_args.args[0][-3:],
                         ["-g", "--before=2026-10-21T01:00:00Z", "surge@0.46.1"])

    def test_the_trial_install_carries_it(self):
        with patch.object(au, "_run", return_value=(1, "ETARGET")) as run:
            ok, why = REAL_TRIAL("surge", "0.46.1", lambda p: (True, ""),
                                 before="2026-10-21T01:00:00Z")
        self.assertFalse(ok)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:4], ["npm", "install", "-g", "--prefix"])
        self.assertEqual(cmd[-2:], ["--before=2026-10-21T01:00:00Z", "surge@0.46.1"])

    def test_without_a_hold_the_commands_are_as_before(self):
        with patch.object(au, "_npm_global_needs_sudo", return_value=False), \
                patch.object(au, "_run", return_value=(0, "")) as run:
            REAL_LIVE("surge@0.46.1")
        self.assertEqual(run.call_args.args[0], ["npm", "install", "-g", "surge@0.46.1"])


class ReportTests(unittest.TestCase):
    WAIT = au.AppStatus("Codex CLI", "codex", "0.163.0", "0.164.0", "waiting",
                        "out 23 Oct, installs after 26 Oct 08:52")

    def test_a_wait_is_its_own_line_not_waiting_on_you(self):
        text = au.summarize([self.WAIT])
        self.assertIn("Waiting out their first 3 days", text)
        self.assertIn("Codex CLI 0.164.0 (out 23 Oct, installs after 26 Oct 08:52)", text)
        self.assertIn("sooner", text)
        self.assertNotIn("waiting on you", text)

    def test_a_wait_is_not_news_but_is_listed_for_the_reminder(self):
        with patch.object(au, "collect", return_value=[self.WAIT]):
            result = au.run_app_update_check(auto_update=True)
        self.assertFalse(result.news)
        self.assertEqual(result.waiting, ())
        self.assertEqual(result.aging, ("Codex CLI 0.164.0",))

    def test_dates_are_said_in_local_time(self):
        import time
        when = datetime(2026, 10, 26, 5, 52, tzinfo=timezone.utc)
        self.addCleanup(time.tzset)
        with patch.dict("os.environ", {"TZ": "Europe/Athens"}):
            time.tzset()
            # Summer time ends on 25 Oct 2026, so Athens is two hours ahead.
            self.assertEqual(au._when(when), "26 Oct 07:52")
            self.assertEqual(au._day(when), "26 Oct")


if __name__ == "__main__":
    unittest.main()
