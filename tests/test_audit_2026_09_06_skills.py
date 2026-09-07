"""Regression tests for the 2026-09-06 audit, skills half.

Each test names its finding id and asserts the behaviour, not the patch. The
skill scripts import their heavy dependencies inside the functions that need
them, so most of this runs on a bare CI runner; the few that genuinely need a
third-party package skip when it is absent rather than pretend to pass.

  F01  a cleanup plan accepted the file being deleted as its own restore copy
  F24  the text overlay named an ImageMagick font that MoviePy 2 cannot open
  F26  tesseract's decimal confidence was read as zero, and Greek was "gre"
  F30  a workflow variable holding $(...) executed instead of staying data
  F31  two controls with the same label got two refs and one selector
  F32  binance and kraken candles shared one cache key
  F33  a 200 carrying an HTML error page was saved as the picture
  F34  a one-day all-day event asked Google for a zero-length range
  F36  a load, a flow and a concentration that cannot all be true were accepted
  F37  four NaN scores passed a quality gate
  F38  a child that ignores SIGTERM escaped the helper without a SIGKILL
  F39  an arc was silently read as more straight lines
  F41  a failed download returned an unrelated earlier video
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"


def load(path: Path, name: str, extra_syspath: list[Path] | None = None):
    """Import a skill script by path, with its own directory importable."""
    for p in (extra_syspath or []) + [path.parent]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves sys.modules[cls.__module__]; without this the
    # decorator raises on a module loaded purely by path.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def have(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


# --- F01: a file cannot be its own recovery copy ----------------------------

class ArchiveRestoreGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # greek-law and postproduction each ship a `_common`, and archive.py
        # imports it by bare name. Under `unittest discover` the greek-law one
        # is often already in sys.modules, so pin the right one for the load
        # and put back whatever was there.
        scripts = SKILLS / "postproduction" / "scripts"
        cls._saved_common = sys.modules.get("_common")
        sys.modules.pop("_common", None)
        load(scripts / "_common.py", "_common")
        try:
            cls.prove = load(scripts / "prove.py", "pp_prove_uc")
            cls.archive = load(scripts / "archive.py", "pp_archive_uc")
        finally:
            if cls._saved_common is not None:
                sys.modules["_common"] = cls._saved_common
            else:
                sys.modules.pop("_common", None)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_an_empty_ledger_verifies_nothing(self):
        ledger = self.tmp / "empty.json"
        ledger.write_text(json.dumps({"entries": []}))
        out = self.prove.verify_ledger(str(ledger))
        self.assertGreater(out["failures"], 0,
                           "zero failures on an empty ledger is a pass for the wrong reason")

    def test_the_condemned_file_is_not_its_own_restore(self):
        doomed = self.tmp / "master.mov"
        doomed.write_bytes(b"the only copy")
        keep = self.tmp / "keep.json"
        keep.write_text(json.dumps({"entries": [
            {"path": str(self.tmp / "other.mov"), "sha256": "0" * 64}]}))
        report = self.archive.sweep(
            str(keep), [str(doomed)],
            restore_map={str(doomed): str(doomed)}, execute=False,
            ledger_out=str(self.tmp / "l.json"))
        gate = [g for g in report["gates"] if g["gate"] == "restore path proved"][0]
        self.assertFalse(gate["pass"])
        self.assertTrue(doomed.exists(), "nothing may be deleted by a dry run")

    def test_a_symlink_back_to_the_condemned_file_is_refused(self):
        doomed = self.tmp / "master.mov"
        doomed.write_bytes(b"the only copy")
        alias = self.tmp / "alias.mov"
        alias.symlink_to(doomed)
        keep = self.tmp / "keep.json"
        keep.write_text(json.dumps({"entries": [
            {"path": str(self.tmp / "other.mov"), "sha256": "0" * 64}]}))
        report = self.archive.sweep(
            str(keep), [str(doomed)],
            restore_map={str(doomed): str(alias)}, execute=False,
            ledger_out=str(self.tmp / "l.json"))
        gate = [g for g in report["gates"] if g["gate"] == "restore path proved"][0]
        self.assertFalse(gate["pass"])

    def test_a_genuine_second_copy_passes(self):
        doomed = self.tmp / "master.mov"
        doomed.write_bytes(b"the only copy")
        backup = self.tmp / "backup" / "master.mov"
        backup.parent.mkdir()
        backup.write_bytes(b"the only copy")
        keep = self.tmp / "keep.json"
        keep.write_text(json.dumps({"entries": [
            {"path": str(self.tmp / "other.mov"), "sha256": "0" * 64}]}))
        report = self.archive.sweep(
            str(keep), [str(doomed)],
            restore_map={str(doomed): str(backup)}, execute=False,
            ledger_out=str(self.tmp / "l.json"))
        gate = [g for g in report["gates"] if g["gate"] == "restore path proved"][0]
        self.assertTrue(gate["pass"], "a real distinct copy with the same bytes must pass")


# --- F24: the text overlay needs a font that exists -------------------------

class VideoFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video = load(SKILLS / "video-editing" / "scripts" / "video.py", "video_uc")

    def test_the_default_font_is_a_file_on_this_machine_or_none(self):
        font = self.video._default_font()
        if font is not None:
            self.assertTrue(os.path.isfile(font))

    def test_the_candidate_list_covers_both_platforms(self):
        paths = self.video.FONT_CANDIDATES
        self.assertTrue(any(p.startswith("/usr/share/fonts") for p in paths))
        self.assertTrue(any("/System/Library/Fonts" in p or "/Library/Fonts" in p
                            for p in paths))

    def test_the_imagemagick_era_font_name_is_no_longer_the_default(self):
        source = (SKILLS / "video-editing" / "scripts" / "video.py").read_text()
        self.assertNotIn("args.font or 'DejaVu-Sans'", source)
        self.assertIn("args.font or _default_font()", source)

    def test_resize_uses_the_installed_parameter_name(self):
        source = (SKILLS / "video-editing" / "scripts" / "video.py").read_text()
        self.assertIn("new_size=", source)
        self.assertNotIn("newsize=", source)


# --- F26: OCR confidence and the Greek language code ------------------------

class OcrConfidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ocr = load(SKILLS / "ocr" / "scripts" / "ocr.py", "ocr_uc")

    def _tsv(self, conf):
        header = "\t".join(["level", "page", "block", "par", "line", "word",
                            "left", "top", "width", "height", "conf", "text"])
        row = "\t".join(["5", "1", "1", "1", "1", "1", "10", "20", "30", "40", conf, "hello"])
        return header + "\n" + row

    def _confidence(self, conf):
        completed = types.SimpleNamespace(stdout=self._tsv(conf), returncode=0, stderr="")
        with mock.patch.object(self.ocr.subprocess, "run", return_value=completed):
            return self.ocr.ocr_image_with_data("x.png")["average_confidence"]

    def test_a_decimal_confidence_survives(self):
        # tesseract writes "96.500000"; isdigit() called that zero.
        self.assertAlmostEqual(self._confidence("96.500000"), 96.5, places=1)

    def test_an_integer_confidence_still_works(self):
        self.assertAlmostEqual(self._confidence("87"), 87.0, places=1)

    def test_the_non_word_sentinel_reads_as_zero(self):
        self.assertEqual(self._confidence("-1"), 0.0)

    def test_the_guide_names_the_language_tesseract_installs(self):
        skill = (SKILLS / "ocr" / "SKILL.md").read_text()
        self.assertIn("--lang ell", skill)
        self.assertNotIn("--lang gre", skill)


# --- F30: workflow data is data ---------------------------------------------

class WorkflowQuotingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have("yaml"):
            sys.modules.setdefault("yaml", types.ModuleType("yaml"))
        cls.wf = load(SKILLS / "workflow" / "scripts" / "workflow.py", "workflow_uc")

    def _run(self):
        run = cls_run = self.wf.WorkflowRun.__new__(self.wf.WorkflowRun)
        run.variables = {}
        run.results = {}
        return cls_run

    def test_command_substitution_in_a_variable_stays_literal(self):
        run = self._run()
        run.variables = {"name": "$(touch /tmp/should-not-exist)"}
        out = run.resolve_variables("echo {{name}}", shell=True)
        # Whatever quoting was chosen, a shell must print it rather than run it.
        printed = subprocess.run(["bash", "-c", out], capture_output=True, text=True)
        self.assertEqual(printed.stdout.strip(), "$(touch /tmp/should-not-exist)")

    def test_a_value_inside_double_quotes_is_escaped_for_that_context(self):
        run = self._run()
        run.variables = {"v": '$(id) "and" `date`'}
        out = run.resolve_variables('echo "{{v}}"', shell=True)
        printed = subprocess.run(["bash", "-c", out], capture_output=True, text=True)
        self.assertEqual(printed.stdout.strip(), '$(id) "and" `date`')

    def test_a_value_inside_single_quotes_is_escaped_for_that_context(self):
        run = self._run()
        run.variables = {"v": "it's $(id)"}
        out = run.resolve_variables("echo '{{v}}'", shell=True)
        printed = subprocess.run(["bash", "-c", out], capture_output=True, text=True)
        self.assertEqual(printed.stdout.strip(), "it's $(id)")

    def test_an_empty_value_does_not_vanish_from_the_argv(self):
        run = self._run()
        run.variables = {"v": ""}
        out = run.resolve_variables("printf '[%s]' {{v}}", shell=True)
        printed = subprocess.run(["bash", "-c", out], capture_output=True, text=True)
        self.assertEqual(printed.stdout.strip(), "[]")

    def test_a_deliberate_shell_fragment_still_has_a_way_through(self):
        run = self._run()
        run.variables = {"flags": "-l -a"}
        out = run.resolve_variables("echo {{raw:flags}}", shell=True)
        self.assertEqual(out, "echo -l -a")

    def test_non_shell_resolution_is_untouched(self):
        run = self._run()
        run.variables = {"v": "$(id)"}
        self.assertEqual(run.resolve_variables("{{v}}"), "$(id)")

    def test_an_unknown_placeholder_is_left_alone(self):
        run = self._run()
        self.assertEqual(run.resolve_variables("{{nope}}", shell=True), "{{nope}}")


# --- F31: two controls with the same label are two selectors ----------------

class BrowserRefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = load(SKILLS / "browser" / "scripts" / "browser.py", "browser_uc")

    def test_duplicate_labels_resolve_to_different_selectors(self):
        snapshot = '- button "Open"\n- button "Open"\n- button "Close"\n'
        _, refs = self.browser.parse_aria_snapshot(snapshot)
        opens = [r for r, d in refs.items() if d["name"] == "Open"]
        self.assertEqual(len(opens), 2)
        selectors = {self.browser.resolve_ref(r, refs) for r in opens}
        self.assertEqual(len(selectors), 2, "two Open buttons must not share one selector")

    def test_a_unique_control_keeps_a_plain_selector(self):
        snapshot = '- button "Open"\n- button "Close"\n'
        _, refs = self.browser.parse_aria_snapshot(snapshot)
        close = [r for r, d in refs.items() if d["name"] == "Close"][0]
        self.assertNotIn(">> nth=", self.browser.resolve_ref(close, refs))

    def test_an_unnamed_control_is_counted_among_everything_its_selector_matches(self):
        """Review of #156: the position was counted among refs with the
        same role AND name, then looked up by `role=button >> nth=N`, which
        Playwright counts across every button. For unnamed controls that is
        a different, larger set, so nth=0 landed on the first NAMED button:
        the wrong one, reliably."""
        snapshot = '- button "Save"\n- button\n- button\n'
        _, refs = self.browser.parse_aria_snapshot(snapshot)
        unnamed = [r for r, d in refs.items() if d["name"] == ""]
        self.assertEqual([self.browser.resolve_ref(r, refs) for r in unnamed],
                         ["role=button >> nth=1", "role=button >> nth=2"])

    def test_a_label_that_is_a_prefix_of_another_is_still_disambiguated(self):
        """`[name="Open"]` is a case-insensitive substring match in
        Playwright, so an "Open" and an "Open file" button both answer to
        it; the first must carry its index and the second stays plain."""
        snapshot = '- button "Open"\n- button "Open file"\n'
        _, refs = self.browser.parse_aria_snapshot(snapshot)
        by_name = {d["name"]: r for r, d in refs.items()}
        self.assertEqual(self.browser.resolve_ref(by_name["Open"], refs),
                         'role=button[name="Open"] >> nth=0')
        self.assertEqual(self.browser.resolve_ref(by_name["Open file"], refs),
                         'role=button[name="Open file"]')


# --- F32: candles belong to the exchange that quoted them -------------------

class TradingCacheKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tc = load(SKILLS / "trading" / "scripts" / "trading_common.py", "trading_uc")

    def test_two_exchanges_do_not_share_one_key(self):
        a = self.tc.cache_path("hist", "BTC/USDT", "1y", "1d", "binance")
        b = self.tc.cache_path("hist", "BTC/USDT", "1y", "1d", "kraken")
        self.assertNotEqual(a, b)

    def test_equities_keep_their_exchange_free_key(self):
        a = self.tc.cache_path("hist", "AAPL", "1y", "1d")
        self.assertNotIn("None", a.name)


# --- F33: a picture must decode before it is a success ----------------------

class PollinationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have("httpx"):
            raise unittest.SkipTest("httpx not installed")
        cls.gen = load(SKILLS / "image-gen" / "scripts" / "generate.py", "imagegen_uc")

    def _respond(self, body, content_type, out):
        resp = types.SimpleNamespace(status_code=200, content=body,
                                     headers={"content-type": content_type}, text="")
        client = mock.MagicMock()
        client.__enter__.return_value.get.return_value = resp
        with mock.patch.object(self.gen.httpx, "Client", return_value=client), \
             mock.patch.object(self.gen.time, "sleep"):
            return self.gen.generate_pollinations("a cat", str(out))

    def test_an_html_error_page_is_not_an_image(self):
        out = Path(tempfile.mkdtemp()) / "o.png"
        result = self._respond(b"<html><body>rate limited</body></html>" * 40,
                               "text/html", out)
        self.assertFalse(result["success"])
        self.assertFalse(out.exists(), "an unusable body must not be saved as the picture")

    @unittest.skipUnless(have("PIL"), "Pillow not installed")
    def test_a_real_image_is_accepted(self):
        from io import BytesIO

        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
        out = Path(tempfile.mkdtemp()) / "o.png"
        result = self._respond(buf.getvalue(), "image/png", out)
        self.assertTrue(result["success"], result.get("error"))
        self.assertTrue(out.exists())

    def test_the_ceiling_keeps_the_aspect_ratio(self):
        seen = {}

        def fake_client(*a, **k):
            client = mock.MagicMock()

            def get(url):
                seen["url"] = url
                return types.SimpleNamespace(status_code=500, content=b"", headers={}, text="")
            client.__enter__.return_value.get.side_effect = get
            return client

        with mock.patch.object(self.gen.httpx, "Client", side_effect=fake_client):
            self.gen.generate_pollinations("x", "/tmp/none.png", width=1280, height=720)
        self.assertIn("width=768", seen["url"])
        self.assertIn("height=432", seen["url"])   # 720 * 768/1280, not 720


# --- F34: Google's end date is exclusive ------------------------------------

class CalendarAllDayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in ("google", "google.auth", "google.auth.transport",
                     "google.auth.transport.requests", "google.oauth2",
                     "google.oauth2.credentials", "google_auth_oauthlib",
                     "google_auth_oauthlib.flow", "googleapiclient",
                     "googleapiclient.discovery", "googleapiclient.errors"):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules["google.auth.transport.requests"].Request = object
        sys.modules["google.oauth2.credentials"].Credentials = object
        sys.modules["google_auth_oauthlib.flow"].InstalledAppFlow = object
        sys.modules["googleapiclient.discovery"].build = lambda *a, **k: None
        sys.modules["googleapiclient.errors"].HttpError = type("HttpError", (Exception,), {})
        cls.gcal = load(SKILLS / "calendar" / "scripts" / "gcal.py", "gcal_uc")

    def _built_event(self, start, end=None):
        captured = {}

        class Events:
            def insert(self, calendarId=None, body=None):
                captured["body"] = body
                return types.SimpleNamespace(execute=lambda: {"htmlLink": "x", "id": "1"})

        service = types.SimpleNamespace(events=lambda: Events())
        with mock.patch.object(self.gcal, "get_service", return_value=service):
            self.gcal.add_event("Holiday", start, end)
        return captured["body"]

    def test_a_one_day_event_ends_the_next_day(self):
        body = self._built_event("2026-10-01")
        self.assertEqual(body["start"]["date"], "2026-10-01")
        self.assertEqual(body["end"]["date"], "2026-10-02")

    def test_an_explicit_last_day_is_inclusive(self):
        body = self._built_event("2026-10-01", "2026-10-03")
        self.assertEqual(body["end"]["date"], "2026-10-04")


# --- F36: the mass balance has to hold --------------------------------------

class WastewaterInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lymata = load(SKILLS / "greek-engineer" / "scripts" / "lymata.py", "lymata_uc")

    def test_inconsistent_load_flow_and_concentration_are_refused(self):
        # 100 m3/d at 1200 mg/L is 120 kg/d, not 60.
        with self.assertRaises(ValueError):
            self.lymata.diastasiologisi(fortio=60, paroxi=100, so=1200)

    def test_a_consistent_triple_is_accepted(self):
        out = self.lymata.diastasiologisi(fortio=120, paroxi=100, so=1200)
        self.assertGreater(out["ogkos_m3"], 0)

    def test_two_of_the_three_still_work(self):
        out = self.lymata.diastasiologisi(fortio=60, paroxi=100)
        self.assertGreater(out["ogkos_m3"], 0)

    def test_a_non_finite_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.lymata.diastasiologisi(fortio=float("nan"), paroxi=100)

    def test_a_nan_never_reads_as_within_range(self):
        verdict = self.lymata._entos(float("nan"), (1.0, 2.0))
        self.assertNotIn("εντός", verdict)


# --- F37: a score must be a number before it can pass -----------------------

class VlmGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load(SKILLS / "img2threejs" / "forge" / "stage4_review" / "vlm_gate.py",
                        "vlm_gate_uc")

    def _verdict(self, score):
        eye = {"hardGateFailures": [], "verdict": "pass", "action": "continue"}
        sample = {c: score for c in self.gate.CRITERIA}
        return self.gate.gate(eye, lambda _i: dict(sample), n_samples=1)

    def test_nan_scores_do_not_pass(self):
        out = self._verdict(float("nan"))
        self.assertNotEqual(out["verdict"], "pass")
        self.assertIn("invalid", out)

    def test_an_out_of_range_score_does_not_pass(self):
        out = self._verdict(4.0)
        self.assertNotEqual(out["verdict"], "pass")
        self.assertIn("invalid", out)

    def test_a_finite_in_range_set_is_still_graded_normally(self):
        out = self._verdict(0.95)
        self.assertNotIn("invalid", out)
        self.assertEqual(out["verdict"], "pass")


# --- F38: a timed-out child is always reaped --------------------------------

class SubprocTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.subproc = load(SKILLS / "last30days" / "scripts" / "lib" / "subproc.py",
                           "subproc_uc")

    def test_a_child_that_ignores_sigterm_is_killed(self):
        script = ("import signal, time\n"
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "print('ready', flush=True)\n"
                  "time.sleep(60)\n")
        with self.assertRaises(self.subproc.SubprocTimeout):
            self.subproc.run_with_timeout([sys.executable, "-c", script], timeout=2)

    def test_a_normal_command_still_returns_its_output(self):
        out = self.subproc.run_with_timeout([sys.executable, "-c", "print('hi')"], timeout=20)
        self.assertEqual(out.stdout.strip(), "hi")


# --- F39: an unsupported curve is refused, not reinterpreted ----------------

class SvgArcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = load(SKILLS / "logo-animate" / "scripts" / "svg_path_audit.py", "svg_uc")

    def test_an_arc_is_refused(self):
        with self.assertRaises(ValueError):
            self.audit.parse_path("M 0 0 H 10 A 5 5 0 0 1 20 10")

    def test_a_smooth_curve_is_refused(self):
        with self.assertRaises(ValueError):
            self.audit.parse_path("M 0 0 C 1 1 2 2 3 3 S 4 4 5 5")

    def test_a_supported_path_still_parses(self):
        segments, closed = self.audit.parse_path("M 0 0 L 10 0 L 10 10 Z")
        self.assertTrue(segments)
        self.assertTrue(closed)


# --- F41: a download belongs to the URL that asked for it -------------------

class WatchDownloadIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dl = load(SKILLS / "watch" / "scripts" / "download.py", "watch_dl_uc")

    def test_a_failed_download_does_not_return_an_earlier_video(self):
        out_dir = Path(tempfile.mkdtemp())
        (out_dir / "video.mp4").write_bytes(b"an older film")
        (out_dir / "video.source.json").write_text(json.dumps({"url": "https://old"}))
        failed = types.SimpleNamespace(returncode=1)
        with mock.patch.object(self.dl.subprocess, "run", return_value=failed), \
             mock.patch.object(self.dl.shutil, "which", return_value="/usr/bin/yt-dlp"):
            with self.assertRaises(SystemExit):
                self.dl.download_url("https://new", out_dir)

    def test_the_stale_file_is_cleared_before_the_downloader_runs(self):
        out_dir = Path(tempfile.mkdtemp())
        stale = out_dir / "video.mp4"
        stale.write_bytes(b"an older film")
        (out_dir / "video.source.json").write_text(json.dumps({"url": "https://old"}))
        failed = types.SimpleNamespace(returncode=1)
        with mock.patch.object(self.dl.subprocess, "run", return_value=failed), \
             mock.patch.object(self.dl.shutil, "which", return_value="/usr/bin/yt-dlp"):
            with self.assertRaises(SystemExit):
                self.dl.download_url("https://new", out_dir)
        self.assertFalse(stale.exists())

    def test_a_repeat_request_for_the_same_url_keeps_its_files(self):
        out_dir = Path(tempfile.mkdtemp())
        video = out_dir / "video.mp4"
        video.write_bytes(b"the right film")
        (out_dir / "video.source.json").write_text(json.dumps({"url": "https://same"}))
        ok = types.SimpleNamespace(returncode=0)
        with mock.patch.object(self.dl.subprocess, "run", return_value=ok), \
             mock.patch.object(self.dl.shutil, "which", return_value="/usr/bin/yt-dlp"):
            result = self.dl.download_url("https://same", out_dir)
        self.assertTrue(result["downloaded"])
        self.assertEqual(Path(result["video_path"]).read_bytes(), b"the right film")


# --- Dependency-bearing fixes ------------------------------------------------

@unittest.skipUnless(have("PIL"), "Pillow not installed")
class IconAndImageTests(unittest.TestCase):
    """F29 favicons kept only the 16px icon; F27 compositing dropped opacity
    and a brightness of 0 was read as 'not given'."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _run(self, script, *args):
        return subprocess.run([sys.executable, str(script), *args],
                              capture_output=True, text=True)

    def test_the_ico_carries_every_requested_size(self):
        from PIL import Image
        src = self.tmp / "src.png"
        Image.new("RGBA", (256, 256), (255, 0, 0, 255)).save(src)
        out = self.tmp / "o.ico"
        run = self._run(SKILLS / "icon-gen" / "scripts" / "icongen.py",
                        "ico", str(src), str(out))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn((256, 256), set(Image.open(out).ico.sizes()))
        self.assertGreaterEqual(len(Image.open(out).ico.sizes()), 6)

    def test_an_overlay_does_not_make_an_opaque_base_transparent(self):
        from PIL import Image
        base = self.tmp / "b.png"
        Image.new("RGBA", (20, 20), (255, 255, 255, 255)).save(base)
        overlay = self.tmp / "o.png"
        Image.new("RGBA", (10, 10), (255, 0, 0, 128)).save(overlay)
        out = self.tmp / "c.png"
        run = self._run(SKILLS / "image-editing" / "scripts" / "image.py",
                        "composite", str(base), str(overlay), str(out))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Image.open(out).getpixel((0, 0))[3], 255)

    def test_brightness_zero_is_a_request_not_an_omission(self):
        from PIL import Image
        src = self.tmp / "w.png"
        Image.new("RGB", (8, 8), (255, 255, 255)).save(src)
        out = self.tmp / "d.png"
        run = self._run(SKILLS / "image-editing" / "scripts" / "image.py",
                        "adjust", str(src), str(out), "--brightness", "0")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Image.open(out).convert("RGB").getpixel((0, 0)), (0, 0, 0))


@unittest.skipUnless(have("PIL") and have("numpy") and have("torch"),
                     "needs Pillow, numpy and torch")
class UpscaleAlphaTests(unittest.TestCase):
    """Review of #156: the alpha fix enlarged the colour and the
    transparency separately, so whatever sat behind the cut out (black, for
    anything out of background removal) was dragged into the edge. A red
    disc on a transparent ground came back with its edge at 202 on average
    and a full black rim at the worst pixel."""

    def test_a_cut_out_keeps_its_colour_to_the_edge(self):
        import numpy as np
        from PIL import Image
        upscale = load(SKILLS / "upscale" / "scripts" / "hybrid_upscale.py", "upscale_uc")
        tmp = Path(tempfile.mkdtemp())
        yy, xx = np.mgrid[:64, :64]
        inside = (xx - 31.5) ** 2 + (yy - 31.5) ** 2 <= 20 ** 2
        rgba = np.zeros((64, 64, 4), np.uint8)
        rgba[inside] = (255, 0, 0, 255)
        Image.fromarray(rgba, "RGBA").save(tmp / "disc.png")
        argv = ["hybrid_upscale.py", str(tmp / "disc.png"), str(tmp / "out.png"),
                "--mode", "lanczos", "--scale", "2"]
        with mock.patch.object(sys, "argv", argv):
            upscale.main()
        out = np.asarray(Image.open(tmp / "out.png").convert("RGBA"), np.int32)
        self.assertEqual(out.shape, (128, 128, 4))
        visible = out[..., 3] >= 32
        red = out[..., 0][visible]
        self.assertGreaterEqual(int(red.min()), 250, "no dark rim inside the visible edge")
        self.assertLessEqual(int(out[..., 1:3][visible].max()), 5, "and no colour cast")


@unittest.skipUnless(have("mido"), "mido not installed")
class MidiTimingTests(unittest.TestCase):
    """F21: quantising moved a note to the wrong grid line, merging two files
    at different resolutions doubled beat positions, and an extracted track
    lost the conductor's tempo."""

    @classmethod
    def setUpClass(cls):
        cls.midi = load(SKILLS / "midi" / "scripts" / "midi_tool.py", "midi_uc")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _file(self, tpb, events):
        import mido
        mid = mido.MidiFile(ticks_per_beat=tpb)
        track = mido.MidiTrack()
        mid.tracks.append(track)
        for msg in events:
            track.append(msg)
        path = self.tmp / f"in_{tpb}_{len(events)}.mid"
        mid.save(path)
        return path

    def test_quantising_snaps_to_the_nearest_grid_line(self):
        import mido
        src = self._file(480, [
            mido.Message("note_on", note=60, velocity=64, time=230),
            mido.Message("note_off", note=60, velocity=64, time=100),
        ])
        out = self.tmp / "q.mid"
        args = types.SimpleNamespace(input=str(src), output=str(out), grid=4)
        self.midi.cmd_quantize(args)
        played = mido.MidiFile(out)
        first = next(m for m in played.tracks[0] if m.type == "note_on")
        self.assertEqual(first.time, 0, "tick 230 is nearer 0 than 480 on a quarter grid")

    def test_quantising_keeps_every_note_its_full_length(self):
        """Review of #156: note_on and note_off were snapped independently,
        so any note shorter than half a grid step collapsed to zero length,
        silent. Eight eighth notes on a quarter grid: four of them vanished
        (lengths 0, 480, 480, 0, 0, 480, 480, 0). A note moves with its
        onset and keeps its duration."""
        import mido
        events = []
        for i in range(8):
            events.append(mido.Message("note_on", note=60 + i, velocity=64, time=0))
            events.append(mido.Message("note_off", note=60 + i, velocity=64, time=240))
        src = self._file(480, events)
        out = self.tmp / "q8.mid"
        self.midi.cmd_quantize(types.SimpleNamespace(input=str(src), output=str(out), grid=4))
        played = mido.MidiFile(out)
        t, on, lengths = 0, {}, []
        for m in played.tracks[0]:
            t += m.time
            if m.type == "note_on" and m.velocity > 0:
                on[m.note] = t
            elif m.type in ("note_off", "note_on"):
                lengths.append(t - on.pop(m.note))
        self.assertEqual(lengths, [240] * 8)
        starts = sorted(set(v for v in on.values()))
        self.assertEqual(starts, [])

    def test_extracting_a_track_keeps_the_tempo(self):
        import mido
        mid = mido.MidiFile(ticks_per_beat=480)
        conductor = mido.MidiTrack()
        conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(90), time=0))
        melody = mido.MidiTrack()
        melody.append(mido.Message("note_on", note=60, velocity=64, time=0))
        melody.append(mido.Message("note_off", note=60, velocity=64, time=480))
        mid.tracks.extend([conductor, melody])
        src = self.tmp / "two.mid"
        mid.save(src)
        out = self.tmp / "one.mid"
        self.midi.cmd_extract(types.SimpleNamespace(input=str(src), output=str(out), track=1))
        tempos = [m for t in mido.MidiFile(out).tracks for m in t if m.type == "set_tempo"]
        self.assertTrue(tempos, "the extracted track plays at 120 BPM without this")
        self.assertEqual(tempos[0].tempo, mido.bpm2tempo(90))

    def test_merging_different_resolutions_keeps_beat_positions(self):
        import mido
        a = self._file(480, [mido.Message("note_on", note=60, velocity=64, time=480),
                             mido.Message("note_off", note=60, velocity=64, time=480)])
        b = self._file(960, [mido.Message("note_on", note=67, velocity=64, time=960),
                             mido.Message("note_off", note=67, velocity=64, time=960)])
        out = self.tmp / "m.mid"
        self.midi.cmd_merge(types.SimpleNamespace(files=[str(a), str(b)], output=str(out)))
        merged = mido.MidiFile(out)
        self.assertEqual(merged.ticks_per_beat, 480)
        starts = []
        for track in merged.tracks:
            t = 0
            for msg in track:
                t += msg.time
                if msg.type == "note_on":
                    starts.append(t)
        self.assertEqual(sorted(starts), [480, 480],
                         "both notes are on beat 2; the 960-tpb one landed on beat 3")

    def test_scaling_a_file_with_no_tempo_event_changes_something(self):
        import mido
        src = self._file(480, [mido.Message("note_on", note=60, velocity=64, time=0),
                               mido.Message("note_off", note=60, velocity=64, time=480)])
        out = self.tmp / "s.mid"
        self.midi.cmd_tempo(types.SimpleNamespace(input=str(src), output=str(out),
                                                  bpm=None, scale=2.0))
        tempos = [m for t in mido.MidiFile(out).tracks for m in t if m.type == "set_tempo"]
        self.assertTrue(tempos, "a scale on a tempo-free file used to be a no-op")
        self.assertAlmostEqual(mido.tempo2bpm(tempos[0].tempo), 240, delta=1)


@unittest.skipUnless(have("pretty_midi"), "pretty_midi not installed")
class CompositionClockTests(unittest.TestCase):
    """F22: chords and melody each assumed 120 BPM, so four bars at another
    tempo ran to the wrong length and the parts disagreed."""

    @classmethod
    def setUpClass(cls):
        cls.compose = load(SKILLS / "algorithmic-composition" / "scripts" / "compose.py",
                           "compose_uc")

    def test_four_bars_of_chords_last_four_bars(self):
        midi = self.compose.generate_chord_progression(bars=4, tempo=120)
        end = max(n.end for inst in midi.instruments for n in inst.notes)
        self.assertAlmostEqual(end, 8.0, delta=0.9)   # 4 bars of 4 beats at 120 BPM

    def test_the_requested_tempo_reaches_the_notes(self):
        slow = self.compose.generate_chord_progression(bars=4, tempo=60)
        fast = self.compose.generate_chord_progression(bars=4, tempo=120)
        slow_end = max(n.end for i in slow.instruments for n in i.notes)
        fast_end = max(n.end for i in fast.instruments for n in i.notes)
        self.assertAlmostEqual(slow_end / fast_end, 2.0, delta=0.1)

    def test_the_full_arrangement_writes_the_tempo_it_was_asked_for(self):
        source = (SKILLS / "algorithmic-composition" / "scripts" / "compose.py").read_text()
        self.assertIn("combine_midi([chord_midi, melody, drums], args.output, tempo=args.tempo)",
                      source)


@unittest.skipUnless(have("PIL"), "Pillow not installed")
class ScreenshotDiffTests(unittest.TestCase):
    """F20: two absent files compared as a match, because the failed parse
    became -1 and -1 is under any threshold."""

    @classmethod
    def setUpClass(cls):
        cls.sd = load(SKILLS / "screenshot-diff" / "scripts" / "screenshot_diff.py",
                      "screenshot_diff_uc")

    def test_two_missing_files_are_not_a_match(self):
        with self.assertRaises(SystemExit):
            self.sd.compare_images("/nonexistent/a.png", "/nonexistent/b.png")

    def test_an_unreadable_file_is_not_a_match(self):
        tmp = Path(tempfile.mkdtemp())
        broken = tmp / "broken.png"
        broken.write_bytes(b"not an image")
        real = tmp / "real.png"
        from PIL import Image
        Image.new("RGB", (4, 4)).save(real)
        with self.assertRaises(SystemExit):
            self.sd.compare_images(str(broken), str(real))


if __name__ == "__main__":
    unittest.main()
