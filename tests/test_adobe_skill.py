"""Offline tests for the adobe skill, and for the five defects found reviewing it.

Nothing here needs Adobe, After Effects or macOS. Both scripts refuse to do
real work off Darwin, so the tests import them and drive the functions, with
the platform gate itself exercised in both directions through a patch.

The defects these lock down:

  1  README advertised 83 skills while 84 sit on disk, and adobe was in no table
  2  --threads passed -mp, the legacy multiprocessing switch, while claiming to
     turn on Multi-Frame Rendering, which is -mfr
  3  a service whose name ps truncates could never be seen as running
  4  deps.json declared brew_cask, a key nothing in the repo reads
  5  the PR described tests that were not in it

SignedInTests is the Mac side's own file, folded in here rather than left as a
second tests/test_adobe_skill.py: its cases were measured against a real Adobe
ID and this Linux machine has none, so they are not mine to re-derive.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "adobe"
SCRIPTS = SKILL / "scripts"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def load(path: Path, name: str):
    """Import a skill script by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ae = load(SCRIPTS / "ae_render.py", "adobe_ae_render")
status = load(SCRIPTS / "adobe_status.py", "adobe_status_script")


@contextlib.contextmanager
def quiet():
    """Swallow a script's own stdout/stderr so the suite stays readable."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        yield


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adobe_skill_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


# --------------------------------------------------------------------------
# 2. Multi-Frame Rendering
# --------------------------------------------------------------------------


class MultiFrameRenderingTests(TempDirCase):
    """-mfr is the flag. -mp is a different, older feature.

    Adobe's documented syntax is `-mfr mfr_flag max_cpu_percent`: ON or OFF,
    then a 1-100 ceiling that aerender ignores when the flag is OFF. The
    percentage is positional, so it goes out even with OFF; dropping it makes
    aerender read the following argument as the percentage.
    """

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "job.aep"
        self.project.write_bytes(b"\x00")
        self.aerender = self.tmp / "aerender"
        self.aerender.write_text("#!/bin/sh\n")

    def parse(self, *argv):
        return ae.build_parser().parse_args(["--project", str(self.project), *argv])

    def cmd(self, *argv):
        return ae.build_command(self.aerender, self.parse(*argv))

    def test_mfr_on_emits_the_documented_three_token_form(self):
        self.assertEqual(self.cmd("--mfr", "on")[-3:], ["-mfr", "ON", "100"])

    def test_mfr_on_carries_the_requested_cpu_ceiling(self):
        self.assertEqual(self.cmd("--mfr", "on", "--max-cpu", "50")[-3:],
                         ["-mfr", "ON", "50"])

    def test_mfr_off_still_carries_a_percentage(self):
        # aerender ignores the value with OFF, but the slot is positional.
        self.assertEqual(self.cmd("--mfr", "off")[-3:], ["-mfr", "OFF", "100"])

    def test_mp_is_never_emitted(self):
        # The regression. -mp is "Render Multiple Frames Simultaneously", the
        # pre-2022 multiprocessing switch, and it does not enable MFR.
        for argv in ([], ["--mfr", "on"], ["--mfr", "off"],
                     ["--mfr", "on", "--max-cpu", "1"]):
            self.assertNotIn("-mp", self.cmd(*argv), argv)

    def test_no_mfr_flag_leaves_aerender_alone(self):
        self.assertNotIn("-mfr", self.cmd())

    def test_threads_option_is_gone(self):
        # The old name promised MFR and delivered -mp. Fail loudly rather than
        # silently accept it again.
        with self.assertRaises(SystemExit), quiet():
            self.parse("--threads")

    def test_mfr_rejects_anything_but_on_or_off(self):
        with self.assertRaises(SystemExit), quiet():
            self.parse("--mfr", "yes")

    def test_max_cpu_without_mfr_is_refused(self):
        # Alone it reaches aerender nowhere, so accepting it would be a lie.
        with self.assertRaises(SystemExit) as cm:
            ae.validate(self.parse("--max-cpu", "50"))
        self.assertIn("--mfr", str(cm.exception))

    def test_max_cpu_out_of_range_is_refused(self):
        for bad in ("0", "101", "-5"):
            with self.assertRaises(SystemExit) as cm:
                ae.validate(self.parse("--mfr", "on", "--max-cpu", bad))
            self.assertIn("between 1 and 100", str(cm.exception))

    def test_max_cpu_at_the_edges_is_accepted(self):
        for good in ("1", "100"):
            ae.validate(self.parse("--mfr", "on", "--max-cpu", good))


# --------------------------------------------------------------------------
# ae_render: the validation and discovery the PR described
# --------------------------------------------------------------------------


class AeRenderValidationTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.project = self.tmp / "job.aep"
        self.project.write_bytes(b"\x00")

    def parse(self, project, *argv):
        return ae.build_parser().parse_args(["--project", str(project), *argv])

    def test_missing_project(self):
        with self.assertRaises(SystemExit) as cm:
            ae.validate(self.parse(self.tmp / "nope.aep"))
        self.assertIn("No project file", str(cm.exception))

    def test_wrong_suffix(self):
        other = self.tmp / "job.mov"
        other.write_bytes(b"\x00")
        with self.assertRaises(SystemExit) as cm:
            ae.validate(self.parse(other))
        self.assertIn("Not an After Effects project", str(cm.exception))

    def test_aepx_is_accepted(self):
        other = self.tmp / "job.aepx"
        other.write_bytes(b"\x00")
        ae.validate(self.parse(other))

    def test_missing_output_folder(self):
        with self.assertRaises(SystemExit) as cm:
            ae.validate(self.parse(self.project, "--comp", "Main",
                                   "--out", str(self.tmp / "gone" / "o.mov")))
        self.assertIn("Output folder does not exist", str(cm.exception))

    def test_end_before_start(self):
        with self.assertRaises(SystemExit) as cm:
            ae.validate(self.parse(self.project, "--start", "50", "--end", "10"))
        self.assertIn("before start frame", str(cm.exception))

    def test_out_without_comp(self):
        with self.assertRaises(SystemExit) as cm:
            ae.validate(self.parse(self.project, "--out", str(self.tmp / "o.mov")))
        self.assertIn("--out needs --comp", str(cm.exception))

    def test_full_command_shape(self):
        args = self.parse(self.project, "--comp", "Main", "--out",
                          str(self.tmp / "o.mov"), "--start", "0", "--end", "99",
                          "--rs-template", "Best Settings",
                          "--om-template", "Lossless")
        cmd = ae.build_command(Path("/x/aerender"), args)
        self.assertEqual(cmd[:2], ["/x/aerender", "-project"])
        for flag, value in (("-comp", "Main"), ("-s", "0"), ("-e", "99"),
                            ("-RStemplate", "Best Settings"),
                            ("-OMtemplate", "Lossless")):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)


class AeRenderDiscoveryTests(TempDirCase):
    def test_explicit_path_must_be_a_file(self):
        with self.assertRaises(SystemExit) as cm:
            ae.find_aerender(str(self.tmp / "nope"))
        self.assertIn("aerender not found", str(cm.exception))

    def test_explicit_path_wins(self):
        binary = self.tmp / "aerender"
        binary.write_text("#!/bin/sh\n")
        self.assertEqual(ae.find_aerender(str(binary)), binary)

    def test_newest_after_effects_wins(self):
        for year in ("2024", "2026", "2025"):
            folder = self.tmp / f"Adobe After Effects {year}"
            folder.mkdir()
            (folder / "aerender").write_text("#!/bin/sh\n")
        with mock.patch.object(ae, "APPS_DIR", self.tmp), \
                mock.patch.object(ae.shutil, "which", return_value=None):
            self.assertEqual(ae.find_aerender().parent.name,
                             "Adobe After Effects 2026")

    def test_no_install_says_so_readably(self):
        with mock.patch.object(ae, "APPS_DIR", self.tmp), \
                mock.patch.object(ae.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                ae.find_aerender()
        self.assertIn("not installed", str(cm.exception))


class PlatformGateTests(TempDirCase):
    """Both scripts are macOS only and must say so rather than half-run."""

    def test_ae_render_refuses_off_darwin(self):
        project = self.tmp / "job.aep"
        project.write_bytes(b"\x00")
        argv = ["ae_render.py", "--project", str(project)]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "platform", "linux"), quiet():
            self.assertEqual(ae.main(), 2)

    def test_ae_render_runs_on_darwin(self):
        project = self.tmp / "job.aep"
        project.write_bytes(b"\x00")
        binary = self.tmp / "aerender"
        binary.write_text("#!/bin/sh\n")
        argv = ["ae_render.py", "--project", str(project), "--comp", "Main",
                "--aerender", str(binary), "--mfr", "on", "--dry-run"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "platform", "darwin"), quiet():
            self.assertEqual(ae.main(), 0)

    def test_status_refuses_off_darwin(self):
        with mock.patch.object(sys, "argv", ["adobe_status.py"]), \
                mock.patch.object(sys, "platform", "linux"), quiet():
            self.assertEqual(status.main(), 2)


# --------------------------------------------------------------------------
# 3. ps truncates the process name
# --------------------------------------------------------------------------


class ServiceDetectionTests(unittest.TestCase):
    """`ps -c` prints the accounting name. Linux caps it at 15 bytes, so "Adobe
    Desktop Service", 21 characters, came back as "Adobe Desktop S" and an
    exact comparison never matched it: the report claimed no Adobe services
    were running while the desktop backend was running.

    Reproduced with a real process of that name on Linux: `ps axco command`
    returned "Adobe Desktop S", 15 characters. Not reproducible on macOS, which
    declares MAXCOMLEN 16 but whose `ps` prints the name whole (measured on
    26.6.2: a 56-character line, and every service matched exactly). These are
    matcher tests, so they feed both cut lengths regardless of the host.
    """

    def test_linux_truncation_is_detected(self):
        self.assertTrue(
            status.service_running("Adobe Desktop Service", {"Adobe Desktop S"})
        )

    def test_sixteen_byte_truncation_is_detected(self):
        self.assertTrue(
            status.service_running("Adobe Desktop Service", {"Adobe Desktop Se"})
        )

    def test_exact_name_still_matches(self):
        self.assertTrue(status.service_running("CCXProcess", {"CCXProcess"}))

    def test_short_unrelated_process_does_not_match(self):
        # "Core" must not be read as "Core Sync" running. The length floor is
        # what stops it: only a line long enough to have been truncated can
        # prefix-match.
        for line in ("Core", "Core Syn", "Adobe", "A"):
            self.assertFalse(status.service_running("Core Sync", {line}), line)
            self.assertFalse(
                status.service_running("Adobe Desktop Service", {line}), line
            )

    def test_nothing_running_is_still_nothing(self):
        for name, _ in status.SERVICES:
            self.assertFalse(status.service_running(name, {"bash", "python3"}))

    def test_every_service_name_survives_its_own_truncation(self):
        # Whatever names the list grows, each must be findable after a
        # kernel has cut it to 15 or 16 bytes.
        for name, _ in status.SERVICES:
            for cap in (15, 16):
                self.assertTrue(status.service_running(name, {name[:cap]}),
                                f"{name} at {cap}")

    def test_a_failed_ps_reports_nothing_running_rather_than_crashing(self):
        with mock.patch.object(status.subprocess, "run",
                               side_effect=OSError("no ps")):
            self.assertEqual([r for _, _, r in status.running_services()],
                             [False] * len(status.SERVICES))


# --------------------------------------------------------------------------
# adobe_status: the sign-in detector
# --------------------------------------------------------------------------
#
# These cases are the Mac side's, measured on a real machine on 11 Sep 2026
# with a real Adobe ID signed in, and they replace the guessed markers the
# original probe read. The stale-marker case is the one that matters: Adobe
# writes logged_out_guid before the first sign-in and does NOT delete it
# afterwards, so reading it as authoritative reported "cannot install" on a
# machine that could. Both that and the first draft's non-empty-directory
# test are the same shape: a presence test over a path something else also
# writes is not a state test. The markers here are keyed by the ACCOUNT ID,
# which is what makes them state.

# A real account id as Adobe writes it: a long uppercase hex string.
ACCOUNT = "AC6680FC6A7B6F390A495F8E"


def load_probe():
    spec = importlib.util.spec_from_file_location(
        "adobe_status_probe", SCRIPTS / "adobe_status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SignedInTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_probe()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.oobe = Path(tmp.name) / "OOBE"
        self.mod.OOBE = self.oobe
        # Isolate the on-disk markers from the live machine's keychain.
        self.mod._keychain_user_info = lambda: False

    def write_installer_scratch(self):
        """Exactly what the installer leaves before anyone has signed in."""
        self.oobe.mkdir(parents=True, exist_ok=True)
        (self.oobe / "filesync.db").write_text("x")
        (self.oobe / "temp_lbs_wid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        (self.oobe / "com.adobe.acc.container.default.prefs").write_text("<prefs/>")
        (self.oobe / "com.adobe.accc.apps.default.prefs").write_text("<prefs/>")

    def write_signed_in(self):
        """The account-keyed artifacts that appear only after a real sign-in."""
        cache = self.oobe / "com.adobe.accc.apps/products" / f"{ACCOUNT}@AdobeID"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "_data").write_text("<channels/>")
        (self.oobe / f"com.adobe.acc.onboarding.{ACCOUNT}.prefs").write_text("<prefs/>")

    def test_no_adobe_directory_at_all(self):
        self.assertIs(self.mod.signed_in(), False)

    def test_installer_scratch_does_not_read_as_signed_in(self):
        self.write_installer_scratch()
        self.assertIsNone(
            self.mod.signed_in(),
            "installer scratch must never be mistaken for an account",
        )

    def test_never_signed_in_is_reported_false(self):
        self.write_installer_scratch()
        (self.oobe / "logged_out_guid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        self.assertIs(self.mod.signed_in(), False)

    def test_stale_logged_out_marker_does_not_beat_a_real_sign_in(self):
        """Adobe leaves logged_out_guid in place after sign-in. It is not a state."""
        self.write_installer_scratch()
        (self.oobe / "logged_out_guid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        self.write_signed_in()
        self.assertIs(self.mod.signed_in(), True)

    def test_signed_in_without_the_stale_marker(self):
        self.write_installer_scratch()
        self.write_signed_in()
        self.assertIs(self.mod.signed_in(), True)

    def test_entitlement_cache_alone_is_sufficient(self):
        (self.oobe / "com.adobe.accc.apps/products" / f"{ACCOUNT}@AdobeID").mkdir(parents=True)
        self.assertIs(self.mod.signed_in(), True)

    def test_account_scoped_prefs_alone_are_sufficient(self):
        self.oobe.mkdir(parents=True)
        (self.oobe / f"com.adobe.acc.container.{ACCOUNT}.prefs").write_text("<prefs/>")
        self.assertIs(self.mod.signed_in(), True)

    def test_default_suffixed_prefs_are_not_account_scoped(self):
        """The discriminator is the account id, not the filename prefix."""
        self.oobe.mkdir(parents=True)
        (self.oobe / "com.adobe.acc.container.default.prefs").write_text("<prefs/>")
        (self.oobe / "com.adobe.acc.fonts.default.prefs").write_text("<prefs/>")
        self.assertIsNone(self.mod.signed_in())

    def test_keychain_alone_is_sufficient(self):
        """Second instrument: disk markers absent, keychain item present."""
        self.write_installer_scratch()
        (self.oobe / "logged_out_guid").write_text("{28628304-0B7C-4156-80ED-1316BBA5B2C9}")
        self.mod._keychain_user_info = lambda: True
        self.assertIs(self.mod.signed_in(), True)


# --------------------------------------------------------------------------
# 4. the manifest declared a key nothing reads
# --------------------------------------------------------------------------


class DepsManifestTests(unittest.TestCase):
    def setUp(self):
        self.deps = json.loads((SKILL / "deps.json").read_text())

    def test_every_key_is_one_the_repo_actually_reads(self):
        from core import self_install

        consumed = set(self_install._PKG_KEYS) | {
            "check", "pip", "npm", "post_install",
            "weight", "min_ram_gb", "min_disk_gb", "install_note",
        }
        unread = set(self.deps) - consumed
        self.assertEqual(unread, set(),
                         f"deps.json declares keys nothing reads: {sorted(unread)}")

    def test_brew_cask_is_gone(self):
        # It was the only brew_cask in 84 manifests and no loader looked at it,
        # so it read as an install route the project does not have.
        self.assertNotIn("brew_cask", self.deps)

    def test_the_manifest_passes_the_repo_validator(self):
        from core import self_install

        self.assertTrue(self_install._validate_deps(self.deps, "adobe"))

    def test_the_cask_command_survives_where_it_is_actually_shown(self):
        # The route is real, it just belongs in the note the skill listing
        # prints rather than in a key the installer ignores.
        self.assertIn("brew install --cask adobe-creative-cloud",
                      self.deps["install_note"])

    def test_the_resource_gate_can_read_it(self):
        from core.self_install import get_skill_resource_info

        info = get_skill_resource_info(SKILL)
        self.assertEqual(info["weight"], "heavy")
        self.assertGreater(info["min_disk_gb"], 0)


# --------------------------------------------------------------------------
# 1. the README count
# --------------------------------------------------------------------------


class ReadmeSkillCountTests(unittest.TestCase):
    """Guards every future skill, not just this one: the count is advertised
    twice and both drifted silently when adobe was added."""

    def test_the_advertised_total_matches_the_folders_on_disk(self):
        on_disk = len(list((REPO / "skills").glob("*/SKILL.md")))
        readme = (REPO / "README.md").read_text()
        # Only the two lines that claim a TOTAL. Elsewhere the README counts
        # subsets, such as the skills carrying a hooks.json, and those are not
        # meant to track this number.
        claims = re.findall(r"\*\*(\d+) skills\*\*", readme)
        claims += re.findall(r"\|\s*\*\*Skills\*\*\s*\|\s*(\d+) skills", readme)
        self.assertEqual(len(claims), 2,
                         f"the README total-count lines moved: {claims}")
        for claim in claims:
            self.assertEqual(int(claim), on_disk)

    def test_adobe_is_in_a_skill_table(self):
        readme = (REPO / "README.md").read_text()
        self.assertRegex(readme, r"(?m)^\| adobe \|")


# --------------------------------------------------------------------------
# the skill document
# --------------------------------------------------------------------------


class SkillDocTests(unittest.TestCase):
    def setUp(self):
        self.doc = (SKILL / "SKILL.md").read_text()

    def test_the_doc_gives_the_full_mfr_syntax(self):
        self.assertIn("`-mfr mfr_flag max_cpu_percent`", self.doc)

    def test_the_doc_says_plainly_that_mp_is_not_the_flag(self):
        self.assertRegex(self.doc, r"not `-mp`")

    def test_the_doc_separates_what_was_measured_from_what_was_not(self):
        # The flag syntax started as a reading of Adobe's help text and was
        # then checked against a real aerender here, so the doc must name the
        # version it was checked against AND still admit that a completed
        # render is unproven. Either half alone is a misleading claim.
        self.assertRegex(self.doc, r"aerender\s+version 26\.5x89")
        self.assertRegex(self.doc, r"completed render is still unproven")

    def test_the_doc_does_not_advertise_a_threads_flag(self):
        self.assertNotIn("--threads", self.doc)


if __name__ == "__main__":
    unittest.main()
