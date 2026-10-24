"""The weekly Python security check (utils/python_audit.py).

Offline except one test that lists the packages of the interpreter running
the tests. OSV is faked at urlopen, the Telegram sender is a tripwire for the
whole file, and the state and log go to a scratch folder.
"""
from __future__ import annotations

import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # the job tests import bot: keep their logging out of bot.log

from utils import python_audit as pa  # noqa: E402

_SCRATCH = tempfile.TemporaryDirectory()
# The real sender, for its own tests; everywhere else it is a tripwire.
REAL_NOTIFY = pa.notify_admin


_TRIPWIRES = []


def setUpModule():
    def tripwire(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"a test reached the real {name}")
        return fail
    for owner, name, value in (
            (pa, "LOG_FILE", Path(_SCRATCH.name) / "python_audit.log"),
            (pa, "STATE_FILE", Path(_SCRATCH.name) / "unused-state.json"),
            (pa, "notify_admin", tripwire("Telegram sender")),
            (pa.urllib.request, "urlopen", tripwire("network"))):
        p = mock.patch.object(owner, name, value)
        p.start()
        _TRIPWIRES.append(p)


def tearDownModule():
    # Not addModuleCleanup: pytest does not run those between files, so the
    # tripwires would outlive this file and break the ones after it.
    while _TRIPWIRES:
        _TRIPWIRES.pop().stop()
    _SCRATCH.cleanup()


# Shapes copied from api.osv.dev answers on 2026-10-24.
BROTLI_GHSA = {"id": "GHSA-9v8h-qqgm-4m35", "aliases": ["CVE-2026-111497"],
               "database_specific": {"severity": "CRITICAL"},
               "affected": [{"package": {"ecosystem": "PyPI", "name": "brotli"},
                             "ranges": [{"type": "ECOSYSTEM", "events": [
                                 {"introduced": "1.0.3"}, {"fixed": "1.2.1"}]}]},
                            {"package": {"ecosystem": "PyPI", "name": "brotlicffi"},
                             "ranges": [{"type": "ECOSYSTEM", "events": [
                                 {"introduced": "0"}, {"fixed": "1.2.1.0"}]}]}]}
BROTLI_PYSEC = {"id": "PYSEC-2026-4322", "aliases": ["CVE-2026-111497", "GHSA-9v8h-qqgm-4m35"],
                "affected": [{"package": {"ecosystem": "PyPI", "name": "Brotli"},
                              "ranges": [{"type": "ECOSYSTEM", "events": [
                                  {"introduced": "0"}, {"fixed": "1.2.1"}]}]}]}
CERTIFI = {"id": "GHSA-6q48-6c7w-9gw2", "aliases": ["PYSEC-2026-4320"],
           "database_specific": {"severity": "CRITICAL"},
           "affected": [{"package": {"ecosystem": "PyPI", "name": "certifi"},
                         "ranges": [{"type": "ECOSYSTEM", "events": [
                             {"introduced": "2019.03.09"}, {"fixed": "2026.10.19"}]}]}]}
CERTIFI_PYSEC = {"id": "PYSEC-2026-4320", "aliases": ["GHSA-6q48-6c7w-9gw2"],
                 "affected": [{"package": {"ecosystem": "PyPI", "name": "certifi"},
                               "ranges": [{"type": "ECOSYSTEM", "events": [
                                   {"introduced": "2019.03.09"}, {"fixed": "2026.10.19"}]}]}]}
OLD_FIX = {"id": "GHSA-old", "database_specific": {"severity": "MODERATE"},
           "affected": [{"package": {"ecosystem": "PyPI", "name": "urllib3"},
                         "ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.26.5"},
                                                {"introduced": "2.0.0"}, {"fixed": "2.8.0"}]}]}]}
NO_FIX = {"id": "GHSA-nofix", "database_specific": {"severity": "HIGH"},
          "affected": [{"package": {"ecosystem": "PyPI", "name": "torch"},
                        "ranges": [{"events": [{"introduced": "0"}]}]}]}
WITHDRAWN = {"id": "GHSA-gone", "withdrawn": "2026-10-01T00:00:00Z",
             "affected": [{"package": {"ecosystem": "PyPI", "name": "idna"},
                           "ranges": [{"events": [{"introduced": "0"}, {"fixed": "9.0"}]}]}]}
# A PYSEC record names the fixing commit in a GIT range beside the release
# range: PYSEC-2021-317 for Pillow carries 9e08eb8f... and 8.3.2. Here the fix
# is committed and no release carries it yet.
COMMIT = "9e08eb8f78fdfd2f476e1b20b7cf38683754866b"
GIT_FIX = {"type": "GIT", "repo": "https://github.com/python-pillow/Pillow",
           "events": [{"introduced": "0"}, {"fixed": COMMIT}]}
PILLOW_HIGH = {"id": "GHSA-pillow", "database_specific": {"severity": "HIGH"},
               "affected": [{"package": {"ecosystem": "PyPI", "name": "pillow"},
                             "ranges": [{"type": "ECOSYSTEM", "events": [
                                 {"introduced": "0"}, {"fixed": "8.3.2"}]}]}]}
PILLOW_UNRELEASED = {"id": "PYSEC-unreleased",
                     "affected": [{"package": {"ecosystem": "PyPI", "name": "pillow"},
                                   "ranges": [GIT_FIX, {"type": "ECOSYSTEM", "events": [
                                       {"introduced": "0"}]}]}]}
DETAILS = {v["id"]: v for v in (BROTLI_GHSA, BROTLI_PYSEC, CERTIFI, CERTIFI_PYSEC, OLD_FIX,
                                 NO_FIX, WITHDRAWN, PILLOW_HIGH, PILLOW_UNRELEASED)}


class FakeOSV:
    """urlopen that answers querybatch from a table and vulns/<id> from DETAILS."""

    def __init__(self, table, pages=None, fail=False):
        self.table, self.pages, self.fail, self.calls = table, pages or {}, fail, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        if self.fail:
            raise OSError("network is unreachable")
        if url == pa.OSV_BATCH:
            body = json.loads(req.data)
            results = []
            for q in body["queries"]:
                key = (pa.normalize(q["package"]["name"]), q["version"])
                if "page_token" in q:
                    results.append({"vulns": [{"id": i} for i in self.pages[q["page_token"]]]})
                    continue
                ids = self.table.get(key, [])
                entry = {"vulns": [{"id": i} for i in ids]} if ids else {}
                if key in self.pages:
                    entry["next_page_token"] = key[0]
                results.append(entry)
            payload = {"results": results}
        else:
            payload = DETAILS.get(url.rsplit("/", 1)[1], {})

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Resp(json.dumps(payload).encode())


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = Path(self.dir.name) / "python_audit.json"
        self.packages = {"~/MyOldMachine/.venv": [("Brotli", "1.2.0"), ("urllib3", "2.6.3"),
                                                  ("certifi", "2026.7.22"), ("numpy", "2.3.5")],
                         "~/.venvs/post": [("torch", "2.6.0+cu118"), ("idna", "3.11"),
                                           ("urllib3", "2.7.0")]}
        self.table = {("brotli", "1.2.0"): ["GHSA-9v8h-qqgm-4m35", "PYSEC-2026-4322"],
                      ("certifi", "2026.7.22"): ["GHSA-6q48-6c7w-9gw2", "PYSEC-2026-4320"],
                      ("urllib3", "2.6.3"): ["GHSA-old"], ("urllib3", "2.7.0"): ["GHSA-old"],
                      ("torch", "2.6.0+cu118"): ["GHSA-nofix"], ("idna", "3.11"): ["GHSA-gone"]}
        self.osv = FakeOSV(self.table)
        for name, value in (
                ("STATE_FILE", self.state),
                ("kits", lambda: [(k, f"/fake/{i}/bin/python") for i, k in enumerate(self.packages)]),
                ("list_packages", lambda python, timeout=60:
                 self.packages.get(list(self.packages)[int(python.split("/")[2])]))):
            p = mock.patch.object(pa, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(pa.urllib.request, "urlopen", self.osv)
        p.start()
        self.addCleanup(p.stop)


class NameAndVersionTests(unittest.TestCase):
    def test_pep503_names(self):
        for a, b in (("Brotli", "brotli"), ("python_dateutil", "python-dateutil"),
                     ("zope.interface", "zope-interface"), ("A__B--c", "a-b-c")):
            with self.subTest(a=a):
                self.assertEqual(pa.normalize(a), b)

    def test_the_lowest_fix_above_the_installed_version(self):
        self.assertEqual(pa.first_fix(OLD_FIX, "urllib3", "2.6.3"), "2.8.0")
        self.assertEqual(pa.first_fix(OLD_FIX, "urllib3", "1.25.0"), "1.26.5")

    def test_the_fix_for_this_package_not_a_sibling(self):
        self.assertEqual(pa.first_fix(BROTLI_GHSA, "Brotli", "1.2.0"), "1.2.1")
        self.assertEqual(pa.first_fix(BROTLI_GHSA, "brotlicffi", "1.1.0.0"), "1.2.1.0")

    def test_a_calendar_version(self):
        # certifi numbers its releases by date.
        self.assertEqual(pa.first_fix(CERTIFI, "certifi", "2026.7.22"), "2026.10.19")
        self.assertEqual(pa.first_fix(CERTIFI, "certifi", "2026.10.23"), "")
        self.assertEqual(pa.first_fix(CERTIFI, "certifi", "2026.10.19"), "")

    def test_a_gpu_build_label_is_not_part_of_the_comparison(self):
        """2.6.0+cu118 is the 2.6.0 release built for CUDA 11.8, so a fix
        of 2.6.0 counts as installed and a fix of 2.6.1 does not."""
        adv = {"affected": [{"package": {"ecosystem": "PyPI", "name": "torch"},
                             "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.6.0"}]}]}]}
        self.assertEqual(pa.first_fix(adv, "torch", "2.5.1+cu118"), "2.6.0")
        self.assertEqual(pa.first_fix(adv, "torch", "2.6.0+cu118"), "")
        adv["affected"][0]["ranges"][0]["events"][1]["fixed"] = "2.6.1"
        self.assertEqual(pa.first_fix(adv, "torch", "2.6.0+cu118"), "2.6.1")
        # A fix older than the installed build is no hole at all.
        self.assertEqual(pa.first_fix(adv, "torch", "2.6.3+cu118"), "")

    def test_no_fix_is_empty(self):
        self.assertEqual(pa.first_fix(NO_FIX, "torch", "2.6.0+cu118"), "")

    def test_a_commit_is_not_a_fix(self):
        """A GIT range's "fixed" is a commit, and as a version it sorts above
        every release, so it must never stand in for one."""
        released = {"affected": [{"package": {"ecosystem": "PyPI", "name": "pillow"},
                                  "ranges": [GIT_FIX, {"type": "ECOSYSTEM", "events": [
                                      {"introduced": "0"}, {"fixed": "8.3.2"}]}]}]}
        self.assertEqual(pa.first_fix(released, "Pillow", "8.3.1"), "8.3.2")
        self.assertEqual(pa.first_fix(PILLOW_UNRELEASED, "Pillow", "8.3.1"), "")
        # A release that fixes only an older line is no fix for this one.
        older_line = {"affected": [{"package": {"ecosystem": "PyPI", "name": "pillow"},
                                    "ranges": [GIT_FIX, {"type": "ECOSYSTEM", "events": [
                                        {"introduced": "0"}, {"fixed": "7.2.1"},
                                        {"introduced": "8.0.0"}]}]}]}
        self.assertEqual(pa.first_fix(older_line, "Pillow", "8.3.1"), "")

    def test_numeric_version_order(self):
        """As text, 1.10.0 sorts BELOW 1.9.5 and the fix would vanish."""
        adv = {"affected": [{"package": {"ecosystem": "PyPI", "name": "x"},
                             "ranges": [{"events": [{"fixed": "1.10.0"}, {"fixed": "1.9.0"}]}]}]}
        self.assertEqual(pa.first_fix(adv, "x", "1.8.0"), "1.9.0")
        self.assertEqual(pa.first_fix(adv, "x", "1.9.5"), "1.10.0")

    def test_one_bug_filed_twice_is_one_hole(self):
        groups = pa.group(["GHSA-9v8h-qqgm-4m35", "PYSEC-2026-4322"], DETAILS)
        self.assertEqual(len(groups), 1)
        self.assertEqual(pa.label(groups[0]), "CVE-2026-111497")

    def test_severity_words(self):
        self.assertEqual(pa.severity(BROTLI_GHSA), "critical")
        self.assertEqual(pa.severity(BROTLI_PYSEC), "")
        self.assertEqual(pa.severity({"database_specific": {"severity": "weird"}}), "")


class KitsTests(unittest.TestCase):
    """The kits are the nightly repair's own walk, minus other users' folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "home"
        self.bot = self.home / "MyOldMachine"
        for kit in (self.home / ".venvs" / "post", self.bot / ".venv",
                    self.bot / "data" / "stt" / "venv",
                    self.bot / "data" / "users" / "77" / "kit",
                    self.home / "Documents" / "client" / "venv"):
            kit.mkdir(parents=True)
            (kit / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        for owner, name, value in ((pa, "BOT_DIR", self.bot),
                                   (pa.venv_repair, "BOT_DIR", self.bot),
                                   (pa.Path, "home", classmethod(lambda cls: self.home))):
            p = mock.patch.object(owner, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_the_bot_its_kits_and_the_home_kits(self):
        got = dict(pa.kits())
        self.assertEqual(sorted(got), ["~/.venvs/post", "~/MyOldMachine/.venv",
                                       "~/MyOldMachine/data/stt/venv"])
        self.assertEqual(got["~/.venvs/post"], str(self.home / ".venvs" / "post" / "bin" / "python"))

    def test_another_users_private_folder_is_never_read(self):
        self.assertNotIn("~/MyOldMachine/data/users/77/kit", dict(pa.kits()))

    def test_the_folders_macos_guards_are_never_read(self):
        self.assertNotIn("~/Documents/client/venv", dict(pa.kits()))


class ListPackagesTests(unittest.TestCase):
    def test_this_interpreter(self):
        got = pa.list_packages(sys.executable)
        self.assertTrue(got)
        self.assertTrue(all(isinstance(n, str) and isinstance(v, str) and n and v for n, v in got))

    def test_a_python_that_fails_is_unknown(self):
        self.assertIsNone(pa.list_packages("/nonexistent/python"))
        self.assertIsNone(pa.list_packages("/bin/false"))

    def test_isolated_mode(self):
        with mock.patch.object(pa.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="[]")
            pa.list_packages("/x/python")
        self.assertEqual(run.call_args.args[0][:2], ["/x/python", "-I"])


class ListCodeTests(unittest.TestCase):
    """What runs inside each kit, run here against stand-in distributions."""

    class Dist:
        def __init__(self, name, version, direct=None):
            self.metadata = {"Name": name}
            self.version = version
            self._direct = direct

        def read_text(self, filename):
            return self._direct if filename == "direct_url.json" else None

    def listed(self, dists):
        import importlib.metadata
        out = io.StringIO()
        with mock.patch.object(importlib.metadata, "distributions", lambda: dists), \
                mock.patch("sys.stdout", out):
            exec(pa.LIST_CODE, {})
        return [tuple(p) for p in json.loads(out.getvalue())]

    def test_a_private_build_never_leaves_the_machine(self):
        got = self.listed([
            self.Dist("requests", "2.32.5"),
            self.Dist("acme-internal", "0.1",
                      json.dumps({"url": "file:///src/acme", "dir_info": {"editable": True}})),
            self.Dist("toolkit", "1.0",
                      json.dumps({"url": "git+ssh://git@corp/toolkit",
                                  "vcs_info": {"vcs": "git", "commit_id": "abc"}})),
            self.Dist("unreadable", "1.0", "{not json"),
        ])
        self.assertEqual(got, [("requests", "2.32.5")])

    def test_a_published_release_installed_from_a_file_is_still_checked(self):
        # A wheel downloaded and installed by path is a release OSV knows.
        got = self.listed([self.Dist("brotli", "1.2.0", json.dumps(
            {"url": "file:///tmp/Brotli-1.2.0-cp312-cp312-linux_x86_64.whl",
             "archive_info": {"hashes": {}}}))])
        self.assertEqual(got, [("brotli", "1.2.0")])


class AuditTests(Base):
    def test_findings(self):
        findings, problems = pa.audit()
        self.assertEqual(problems, [])
        got = {(f["kit"], f["name"]): f for f in findings}
        brotli = got[("~/MyOldMachine/.venv", "Brotli")]
        self.assertEqual(brotli["issues"], ["CVE-2026-111497"])
        self.assertEqual(brotli["fix"], "1.2.1")
        self.assertEqual(brotli["worst"], "critical")
        self.assertEqual(got[("~/MyOldMachine/.venv", "certifi")]["fix"], "2026.10.19")
        self.assertEqual(got[("~/.venvs/post", "torch")]["fix"], "")
        self.assertNotIn(("~/MyOldMachine/.venv", "numpy"), got)

    def test_a_withdrawn_advisory_is_not_a_hole(self):
        findings, _ = pa.audit()
        self.assertNotIn("idna", {f["name"] for f in findings})

    def test_each_advisory_is_fetched_once(self):
        pa.audit()
        details = [u for u in self.osv.calls if "/vulns/" in u]
        self.assertEqual(len(details), len(set(details)))

    def test_osv_down_is_a_problem_not_a_clean_bill(self):
        self.osv.fail = True
        findings, problems = pa.audit()
        self.assertEqual(findings, [])
        self.assertEqual(problems, ["could not reach OSV, the vulnerability database"])

    def test_an_advisory_whose_details_fail_makes_the_week_unchecked(self):
        """Otherwise it reads as 'no fix released yet', unrated, and is then
        marked as told."""
        real = pa._get_json
        with mock.patch.object(pa, "_get_json", lambda url, timeout: None if "GHSA-old" in url
                               else real(url, timeout)):
            _, problems = pa.audit()
        self.assertIn("could not reach OSV, the vulnerability database", problems)

    def test_a_kit_that_will_not_list_is_named(self):
        self.packages["~/.venvs/post"] = None
        findings, problems = pa.audit()
        self.assertIn("could not list the packages in ~/.venvs/post", problems)
        self.assertIn("Brotli", {f["name"] for f in findings})

    def test_a_short_answer_from_osv_is_unknown(self):
        """A results list that does not line up with the queries would pin
        every advisory on the wrong package."""
        with mock.patch.object(pa, "_post_json", lambda url, payload, timeout: {"results": [{}]}):
            self.assertIsNone(pa.osv_ids([("a", "1"), ("b", "2")]))

    def test_paged_answers_are_followed(self):
        self.osv.pages = {("urllib3", "2.6.3"): ["GHSA-nofix"], "urllib3": ["GHSA-nofix"]}
        ids = pa.osv_ids([("urllib3", "2.6.3")])
        self.assertEqual(ids, [["GHSA-old", "GHSA-nofix"]])


class MessageTests(Base):
    def test_worst_first_one_row_per_package(self):
        findings, _ = pa.audit()
        msg = pa.build_message(findings, [])
        lines = msg.splitlines()
        self.assertIn("4 packages have known security holes", lines[0])
        self.assertEqual(lines[1], "Critical:")
        self.assertIn("Brotli 1.2.0 in ~/MyOldMachine/.venv: 1 hole, fixed in 1.2.1", msg)
        self.assertIn("certifi 2026.7.22 in ~/MyOldMachine/.venv: 1 hole, fixed in 2026.10.19", msg)
        self.assertLess(msg.index("Critical:"), msg.index("High:"))
        self.assertIn("torch 2.6.0+cu118 in ~/.venvs/post: 1 hole, no fix released yet", msg)
        # urllib3 in two kits with two versions: one row.
        self.assertIn("Moderate, low or unrated: urllib3.", msg)
        self.assertEqual(msg.count("urllib3"), 1)

    def test_a_fix_only_committed_reads_as_no_fix_released(self):
        """One hole fixed in 8.3.2 and one fixed only in a commit: no release
        fixes both, and the commit must not be named as the version."""
        self.packages["~/.venvs/post"].append(("Pillow", "8.3.1"))
        self.table[("pillow", "8.3.1")] = ["GHSA-pillow", "PYSEC-unreleased"]
        msg = pa.build_message(pa.audit()[0], [])
        self.assertIn("Pillow 8.3.1 in ~/.venvs/post: 2 holes, no fix released yet", msg)
        self.assertNotIn(COMMIT[:7], msg)

    def test_the_same_package_in_many_kits_is_one_row(self):
        rows = pa.by_package([
            {"kit": k, "name": "pillow", "version": ver, "keys": ["A", "B"], "fix": fix, "worst": "high"}
            for k, ver, fix in (("a", "12.1.0", "13.0.0"), ("b", "12.2.0", "12.3.0"),
                                ("c", "12.3.0", "13.0.0"), ("d", "12.3.0", "13.0.0"))])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fix"], "13.0.0")
        self.assertEqual(pa._where(rows[0]), "12.1.0 / 12.2.0 / 12.3.0 in a, b, c and 1 more")

    def test_no_fix_anywhere_means_no_fix(self):
        """Also without the packaging library, where an empty fix no longer
        happens to sort above every real one."""
        found = [{"kit": "a", "name": "x", "version": "1", "keys": ["K"], "fix": "2", "worst": ""},
                 {"kit": "b", "name": "x", "version": "1", "keys": ["K"], "fix": "", "worst": ""}]
        self.assertEqual(pa.by_package(found)[0]["fix"], "")
        with mock.patch.dict(sys.modules, {"packaging.version": None}):
            self.assertEqual(pa._version_key("2"), (1, (2,)))
            self.assertEqual(pa.by_package(found)[0]["fix"], "")

    def test_long_reports_split_between_lines(self):
        msg = "\n".join(f"line {i} " + "x" * 90 for i in range(200))
        parts = pa.chunks(msg)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 3900 for p in parts))
        self.assertEqual("\n".join(parts), msg)

    def test_one_giant_line_is_cut_not_lost(self):
        parts = pa.chunks("y" * 9000)
        self.assertEqual("".join(parts), "y" * 9000)
        self.assertTrue(all(len(p) <= 3900 for p in parts))


class RunTests(Base):
    def test_said_once_then_silent(self):
        sent = []
        msg, delivered = pa.run(notify=lambda m: sent.append(m) or True)
        self.assertTrue(delivered)
        self.assertIn("Brotli", msg)
        again, _ = pa.run(notify=lambda m: sent.append(m) or True)
        self.assertEqual(again, "")
        self.assertEqual(len(sent), 1)

    def test_a_failed_send_records_nothing(self):
        pa.run(notify=lambda m: False)
        self.assertFalse(self.state.exists())
        msg, _ = pa.run(notify=lambda m: True)
        self.assertIn("Brotli", msg)

    def test_every_piece_must_land(self):
        calls = []
        with mock.patch.object(pa, "chunks", lambda m, limit=3900: ["one", "two"]):
            _, delivered = pa.run(notify=lambda m: calls.append(m) or m == "one")
        self.assertFalse(delivered)
        self.assertEqual(calls, ["one", "two"])
        self.assertFalse(self.state.exists())

    def test_a_look_by_hand_changes_nothing(self):
        msg, _ = pa.run(notify=None, bank=False)
        self.assertIn("Brotli", msg)
        self.assertFalse(self.state.exists())

    def test_a_new_advisory_on_a_known_package_is_news(self):
        pa.run(notify=lambda m: True)
        self.table[("brotli", "1.2.0")] = ["GHSA-9v8h-qqgm-4m35", "PYSEC-2026-4322", "GHSA-old"]
        msg, _ = pa.run(notify=lambda m: True)
        self.assertIn("Brotli", msg)
        self.assertNotIn("torch", msg)

    def test_an_updated_package_drops_out_and_can_return(self):
        pa.run(notify=lambda m: True)
        self.packages["~/MyOldMachine/.venv"] = [("Brotli", "1.2.1")]
        pa.run(notify=lambda m: True)
        self.packages["~/MyOldMachine/.venv"] = [("Brotli", "1.2.0")]
        msg, _ = pa.run(notify=lambda m: True)
        self.assertIn("Brotli", msg)

    def test_a_week_without_osv_keeps_the_marks(self):
        pa.run(notify=lambda m: True)
        self.osv.fail = True
        msg, _ = pa.run(notify=lambda m: True)
        self.assertEqual(msg, "Weekly Python check could not reach OSV, the vulnerability database.")
        self.osv.fail = False
        again, _ = pa.run(notify=lambda m: True)
        self.assertEqual(again, "", "a blip re-reported everything")

    def test_a_lasting_problem_is_said_once(self):
        self.packages["~/.venvs/post"] = None
        first, _ = pa.run(notify=lambda m: True)
        self.assertIn("could not list the packages in ~/.venvs/post", first)
        second, _ = pa.run(notify=lambda m: True)
        self.assertEqual(second, "")


class MainTests(unittest.TestCase):
    def test_no_notify_neither_sends_nor_records(self):
        seen = {}

        def fake_run(notify=None, bank=True):
            seen.update(notify=notify, bank=bank)
            return "x", True
        with mock.patch.object(pa, "run", fake_run), mock.patch.object(sys, "argv", ["p", "--no-notify"]), \
                mock.patch("builtins.print"):
            self.assertEqual(pa.main(), 0)
        self.assertEqual(seen, {"notify": None, "bank": False})

    def test_the_monday_run_sends_to_the_admin(self):
        seen = {}

        def fake_run(notify=None, bank=True):
            seen.update(notify=notify, bank=bank)
            return "", True
        with mock.patch.object(pa, "run", fake_run), mock.patch.object(sys, "argv", ["p"]), \
                mock.patch.object(pa, "enabled", return_value=True):
            self.assertEqual(pa.main(), 0)
        self.assertEqual(seen, {"notify": pa.notify_admin, "bank": True})

    def test_a_crash_is_said(self):
        said = []

        def boom(**k):
            raise RuntimeError("disk full")
        with mock.patch.object(pa, "run", boom), mock.patch.object(sys, "argv", ["p"]), \
                mock.patch.object(pa, "enabled", return_value=True), \
                mock.patch.object(pa, "notify_admin", lambda m: said.append(m) or True):
            self.assertEqual(pa.main(), 1)
        self.assertIn("crashed", said[0])

    def test_switched_off_checks_nothing(self):
        with mock.patch.object(pa, "run") as run, mock.patch.object(sys, "argv", ["p"]), \
                mock.patch("utils.maintenance.load_config", return_value={"python_audit": False}):
            self.assertEqual(pa.main(), 0)
        run.assert_not_called()

    def test_unreadable_settings_leave_it_on(self):
        with mock.patch("utils.maintenance.load_config", side_effect=OSError("gone")):
            self.assertTrue(pa.enabled())
        with mock.patch("utils.maintenance.load_config", return_value={}):
            self.assertTrue(pa.enabled())


class SendTests(unittest.TestCase):
    """notify_admin itself, with send_to_telegram.py replaced by a stand-in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seen = Path(self.tmp.name) / "argv.json"
        script = Path(self.tmp.name) / "send_to_telegram.py"
        script.write_text(
            "import json, sys\n"
            f"open({str(self.seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
            "sys.exit(int(sys.argv[-1] == 'refuse') * 2)\n", encoding="utf-8")
        self.notify = REAL_NOTIFY
        for name, value in (("SEND_SCRIPT", script), ("VENV_PYTHON", Path(sys.executable))):
            p = mock.patch.object(pa, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_one_message_to_the_admin(self):
        with mock.patch("core.config.get_primary_admin_id", return_value=77):
            self.assertTrue(self.notify("hello"))
        self.assertEqual(json.loads(self.seen.read_text()), ["--user", "77", "--message", "hello"])

    def test_a_refused_send_is_not_delivered(self):
        with mock.patch("core.config.get_primary_admin_id", return_value=77):
            self.assertFalse(self.notify("refuse"))

    def test_no_admin_sends_nothing(self):
        with mock.patch("core.config.get_primary_admin_id", return_value=None):
            self.assertFalse(self.notify("hello"))
        self.assertFalse(self.seen.exists())



class ShippedDefaultsTests(unittest.TestCase):
    def test_it_is_on_by_default(self):
        from utils.maintenance import DEFAULT_CONFIG
        self.assertIs(DEFAULT_CONFIG["python_audit"], True)

    def test_its_record_and_log_live_in_the_bots_data_folder(self):
        # A second copy of the module, so the patched one stays patched.
        import importlib.util
        spec = importlib.util.spec_from_file_location("python_audit_shipped",
                                                      ROOT / "utils" / "python_audit.py")
        shipped = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shipped)
        self.assertEqual(shipped.STATE_FILE, ROOT / "data" / "python_audit.json")
        self.assertEqual(shipped.LOG_FILE, ROOT / "data" / "logs" / "python_audit.log")


class JobRegistrationTests(unittest.TestCase):
    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, **kw):
            self.jobs.append(kw)

    def _register(self, existing_meta):
        import bot as botmod
        import core.scheduler as sched_mod
        fake = self.FakeScheduler()
        with mock.patch.object(sched_mod, "_get_all_meta", lambda: existing_meta), \
                mock.patch.object(botmod, "get_primary_admin_id", lambda: 77):
            botmod._setup_weekly_python_audit_job(fake)
        return fake.jobs

    def test_registers_a_monday_0830_command_job(self):
        (job,) = self._register([])
        self.assertEqual(job["name"], "weekly-python-audit")
        self.assertEqual(job["repeat"], "weekly")
        self.assertEqual(job["job_type"], "command")
        self.assertFalse(job["notify"])
        # The scheduler runs the command through a shell: two quoted words.
        import bot as botmod
        self.assertEqual(shlex.split(job["command"]),
                         [str(botmod.BOT_DIR / ".venv" / "bin" / "python"),
                          str(botmod.BOT_DIR / "utils" / "python_audit.py")])
        self.assertEqual(job["user_id"], 77)
        run_at = job["run_at"]
        self.assertEqual((run_at.weekday(), run_at.hour, run_at.minute), (0, 8, 30))
        self.assertGreater(run_at, datetime.now())

    def test_an_existing_job_is_not_duplicated(self):
        self.assertEqual(self._register([{"name": "weekly-python-audit"}]), [])


if __name__ == "__main__":
    unittest.main()
