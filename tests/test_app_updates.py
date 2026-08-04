"""Unit tests for utils.app_updates.

The module exists because nothing on the machine had ever looked at the apps
the package manager does not track. These tests hold the two properties that
make it safe to run unattended at 4am:

  - a check that cannot reach its version source reports "unknown", never
    "current". Silence that looks like good news is the failure being fixed.
  - nothing installs unless it is explicitly allowed to. GUI applications
    never install, and no package crosses a major version boundary on its own.

Every subprocess call and every HTTP fetch is mocked. Nothing here touches the
network, npm, or the Claude CLI.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import app_updates as au  # noqa: E402


class VersionOrderingTests(unittest.TestCase):
    def test_reads_numbers_out_of_decorated_strings(self):
        # What `claude --version` actually prints.
        self.assertEqual(au.version_tuple("2.1.221 (Claude Code)"), (2, 1, 221))
        self.assertEqual(au.version_tuple("v21.0.3-build4"), (21, 0, 3, 4))
        self.assertEqual(au.version_tuple(""), ())
        self.assertEqual(au.version_tuple("unknown"), ())

    def test_orders_by_number_not_by_string(self):
        # The string comparison every naive version check gets wrong.
        self.assertTrue(au.is_newer("2.1.221", "2.1.99"))
        self.assertTrue(au.is_newer("11.16.0", "11.14.0"))
        self.assertFalse(au.is_newer("21.0.2", "21.0.3"))

    def test_equal_versions_are_not_newer(self):
        self.assertFalse(au.is_newer("21.0.3", "21.0.3"))

    def test_pads_uneven_lengths(self):
        self.assertFalse(au.is_newer("21.0", "21.0.3"))
        self.assertTrue(au.is_newer("21.0.3", "21.0"))

    def test_unreadable_version_is_never_an_update(self):
        # An app whose version could not be read must not be reported as
        # having one available — that is a nightly nag nobody can clear.
        self.assertFalse(au.is_newer("", "21.0.3"))
        self.assertFalse(au.is_newer("21.0.3", ""))
        self.assertFalse(au.is_newer("stable", "21.0.3"))


class MajorJumpTests(unittest.TestCase):
    """The line between installing unattended and asking a human."""

    def test_patch_and_minor_are_safe(self):
        self.assertFalse(au.is_major_jump("13.3.0", "13.4.1"))
        self.assertFalse(au.is_major_jump("11.14.0", "11.16.0"))
        self.assertFalse(au.is_major_jump("2.1.217", "2.1.221"))

    def test_major_bump_is_held_back(self):
        self.assertTrue(au.is_major_jump("1.2.3", "2.0.0"))
        # Real case, 2026-08-04: image-gen spends money through this CLI.
        self.assertTrue(au.is_major_jump("0.1.40", "1.1.20"))

    def test_zero_x_minor_counts_as_breaking(self):
        # Pre-1.0 packages put their breaking changes in the minor slot.
        # Real case, 2026-08-04: presentations are delivered with surge.
        self.assertTrue(au.is_major_jump("0.27.3", "0.41.2"))
        self.assertFalse(au.is_major_jump("0.27.3", "0.27.9"))

    def test_unreadable_versions_are_held_back(self):
        self.assertTrue(au.is_major_jump("", "1.0.0"))
        self.assertTrue(au.is_major_jump("1.0.0", ""))


class ClaudeCodeTests(unittest.TestCase):
    @patch("utils.app_updates.shutil.which", return_value=None)
    def test_not_installed_reports_nothing(self, _which):
        self.assertEqual(au.check_claude_code(), [])

    @patch("utils.app_updates._claude_latest_version", return_value="2.1.221")
    @patch("utils.app_updates._claude_installed_version", return_value="2.1.221")
    def test_current(self, _inst, _latest):
        (s,) = au.check_claude_code()
        self.assertEqual(s.state, "current")

    @patch("utils.app_updates._claude_latest_version", return_value="2.1.221")
    @patch("utils.app_updates._claude_installed_version", return_value="2.1.217")
    def test_outdated_without_auto_update_installs_nothing(self, _inst, _latest):
        with patch("utils.app_updates._run") as run:
            (s,) = au.check_claude_code(auto_update=False)
        self.assertEqual(s.state, "outdated")
        run.assert_not_called()

    @patch("utils.app_updates._claude_latest_version", return_value="2.1.221")
    def test_auto_update_installs_and_reports_the_new_version(self, _latest):
        versions = iter(["2.1.217", "2.1.221"])  # before, then after
        with patch("utils.app_updates._claude_installed_version",
                   side_effect=lambda: next(versions)), \
             patch("utils.app_updates._run", return_value=(0, "Successfully updated")) as run:
            (s,) = au.check_claude_code(auto_update=True)
        self.assertEqual(s.state, "updated")
        self.assertEqual(s.installed, "2.1.221")
        self.assertEqual(run.call_args.args[0], ["claude", "update"])

    @patch("utils.app_updates._claude_latest_version", return_value="2.1.221")
    @patch("utils.app_updates._claude_installed_version", return_value="2.1.217")
    @patch("utils.app_updates._run", return_value=(1, "network unreachable"))
    def test_failed_update_is_reported_not_swallowed(self, _run, _inst, _latest):
        (s,) = au.check_claude_code(auto_update=True)
        self.assertEqual(s.state, "failed")
        self.assertIn("network unreachable", s.detail)

    @patch("utils.app_updates._claude_latest_version", return_value="2.1.221")
    @patch("utils.app_updates._claude_installed_version", return_value="2.1.217")
    @patch("utils.app_updates._run", return_value=(0, "no change"))
    def test_update_that_did_not_move_the_version_is_a_failure(self, _run, _i, _l):
        # Exit code 0 is not proof: the check is whether the version moved.
        (s,) = au.check_claude_code(auto_update=True)
        self.assertEqual(s.state, "failed")

    @patch("utils.app_updates._claude_latest_version", return_value="3.0.0")
    @patch("utils.app_updates._claude_installed_version", return_value="2.1.221")
    def test_major_version_is_not_installed_unattended(self, _inst, _latest):
        with patch("utils.app_updates._run") as run:
            (s,) = au.check_claude_code(auto_update=True)
        self.assertEqual(s.state, "outdated")
        run.assert_not_called()

    @patch("utils.app_updates._claude_latest_version", return_value="")
    @patch("utils.app_updates._claude_installed_version", return_value="2.1.221")
    def test_unreachable_channel_is_unknown_not_current(self, _inst, _latest):
        (s,) = au.check_claude_code()
        self.assertEqual(s.state, "unknown")
        self.assertFalse(s.needs_attention)

    @patch("utils.app_updates.shutil.which", return_value="/usr/local/bin/claude")
    @patch("utils.app_updates._run", return_value=(0, "2.1.221 (Claude Code)"))
    def test_version_is_parsed_out_of_the_cli_banner(self, _run, _which):
        self.assertEqual(au._claude_installed_version(), "2.1.221")

    @patch("utils.app_updates._fetch")
    def test_npm_registry_is_the_fallback_when_the_channel_is_down(self, fetch):
        fetch.side_effect = ["", json.dumps({"version": "2.1.221"})]
        self.assertEqual(au._claude_latest_version(), "2.1.221")

    @patch("utils.app_updates._fetch", return_value="<!DOCTYPE html><html>...")
    def test_an_html_error_page_is_not_a_version(self, _fetch):
        # A captive portal or proxy answering the channel URL with a page must
        # not be read as a version string.
        self.assertEqual(au._claude_latest_version(), "")


class DaVinciResolveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _bundle(self, version: str) -> str:
        app = Path(self.tmp.name) / "DaVinci Resolve.app"
        (app / "Contents").mkdir(parents=True)
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)
        return str(app)

    @patch("utils.app_updates.platform.system", return_value="Darwin")
    def test_reads_the_version_out_of_the_app_bundle(self, _plat):
        with patch.object(au, "RESOLVE_BUNDLES", (self._bundle("21.0.3"),)):
            self.assertEqual(au._resolve_installed_version(), "21.0.3")

    @patch("utils.app_updates.platform.system", return_value="Darwin")
    def test_missing_bundle_reports_nothing(self, _plat):
        with patch.object(au, "RESOLVE_BUNDLES", ("/nowhere/Resolve.app",)):
            self.assertEqual(au._resolve_installed_version(), "")

    @patch("utils.app_updates._resolve_installed_version", return_value="")
    def test_not_installed_reports_nothing(self, _inst):
        self.assertEqual(au.check_davinci_resolve(), [])

    @patch("utils.app_updates._resolve_latest_version", return_value="21.0.3")
    @patch("utils.app_updates._resolve_installed_version", return_value="21.0.2")
    def test_never_installs_even_when_told_to(self, _inst, _latest):
        # The download sits behind a registration form, the installer wants
        # admin rights and a GUI, and replacing the app under a colourist with
        # a project open loses their work. auto_update must not reach it.
        with patch("utils.app_updates._run") as run:
            (s,) = au.check_davinci_resolve(auto_update=True)
        self.assertEqual(s.state, "outdated")
        run.assert_not_called()

    @patch("utils.app_updates._resolve_latest_version", return_value="")
    @patch("utils.app_updates._resolve_installed_version", return_value="21.0.3")
    def test_unreachable_feed_is_unknown_not_current(self, _inst, _latest):
        (s,) = au.check_davinci_resolve()
        self.assertEqual(s.state, "unknown")

    @patch("utils.app_updates.platform.system", return_value="Darwin")
    @patch("utils.app_updates._fetch")
    def test_parses_blackmagics_feed_for_this_platform(self, fetch, _plat):
        # Shape captured from the live feed, 2026-08-04. Newest first, and the
        # Studio edition carries its own version numbering.
        fetch.return_value = json.dumps({"downloads": [
            {"name": "DaVinci Resolve Studio 21.0.3 Update",
             "urls": {"Mac OS X": [{"product": "davinci-resolve-studio",
                                    "major": 99, "minor": 9, "releaseNum": 9}]}},
            {"name": "DaVinci Resolve 21.0.3 Update",
             "urls": {"Mac OS X": [{"product": "davinci-resolve",
                                    "major": 21, "minor": 0, "releaseNum": 3}],
                      "Linux": [{"product": "davinci-resolve",
                                 "major": 21, "minor": 0, "releaseNum": 3}]}},
            {"name": "DaVinci Resolve 21.0.2 Update",
             "urls": {"Mac OS X": [{"product": "davinci-resolve",
                                    "major": 21, "minor": 0, "releaseNum": 2}]}},
        ]})
        self.assertEqual(au._resolve_latest_version(), "21.0.3")

    @patch("utils.app_updates.platform.system", return_value="Darwin")
    @patch("utils.app_updates._fetch", return_value="not json at all")
    def test_garbage_feed_reports_nothing(self, _fetch, _plat):
        self.assertEqual(au._resolve_latest_version(), "")


class NpmCliTests(unittest.TestCase):
    def _outdated(self, payload: dict):
        return patch("utils.app_updates._npm_outdated_global", return_value=payload)

    def test_npm_itself_is_never_reported_or_touched(self):
        # Node comes from Homebrew and Homebrew owns npm's files inside its
        # prefix. `npm install -g npm` overwrites them and the next
        # `brew upgrade node` then collides with files brew did not place.
        with self._outdated({"npm": {"current": "11.18.0", "latest": "12.0.2"}}):
            with patch("utils.app_updates._run") as run:
                self.assertEqual(au.check_npm_clis(auto_update=True), [])
            run.assert_not_called()

    def test_safe_bump_of_a_skill_cli_installs(self):
        with self._outdated({"lighthouse": {"current": "13.3.0", "latest": "13.4.1"}}):
            with patch("utils.app_updates._run", return_value=(0, "")) as run:
                (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "updated")
        self.assertEqual(s.installed, "13.4.1")
        self.assertEqual(run.call_args.args[0],
                         ["npm", "install", "-g", "lighthouse@13.4.1"])

    def test_major_bump_of_a_skill_cli_is_reported_not_installed(self):
        with self._outdated({"@higgsfield/cli": {"current": "0.1.40", "latest": "1.1.20"}}):
            with patch("utils.app_updates._run") as run:
                (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "outdated")
        self.assertIn("major version", s.detail)
        run.assert_not_called()

    def test_a_package_no_skill_installed_is_reported_not_touched(self):
        with self._outdated({"cowsay": {"current": "1.0.0", "latest": "1.0.1"}}):
            with patch("utils.app_updates._run") as run:
                (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "outdated")
        self.assertEqual(s.detail, "installed by hand")
        run.assert_not_called()

    def test_failed_install_is_reported(self):
        with self._outdated({"lighthouse": {"current": "13.3.0", "latest": "13.4.1"}}):
            with patch("utils.app_updates._run", return_value=(1, "EACCES denied")):
                (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "failed")
        self.assertIn("EACCES", s.detail)

    def test_report_only_mode_installs_nothing(self):
        with self._outdated({"lighthouse": {"current": "13.3.0", "latest": "13.4.1"}}):
            with patch("utils.app_updates._run") as run:
                (s,) = au.check_npm_clis(auto_update=False)
        self.assertEqual(s.state, "outdated")
        run.assert_not_called()

    def test_entry_already_current_is_dropped(self):
        with self._outdated({"surge": {"current": "0.41.2", "latest": "0.41.2"}}):
            self.assertEqual(au.check_npm_clis(), [])

    def test_malformed_entry_is_skipped(self):
        with self._outdated({"broken": "not a dict", "other": {"current": "1.0.0"}}):
            self.assertEqual(au.check_npm_clis(), [])

    @patch("utils.app_updates.shutil.which", return_value="/opt/homebrew/bin/npm")
    @patch("utils.app_updates._run")
    def test_npm_exit_code_one_still_yields_its_json(self, run, _which):
        # npm exits 1 precisely when something is outdated, which is the case
        # this check exists for, so the return code cannot gate the parse.
        run.return_value = (1, json.dumps({"surge": {"current": "0.27.3",
                                                     "latest": "0.41.2"}}))
        self.assertIn("surge", au._npm_outdated_global())

    @patch("utils.app_updates.shutil.which", return_value=None)
    def test_no_npm_on_the_box_is_not_an_error(self, _which):
        self.assertEqual(au._npm_outdated_global(), {})

    @patch("utils.app_updates.shutil.which", return_value="/opt/homebrew/bin/npm")
    @patch("utils.app_updates._run", return_value=(0, "npm WARN config chatter"))
    def test_non_json_chatter_is_not_parsed_as_packages(self, _run, _which):
        self.assertEqual(au._npm_outdated_global(), {})

    # -- npm keeps its warnings on stderr and its JSON on stdout ------------
    # Patched at subprocess.run rather than at _run, because the bug being
    # held here lives in how _run itself joins the two streams. Reproduced on
    # a Linux box with npm 10.8.2 (2026-08-04): a single `npm warn config`
    # line turned a correct report of two stale CLIs into an empty one.

    @staticmethod
    def _completed(stdout: str, stderr: str, rc: int = 1):
        return subprocess.CompletedProcess(
            args=["npm"], returncode=rc, stdout=stdout, stderr=stderr,
        )

    @patch("utils.app_updates.shutil.which", return_value="/usr/bin/npm")
    @patch("utils.app_updates.subprocess.run")
    def test_a_warning_on_stderr_does_not_erase_the_report(self, run, _which):
        run.return_value = self._completed(
            stdout=json.dumps({"surge": {"current": "0.27.3", "latest": "0.41.2"}}),
            stderr="npm warn config production Use `--omit=dev` instead.\n",
        )
        self.assertIn("surge", au._npm_outdated_global())

    @patch("utils.app_updates.shutil.which", return_value="/usr/bin/npm")
    @patch("utils.app_updates.subprocess.run")
    def test_a_warning_on_stderr_still_reaches_check_npm_clis(self, run, _which):
        run.return_value = self._completed(
            stdout=json.dumps({"surge": {"current": "0.27.3", "latest": "0.41.2"}}),
            stderr="npm warn config production Use `--omit=dev` instead.\n",
        )
        (s,) = au.check_npm_clis(auto_update=False)
        self.assertEqual((s.name, s.installed, s.latest, s.state),
                         ("surge", "0.27.3", "0.41.2", "outdated"))

    @patch("utils.app_updates.shutil.which", return_value="/usr/bin/npm")
    @patch("utils.app_updates.subprocess.run")
    def test_warnings_alone_are_still_not_packages(self, run, _which):
        # The other half of the same seam: dropping stderr must not turn
        # chatter into a report either. Empty stdout stays empty.
        run.return_value = self._completed(
            stdout="", stderr="npm warn config production\n", rc=0,
        )
        self.assertEqual(au._npm_outdated_global(), {})

    @patch("utils.app_updates.subprocess.run")
    def test_merged_streams_remain_the_default(self, run):
        # Everything else in this module reads human-facing output, where a
        # tool's error text on stderr is the useful part of the answer.
        run.return_value = self._completed(stdout="out", stderr="err", rc=0)
        self.assertEqual(au._run(["x"]), (0, "outerr"))
        self.assertEqual(au._run(["x"], merge_stderr=False), (0, "out"))


class FlatpakTests(unittest.TestCase):
    @patch("utils.app_updates.platform.system", return_value="Darwin")
    def test_skipped_off_linux(self, _plat):
        self.assertEqual(au.check_flatpak(), [])

    @patch("utils.app_updates.platform.system", return_value="Linux")
    @patch("utils.app_updates.shutil.which", return_value=None)
    def test_skipped_when_flatpak_is_not_installed(self, _which, _plat):
        self.assertEqual(au.check_flatpak(), [])

    @patch("utils.app_updates.platform.system", return_value="Linux")
    @patch("utils.app_updates.shutil.which", return_value="/usr/bin/flatpak")
    @patch("utils.app_updates._run")
    def test_lists_pending_app_updates(self, run, _which, _plat):
        run.return_value = (0, "org.blender.Blender\t4.2.1\norg.gimp.GIMP\t3.0.4")
        names = [s.name for s in au.check_flatpak()]
        self.assertEqual(names, ["org.blender.Blender", "org.gimp.GIMP"])

    @patch("utils.app_updates.platform.system", return_value="Linux")
    @patch("utils.app_updates.shutil.which", return_value="/usr/bin/flatpak")
    @patch("utils.app_updates._run")
    def test_headers_and_chatter_are_not_apps(self, run, _which, _plat):
        run.return_value = (0, "Application ID\tVersion\norg.blender.Blender\t4.2.1")
        self.assertEqual([s.name for s in au.check_flatpak()], ["org.blender.Blender"])

    @patch("utils.app_updates.platform.system", return_value="Linux")
    @patch("utils.app_updates.shutil.which", return_value="/usr/bin/flatpak")
    @patch("utils.app_updates._run", return_value=(0, "org.blender.Blender\t4.2.1"))
    def test_never_installs(self, run, _which, _plat):
        # A flatpak update pulls whole runtimes and can run to gigabytes.
        au.check_flatpak(auto_update=True)
        self.assertEqual(run.call_count, 1)
        self.assertIn("remote-ls", run.call_args.args[0])


class CollectTests(unittest.TestCase):
    def test_a_broken_check_does_not_take_the_others_down(self):
        def boom(_auto):
            raise RuntimeError("feed exploded")

        good = au.AppStatus("Fine", "test", "1.0", "1.0", "current")
        logged = []
        with patch.object(au, "CHECKS", (("resolve", boom),
                                         ("npm", lambda _a: [good]))):
            self.assertEqual(au.collect(log_fn=logged.append), [good])
        self.assertTrue(any("feed exploded" in line for line in logged))

    def test_auto_update_reaches_cli_families_only(self):
        seen = {}

        def spy(family):
            def check(auto):
                seen[family] = auto
                return []
            return check

        with patch.object(au, "CHECKS", tuple(
            (family, spy(family))
            for family in ("claude-code", "resolve", "npm", "flatpak")
        )):
            au.collect(auto_update=True)
        self.assertTrue(seen["claude-code"])
        self.assertTrue(seen["npm"])
        # Applications are never installed by an unattended job.
        self.assertFalse(seen["resolve"])
        self.assertFalse(seen["flatpak"])

    def test_report_only_mode_reaches_nothing(self):
        seen = {}

        def spy(family):
            def check(auto):
                seen[family] = auto
                return []
            return check

        with patch.object(au, "CHECKS", tuple(
            (family, spy(family)) for family in ("claude-code", "npm")
        )):
            au.collect(auto_update=False)
        self.assertEqual(set(seen.values()), {False})

    def test_every_registered_family_is_a_known_name(self):
        # A typo in a family name would silently disable its install gate.
        families = {family for family, _ in au.CHECKS}
        self.assertTrue(au.AUTO_INSTALLABLE <= families)


class SummaryTests(unittest.TestCase):
    def test_silent_when_everything_is_current(self):
        # A nightly "all fine" from every subsystem trains people to stop
        # reading the digest.
        statuses = [au.AppStatus("Claude Code", "claude-code", "2.1.221",
                                 "2.1.221", "current")]
        self.assertEqual(au.summarize(statuses), "")

    def test_silent_when_a_version_could_not_be_read(self):
        statuses = [au.AppStatus("DaVinci Resolve", "resolve", "21.0.3", "",
                                 "unknown", "feed unreachable")]
        self.assertEqual(au.summarize(statuses), "")

    def test_names_what_moved_and_what_waits(self):
        statuses = [
            au.AppStatus("lighthouse", "npm", "13.4.1", "13.4.1", "updated"),
            au.AppStatus("surge", "npm", "0.27.3", "0.41.2", "outdated"),
            au.AppStatus("@higgsfield/cli", "npm", "0.1.40", "1.1.20", "failed"),
        ]
        out = au.summarize(statuses)
        self.assertIn("lighthouse 13.4.1", out)
        self.assertIn("surge 0.27.3 to 0.41.2", out)
        self.assertIn("failed", out)


class RunAppUpdateCheckTests(unittest.TestCase):
    def _with(self, statuses):
        return patch.object(au, "collect", return_value=statuses)

    def test_nothing_installed_is_an_empty_result(self):
        with self._with([]):
            self.assertEqual(au.run_app_update_check(), au.AppCheckResult())

    def test_an_installed_update_is_news(self):
        with self._with([au.AppStatus("lighthouse", "npm", "13.4.1", "13.4.1",
                                      "updated")]):
            r = au.run_app_update_check()
        self.assertTrue(r.news)
        self.assertEqual(r.waiting, ())

    def test_a_failure_is_news(self):
        with self._with([au.AppStatus("lighthouse", "npm", "13.3.0", "13.4.1",
                                      "failed")]):
            self.assertTrue(au.run_app_update_check().news)

    def test_a_waiting_app_is_not_news(self):
        # Waiting apps persist for weeks; they get throttled by the caller
        # rather than pinged every single night.
        with self._with([au.AppStatus("DaVinci Resolve", "resolve", "21.0.2",
                                      "21.0.3", "outdated")]):
            r = au.run_app_update_check()
        self.assertFalse(r.news)
        self.assertEqual(r.waiting, ("DaVinci Resolve",))

    def test_everything_current_says_nothing(self):
        with self._with([au.AppStatus("Claude Code", "claude-code", "2.1.221",
                                      "2.1.221", "current")]):
            r = au.run_app_update_check()
        self.assertEqual(r.summary, "")
        self.assertFalse(r.news)
        self.assertEqual(r.waiting, ())


class RegistryTests(unittest.TestCase):
    """The lists that decide what may be installed without a human."""

    def test_only_cli_families_may_install(self):
        self.assertEqual(au.AUTO_INSTALLABLE, frozenset({"claude-code", "npm"}))
        self.assertNotIn("resolve", au.AUTO_INSTALLABLE)
        self.assertNotIn("flatpak", au.AUTO_INSTALLABLE)

    def test_brew_owned_node_packages_are_off_limits(self):
        for pkg in ("npm", "node", "npx", "corepack"):
            self.assertIn(pkg, au.NPM_NEVER_TOUCH)

    def test_every_named_cli_points_at_a_skill_that_actually_uses_it(self):
        # Only these packages are auto-installed, so a stale name here means a
        # CLI silently stops being maintained (or the wrong one gets moved).
        # Checking the package name really appears in that skill keeps the map
        # honest when a skill is renamed or swaps its tool out.
        skills = ROOT / "skills"
        for pkg, skill in au.NPM_SKILL_CLIS.items():
            skill_dir = skills / skill
            self.assertTrue((skill_dir / "SKILL.md").is_file(),
                            f"{pkg} claims skill '{skill}', which has no SKILL.md")
            found = any(
                pkg in f.read_text(encoding="utf-8", errors="replace")
                for f in skill_dir.rglob("*")
                if f.is_file() and f.suffix in (".md", ".py", ".sh", ".js")
            )
            self.assertTrue(found, f"skill '{skill}' never mentions {pkg}")

    def test_no_skill_cli_is_also_on_the_never_touch_list(self):
        self.assertEqual(set(au.NPM_SKILL_CLIS) & au.NPM_NEVER_TOUCH, set())


class FetchTests(unittest.TestCase):
    @patch("utils.app_updates.urllib.request.urlopen", side_effect=OSError("no route"))
    def test_a_dead_network_is_an_empty_string_not_an_exception(self, _open):
        # Every caller turns "" into "unknown". A raised exception here would
        # take down the whole nightly job.
        self.assertEqual(au._fetch("https://example.invalid"), "")


if __name__ == "__main__":
    unittest.main()
