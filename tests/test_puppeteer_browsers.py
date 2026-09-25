"""Tests for utils.puppeteer_browsers and the first-install hook in core.self_install.

A global npm CLI that draws through Puppeteer (mermaid-cli) gets its browser
from an install script. On Linux that script runs as root under sudo, so the
browser lands in root's cache, and anywhere it swallows a failed download and
exits 0. Both leave "Could not find chrome-headless-shell" on every render
while npm reports success; both were reproduced on the Mac on 25 Sep 2026
with scratch prefixes and caches. The fix fetches the browser again as the
bot's own user, through the package's own Puppeteer CLI.

Every subprocess is mocked. Nothing here touches npm, node or the network.
"""
from __future__ import annotations

import json
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

from utils import puppeteer_browsers as pb  # noqa: E402

PKG = "@mermaid-js/mermaid-cli"

# The shape of a failed `puppeteer browsers install`, measured with a
# read-only cache: yargs prints the whole usage text, then the error, then a
# stack. Paths shortened.
FAILED_OUTPUT = """puppeteer browsers install [browser]

Download and install the specified browser.

Options:
  --version       Show version number  [boolean]
  --base-url      Base URL to download from  [string]

Error: All providers failed for chrome-headless-shell 154.0.8037.57:
  - DefaultProvider: EACCES: permission denied, open '/cache/chrome-headless-shell/154.zip'
    at install (file:///nm/@puppeteer/browsers/lib/install.js:134:12)
    at async Object.handler (file:///nm/@puppeteer/browsers/lib/CLI.js:181:21)
    at async Promise.allSettled (index 2)"""


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="pb_test_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)

    def puppeteer(self, package=PKG, bin_field="./lib/puppeteer/node/cli.js", name="puppeteer"):
        folder = self.root / package / "node_modules" / name
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"name": name, "version": "25.12.0", "bin": bin_field}))
        if isinstance(bin_field, dict):
            bin_field = next(iter(bin_field.values()))
        cli = folder / bin_field
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("// the browsers command\n")
        return folder


class PackageNameTests(unittest.TestCase):
    def test_a_version_is_dropped_and_a_scope_kept(self):
        for spec, name in (("@mermaid-js/mermaid-cli@11.17.0", PKG), (PKG, PKG), ("surge@^0.44", "surge"),
                           ("surge", "surge")):
            with self.subTest(spec):
                self.assertEqual(pb.package_name(spec), name)


class BundledPuppeteerTests(TempRoot):
    def test_found_where_npm_puts_a_package_own_dependency(self):
        folder = self.puppeteer()
        self.assertEqual(pb.bundled_puppeteer(PKG, self.root), folder)
        self.assertEqual(pb.bundled_puppeteer(f"{PKG}@12.0.0", self.root), folder)

    def test_puppeteer_core_downloads_nothing_so_it_does_not_count(self):
        self.puppeteer(package="lighthouse", name="puppeteer-core")
        self.assertIsNone(pb.bundled_puppeteer("lighthouse", self.root))

    def test_a_package_without_it_or_without_npm_is_none(self):
        self.assertIsNone(pb.bundled_puppeteer("surge", self.root))
        with patch.object(pb, "npm_global_root", return_value=None):
            self.assertIsNone(pb.bundled_puppeteer(PKG))


class NpmGlobalRootTests(unittest.TestCase):
    def test_a_prefix_is_passed_through(self):
        done = subprocess.CompletedProcess([], 0, "/tmp/trial/lib/node_modules\n", "")
        with patch.object(pb.shutil, "which", return_value="/usr/bin/npm"), \
                patch.object(pb.subprocess, "run", return_value=done) as run:
            self.assertEqual(pb.npm_global_root(Path("/tmp/trial")), Path("/tmp/trial/lib/node_modules"))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/npm", "root", "-g", "--prefix", "/tmp/trial"])

    def test_no_npm_or_a_failed_npm_is_none(self):
        with patch.object(pb.shutil, "which", return_value=None):
            self.assertIsNone(pb.npm_global_root())
        with patch.object(pb.shutil, "which", return_value="/usr/bin/npm"), \
                patch.object(pb.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "boom")):
            self.assertIsNone(pb.npm_global_root())


class EnsureBrowsersTests(TempRoot):
    def ensure(self, result=None, side_effect=None, node="/usr/bin/node"):
        with patch.object(pb.shutil, "which", return_value=node), \
                patch.object(pb.subprocess, "run", return_value=result, side_effect=side_effect) as run:
            outcome = pb.ensure_browsers(PKG, root=self.root)
        return outcome, run

    def test_runs_the_package_own_puppeteer_cli_as_this_user(self):
        folder = self.puppeteer()
        line = f"chrome-headless-shell@154.0.8037.57 {self.root}/cache/chrome-headless-shell"
        (ok, detail), run = self.ensure(subprocess.CompletedProcess([], 0, line, ""))
        self.assertTrue(ok)
        self.assertIn("chrome-headless-shell@154.0.8037.57", detail)
        cmd = run.call_args.args[0]
        # no browser named: the same set the install script fetches; no sudo
        self.assertEqual(cmd, ["/usr/bin/node", str(folder / "lib/puppeteer/node/cli.js"), "browsers", "install"])
        self.assertEqual(run.call_args.kwargs["cwd"], folder)

    def test_a_bin_given_as_a_map_is_read_too(self):
        folder = self.puppeteer(bin_field={"puppeteer": "./bin/cli.js"})
        (ok, _), run = self.ensure(subprocess.CompletedProcess([], 0, "", ""))
        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0][1], str(folder / "bin/cli.js"))

    def test_a_failed_download_is_a_failure_with_the_error_not_the_stack(self):
        self.puppeteer()
        (ok, detail), _ = self.ensure(subprocess.CompletedProcess([], 1, "", FAILED_OUTPUT))
        self.assertFalse(ok)
        self.assertIn("All providers failed for chrome-headless-shell 154.0.8037.57", detail)
        self.assertIn("EACCES: permission denied", detail)
        self.assertNotIn("CLI.js:181", detail)
        self.assertNotIn("Show version number", detail)

    def test_a_download_that_hangs_is_a_failure(self):
        self.puppeteer()
        (ok, detail), _ = self.ensure(side_effect=subprocess.TimeoutExpired(["node"], 900))
        self.assertFalse(ok)
        self.assertIn("did not finish", detail)

    def test_nothing_to_do_without_puppeteer(self):
        (ok, detail), run = self.ensure()
        self.assertEqual((ok, detail), (True, ""))
        run.assert_not_called()

    def test_no_node_or_no_cli_is_a_failure_not_a_pass(self):
        self.puppeteer()
        (ok, detail), run = self.ensure(node=None)
        self.assertFalse(ok)
        self.assertIn("node", detail)
        run.assert_not_called()
        (self.root / PKG / "node_modules" / "puppeteer" / "lib/puppeteer/node/cli.js").unlink()
        (ok, detail), run = self.ensure()
        self.assertFalse(ok)
        self.assertIn("no browsers command", detail)
        run.assert_not_called()


class ErrorLinesTests(unittest.TestCase):
    def test_the_error_and_its_reason_without_usage_or_stack(self):
        self.assertEqual(
            pb.error_lines(FAILED_OUTPUT),
            "Error: All providers failed for chrome-headless-shell 154.0.8037.57: - DefaultProvider: EACCES: "
            "permission denied, open '/cache/chrome-headless-shell/154.zip'")

    def test_output_with_no_error_line_falls_back_to_its_tail(self):
        self.assertEqual(pb.error_lines("x" * 400), "x" * 300)


class FirstInstallTests(unittest.TestCase):
    """core.self_install: after `npm install -g` the browser is fetched again,
    as the bot's user. On Linux the npm step runs under sudo; the browser must
    not, or it lands in root's cache again."""

    def install(self, linux: bool, ensure=(True, "")):
        from core import self_install as si
        skill = Path(tempfile.mkdtemp(prefix="pb_skill_"))
        self.addCleanup(shutil.rmtree, skill, True)
        (skill / "deps.json").write_text(json.dumps({"npm": [PKG]}))
        done = subprocess.CompletedProcess([], 0, "", "")
        si._verified_cache.discard(skill.name)
        with patch.object(si, "check_skill_deps", return_value=[f"npm:{PKG}"]), \
                patch.object(si, "check_resource_requirements", return_value=None), \
                patch.object(si, "get_sudo_password", return_value="pw"), \
                patch.object(si, "_is_linux", return_value=linux), \
                patch.object(si, "_sudo_run", return_value=done) as sudo, \
                patch.object(si, "_run", return_value=done) as run, \
                patch.object(pb, "ensure_browsers", return_value=ensure) as ens:
            outcome = si.install_missing(skill)
        return outcome, sudo, run, ens

    def test_linux_installs_with_sudo_and_fetches_the_browser_without_it(self):
        (ok, installed), sudo, run, ens = self.install(linux=True)
        self.assertTrue(ok)
        self.assertEqual(installed, [PKG])
        self.assertEqual([c.args[0] for c in sudo.call_args_list], [f"npm install -g {PKG}"])
        run.assert_not_called()
        ens.assert_called_once_with(PKG)

    def test_the_mac_fetches_it_the_same_way(self):
        (ok, _), sudo, run, ens = self.install(linux=False)
        self.assertTrue(ok)
        sudo.assert_not_called()
        self.assertEqual([c.args[0] for c in run.call_args_list], [f"npm install -g {PKG}"])
        ens.assert_called_once_with(PKG)

    def test_a_browser_that_did_not_arrive_fails_the_install(self):
        (ok, _), _, _, _ = self.install(linux=True, ensure=(False, "the browser download failed: EACCES"))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
