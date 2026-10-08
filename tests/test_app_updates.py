"""Unit tests for utils.app_updates.

The module exists because nothing on the machine had ever looked at the apps
the package manager does not track. These tests hold the two properties that
make it safe to run unattended at 4am:

  - a check that cannot reach its version source reports "unknown", never
    "current". Silence that looks like good news is the failure being fixed.
  - nothing installs unless it is explicitly allowed to. GUI applications
    never install, and no package crosses a major version boundary on its own.

Every subprocess call and every HTTP fetch is mocked. Nothing here touches the
network, npm, or the Claude CLI. The Codex tests run a stand-in codex script and
tar for real, and one asks the real codex on PATH the bot's questions (skipped
where there is none). None of them installs anything or starts a turn. The
Rive tests do the same with a stand-in rive and the rive skill's real doctor,
and two ask the real Rive CLI and editor, where Homebrew installed them.
"""
from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
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


class _UserOwnedNpm:
    """Pins npm's global folder as writable. Without it, on a Linux box whose
    npm folder belongs to root, these tests would take the sudo path and run
    the real sudo."""

    def setUp(self):
        p = patch("utils.app_updates._npm_global_needs_sudo", return_value=False)
        p.start()
        self.addCleanup(p.stop)


class NpmCliTests(_UserOwnedNpm, unittest.TestCase):
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


class NpmProofTests(_UserOwnedNpm, unittest.TestCase):
    """An update npm reports as clean can still leave a skill unable to work.

    Measured on the Mac, 25 Sep 2026, on a scratch copy of the global prefix:
    with the browser download blocked, the code before this change installed
    mermaid-cli 11.17.0, called it "updated", and every diagram then failed
    with "Could not find chrome-headless-shell (ver. 154.0.8037.57)".
    """

    PKG = "@mermaid-js/mermaid-cli"

    def run_check(self, proof_results, npm_rc=(0, 0)):
        """check_npm_clis on a safe mermaid-cli bump with the proof faked.

        Returns (statuses, npm commands run, prefixes the proof was given).
        """
        commands, prefixes, results, rcs = [], [], list(proof_results), list(npm_rc)

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            commands.append(cmd)
            return rcs.pop(0), "npm said so"

        def proof(prefix):
            prefixes.append(prefix)
            if prefix is not None:
                self.assertTrue(Path(prefix).is_dir(), "the proof ran after the scratch prefix was gone")
            return results.pop(0)

        with patch("utils.app_updates._npm_outdated_global",
                   return_value={self.PKG: {"current": "11.16.0", "latest": "11.17.0"}}), \
                patch("utils.app_updates._run", side_effect=run), \
                patch.dict(au.NPM_PROOF, {self.PKG: proof}):
            statuses = au.check_npm_clis(auto_update=True)
        return statuses, commands, prefixes

    def test_an_update_is_tried_in_a_scratch_prefix_before_it_replaces_the_live_one(self):
        (s,), commands, prefixes = self.run_check([(True, ""), (True, "")])
        self.assertEqual(s.state, "updated")
        self.assertEqual(s.installed, "11.17.0")
        scratch = prefixes[0]
        self.assertEqual(commands, [
            ["npm", "install", "-g", "--prefix", str(scratch), f"{self.PKG}@11.17.0"],
            ["npm", "install", "-g", f"{self.PKG}@11.17.0"],
        ])
        # checked in the scratch prefix, then again where it landed
        self.assertEqual(prefixes[1:], [None])
        self.assertIn("app_update_trial_", str(scratch))
        self.assertFalse(Path(scratch).exists(), "the scratch prefix was left behind")

    def test_a_failed_trial_leaves_the_working_version_in_place(self):
        why = "the browser download failed: Error: All providers failed for chrome-headless-shell"
        (s,), commands, _ = self.run_check([(False, why)])
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.installed, "11.16.0")
        self.assertIn("11.16.0 was kept", s.detail)
        self.assertIn(why, s.detail)
        self.assertEqual(len(commands), 1, "the live copy was touched after a failed trial")
        self.assertIn("--prefix", commands[0])

    def test_a_trial_that_npm_refuses_is_a_failure_and_touches_nothing(self):
        (s,), commands, prefixes = self.run_check([], npm_rc=(1,))
        self.assertEqual(s.state, "failed")
        self.assertIn("the trial install failed", s.detail)
        self.assertEqual(prefixes, [])
        self.assertEqual(len(commands), 1)

    def test_a_proof_that_fails_where_it_landed_is_not_an_update(self):
        (s,), _, _ = self.run_check([(True, ""), (False, "a test diagram did not draw: boom")])
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.installed, "11.17.0")
        self.assertIn("passed its trial but not where it was installed", s.detail)
        self.assertIn("boom", s.detail)

    def test_a_cli_with_no_proof_is_taken_at_npm_word(self):
        with patch("utils.app_updates._npm_outdated_global",
                   return_value={"surge": {"current": "0.44.2", "latest": "0.44.3"}}), \
                patch("utils.app_updates._run", return_value=(0, "")) as run:
            (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "updated")
        self.assertEqual([c.args[0] for c in run.call_args_list], [["npm", "install", "-g", "surge@0.44.3"]])


class NpmRootOwnedPrefixTests(unittest.TestCase):
    """Linux, node from the distro or NodeSource: npm's global folder is
    /usr/lib/node_modules, owned by root, and the nightly job runs as the bot's
    user. Before 2026-09-26 every update there failed with EACCES, every night.
    """

    PKG = "lighthouse"

    def _needs_sudo(self, value: bool):
        return patch("utils.app_updates._npm_global_needs_sudo", return_value=value)

    def test_darwin_never_asks_for_sudo(self):
        with patch("utils.app_updates.platform.system", return_value="Darwin"), \
                patch.object(au.puppeteer_browsers, "npm_global_root") as root:
            self.assertFalse(au._npm_global_needs_sudo())
        root.assert_not_called()

    def test_a_folder_this_user_cannot_write_needs_sudo(self):
        with tempfile.TemporaryDirectory() as d, \
                patch("utils.app_updates.platform.system", return_value="Linux"), \
                patch.object(au.puppeteer_browsers, "npm_global_root", return_value=Path(d)), \
                patch("utils.app_updates.os.access", return_value=False) as access:
            self.assertTrue(au._npm_global_needs_sudo())
        self.assertEqual(access.call_args.args[0], Path(d))

    def test_a_user_owned_folder_does_not(self):
        with tempfile.TemporaryDirectory() as d, \
                patch("utils.app_updates.platform.system", return_value="Linux"), \
                patch.object(au.puppeteer_browsers, "npm_global_root", return_value=Path(d)):
            self.assertFalse(au._npm_global_needs_sudo())

    def test_a_missing_folder_is_judged_by_the_one_npm_would_create_it_in(self):
        with tempfile.TemporaryDirectory() as d, \
                patch("utils.app_updates.platform.system", return_value="Linux"), \
                patch.object(au.puppeteer_browsers, "npm_global_root",
                             return_value=Path(d) / "lib" / "node_modules"), \
                patch("utils.app_updates.os.access", return_value=True) as access:
            self.assertFalse(au._npm_global_needs_sudo())
        self.assertEqual(access.call_args.args[0], Path(d))

    def test_no_npm_answer_is_not_a_reason_for_sudo(self):
        with patch("utils.app_updates.platform.system", return_value="Linux"), \
                patch.object(au.puppeteer_browsers, "npm_global_root", return_value=None):
            self.assertFalse(au._npm_global_needs_sudo())

    def test_a_writable_folder_installs_without_sudo(self):
        with self._needs_sudo(False), \
                patch("utils.app_updates._run", return_value=(0, "")) as run, \
                patch("utils.app_updates.subprocess.run") as sub:
            self.assertEqual(au._npm_install_live("lighthouse@13.4.1"), (0, ""))
        self.assertEqual(run.call_args.args[0], ["npm", "install", "-g", "lighthouse@13.4.1"])
        sub.assert_not_called()

    def test_root_owned_folder_installs_through_sudo_with_the_stored_password(self):
        done = subprocess.CompletedProcess([], 0, stdout="added 1 package", stderr="")
        with self._needs_sudo(True), \
                patch("install.sudo.get_sudo_password", return_value="pw"), \
                patch("utils.app_updates.subprocess.run", return_value=done) as sub, \
                patch("utils.app_updates._run") as run:
            rc, out = au._npm_install_live("lighthouse@13.4.1")
        self.assertEqual((rc, out), (0, "added 1 package"))
        self.assertEqual(sub.call_args.args[0],
                         ["sudo", "-S", "-p", "", "env", "PUPPETEER_SKIP_DOWNLOAD=true",
                          "npm", "install", "-g", "lighthouse@13.4.1"])
        self.assertEqual(sub.call_args.kwargs["input"], "pw\n")
        self.assertNotIn("shell", sub.call_args.kwargs)
        run.assert_not_called()

    def test_without_a_stored_password_sudo_never_waits_for_one(self):
        refused = subprocess.CompletedProcess([], 1, stdout="",
                                              stderr="sudo: a password is required")
        with self._needs_sudo(True), \
                patch("install.sudo.get_sudo_password", return_value=None), \
                patch("utils.app_updates.subprocess.run", return_value=refused) as sub:
            rc, out = au._npm_install_live("lighthouse@13.4.1")
        self.assertEqual(sub.call_args.args[0][:2], ["sudo", "-n"])
        self.assertIsNone(sub.call_args.kwargs["input"])
        self.assertEqual(rc, 1)
        self.assertIn("no sudo password is stored", out)

    def test_under_sudo_puppeteer_fetches_no_browser_for_root(self):
        """Puppeteer's install script runs as root under sudo, and the browser
        it fetches lands in root's cache, which nothing reads: the bot's copy
        is fetched as its own user straight after (_diagram_draws). The skip
        rides in the argv through env, because sudo resets the environment,
        so a variable set on the bot's side never reaches npm. It must stay
        out of the bot's own environment as well: Puppeteer's browsers
        command honours it too, so a leak would turn that fetch into a no-op
        that exits 0."""
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        spec = "@mermaid-js/mermaid-cli@12.0.1"
        for password, sudo in (("pw", ["sudo", "-S", "-p", ""]), (None, ["sudo", "-n"])):
            with self.subTest(password=password):
                with self._needs_sudo(True), \
                        patch("install.sudo.get_sudo_password", return_value=password), \
                        patch("utils.app_updates.subprocess.run", return_value=done) as sub, \
                        patch.dict(os.environ):
                    os.environ.pop("PUPPETEER_SKIP_DOWNLOAD", None)
                    au._npm_install_live(spec)
                    self.assertNotIn("PUPPETEER_SKIP_DOWNLOAD", os.environ)
                self.assertEqual(sub.call_args.args[0],
                                 sudo + ["env", "PUPPETEER_SKIP_DOWNLOAD=true", "npm", "install", "-g", spec])

    def test_a_user_owned_install_keeps_its_browser_download(self):
        """Without sudo the script runs as the bot's user and fetches into
        the cache mmdc reads, so nothing is skipped there."""
        with self._needs_sudo(False), \
                patch("utils.app_updates._run", return_value=(0, "")) as run:
            au._npm_install_live("@mermaid-js/mermaid-cli@12.0.1")
        self.assertEqual(run.call_args.args[0], ["npm", "install", "-g", "@mermaid-js/mermaid-cli@12.0.1"])
        self.assertIsNone(run.call_args.kwargs.get("env"))

    def test_nightly_update_lands_on_a_root_owned_prefix(self):
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("utils.app_updates._npm_outdated_global",
                   return_value={self.PKG: {"current": "13.3.0", "latest": "13.4.1"}}), \
                self._needs_sudo(True), \
                patch("install.sudo.get_sudo_password", return_value="pw"), \
                patch("utils.app_updates.subprocess.run", return_value=done) as sub:
            (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "updated")
        self.assertEqual(sub.call_args.args[0][-1], "lighthouse@13.4.1")

    def test_the_trial_install_never_uses_sudo(self):
        pkg = "@mermaid-js/mermaid-cli"
        commands = []

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            commands.append(cmd)
            return 0, ""
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("utils.app_updates._npm_outdated_global",
                   return_value={pkg: {"current": "11.16.0", "latest": "11.17.0"}}), \
                self._needs_sudo(True), \
                patch("install.sudo.get_sudo_password", return_value="pw"), \
                patch("utils.app_updates._run", side_effect=run), \
                patch("utils.app_updates.subprocess.run", return_value=done) as sub, \
                patch.dict(au.NPM_PROOF, {pkg: lambda prefix: (True, "")}):
            (s,) = au.check_npm_clis(auto_update=True)
        self.assertEqual(s.state, "updated")
        self.assertEqual(len(commands), 1)
        self.assertIn("--prefix", commands[0])
        self.assertEqual(sub.call_args.args[0][:2], ["sudo", "-S"])


class DiagramDrawsTests(unittest.TestCase):
    """The proof for mermaid-cli: its browser, then one real diagram."""

    def draws(self, ensure=(True, ""), rc=0, write=b"\x89PNG\r\n\x1a\n rest", prefix=None):
        seen = {}

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            seen["cmd"], seen["env"] = cmd, env
            if write is not None:
                Path(cmd[cmd.index("-o") + 1]).write_bytes(write)
            return rc, "Error: Could not find chrome-headless-shell" if rc else ""

        with patch.object(au.puppeteer_browsers, "npm_global_root", return_value=Path("/nm")) as root, \
                patch.object(au.puppeteer_browsers, "ensure_browsers", return_value=ensure) as ens, \
                patch("utils.app_updates._run", side_effect=run):
            result = au._diagram_draws(prefix)
        seen["root_prefix"] = root.call_args.args[0] if root.call_args.args else None
        seen["ensure"] = ens.call_args
        return result, seen

    def test_a_png_drawn_by_the_skill_script_is_proof(self):
        (ok, why), seen = self.draws()
        self.assertTrue(ok, why)
        self.assertEqual(Path(seen["cmd"][1]), ROOT / "skills" / "diagram" / "scripts" / "diagram.py")
        self.assertTrue(seen["cmd"][-1].endswith(".png"))
        self.assertIsNone(seen["env"])
        self.assertEqual(seen["ensure"].args, ("@mermaid-js/mermaid-cli",))
        self.assertEqual(seen["ensure"].kwargs, {"root": Path("/nm")})

    def test_a_trial_copy_is_the_one_drawn_with(self):
        (ok, _), seen = self.draws(prefix=Path("/tmp/trial"))
        self.assertTrue(ok)
        self.assertEqual(seen["root_prefix"], Path("/tmp/trial"))
        self.assertTrue(seen["env"]["PATH"].startswith(str(Path("/tmp/trial") / "bin")))

    def test_no_browser_means_no_drawing_is_attempted(self):
        (ok, why), seen = self.draws(ensure=(False, "the browser download failed: EACCES"))
        self.assertFalse(ok)
        self.assertEqual(why, "the browser download failed: EACCES")
        self.assertNotIn("cmd", seen)

    def test_a_failed_render_is_the_reason(self):
        (ok, why), _ = self.draws(rc=1, write=None)
        self.assertFalse(ok)
        self.assertIn("Could not find chrome-headless-shell", why)

    def test_exit_zero_without_a_png_is_not_a_drawing(self):
        for write in (None, b"<svg/>"):
            with self.subTest(write=write):
                (ok, _), _ = self.draws(write=write)
                self.assertFalse(ok)


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
            for family in ("claude-code", "codex", "rive", "resolve", "npm", "flatpak")
        )):
            au.collect(auto_update=True)
        self.assertTrue(seen["claude-code"])
        self.assertTrue(seen["codex"])
        self.assertTrue(seen["rive"])
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

    def test_which_families_may_install(self):
        # CLIs, plus Rive, whose editor is the one application installed
        # unattended: tried from Rive's own notarized image, and only while
        # it is closed.
        self.assertEqual(au.AUTO_INSTALLABLE, frozenset({"claude-code", "codex", "npm", "rive"}))
        self.assertNotIn("resolve", au.AUTO_INSTALLABLE)
        self.assertNotIn("flatpak", au.AUTO_INSTALLABLE)

    def test_each_rive_app_has_its_own_entry(self):
        # One check throwing must not take the other Rive app down with it,
        # and collect() isolates entries, not families.
        rive = [check for family, check in au.CHECKS if family == "rive"]
        self.assertEqual(rive, [au.check_rive_cli, au.check_rive_editor])

    def test_the_cask_list_leaves_out_what_is_updated_here_by_brew_outdated_name(self):
        # brew outdated --cask --quiet prints a tap's cask by its bare token
        # (measured 8 Oct 2026: "rive-cli", not "rive-app/tap/rive-cli").
        self.assertEqual(au.CASKS_UPDATED_HERE, frozenset({"codex", "rive", "rive-cli"}))

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

    def test_every_proof_belongs_to_a_cli_that_may_be_installed(self):
        # a proof keyed on a name the updater never installs would never run
        self.assertTrue(au.NPM_PROOF)
        self.assertEqual(set(au.NPM_PROOF) - set(au.NPM_SKILL_CLIS), set())


# --------------------------------------------------------------------------
# Codex CLI
# --------------------------------------------------------------------------

_BOT_FLAGS = ("--json", "--ephemeral", "--skip-git-repo-check", "--sandbox", "-m",
              "-c", "--dangerously-bypass-hook-trust", "--disable")
_TAKES_VALUE = ("--sandbox", "-m", "-c", "--disable")
_MODES = ("read-only", "workspace-write", "danger-full-access")


def _fake_codex(folder: Path, version: str = "0.160.0", flags=_BOT_FLAGS,
                modes=_MODES, features=("multi_agent", "multi_agent_v2", "apps"),
                record: Path | None = None, warn: bool = False) -> Path:
    """A codex that parses like the real one, as measured on 0.159.2.

    clap reads every flag, and the --sandbox value, before it acts on --help:
    an unknown one exits 2 with "error: unexpected argument", and --help at
    the end then exits 0 without starting a turn. Feature names are only
    listed, never checked while parsing.

    warn: open every call with the stderr line the real one prints on Linux
    when CODEX_HOME is under /tmp, as the trial's scratch home is there.
    """
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "codex"
    script.write_text(f"""#!{sys.executable}
import os, sys
if {str(record) if record else ''!r}:
    with open({str(record) if record else ''!r}, "a") as fh:
        fh.write(os.environ.get("CODEX_HOME", "") + "\\n")
if {warn!r}:
    print('WARNING: proceeding, even though we could not create PATH aliases: '
          'Refusing to create helper binaries under temporary dir "/tmp"', file=sys.stderr)
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli {version}")
    sys.exit(0)
if args[:2] == ["features", "list"]:
    for name in {tuple(features)!r}:
        print(f"{{name:<40}} stable             false")
    sys.exit(0)
if args[:1] == ["exec"]:
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--help":
            print("Run Codex non-interactively")
            sys.exit(0)
        if a not in {tuple(flags)!r}:
            print(f"error: unexpected argument '{{a}}' found", file=sys.stderr)
            sys.exit(2)
        if a in {_TAKES_VALUE!r}:
            i += 1
            if a == "--sandbox" and args[i] not in {tuple(modes)!r}:
                print(f"error: invalid value '{{args[i]}}' for '--sandbox <SANDBOX_MODE>'",
                      file=sys.stderr)
                sys.exit(2)
        i += 1
    sys.exit(3)
sys.exit(1)
""", encoding="utf-8")
    script.chmod(0o755)
    return script


class CodexTakesTheBotTests(unittest.TestCase):
    """The question a new build has to answer before it replaces the live one."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_a_build_that_takes_the_bots_command_line_passes(self):
        codex = _fake_codex(self.dir)
        self.assertEqual(au._codex_takes_the_bot(str(codex), "0.160.0"), (True, ""))

    def test_a_dropped_hook_trust_flag_is_refused(self):
        # The running bot cached "yes" for this flag when it started. A build
        # without it would abort every Codex turn until a restart.
        codex = _fake_codex(self.dir, flags=tuple(
            f for f in _BOT_FLAGS if f != "--dangerously-bypass-hook-trust"))
        ok, why = au._codex_takes_the_bot(str(codex), "0.160.0")
        self.assertFalse(ok)
        self.assertIn("--dangerously-bypass-hook-trust", why)

    def test_any_flag_the_bot_passes_is_required(self):
        for dropped in ("--ephemeral", "--skip-git-repo-check", "--json", "--disable"):
            with self.subTest(dropped=dropped):
                codex = _fake_codex(self.dir / dropped.strip("-"),
                                    flags=tuple(f for f in _BOT_FLAGS if f != dropped))
                ok, why = au._codex_takes_the_bot(str(codex), "0.160.0")
                self.assertFalse(ok)
                self.assertIn(dropped, why)

    def test_the_linux_temp_home_warning_is_not_a_refusal(self):
        # Real codex on Linux opens every call with this line on stderr when
        # CODEX_HOME is under /tmp. Only stdout is read for the version and
        # the feature names, so a good build still passes.
        codex = _fake_codex(self.dir, warn=True)
        self.assertEqual(au._codex_takes_the_bot(str(codex), "0.160.0"), (True, ""))

    def test_the_reason_given_is_the_refusal_not_the_warning(self):
        codex = _fake_codex(self.dir, warn=True, flags=tuple(
            f for f in _BOT_FLAGS if f != "--ephemeral"))
        ok, why = au._codex_takes_the_bot(str(codex), "0.160.0")
        self.assertFalse(ok)
        self.assertIn("error: unexpected argument '--ephemeral'", why)
        self.assertNotIn("WARNING", why)

    def test_a_dropped_sandbox_mode_is_refused(self):
        codex = _fake_codex(self.dir, modes=("read-only", "workspace-write"))
        ok, why = au._codex_takes_the_bot(str(codex), "0.160.0")
        self.assertFalse(ok)
        self.assertIn("danger-full-access", why)

    def test_a_dropped_feature_name_is_refused(self):
        # Not caught while parsing: an unknown --disable name aborts the turn
        # later, so the list has to be read.
        codex = _fake_codex(self.dir, features=("multi_agent", "apps"))
        ok, why = au._codex_takes_the_bot(str(codex), "0.160.0")
        self.assertFalse(ok)
        self.assertIn("multi_agent_v2", why)

    def test_a_build_that_is_not_the_version_meant_is_refused(self):
        codex = _fake_codex(self.dir, version="0.159.2")
        ok, why = au._codex_takes_the_bot(str(codex), "0.160.0")
        self.assertFalse(ok)
        self.assertIn("0.159.2", why)

    def test_the_trial_never_opens_the_live_codex_home(self):
        record = self.dir / "homes.txt"
        codex = _fake_codex(self.dir / "bin", record=record)
        self.assertTrue(au._codex_takes_the_bot(str(codex), "0.160.0")[0])
        homes = set(record.read_text(encoding="utf-8").split())
        self.assertEqual(len(homes), 1, homes)
        (home,) = homes
        self.assertIn("codex_trial_home_", home)
        self.assertNotEqual(Path(home), Path.home() / ".codex")
        self.assertFalse(Path(home).exists(), "the scratch home was left behind")

    @unittest.skipUnless(shutil.which("codex"), "no codex on PATH")
    def test_the_installed_codex_takes_the_bot(self):
        # The real build on this machine, asked the real questions. Skips where
        # codex is not installed, CI included.
        binary = shutil.which("codex")
        version = au._codex_version(binary)
        self.assertTrue(version, "codex --version gave no version")
        self.assertEqual(au._codex_takes_the_bot(binary, version), (True, ""))


class CodexCaskTrialTests(unittest.TestCase):
    """The cask trial unpacks the very archive brew will install."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.brew_calls = []
        self.real_run = au._run

    def _archive(self, **fake) -> Path:
        package = self.dir / "package"
        _fake_codex(package / "bin", **fake)
        (package / "codex-package.json").write_text("{}", encoding="utf-8")
        archive = self.dir / "codex-package-aarch64-apple-darwin.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), "-C", str(package), "."], check=True)
        return archive

    def _brew(self, archive, fetch_rc=0):
        def run(cmd, timeout=60, merge_stderr=True, env=None):
            if cmd[0] == "brew":
                self.brew_calls.append((cmd, env))
                if cmd[1] == "fetch":
                    return fetch_rc, "" if fetch_rc == 0 else "curl: (6) Could not resolve host"
                if cmd[1] == "--cache":
                    return 0, str(archive)
                raise AssertionError(f"unexpected brew call {cmd}")
            return self.real_run(cmd, timeout=timeout, merge_stderr=merge_stderr, env=env)
        return patch("utils.app_updates._run", side_effect=run)

    def test_the_download_is_unpacked_and_tried(self):
        archive = self._archive()
        seen = []
        real_trial = au._codex_takes_the_bot

        def trial(binary, version):
            seen.append(Path(binary))
            return real_trial(binary, version)

        with self._brew(archive), patch("utils.app_updates._codex_takes_the_bot", side_effect=trial):
            self.assertEqual(au._codex_cask_trial("0.160.0"), (True, ""))
        self.assertEqual([c[:2] for c, _ in self.brew_calls],
                         [["brew", "fetch"], ["brew", "--cache"]])
        for _cmd, env in self.brew_calls:
            self.assertEqual(env.get("HOMEBREW_NO_AUTO_UPDATE"), "1")
        (binary,) = seen
        self.assertEqual(binary.parts[-2:], ("bin", "codex"))
        self.assertIn("app_update_trial_", str(binary))
        self.assertFalse(binary.exists(), "the unpacked trial copy was left behind")

    def test_a_build_the_bot_cannot_use_fails_its_trial(self):
        archive = self._archive(features=("multi_agent",))
        with self._brew(archive):
            ok, why = au._codex_cask_trial("0.160.0")
        self.assertFalse(ok)
        self.assertIn("multi_agent_v2", why)

    def test_a_download_brew_could_not_fetch_is_a_failed_trial(self):
        with self._brew(self.dir / "missing.tar.gz", fetch_rc=1):
            ok, why = au._codex_cask_trial("0.160.0")
        self.assertFalse(ok)
        self.assertIn("could not download", why)

    def test_an_archive_without_bin_codex_is_a_failed_trial(self):
        package = self.dir / "odd"
        package.mkdir()
        (package / "README").write_text("nothing here", encoding="utf-8")
        archive = self.dir / "odd.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), "-C", str(package), "."], check=True)
        with self._brew(archive):
            ok, why = au._codex_cask_trial("0.160.0")
        self.assertFalse(ok)
        self.assertIn("bin/codex", why)


class CodexSourceTests(unittest.TestCase):
    """Which installer owns the codex on PATH, read off where it points."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        (self.dir / "bin").mkdir()

    def _link(self, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        link = self.dir / "bin" / "codex"
        link.symlink_to(target)
        return str(link)

    def test_a_caskroom_link_is_the_cask(self):
        link = self._link(self.dir / "Caskroom" / "codex" / "0.159.2" / "bin" / "codex")
        with patch("utils.app_updates.puppeteer_browsers.npm_global_root") as root:
            self.assertEqual(au._codex_source(link), "cask")
        root.assert_not_called()

    def test_an_npm_link_is_npm(self):
        modules = self.dir / "lib" / "node_modules"
        link = self._link(modules / "@openai" / "codex" / "bin" / "codex.js")
        with patch("utils.app_updates.puppeteer_browsers.npm_global_root", return_value=modules):
            self.assertEqual(au._codex_source(link), "npm")

    def test_anything_else_is_left_alone(self):
        link = self._link(self.dir / "src" / "codex-rs" / "target" / "release" / "codex")
        with patch("utils.app_updates.puppeteer_browsers.npm_global_root",
                   return_value=self.dir / "lib" / "node_modules"):
            self.assertEqual(au._codex_source(link), "")

    def test_another_cask_is_not_codexs(self):
        link = self._link(self.dir / "Caskroom" / "codex-nightly" / "1.0" / "codex")
        with patch("utils.app_updates.puppeteer_browsers.npm_global_root", return_value=None):
            self.assertEqual(au._codex_source(link), "")


class CodexVersionTests(unittest.TestCase):
    def test_the_version_is_read_out_of_the_banner(self):
        with patch("utils.app_updates._run", return_value=(0, "codex-cli 0.159.2")):
            self.assertEqual(au._codex_version("codex"), "0.159.2")

    def test_a_failed_or_empty_answer_is_no_version(self):
        for answer in ((1, "codex-cli 0.159.2"), (0, ""), (0, "codex-cli dev")):
            with self.subTest(answer=answer), patch("utils.app_updates._run", return_value=answer):
                self.assertEqual(au._codex_version("codex"), "")

    def test_the_cask_version_is_what_brew_would_install_now(self):
        payload = json.dumps({"formulae": [], "casks": [{"token": "codex", "version": "0.159.2"}]})
        with patch("utils.app_updates._run", return_value=(0, payload)) as run:
            self.assertEqual(au._codex_latest("cask"), "0.159.2")
        self.assertEqual(run.call_args.args[0][:3], ["brew", "info", "--cask"])
        self.assertEqual(run.call_args.kwargs["env"].get("HOMEBREW_NO_AUTO_UPDATE"), "1")

    def test_the_npm_version_is_npms_latest(self):
        with patch("utils.app_updates._run", return_value=(0, "0.159.2")) as run:
            self.assertEqual(au._codex_latest("npm"), "0.159.2")
        self.assertEqual(run.call_args.args[0], ["npm", "view", "@openai/codex", "version"])

    def test_unreadable_answers_are_no_version(self):
        for source, answer in (("cask", (0, "Error: no cask")), ("cask", (1, "")),
                               ("npm", (1, "E404")), ("npm", (0, "<html>")), ("", (0, "1.0"))):
            with self.subTest(source=source, answer=answer), \
                    patch("utils.app_updates._run", return_value=answer):
                self.assertEqual(au._codex_latest(source), "")

    def test_busy_is_read_from_the_process_table_only(self):
        for rc, busy in ((0, True), (1, False)):
            with self.subTest(rc=rc), patch("utils.app_updates._run", return_value=(rc, "")) as run:
                self.assertEqual(au._codex_running(), busy)
            self.assertEqual(run.call_args.args[0], ["pgrep", "-x", "codex"])


class CodexCliTests(unittest.TestCase):
    """check_codex_cli from the version read to the install, every step faked."""

    BIN = "/opt/homebrew/bin/codex"

    def run_check(self, auto=True, source="cask", installed="0.158.0", latest="0.159.2",
                  busy=False, trial=(True, ""), install_rc=0, after=None, landed=(True, "")):
        """Returns (statuses, calls): calls lists every step that ran, in order."""
        calls = []
        versions = [installed, latest if after is None else after]

        def version(binary, env=None):
            calls.append(("version", binary))
            return versions.pop(0) if versions else ""

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            calls.append(("run", cmd, (env or {}).get("HOMEBREW_NO_AUTO_UPDATE")))
            return install_rc, "brew said so"

        def npm_live(spec):
            calls.append(("npm-live", spec))
            return install_rc, "npm said so"

        def cask_trial(version):
            calls.append(("cask-trial", version))
            return trial

        def npm_trial(pkg, version, proof):
            calls.append(("npm-trial", pkg, version))
            return trial

        def takes(binary, version):
            calls.append(("live-check", binary, version))
            return landed

        with patch("utils.app_updates.shutil.which", return_value=self.BIN), \
                patch("utils.app_updates._codex_version", side_effect=version), \
                patch("utils.app_updates._codex_source", return_value=source), \
                patch("utils.app_updates._codex_latest", return_value=latest), \
                patch("utils.app_updates._codex_running",
                      side_effect=list(busy) if isinstance(busy, list) else lambda: busy), \
                patch("utils.app_updates._codex_cask_trial", side_effect=cask_trial), \
                patch("utils.app_updates._trial_install", side_effect=npm_trial), \
                patch("utils.app_updates._codex_takes_the_bot", side_effect=takes), \
                patch("utils.app_updates._npm_install_live", side_effect=npm_live), \
                patch("utils.app_updates._run", side_effect=run):
            statuses = au.check_codex_cli(auto_update=auto)
        return statuses, calls

    @staticmethod
    def installs(calls):
        return [c for c in calls if c[0] in ("run", "npm-live")]

    def test_not_installed_reports_nothing(self):
        with patch("utils.app_updates.shutil.which", return_value=None):
            self.assertEqual(au.check_codex_cli(auto_update=True), [])

    def test_a_zero_x_minor_is_installed(self):
        # The whole point. is_major_jump holds this exact move, which is why
        # Codex sat waiting on a human every night.
        self.assertTrue(au.is_major_jump("0.158.0", "0.159.2"))
        (s,), calls = self.run_check()
        self.assertEqual((s.name, s.state, s.installed, s.latest),
                         ("Codex CLI", "updated", "0.159.2", "0.159.2"))
        self.assertEqual(self.installs(calls), [("run", ["brew", "upgrade", "--cask", "codex"], "1")])

    def test_the_cask_is_tried_before_brew_touches_the_live_one(self):
        _, calls = self.run_check()
        steps = [c[0] for c in calls]
        self.assertLess(steps.index("cask-trial"), steps.index("run"))
        self.assertLess(steps.index("run"), steps.index("live-check"))
        self.assertIn(("cask-trial", "0.159.2"), calls)
        self.assertIn(("live-check", self.BIN, "0.159.2"), calls)

    def test_npm_is_tried_in_a_scratch_prefix_then_installed_pinned(self):
        (s,), calls = self.run_check(source="npm")
        self.assertEqual(s.state, "updated")
        self.assertIn(("npm-trial", "@openai/codex", "0.159.2"), calls)
        self.assertEqual(self.installs(calls), [("npm-live", "@openai/codex@0.159.2")])

    def test_a_failed_trial_keeps_the_working_version(self):
        for source in ("cask", "npm"):
            with self.subTest(source=source):
                (s,), calls = self.run_check(
                    source=source, trial=(False, "it refuses the bot's command line: error: x"))
                self.assertEqual(s.state, "failed")
                self.assertEqual(s.installed, "0.158.0")
                self.assertIn("0.158.0 was kept", s.detail)
                self.assertIn("refuses the bot's command line", s.detail)
                self.assertEqual(self.installs(calls), [])

    def test_a_busy_codex_is_not_swapped_under_a_turn(self):
        (s,), calls = self.run_check(busy=True)
        self.assertEqual(s.state, "outdated")
        self.assertIn("in use", s.detail)
        self.assertFalse([c for c in calls if c[0] in ("cask-trial", "npm-trial", "run", "npm-live")])

    def test_a_turn_that_starts_during_the_trial_still_waits(self):
        (s,), calls = self.run_check(busy=[False, True])
        self.assertEqual(s.state, "outdated")
        self.assertIn("in use", s.detail)
        self.assertIn(("cask-trial", "0.159.2"), calls)
        self.assertEqual(self.installs(calls), [])

    def test_a_new_leading_number_waits_for_a_human(self):
        (s,), calls = self.run_check(installed="0.159.2", latest="1.0.0")
        self.assertEqual(s.state, "outdated")
        self.assertIn("major version", s.detail)
        self.assertFalse([c for c in calls if c[0] in ("cask-trial", "run")])

    def test_report_only_mode_installs_nothing(self):
        (s,), calls = self.run_check(auto=False)
        self.assertEqual(s.state, "outdated")
        self.assertEqual([c[0] for c in calls], ["version"])

    def test_current_is_current(self):
        (s,), calls = self.run_check(installed="0.159.2", latest="0.159.2")
        self.assertEqual(s.state, "current")
        self.assertEqual([c[0] for c in calls], ["version"])

    def test_an_install_some_other_way_is_never_moved(self):
        (s,), calls = self.run_check(source="")
        self.assertEqual(s.state, "unknown")
        self.assertEqual([c[0] for c in calls], ["version"])

    def test_an_unreadable_latest_is_unknown_not_current(self):
        (s,), _ = self.run_check(latest="")
        self.assertEqual(s.state, "unknown")
        self.assertIn("Homebrew", s.detail)

    def test_a_refused_install_is_a_failure(self):
        (s,), calls = self.run_check(install_rc=1)
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.installed, "0.158.0")
        self.assertNotIn("live-check", [c[0] for c in calls])

    def test_an_install_that_did_not_move_the_version_is_a_failure(self):
        (s,), _ = self.run_check(after="0.158.0")
        self.assertEqual(s.state, "failed")
        self.assertIn("reports 0.158.0", s.detail)

    def test_a_build_that_fails_where_it_landed_is_not_an_update(self):
        (s,), _ = self.run_check(landed=(False, "it no longer lists multi_agent_v2"))
        self.assertEqual(s.state, "failed")
        self.assertIn("passed its trial but not where it was installed", s.detail)

    def test_the_npm_check_leaves_codex_to_this_one(self):
        # Reported once, by the check that knows how to try it first.
        with patch("utils.app_updates._npm_outdated_global",
                   return_value={"@openai/codex": {"current": "0.158.0", "latest": "0.159.2"}}), \
                patch("utils.app_updates._run") as run:
            self.assertEqual(au.check_npm_clis(auto_update=True), [])
        run.assert_not_called()


class _CodexSpawnRefused(RuntimeError):
    pass


class CodexContractDriftTests(unittest.IsolatedAsyncioTestCase):
    """The nightly asks about the command line the bot really builds."""

    async def test_every_flag_the_bot_passes_is_asked_about(self):
        from core import llm

        captured = []

        async def spawn(*cmd, **_kwargs):
            captured.extend(cmd)
            raise _CodexSpawnRefused("no real subprocess in tests")

        provider = llm.CodexCLIProvider("gpt-6-astra")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".codex").mkdir()
            (Path(tmp) / ".codex" / "hooks.json").write_text("{}", encoding="utf-8")
            provider._bot_dir = Path(tmp)
            with patch("asyncio.create_subprocess_exec", new=spawn), \
                    patch("core.llm._codex_accepts_hook_trust_bypass", return_value=True), \
                    patch("core.llm._codex_feature_names",
                          return_value=frozenset(au.CODEX_BOT_FEATURES) | {"apps"}), \
                    patch("core.config.get_llm_effort", return_value="max"):
                try:
                    await provider.complete("sys", [], user_id=None)
                except _CodexSpawnRefused:
                    pass
        self.assertTrue(captured, "the provider never reached the spawn")
        nightly = list(au.CODEX_BOT_ARGV)
        for name in au.CODEX_BOT_FEATURES:
            nightly += ["--disable", name]
        bot_flags = {a for a in captured if a.startswith("-") and a != "-"}
        self.assertIn("--dangerously-bypass-hook-trust", bot_flags)
        self.assertEqual(bot_flags - set(nightly), set(),
                         "the bot passes a flag the nightly check never asks a new build about")
        self.assertEqual(captured[captured.index("--sandbox") + 1],
                         nightly[nightly.index("--sandbox") + 1])
        disabled = [captured[i + 1] for i, a in enumerate(captured) if a == "--disable"]
        self.assertEqual(sorted(disabled), sorted(au.CODEX_BOT_FEATURES))

    def test_the_feature_names_are_the_bots(self):
        from core import llm
        self.assertEqual(tuple(au.CODEX_BOT_FEATURES), tuple(llm._CODEX_DISABLED_FEATURES))


# --------------------------------------------------------------------------
# Rive: the CLI and the editor
# --------------------------------------------------------------------------

_RIVE_ON_PATH = shutil.which("rive")
_HOMEBREW_RIVE = bool(_RIVE_ON_PATH) and "/Caskroom/rive-cli/" in os.path.realpath(_RIVE_ON_PATH or "")

# rive_doctor.py --json, shaped as it prints them (CLI 1.5.0, 8 Oct 2026).
_DOCTOR_READY = {"ok": True, "results": [
    {"check": "rive cli", "ok": True, "detail": "/x/rive (1.5.0)", "fatal": True},
    {"check": "tested version", "ok": False, "fatal": False,
     "detail": "Rive CLI 1.5.0 is not a version this skill was measured against"},
    {"check": "flags", "ok": True, "detail": "all present", "fatal": True},
    {"check": "gesture timing", "ok": True, "fatal": True,
     "detail": "a click costs 3 frames, as rivetimeline.py assumes"},
]}
_DOCTOR_FLAG_GONE = {"ok": False, "results": [
    {"check": "rive cli", "ok": True, "detail": "/x/rive (1.6.0)", "fatal": True},
    {"check": "flags", "ok": False, "fatal": True,
     "detail": "no longer in --help: --advance -- read the release notes and fix the "
               "scripts before rendering"},
]}


def _fake_rive(folder: Path, version: str = "1.5.0", notice: str = "",
               record: Path | None = None) -> Path:
    """A rive that answers --version as the real one does ("rive 1.5.0") and
    knows nothing else, so the skill's real doctor finds every flag gone.

    notice is printed to stderr after the version, and record, when given,
    gets the quiet settings --version was run with."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "rive"
    script.write_text(f"""#!{sys.executable}
import json, os, sys
if sys.argv[1:] == ["--version"]:
    if {str(record) if record else ''!r}:
        with open({str(record) if record else ''!r}, "w") as fh:
            json.dump({{k: os.environ.get(k) for k in ("RIVE_ANALYTICS", "RIVE_NO_TUI", "NO_COLOR")}}, fh)
    print("rive {version}")
    if {notice!r}:
        print({notice!r}, file=sys.stderr)
    sys.exit(0)
print("usage: rive COMMAND")
sys.exit(2)
""", encoding="utf-8")
    script.chmod(0o755)
    return script


def _fake_doctor(folder: Path, report, rc: int = 0, record: Path | None = None,
                 warn: str = "") -> Path:
    """A rive_doctor.py that prints this report (a dict, or raw text) and
    writes down how it was run. warn goes to stderr first."""
    script = folder / "fake_doctor.py"
    text = report if isinstance(report, str) else json.dumps(report)
    script.write_text(f"""import json, os, sys
if {str(record) if record else ''!r}:
    with open({str(record) if record else ''!r}, "w") as fh:
        json.dump({{"argv": sys.argv[1:], "cli": os.environ.get("RIVE_SKILL_CLI"),
                   "analytics": os.environ.get("RIVE_ANALYTICS")}}, fh)
if {warn!r}:
    print({warn!r}, file=sys.stderr, flush=True)
print({text!r})
sys.exit({rc})
""", encoding="utf-8")
    return script


class RiveCliWorksTests(unittest.TestCase):
    """The question a new Rive CLI has to answer before it replaces the live
    one: does the rive skill still work on it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.rive = str(_fake_rive(self.dir / "bin"))
        self.record = self.dir / "doctor_run.json"
        signed = patch("utils.app_updates._signed_by_rive", return_value=(True, ""))
        self.signed = signed.start()
        self.addCleanup(signed.stop)

    def works(self, report, rc=0, version="1.5.0", warn=""):
        doctor = _fake_doctor(self.dir, report, rc, self.record, warn)
        with patch.object(au, "RIVE_DOCTOR", doctor):
            return au._rive_cli_works(self.rive, version)

    def test_a_build_the_doctor_passes_works(self):
        self.assertEqual(self.works(_DOCTOR_READY), (True, ""))

    def test_the_unmeasured_version_note_is_not_a_failure(self):
        # Every new version carries it until someone compares its renders
        # frame by frame, so failing on it would hold every release.
        note = [r for r in _DOCTOR_READY["results"] if not r["ok"]]
        self.assertEqual([r["fatal"] for r in note], [False])
        self.assertTrue(self.works(_DOCTOR_READY)[0])

    def test_the_doctor_is_pointed_at_this_build_with_analytics_off(self):
        self.works(_DOCTOR_READY)
        seen = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual(seen, {"argv": ["--json"], "cli": self.rive, "analytics": "off"})

    def test_a_dropped_flag_is_refused_and_named(self):
        ok, why = self.works(_DOCTOR_FLAG_GONE, rc=1)
        self.assertFalse(ok)
        self.assertIn("flags: no longer in --help: --advance", why)

    def test_a_failed_check_wins_over_an_ok_it_contradicts(self):
        self.assertFalse(self.works({"ok": True, "results": _DOCTOR_FLAG_GONE["results"]})[0])
        self.assertFalse(self.works(_DOCTOR_READY, rc=1)[0])
        # and the doctor's own "not ok" stands even when no row says why
        self.assertFalse(self.works({"ok": False, "results": []})[0])

    def test_a_doctor_that_warns_on_stderr_is_still_read(self):
        # The report is stdout alone. A warning on stderr (a deprecation, the
        # CLI talking) must not turn a passing build into "no report".
        self.assertEqual(self.works(_DOCTOR_READY, warn="DeprecationWarning: old"), (True, ""))

    def test_a_doctor_that_crashed_is_a_failure(self):
        ok, why = self.works("Traceback (most recent call last):", rc=1)
        self.assertFalse(ok)
        self.assertIn("no report", why)

    def test_a_build_that_is_not_the_version_meant_is_refused_before_the_doctor(self):
        ok, why = self.works(_DOCTOR_READY, version="1.6.0")
        self.assertFalse(ok)
        self.assertIn("1.5.0", why)
        self.assertFalse(self.record.exists(), "the doctor ran on the wrong build")

    @patch("utils.app_updates.platform.system", return_value="Darwin")
    def test_a_build_rive_did_not_sign_is_refused_before_the_doctor(self, _plat):
        self.signed.return_value = (False, "it is signed by ABCDE12345, not by Rive (NJ3JMFUNS9)")
        ok, why = self.works(_DOCTOR_READY)
        self.assertFalse(ok)
        self.assertIn("not by Rive", why)
        self.assertFalse(self.record.exists())

    def test_the_skills_own_doctor_refuses_a_cli_without_the_skills_flags(self):
        # The contract end to end, with nothing faked but the CLI: the real
        # rive_doctor.py finds this build through RIVE_SKILL_CLI, and it
        # knows none of the flags the skill's scripts pass.
        ok, why = au._rive_cli_works(self.rive, "1.5.0")
        self.assertFalse(ok)
        self.assertIn("flags: no longer in --help", why)


class SignedByRiveTests(unittest.TestCase):
    """codesign and Gatekeeper, the checks each Rive update by hand was proved
    with."""

    def check(self, path, verify=0, team="NJ3JMFUNS9", assess=0, identifier="app.rive.cli"):
        calls = []

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            calls.append(cmd)
            if cmd[:2] == ["codesign", "--verify"]:
                return verify, "" if not verify else f"{path}: a sealed resource is missing or invalid"
            if cmd[:2] == ["codesign", "-dv"]:
                return 0, (f"Executable={path}\nIdentifier={identifier}\n"
                           f"Authority=Developer ID Application: Rive, Inc (NJ3JMFUNS9)\n"
                           f"TeamIdentifier={team}\n")
            if cmd[0] == "spctl":
                return assess, (f"{path}: accepted\nsource=Notarized Developer ID" if not assess
                                else f"{path}: rejected\nsource=Unnotarized Developer ID")
            raise AssertionError(f"unexpected call {cmd}")

        with patch("utils.app_updates._run", side_effect=run):
            return au._signed_by_rive(Path(path)), calls

    def test_rives_notarized_build_passes(self):
        for path in ("/x/rive", "/x/Rive.app"):
            with self.subTest(path=path):
                self.assertEqual(self.check(path)[0], (True, ""))

    def test_a_broken_signature_fails_at_once(self):
        (ok, why), calls = self.check("/x/rive", verify=1)
        self.assertFalse(ok)
        self.assertIn("does not hold", why)
        self.assertEqual(len(calls), 1)

    def test_another_teams_signature_fails(self):
        for team in ("ABCDE12345", "not set"):
            with self.subTest(team=team):
                (ok, why), _ = self.check("/x/rive", team=team)
                self.assertFalse(ok)
                self.assertIn("not by Rive (NJ3JMFUNS9)", why)

    def test_a_build_apple_did_not_notarize_fails(self):
        (ok, why), _ = self.check("/x/Rive.app", assess=3)
        self.assertFalse(ok)
        self.assertIn("Gatekeeper refuses it", why)

    def test_an_app_is_assessed_to_run_and_a_bare_binary_as_a_file(self):
        # spctl's execute check refuses anything that is not an app (measured
        # on CLI 1.4.0), so the CLI is asked against its own signature.
        _, calls = self.check("/x/Rive.app")
        self.assertEqual(calls[-1], ["spctl", "--assess", "--type", "execute", "/x/Rive.app"])
        _, calls = self.check("/x/rive")
        self.assertEqual(calls[-1], ["spctl", "--assess", "--type", "open", "--context",
                                     "context:primary-signature", "/x/rive"])

    def test_the_signature_is_verified_deep_and_strict(self):
        # --deep verifies the code nested inside Rive.app as well, and
        # --strict adds codesign's extra restrictions.
        for path in ("/x/rive", "/x/Rive.app"):
            with self.subTest(path=path):
                _, calls = self.check(path)
                self.assertEqual(calls[0], ["codesign", "--verify", "--deep", "--strict", path])

    def test_the_team_is_read_from_its_own_line(self):
        # The identifier is the signer's own text, so a build signed by
        # another team cannot pass by writing Rive's team into it.
        (ok, why), _ = self.check("/x/rive", team="ABCDE12345",
                                  identifier="app.x.TeamIdentifier=NJ3JMFUNS9")
        self.assertFalse(ok)
        self.assertIn("signed by ABCDE12345", why)


class _BrewDownload:
    """brew fetch and brew --cache, answered from a file made in the test."""

    def brew(self, download: Path, fetch_rc=0, extra=None):
        self.brew_calls = []
        real_run = au._run

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            if cmd[0] == "brew":
                self.brew_calls.append((cmd, env))
                if cmd[1] == "fetch":
                    return fetch_rc, "" if fetch_rc == 0 else "curl: (6) Could not resolve host"
                if cmd[1] == "--cache":
                    return 0, str(download)
                raise AssertionError(f"unexpected brew call {cmd}")
            if extra is not None and cmd[0] in extra:
                return extra[cmd[0]](cmd)
            return real_run(cmd, timeout=timeout, merge_stderr=merge_stderr, env=env)

        return patch("utils.app_updates._run", side_effect=run)

    def assert_brew_asked_about(self, token):
        self.assertEqual([c[:2] for c, _ in self.brew_calls], [["brew", "fetch"], ["brew", "--cache"]])
        for cmd, env in self.brew_calls:
            self.assertEqual(cmd[-1], token)
            self.assertEqual(env.get("HOMEBREW_NO_AUTO_UPDATE"), "1")


class RiveCliTrialTests(_BrewDownload, unittest.TestCase):
    """The trial unpacks the very archive brew will install, docs and samples
    with it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _archive(self, with_binary=True) -> Path:
        package = self.dir / "package"
        if with_binary:
            _fake_rive(package)
        (package / "docs").mkdir(parents=True)
        (package / "samples" / "rml_triangle").mkdir(parents=True)
        archive = self.dir / "rive-macos-arm64.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), "-C", str(package), "."], check=True)
        return archive

    def test_the_download_is_unpacked_whole_and_tried(self):
        seen = []

        def works(binary, version):
            folder = Path(binary).parent
            seen.append((Path(binary), version, (folder / "samples" / "rml_triangle").is_dir(),
                         (folder / "docs").is_dir()))
            return True, ""

        with self.brew(self._archive()), patch("utils.app_updates._rive_cli_works", side_effect=works):
            self.assertEqual(au._rive_cli_trial("1.5.0"), (True, ""))
        self.assert_brew_asked_about("rive-app/tap/rive-cli")
        ((binary, version, samples, docs),) = seen
        self.assertEqual((binary.name, version), ("rive", "1.5.0"))
        self.assertIn("app_update_trial_", str(binary))
        # `rive samples` looks only beside the binary, and the doctor builds one
        self.assertTrue(samples and docs)
        self.assertFalse(binary.exists(), "the unpacked trial copy was left behind")

    def test_a_build_the_doctor_fails_fails_its_trial(self):
        failed = (False, "the rive skill's doctor fails it: flags: no longer in --help: --advance")
        with self.brew(self._archive()), patch("utils.app_updates._rive_cli_works", return_value=failed):
            self.assertEqual(au._rive_cli_trial("1.5.0"), failed)

    def test_a_download_brew_could_not_fetch_is_a_failed_trial(self):
        with self.brew(self.dir / "missing.tar.gz", fetch_rc=1):
            ok, why = au._rive_cli_trial("1.5.0")
        self.assertFalse(ok)
        self.assertIn("could not download", why)

    def test_an_archive_without_rive_is_a_failed_trial(self):
        with self.brew(self._archive(with_binary=False)), \
                patch("utils.app_updates._rive_cli_works") as works:
            ok, why = au._rive_cli_trial("1.5.0")
        self.assertFalse(ok)
        self.assertIn("did not unpack to rive", why)
        works.assert_not_called()


class RiveEditorTrialTests(_BrewDownload, unittest.TestCase):
    """The editor's disk image is opened read-only and out of sight, checked,
    and closed again."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dmg = Path(tmp.name) / "Rive.dmg"
        self.dmg.write_bytes(b"a disk image")
        self.hdiutil = []
        self.mount = None
        signed = patch("utils.app_updates._signed_by_rive", return_value=(True, ""))
        self.signed = signed.start()
        self.addCleanup(signed.stop)

    def hdiutil_answers(self, version="0.9.157", attach_rc=0, detach_rcs=()):
        detach_rcs = list(detach_rcs)

        def hdiutil(cmd):
            self.hdiutil.append(cmd)
            if cmd[1] == "attach":
                if attach_rc:
                    return attach_rc, "hdiutil: attach failed - image not recognized"
                self.mount = Path(cmd[cmd.index("-mountpoint") + 1])
                contents = self.mount / "Rive.app" / "Contents"
                contents.mkdir(parents=True)
                with open(contents / "Info.plist", "wb") as fh:
                    plistlib.dump({"CFBundleShortVersionString": version}, fh)
                return 0, f"/dev/disk8s1  Apple_HFS  {self.mount}"
            if cmd[1] == "detach":
                rc = detach_rcs.pop(0) if detach_rcs else 0
                if rc == 0:
                    shutil.rmtree(Path(cmd[-1]) / "Rive.app")  # what unmounting does
                    return 0, '"disk8" ejected.'
                return rc, "hdiutil: couldn't unmount \"disk8\" - Resource busy"
            raise AssertionError(f"unexpected call {cmd}")

        return {"hdiutil": hdiutil}

    def trial(self, version="0.9.157", **answers):
        with self.brew(self.dmg, extra=self.hdiutil_answers(**answers)):
            return au._rive_editor_trial(version)

    def test_the_image_is_opened_read_only_checked_and_closed(self):
        self.assertEqual(self.trial(), (True, ""))
        self.assert_brew_asked_about("rive")
        attach, detach = self.hdiutil
        for flag in ("-readonly", "-nobrowse", "-noautoopen"):
            self.assertIn(flag, attach)
        self.assertEqual(attach[-1], str(self.dmg))
        self.assertEqual(detach, ["hdiutil", "detach", str(self.mount)])
        self.signed.assert_called_once_with(self.mount / "Rive.app")
        self.assertIn("app_update_trial_", str(self.mount))
        self.assertFalse(self.mount.exists(), "the mount point was left behind")

    def test_an_app_that_is_not_the_version_meant_fails_and_is_still_closed(self):
        ok, why = self.trial(version="0.9.158")
        self.assertFalse(ok)
        self.assertIn("reports version 0.9.157, not 0.9.158", why)
        self.assertEqual(self.hdiutil[-1][:2], ["hdiutil", "detach"])
        self.signed.assert_not_called()

    def test_an_app_rive_did_not_sign_fails(self):
        self.signed.return_value = (False, "Gatekeeper refuses it: rejected")
        self.assertEqual(self.trial(), (False, "Gatekeeper refuses it: rejected"))
        self.assertFalse(self.mount.exists())

    def test_an_image_that_will_not_open_is_a_failed_trial(self):
        ok, why = self.trial(attach_rc=1)
        self.assertFalse(ok)
        self.assertIn("would not open", why)
        self.assertEqual([c[1] for c in self.hdiutil], ["attach"])

    def test_a_busy_image_is_forced_and_never_deleted_into(self):
        # Both detaches fail, so the image is still mounted on the folder:
        # the folder stays, and so does everything in it.
        ok, _ = self.trial(detach_rcs=(16, 16))
        self.addCleanup(shutil.rmtree, self.mount, True)
        self.assertTrue(ok)
        self.assertEqual([c[1:3] for c in self.hdiutil[1:]],
                         [["detach", str(self.mount)], ["detach", "-force"]])
        self.assertTrue((self.mount / "Rive.app" / "Contents" / "Info.plist").is_file())


class RiveBusyTests(unittest.TestCase):
    """What makes a Rive update wait. The process table is read, never acted on."""

    def test_a_rive_process_or_a_skill_script_means_wait(self):
        for answers, busy in (((0, 1), True), ((1, 0), True), ((1, 1), False)):
            answer = iter(answers)
            with self.subTest(answers=answers), \
                    patch("utils.app_updates._run", side_effect=lambda *a, **k: (next(answer), "")) as run:
                self.assertEqual(au._rive_cli_busy(), busy)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][0], "pgrep")

    def test_the_probes(self):
        with patch("utils.app_updates._run", return_value=(1, "")) as run:
            self.assertFalse(au._rive_cli_busy())
            self.assertFalse(au._rive_editor_open())
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["pgrep", "-x", "rive"], ["pgrep", "-f", "skills/rive/scripts/"],
                          ["pgrep", "-f", "/Applications/Rive.app/Contents/"]])

    def test_a_skill_script_run_from_the_repo_root_is_seen(self):
        # A relative path, the form a pattern with a leading slash would miss.
        # pgrep -f matches against the whole command line, so a full path and
        # a user's own forked copy of the skill carry the same text.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                                 "skills/rive/scripts/rive_render.py"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)  # the test's own child, nothing else
        for _ in range(50):
            if au._rive_cli_busy():
                break
            subprocess.run(["sleep", "0.1"])
        self.assertTrue(au._rive_cli_busy())


class RiveCliTests(unittest.TestCase):
    """check_rive_cli from the version read to the install, every step faked."""

    BIN = "/opt/homebrew/bin/rive"

    def run_check(self, auto=True, installed="1.4.0", latest="1.5.0", from_cask=True,
                  busy=False, trial=(True, ""), install_rc=0, after=None, landed=(True, "")):
        """Returns (statuses, calls): calls lists every step that ran, in order."""
        calls = []
        versions = [installed, latest if after is None else after]

        def version(binary):
            calls.append(("version", binary))
            return versions.pop(0) if versions else ""

        def cask(token):
            calls.append(("cask", token))
            return {"token": "rive-cli", "version": latest, "installed": installed}

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            calls.append(("run", cmd, (env or {}).get("HOMEBREW_NO_AUTO_UPDATE")))
            return install_rc, "brew said so"

        def cli_trial(version):
            calls.append(("trial", version))
            return trial

        def works(binary, version):
            calls.append(("live-check", binary, version))
            return landed

        with patch("utils.app_updates.shutil.which", return_value=self.BIN), \
                patch("utils.app_updates._rive_version", side_effect=version), \
                patch("utils.app_updates._linked_from_cask", return_value=from_cask), \
                patch("utils.app_updates._brew_cask", side_effect=cask), \
                patch("utils.app_updates._rive_cli_busy",
                      side_effect=list(busy) if isinstance(busy, list) else lambda: busy), \
                patch("utils.app_updates._rive_cli_trial", side_effect=cli_trial), \
                patch("utils.app_updates._rive_cli_works", side_effect=works), \
                patch("utils.app_updates._run", side_effect=run):
            statuses = au.check_rive_cli(auto_update=auto)
        return statuses, calls

    @staticmethod
    def installs(calls):
        return [c for c in calls if c[0] == "run"]

    def test_not_installed_reports_nothing(self):
        with patch("utils.app_updates.shutil.which", return_value=None):
            self.assertEqual(au.check_rive_cli(auto_update=True), [])

    def test_a_new_release_is_tried_then_installed(self):
        (s,), calls = self.run_check()
        self.assertEqual((s.name, s.family, s.state, s.installed, s.latest),
                         ("Rive CLI", "rive", "updated", "1.5.0", "1.5.0"))
        self.assertEqual(self.installs(calls),
                         [("run", ["brew", "upgrade", "--cask", "rive-app/tap/rive-cli"], "1")])
        self.assertIn(("cask", "rive-app/tap/rive-cli"), calls)

    def test_the_trial_comes_before_brew_touches_the_live_one(self):
        _, calls = self.run_check()
        steps = [c[0] for c in calls]
        self.assertLess(steps.index("trial"), steps.index("run"))
        self.assertLess(steps.index("run"), steps.index("live-check"))
        self.assertIn(("trial", "1.5.0"), calls)
        self.assertIn(("live-check", self.BIN, "1.5.0"), calls)

    def test_a_failed_trial_keeps_the_working_version(self):
        (s,), calls = self.run_check(trial=(False, "the rive skill's doctor fails it: flags: x"))
        self.assertEqual((s.state, s.installed), ("failed", "1.4.0"))
        self.assertIn("1.4.0 was kept", s.detail)
        self.assertIn("doctor fails it", s.detail)
        self.assertEqual(self.installs(calls), [])

    def test_a_render_in_progress_is_not_swapped_under(self):
        (s,), calls = self.run_check(busy=True)
        self.assertEqual(s.state, "outdated")
        self.assertIn("waits", s.detail)
        self.assertFalse([c for c in calls if c[0] in ("trial", "run")])

    def test_a_render_that_starts_during_the_trial_still_waits(self):
        (s,), calls = self.run_check(busy=[False, True])
        self.assertEqual(s.state, "outdated")
        self.assertIn(("trial", "1.5.0"), calls)
        self.assertEqual(self.installs(calls), [])

    def test_a_new_leading_number_waits_for_a_human(self):
        (s,), calls = self.run_check(installed="1.5.0", latest="2.0.0")
        self.assertEqual(s.state, "outdated")
        self.assertIn("major version", s.detail)
        self.assertFalse([c for c in calls if c[0] in ("trial", "run")])

    def test_report_only_mode_installs_nothing(self):
        (s,), calls = self.run_check(auto=False)
        self.assertEqual(s.state, "outdated")
        self.assertEqual([c[0] for c in calls], ["version", "cask"])

    def test_current_is_current(self):
        (s,), calls = self.run_check(installed="1.5.0", latest="1.5.0")
        self.assertEqual(s.state, "current")
        self.assertEqual([c[0] for c in calls], ["version", "cask"])

    def test_a_cli_from_rives_own_installer_is_never_moved(self):
        (s,), calls = self.run_check(from_cask=False)
        self.assertEqual(s.state, "unknown")
        self.assertIn("some other way than Homebrew", s.detail)
        self.assertEqual([c[0] for c in calls], ["version"])

    def test_a_cli_linked_from_the_taps_caskroom_is_recognised(self):
        # brew links the tap's cask from Caskroom/rive-cli/<version>/, under
        # the bare token. Looking for the full tap name there would read every
        # brew install as "installed some other way", and the CLI would never
        # update or say so. Nothing is faked here but brew's answer.
        with tempfile.TemporaryDirectory() as tmp:
            brew = Path(tmp) / "brew"
            real = _fake_rive(brew / "Caskroom" / "rive-cli" / "1.4.0", version="1.4.0")
            (brew / "bin").mkdir()
            (brew / "bin" / "rive").symlink_to(real)
            curl = _fake_rive(Path(tmp) / ".rive" / "versions" / "1.4.0", version="1.4.0")
            cask = {"token": "rive-cli", "version": "1.4.0", "installed": "1.4.0"}
            with patch("utils.app_updates._brew_cask", return_value=cask):
                for binary, state in ((brew / "bin" / "rive", "current"), (curl, "unknown")):
                    with self.subTest(binary=str(binary)), \
                            patch("utils.app_updates.shutil.which", return_value=str(binary)):
                        (s,) = au.check_rive_cli(auto_update=False)
                        self.assertEqual((s.state, s.installed), (state, "1.4.0"))

    def test_an_unreadable_latest_is_unknown_not_current(self):
        (s,), _ = self.run_check(latest="")
        self.assertEqual(s.state, "unknown")
        self.assertIn("Homebrew", s.detail)

    def test_a_refused_install_is_a_failure(self):
        (s,), calls = self.run_check(install_rc=1)
        self.assertEqual((s.state, s.installed), ("failed", "1.4.0"))
        self.assertNotIn("live-check", [c[0] for c in calls])

    def test_an_install_that_did_not_move_the_version_is_a_failure(self):
        (s,), _ = self.run_check(after="1.4.0")
        self.assertEqual(s.state, "failed")
        self.assertIn("reports 1.4.0", s.detail)

    def test_a_build_that_fails_where_it_landed_is_not_an_update(self):
        (s,), _ = self.run_check(landed=(False, "the rive skill's doctor fails it: pixels: blank"))
        self.assertEqual(s.state, "failed")
        self.assertIn("passed its trial but not where it was installed", s.detail)


class RiveEditorTests(unittest.TestCase):
    """check_rive_editor from the version read to the install, every step faked."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "Rive.app"
        self.app.mkdir()

    def run_check(self, auto=True, installed="0.9.104", latest="0.9.157", in_brew="0.9.104",
                  is_open=False, trial=(True, ""), install_rc=0, after=None, landed=(True, ""),
                  app_left=True, system="Darwin"):
        """Returns (statuses, calls): calls lists every step that ran, in order."""
        calls = []
        versions = [installed, latest if after is None else after]

        def bundle(app):
            calls.append(("bundle", app))
            return versions.pop(0) if versions else ""

        def cask(token):
            calls.append(("cask", token))
            return {"token": "rive", "version": latest, "installed": in_brew}

        def run(cmd, timeout=60, merge_stderr=True, env=None):
            calls.append(("run", cmd, (env or {}).get("HOMEBREW_NO_AUTO_UPDATE")))
            if not app_left:
                self.app.rmdir()
            return install_rc, "brew said so"

        def editor_trial(version):
            calls.append(("trial", version))
            return trial

        def good(app, version):
            calls.append(("live-check", app, version))
            return landed

        with patch("utils.app_updates.platform.system", return_value=system), \
                patch("utils.app_updates.shutil.which", return_value="/opt/homebrew/bin/brew"), \
                patch.object(au, "RIVE_EDITOR_APP", self.app), \
                patch("utils.app_updates._bundle_version", side_effect=bundle), \
                patch("utils.app_updates._brew_cask", side_effect=cask), \
                patch("utils.app_updates._rive_editor_open",
                      side_effect=list(is_open) if isinstance(is_open, list) else lambda: is_open), \
                patch("utils.app_updates._rive_editor_trial", side_effect=editor_trial), \
                patch("utils.app_updates._rive_editor_good", side_effect=good), \
                patch("utils.app_updates._run", side_effect=run):
            statuses = au.check_rive_editor(auto_update=auto)
        return statuses, calls

    @staticmethod
    def installs(calls):
        return [c for c in calls if c[0] == "run"]

    def test_only_on_a_mac(self):
        self.assertEqual(self.run_check(system="Linux"), ([], []))

    def test_not_installed_reports_nothing(self):
        self.assertEqual(self.run_check(installed="", in_brew=None)[0], [])

    def test_an_app_gone_while_brew_still_lists_it_is_reported(self):
        # What a killed upgrade leaves (system_update._UPGRADE_CMDS, Blender in
        # July 2026), and brew's own list leaves Rive to this check, so silence
        # here would be silence everywhere.
        (s,), calls = self.run_check(installed="", in_brew="0.9.104")
        self.assertEqual(s.state, "failed")
        self.assertIn("brew reinstall --cask rive", s.detail)
        self.assertEqual(self.installs(calls), [])

    def test_an_editor_installed_without_brew_is_left_to_its_own_updater(self):
        (s,), calls = self.run_check(in_brew=None)
        self.assertEqual(s.state, "unknown")
        self.assertEqual(self.installs(calls), [])

    def test_report_only_mode_installs_nothing(self):
        (s,), calls = self.run_check(auto=False)
        self.assertEqual(s.state, "outdated")
        self.assertEqual([c[0] for c in calls], ["bundle", "cask"])

    def test_current_is_current(self):
        (s,), _ = self.run_check(installed="0.9.157")
        self.assertEqual(s.state, "current")

    def test_an_editor_that_updated_itself_past_brew_is_current(self):
        # Opened once, its own updater can take it past the cask.
        (s,), calls = self.run_check(installed="0.9.160")
        self.assertEqual(s.state, "current")
        self.assertEqual(self.installs(calls), [])

    def test_an_unreadable_latest_is_unknown_not_current(self):
        # As for the CLI: a brew that cannot say what is newest is "cannot
        # tell", never good news.
        (s,), calls = self.run_check(latest="")
        self.assertEqual(s.state, "unknown")
        self.assertIn("Homebrew", s.detail)
        self.assertEqual(self.installs(calls), [])

    def test_a_new_release_is_tried_then_installed(self):
        (s,), calls = self.run_check()
        self.assertEqual((s.name, s.family, s.state, s.installed, s.latest),
                         ("Rive editor", "rive", "updated", "0.9.157", "0.9.157"))
        self.assertEqual(self.installs(calls), [("run", ["brew", "upgrade", "--cask", "rive"], "1")])
        steps = [c[0] for c in calls]
        self.assertLess(steps.index("trial"), steps.index("run"))
        self.assertLess(steps.index("run"), steps.index("live-check"))
        self.assertIn(("live-check", self.app, "0.9.157"), calls)

    def test_a_zero_x_minor_is_installed(self):
        # Only the leading number waits, as for Codex: the editor has been 0.x
        # for its whole life and is checked by its signature, not its number.
        self.assertTrue(au.is_major_jump("0.9.157", "0.10.0"))
        (s,), _ = self.run_check(installed="0.9.157", latest="0.10.0", in_brew="0.9.157")
        self.assertEqual(s.state, "updated")

    def test_a_new_leading_number_waits_for_a_human(self):
        (s,), calls = self.run_check(installed="0.9.157", latest="1.0.0")
        self.assertEqual(s.state, "outdated")
        self.assertIn("major version", s.detail)
        self.assertFalse([c for c in calls if c[0] in ("trial", "run")])

    def test_an_open_editor_is_not_replaced_under_someone(self):
        (s,), calls = self.run_check(is_open=True)
        self.assertEqual(s.state, "outdated")
        self.assertIn("open", s.detail)
        self.assertFalse([c for c in calls if c[0] in ("trial", "run")])

    def test_an_editor_opened_during_the_trial_still_waits(self):
        (s,), calls = self.run_check(is_open=[False, True])
        self.assertEqual(s.state, "outdated")
        self.assertIn(("trial", "0.9.157"), calls)
        self.assertEqual(self.installs(calls), [])

    def test_a_failed_trial_keeps_the_working_version(self):
        (s,), calls = self.run_check(trial=(False, "it is signed by ABCDE12345, not by Rive"))
        self.assertEqual((s.state, s.installed), ("failed", "0.9.104"))
        self.assertIn("0.9.104 was kept", s.detail)
        self.assertEqual(self.installs(calls), [])

    def test_a_refused_install_is_a_failure(self):
        (s,), calls = self.run_check(install_rc=1)
        self.assertEqual((s.state, s.installed), ("failed", "0.9.104"))
        self.assertNotIn("is gone", s.detail)
        self.assertNotIn("live-check", [c[0] for c in calls])

    def test_a_refused_install_that_took_the_app_says_how_to_put_it_back(self):
        (s,), _ = self.run_check(install_rc=1, app_left=False)
        self.assertEqual(s.state, "failed")
        self.assertIn("is gone, `brew reinstall --cask rive` puts it back", s.detail)

    def test_an_install_that_did_not_move_the_version_is_a_failure(self):
        (s,), _ = self.run_check(after="0.9.104")
        self.assertEqual(s.state, "failed")
        self.assertIn("reports 0.9.104", s.detail)

    def test_an_app_that_fails_where_it_landed_is_not_an_update(self):
        (s,), _ = self.run_check(landed=(False, "Gatekeeper refuses it: rejected"))
        self.assertEqual(s.state, "failed")
        self.assertIn("passed its trial but not where it was installed", s.detail)


class RiveContractTests(unittest.TestCase):
    """The nightly runs the CLI and the doctor the way the rive skill does."""

    @classmethod
    def setUpClass(cls):
        scripts = ROOT / "skills" / "rive" / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import rivelib
        cls.L = rivelib

    def test_the_cli_is_run_as_the_skill_runs_it(self):
        env = self.L.rive_env()
        for key, value in au.RIVE_QUIET.items():
            self.assertEqual(env.get(key), value, key)

    def test_the_doctor_tries_the_build_it_is_pointed_at(self):
        self.assertTrue(au.RIVE_DOCTOR.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            rive = str(_fake_rive(Path(tmp)))
            with patch.dict(os.environ, {"RIVE_SKILL_CLI": rive}):
                self.assertEqual(self.L.find_rive(), rive)
                self.assertEqual(self.L.cli_version(), "1.5.0")

    def test_the_trial_reads_the_cli_version_as_the_skill_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            rive = str(_fake_rive(Path(tmp), version="1.6.2"))
            self.assertEqual(au._rive_version(rive), self.L.cli_version(rive))

    def test_the_version_is_read_from_stdout_with_analytics_off(self):
        # Asked every night of the live CLI and of each trial copy: run quiet,
        # as the skill runs it, and a notice on stderr is not the version.
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            for key in au.RIVE_QUIET:
                os.environ.pop(key, None)
            record = Path(tmp) / "env.json"
            rive = str(_fake_rive(Path(tmp) / "bin", version="1.5.0",
                                  notice="rive 1.6.0 is available", record=record))
            self.assertEqual(au._rive_version(rive), "1.5.0")
            self.assertEqual(json.loads(record.read_text(encoding="utf-8")), au.RIVE_QUIET)


class RiveLiveTests(unittest.TestCase):
    """The Rive on this machine, asked the nightly's questions. Installs nothing,
    and skips where Homebrew did not install it, CI included."""

    @unittest.skipUnless(_HOMEBREW_RIVE, "no Homebrew Rive CLI on PATH")
    def test_the_installed_cli_passes_the_trial_it_would_get(self):
        version = au._rive_version(_RIVE_ON_PATH)
        self.assertTrue(version, "rive --version gave no version")
        self.assertEqual(au._rive_cli_works(_RIVE_ON_PATH, version), (True, ""))

    @unittest.skipUnless(platform.system() == "Darwin" and Path("/Applications/Rive.app").is_dir(),
                         "no Rive editor here")
    def test_the_installed_editor_is_rives_and_notarized(self):
        version = au._bundle_version(au.RIVE_EDITOR_APP)
        self.assertTrue(version)
        self.assertEqual(au._rive_editor_good(au.RIVE_EDITOR_APP, version), (True, ""))


class FetchTests(unittest.TestCase):
    @patch("utils.app_updates.urllib.request.urlopen", side_effect=OSError("no route"))
    def test_a_dead_network_is_an_empty_string_not_an_exception(self, _open):
        # Every caller turns "" into "unknown". A raised exception here would
        # take down the whole nightly job.
        self.assertEqual(au._fetch("https://example.invalid"), "")


if __name__ == "__main__":
    unittest.main()
