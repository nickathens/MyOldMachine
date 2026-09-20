#!/usr/bin/env python3
"""Guards for the archify port inside the diagram skill.

archify is a vendored third party renderer (skills/diagram/archify, MIT) with
a first party wrapper in front of it. Each test here is anchored on something
measured while porting, not on a hypothetical: the update checker is a live
network call unless one variable is set, Chrome on this machine aborts without
the sandbox flag, the upstream `examples` command writes 4 MB of rendered
pages back into the vendored tree, and a failed delivery leaves the previous
file in place, so a stale artifact can pass for a fresh one.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "diagram"
VENDOR = SKILL / "archify"
WRAPPER = SKILL / "scripts" / "archify.py"
SKILL_MD = SKILL / "SKILL.md"
UPSTREAM_COMMIT = "72c750bb070d95171dbb2244e5b62b1b7da69c12"

# The example each type's doctor check names, all JSON, no rendered page.
EXAMPLES = {
    "architecture": "web-app.architecture.json",
    "workflow": "agent-tool-call.workflow.json",
    "sequence": "cache-miss-request.sequence.json",
    "dataflow": "product-analytics.dataflow.json",
    "lifecycle": "agent-run.lifecycle.json",
}

sys.path.insert(0, str(REPO))


def load_wrapper():
    spec = importlib.util.spec_from_file_location("diagram_archify", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_wrapper(*args, env=None, timeout=180):
    return subprocess.run(
        [sys.executable, str(WRAPPER), *map(str, args)],
        capture_output=True, text=True, env=env, timeout=timeout, cwd=REPO,
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def broken_copy(example: Path, into: Path) -> Path:
    """The example with one unknown field, which the strict schema rejects."""
    spec = json.loads(example.read_text(encoding="utf-8"))
    spec["components"][0]["colour"] = "red"
    target = into / "broken.json"
    target.write_text(json.dumps(spec), encoding="utf-8")
    return target


class VendoredTreeTests(unittest.TestCase):
    def test_pin_and_licences_travel(self):
        vendor = (VENDOR / "VENDOR.md").read_text(errors="replace")
        self.assertIn("github.com/tt-a1i/archify", vendor)
        self.assertIn(f"Commit: {UPSTREAM_COMMIT}", vendor)
        licence = (VENDOR / "LICENSE").read_text(errors="replace")
        for token in ("MIT License", "Permission is hereby granted", "WITHOUT WARRANTY OF ANY KIND",
                      "tt-a1i", "Cocoon AI"):
            self.assertIn(token, licence, f"LICENSE lost {token!r}")
        notices = (VENDOR / "THIRD_PARTY_NOTICES.md").read_text(errors="replace")
        for token in ("Simple Icons", "JetBrains Mono"):
            self.assertIn(token, notices, f"THIRD_PARTY_NOTICES.md lost {token!r}")
        ofl = (VENDOR / "assets" / "JetBrainsMono-OFL.txt").read_text(errors="replace")
        self.assertIn("SIL OPEN FONT LICENSE", ofl)

    def test_no_rendered_example_pages_in_the_tree(self):
        """Upstream `examples` writes five 800 KB pages here; they never belong in git."""
        pages = sorted(p.name for p in (VENDOR / "examples").glob("*.html"))
        self.assertEqual(pages, [], "rendered example pages crept into the vendored tree")
        self.assertEqual(
            sorted(EXAMPLES.values()),
            sorted(name for name in EXAMPLES.values() if (VENDOR / "examples" / name).is_file()),
            "an example the doctor check names is missing",
        )

    def test_every_vendored_file_is_tracked_by_git(self):
        """On disk is not in the repo: an unanchored ignore rule drops files silently."""
        proc = subprocess.run(
            ["git", "ls-files", "--", str(VENDOR.relative_to(REPO))],
            cwd=REPO, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            self.skipTest("not a git working tree")
        tracked = {line for line in proc.stdout.splitlines() if line}
        on_disk = {str(p.relative_to(REPO)) for p in VENDOR.rglob("*") if p.is_file()}
        self.assertEqual(on_disk - tracked, set(), "vendored files exist on disk that git does not carry")
        self.assertGreater(len(tracked), 70, "the vendored tree looks truncated")

    def test_upstream_switches_the_wrapper_relies_on_still_exist(self):
        """A re-pull can rename any of these; the wrapper would then silently do nothing."""
        checker = (VENDOR / "scripts" / "check-update.mjs").read_text(errors="replace")
        self.assertIn("ARCHIFY_UPDATE_CHECK_DISABLED", checker)
        visual = (VENDOR / "bin" / "visual-check.mjs").read_text(errors="replace")
        self.assertIn("ARCHIFY_CHROME'", visual)
        self.assertIn("ARCHIFY_CHROME_NO_SANDBOX", visual)
        self.assertIn("${base}.${width}x${height}.${theme}.png", visual)
        self.assertIn(".visual-check", visual)

    def test_doctor_is_green(self):
        proc = run_wrapper("doctor")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Archify is ready", proc.stdout)
        for bad in ("[missing]", "[invalid]", "[unsupported]"):
            self.assertNotIn(bad, proc.stdout)


class WrapperEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = load_wrapper()

    def test_update_checker_is_always_off(self):
        self.assertEqual(self.w.build_env({}, "linux")["ARCHIFY_UPDATE_CHECK_DISABLED"], "1")
        forced = self.w.build_env({"ARCHIFY_UPDATE_CHECK_DISABLED": "0"}, "linux")
        self.assertEqual(forced["ARCHIFY_UPDATE_CHECK_DISABLED"], "1", "an inherited 0 re-enabled the network call")

    def test_linux_gets_the_sandbox_opt_out_and_can_refuse_it(self):
        self.assertEqual(self.w.build_env({}, "linux")["ARCHIFY_CHROME_NO_SANDBOX"], "1")
        self.assertEqual(self.w.build_env({"ARCHIFY_CHROME_NO_SANDBOX": "0"}, "linux")["ARCHIFY_CHROME_NO_SANDBOX"], "0")
        self.assertNotIn("ARCHIFY_CHROME_NO_SANDBOX", self.w.build_env({}, "darwin"))

    def _fake_cache(self, root: Path, family: str, build: str, tail: tuple, executable=True) -> Path:
        exe = root / family / build
        for part in tail:
            exe = exe / part
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\n")
        if executable:
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        return exe

    def test_chrome_discovery_prefers_the_newest_full_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "puppeteer"
            full = ("chrome-linux64", "chrome")
            shell = ("chrome-headless-shell-linux64", "chrome-headless-shell")
            self._fake_cache(cache, "chrome", "linux-148.0.7778.97", full)
            self._fake_cache(cache, "chrome", "linux-152.0.7977.54", full)
            # Numerically newest, lexically smaller than 152: a string sort picks wrong.
            newest = self._fake_cache(cache, "chrome", "linux-1000.0.0.0", full)
            # Newer still, but not executable: a half finished download.
            self._fake_cache(cache, "chrome", "linux-2000.0.0.0", full, executable=False)
            # The shell is newer than every full build and must still lose.
            self._fake_cache(cache, "chrome-headless-shell", "linux-3000.0.0.0", shell)
            env = {"PUPPETEER_CACHE_DIR": str(cache), "PATH": ""}
            self.assertEqual(self.w.find_chrome(env, "linux"), newest)

    def test_chrome_discovery_falls_back_to_the_headless_shell_then_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "puppeteer"
            shell = self._fake_cache(
                cache, "chrome-headless-shell", "linux-152.0.7977.54",
                ("chrome-headless-shell-linux64", "chrome-headless-shell"),
            )
            env = {"PUPPETEER_CACHE_DIR": str(cache), "PATH": ""}
            self.assertEqual(self.w.find_chrome(env, "linux"), shell)

            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            on_path = bindir / "chromium"
            on_path.write_text("#!/bin/sh\n")
            on_path.chmod(0o755)
            env = {"PUPPETEER_CACHE_DIR": str(Path(tmp) / "empty"), "PATH": str(bindir)}
            self.assertEqual(self.w.find_chrome(env, "linux"), on_path)

            env = {"PUPPETEER_CACHE_DIR": str(Path(tmp) / "empty"), "PATH": ""}
            self.assertIsNone(self.w.find_chrome(env, "linux"))

    def test_explicit_chrome_wins_and_other_platforms_defer_to_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "puppeteer"
            self._fake_cache(cache, "chrome", "linux-152.0.7977.54", ("chrome-linux64", "chrome"))
            env = {"PUPPETEER_CACHE_DIR": str(cache), "ARCHIFY_CHROME": "/opt/mine/chrome", "PATH": ""}
            self.assertEqual(self.w.find_chrome(env, "linux"), Path("/opt/mine/chrome"))
            env = {"PUPPETEER_CACHE_DIR": str(cache), "PATH": ""}
            self.assertIsNone(self.w.find_chrome(env, "darwin"))
            built = self.w.build_env(env, "darwin")
            self.assertNotIn("ARCHIFY_CHROME", built)

    def test_cache_dir_follows_home_when_not_overridden(self):
        self.assertEqual(
            self.w.puppeteer_cache_dir({"HOME": "/srv/bot"}),
            Path("/srv/bot/.cache/puppeteer"),
        )
        self.assertEqual(
            self.w.puppeteer_cache_dir({"HOME": "/srv/bot", "PUPPETEER_CACHE_DIR": "/var/cache/pp"}),
            Path("/var/cache/pp"),
        )

    def test_credential_shaped_variables_never_reach_node(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "t", "GH_TOKEN": "g", "OPENAI_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "s",
            "DB_PASSWORD": "p", "PATH": "/usr/bin", "HOME": "/h", "PUPPETEER_CACHE_DIR": "/c", "XDG_CACHE_HOME": "/x",
        }
        built = self.w.build_env(env, "linux")
        for name in ("TELEGRAM_BOT_TOKEN", "GH_TOKEN", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD"):
            self.assertNotIn(name, built, f"{name} leaked into the archify environment")
        for name in ("PATH", "HOME", "PUPPETEER_CACHE_DIR", "XDG_CACHE_HOME"):
            self.assertEqual(built[name], env[name])

    def test_run_refuses_the_tree_writing_examples_command(self):
        # If the refusal ever breaks, upstream renders five pages into the
        # vendored tree during this very test. Remove what this test caused,
        # and only that, so one red run does not poison the next.
        before = set((VENDOR / "examples").glob("*.html"))
        self.addCleanup(lambda: [p.unlink() for p in set((VENDOR / "examples").glob("*.html")) - before])
        proc = run_wrapper("run", "examples")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("demo", proc.stderr)
        self.assertEqual(sorted(p.name for p in (VENDOR / "examples").glob("*.html")), [])

    def test_a_hung_node_is_cut_off_by_the_timeout(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "node"
            fake.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
            fake.chmod(0o755)
            path = f"{tmp}:/usr/bin:/bin"  # the fake node first, then a real sleep
            with mock.patch.dict(os.environ, {"PATH": path}), mock.patch.object(self.w, "ARCHIFY_TIMEOUT", 1):
                import io
                import contextlib

                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = self.w.main(["doctor"])
        self.assertEqual(rc, 1)
        self.assertIn("timed out after 1 s", err.getvalue())

    def test_build_version_orders_numerically(self):
        self.assertGreater(self.w._build_version("linux-1000.0.0.0"), self.w._build_version("linux-152.0.7977.54"))
        self.assertEqual(self.w._build_version("junk"), (-1,))
        self.assertEqual(self.w._build_version("linux-1.2.x"), (-1,))


class DeliveryTests(unittest.TestCase):
    """Node only; no browser is started here."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="archify-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_every_example_type_delivers_a_self_contained_page(self):
        for kind, name in EXAMPLES.items():
            with self.subTest(type=kind):
                out = self.tmp / f"{kind}.html"
                proc = run_wrapper("deliver", kind, VENDOR / "examples" / name, "-o", out)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn(f"Wrote {out} ({kind}, showcase 9/9 checks, 0 errors, 0 warnings", proc.stdout)
                html = out.read_text(encoding="utf-8")
                self.assertGreater(len(html), 500_000, "the viewer is not inlined")
                loads = re.findall(r"<(?:script|link|img|iframe)\b[^>]*?(?:src|href)=[\"']https?:", html)
                self.assertEqual(loads, [], "the page loads something from the network")
                self.assertNotIn("@import", html)
                self.assertNotIn("url(http", html)
                hosts = set(re.findall(r"https://([a-z0-9.-]+)", html))
                self.assertLessEqual(
                    hosts, {"github.com", "openfontlicense.org"},
                    "a new outbound reference appeared in the delivered page",
                )

    def test_a_failed_spec_writes_nothing_and_names_the_previous_file(self):
        out = self.tmp / "map.html"
        good = run_wrapper("deliver", "architecture", VENDOR / "examples" / EXAMPLES["architecture"], "-o", out)
        self.assertEqual(good.returncode, 0, good.stderr)
        before = sha256(out)

        broken = broken_copy(VENDOR / "examples" / EXAMPLES["architecture"], self.tmp)
        proc = run_wrapper("deliver", "architecture", broken, "-o", out)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("deliver failed", proc.stderr)
        self.assertIn("colour", proc.stderr, "the diagnostic does not name the offending field")
        self.assertIn("PREVIOUS delivery", proc.stderr)
        self.assertEqual(sha256(out), before, "a failed delivery touched the previous file")

        fresh = self.tmp / "fresh.html"
        proc = run_wrapper("deliver", "architecture", broken, "-o", fresh)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(fresh.exists(), "a failed delivery wrote a file")
        self.assertNotIn("PREVIOUS delivery", proc.stderr)

    def test_validate_reports_the_diagnostic_without_writing(self):
        broken = broken_copy(VENDOR / "examples" / EXAMPLES["architecture"], self.tmp)
        proc = run_wrapper("validate", "architecture", broken)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("colour", proc.stdout + proc.stderr)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["broken.json"])

        good = run_wrapper("validate", "architecture", VENDOR / "examples" / EXAMPLES["architecture"], "--json")
        self.assertEqual(good.returncode, 0, good.stderr)
        receipt = json.loads(good.stdout)
        self.assertTrue(receipt.get("ok"))
        self.assertEqual(len(receipt["checks"]), 9, "validate no longer reports the nine artifact checks")
        self.assertTrue(all(check["ok"] for check in receipt["checks"]))
        self.assertEqual(receipt["composition"]["status"], "pass")

    def test_the_update_checker_is_silenced_by_the_wrapper_env(self):
        """Delivery never runs the checker; upstream's SKILL.md tells the agent to.

        So the guard has to run the checker itself. Under the wrapper's
        environment it must answer "disabled" and write no reminder state.
        The control runs the same checker without the switch, with fetch
        replaced by a stub that throws, so no real network is touched: it
        must NOT answer "disabled", and it must write its failure state to
        the cache. That is the difference the one variable makes. (Editing
        the manifest URL is not a usable control: the checker pins it to a
        constant and answers invalid-local-release.)
        """
        w = load_wrapper()
        cache = self.tmp / "xdg-cache"
        cache.mkdir()
        offline = self.tmp / "offline.mjs"
        offline.write_text("globalThis.fetch = async () => { throw new Error('offline by test'); };\n")
        checker = VENDOR / "scripts" / "check-update.mjs"
        base = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.tmp),
            "XDG_CACHE_HOME": str(cache),
            "NODE_OPTIONS": f"--import={offline}",
        }

        env = w.build_env(base)
        proc = subprocess.run([w.node_binary(), str(checker)], env=env, capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"status": "silent", "reason": "disabled"})
        self.assertEqual(sorted(p.name for p in cache.iterdir()), [], "the disabled checker still wrote state")

        control_env = {k: v for k, v in env.items() if k != "ARCHIFY_UPDATE_CHECK_DISABLED"}
        proc = subprocess.run([w.node_binary(), str(checker)], env=control_env, capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        verdict = json.loads(proc.stdout)
        self.assertNotEqual(verdict.get("reason"), "disabled", "the control did not exercise the switch")
        self.assertTrue((cache / "archify-skill").is_dir(), "without the switch the checker should have written state")

    def test_output_must_be_html_and_spec_must_exist(self):
        proc = run_wrapper("deliver", "architecture", VENDOR / "examples" / EXAMPLES["architecture"], "-o", self.tmp / "map.htm")
        self.assertEqual(proc.returncode, 2)
        self.assertIn(".html", proc.stderr)
        proc = run_wrapper("deliver", "architecture", self.tmp / "nope.json", "-o", self.tmp / "map.html")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Spec not found", proc.stderr)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [])


class PreviewTests(unittest.TestCase):
    """Starts Chrome through archify's own browser check."""

    @classmethod
    def setUpClass(cls):
        cls.w = load_wrapper()
        if cls.w.find_chrome() is None:
            raise unittest.SkipTest("no Chrome or Chromium on this machine; preview cannot be measured")
        cls.tmp = Path(tempfile.mkdtemp(prefix="archify-preview-test-"))
        cls.html = cls.tmp / "map.html"
        cls.png = cls.tmp / "map.png"
        cls.proc = run_wrapper(
            "deliver", "architecture", VENDOR / "examples" / EXAMPLES["architecture"],
            "-o", cls.html, "--preview", cls.png, "--theme", "dark",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _mean_luminance(png: Path) -> float:
        from PIL import Image, ImageStat

        with Image.open(png) as im:
            return ImageStat.Stat(im.convert("L")).mean[0], im.size

    def test_dark_preview_is_dark_and_light_is_the_control(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout + self.proc.stderr)
        self.assertIn(f"Wrote {self.png} (1440x900, dark)", self.proc.stdout)
        dark, size = self._mean_luminance(self.png)
        self.assertEqual(size, (1440, 900))
        self.assertLess(dark, 60, "the dark preview is not dark")

        light_png = self.tmp / "light.png"
        ok, message = self.w.make_preview(self.html, light_png, "light")
        self.assertTrue(ok, message)
        light, size = self._mean_luminance(light_png)
        self.assertEqual(size, (1440, 900))
        self.assertGreater(light, 200, "the light control is not light")
        self.assertNotEqual(sha256(self.png), sha256(light_png))

    def test_preview_leaves_no_sidecars_beside_the_page(self):
        names = sorted(p.name for p in self.tmp.iterdir())
        self.assertNotIn("map.visual-check.json", names)
        self.assertEqual([n for n in names if ".visual-check." in n], [])

    def test_json_receipt_carries_the_preview(self):
        out = self.tmp / "json.html"
        png = self.tmp / "json.png"
        proc = run_wrapper(
            "deliver", "architecture", VENDOR / "examples" / EXAMPLES["architecture"],
            "-o", out, "--preview", png, "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["html"], str(out))
        self.assertTrue(payload["receipt"]["ok"])
        self.assertEqual(payload["preview"]["path"], str(png))
        self.assertTrue(payload["preview"]["ok"], payload["preview"]["message"])
        self.assertTrue(png.is_file())

    def test_missing_chrome_is_reported_as_a_skip_not_a_crash(self):
        """Exit 3: the page is written, the preview is not, and the message says why."""
        # PATH stays: node has to be found. The explicit path to a Chrome that
        # does not exist wins over every lookup, in the wrapper and upstream.
        env = {k: v for k, v in os.environ.items() if not k.startswith("ARCHIFY_")}
        env["ARCHIFY_CHROME"] = str(self.tmp / "no-such-chrome")
        out = self.tmp / "nochrome.html"
        proc = run_wrapper(
            "deliver", "architecture", VENDOR / "examples" / EXAMPLES["architecture"],
            "-o", out, "--preview", self.tmp / "nochrome.png", env=env,
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertTrue(out.is_file(), "the page must still be delivered")
        self.assertFalse((self.tmp / "nochrome.png").exists())
        self.assertIn("Preview skipped", proc.stdout)


class SkillDocTests(unittest.TestCase):
    def test_skill_md_names_the_wrapper_and_the_contract(self):
        text = SKILL_MD.read_text(errors="replace")
        for needle in ("scripts/archify.py", "archify/SKILL.md", "archify/VENDOR.md",
                       "archify/schemas/", "archify/examples/", "--preview"):
            self.assertIn(needle, text, f"SKILL.md no longer mentions {needle}")

    def test_loader_description_mentions_the_new_mode_and_fits(self):
        from core.skill_loader import SkillManager

        skill = SkillManager(REPO / "skills").get_skill("diagram")
        self.assertIsNotNone(skill)
        self.assertIn("HTML", skill.description)
        self.assertIn("archify", skill.description)
        self.assertLessEqual(len(skill.description), 200)
        # The 200 character cut must not land mid claim.
        self.assertTrue(skill.description.endswith("."), skill.description)

    def test_every_vendored_path_the_doc_names_exists(self):
        text = SKILL_MD.read_text(errors="replace")
        named = set(re.findall(r"`(archify/[A-Za-z0-9_./-]+)`", text))
        named |= set(re.findall(r"(archify/[A-Za-z0-9_./-]+\.(?:md|json|mjs))", text))
        self.assertGreaterEqual(len(named), 4, "the doc names too few vendored paths to be the real section")
        for rel in sorted(named):
            with self.subTest(path=rel):
                target = SKILL / rel.rstrip("/")
                self.assertTrue(target.exists(), f"SKILL.md names {rel}, which is not there")

    def test_no_em_or_en_dashes_in_first_party_text(self):
        for path in (WRAPPER, SKILL_MD, VENDOR / "VENDOR.md", Path(__file__)):
            with self.subTest(file=path.name):
                text = path.read_text(errors="replace")
                for ch in (chr(0x2014), chr(0x2013)):
                    self.assertNotIn(ch, text, f"{path.name} carries a dash the house style forbids")

    def test_no_donor_machine_paths_ride_along(self):
        """MOM ships to strangers: no operator name, home dir or private project."""
        markers = ("claude-telegram-bot", "/home/ntouri", "coocoo")
        files = [WRAPPER, SKILL_MD] + [p for p in VENDOR.rglob("*") if p.is_file()]
        for path in files:
            text = path.read_text(errors="replace").lower()
            for marker in markers:
                with self.subTest(file=str(path.relative_to(REPO)), marker=marker):
                    self.assertNotIn(marker, text, f"{path.relative_to(REPO)} names the donor machine")

    def test_wrapper_help_runs(self):
        proc = run_wrapper("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for sub in ("deliver", "validate", "doctor", "run"):
            self.assertIn(sub, proc.stdout)


if __name__ == "__main__":
    unittest.main()
