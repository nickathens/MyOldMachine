"""Skill fixes ported from the Linux bot's sweep of 2026-10-07, for skills
that have no test file of their own here. Each test is red on main.

- calendar: a failed sign-in claimed it "did not complete within 5 minutes"
  without naming the token or the cause; a short id that matched several
  occurrences of a repeating event deleted the first of them.
- weather: a Greek place name found nothing, and an API refusal ended in a
  KeyError traceback.
- JSON printed by skills escaped every Greek letter.
- stems and audio-analysis ran inside the bot's service with no memory cap.
- blender: --frames set the length of one scene only; a relative --output
  failed (both live, BLENDER_TESTS=1).
- image-gen: main() cut each side of a Pollinations request to 768, so 16:9
  came out 768x720; a refusal that named a token was read as a sign-in failure.
- email: a Greek recipient name broke the address; an HTML-only mail read
  empty; a Greek mail in its own charset came out garbled; a cut body did not
  say so.
- browser: the first goto to a slow page died with a traceback, and the
  cookie jar was readable by every account.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email import message_from_bytes, policy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
# Skill libraries CI does not install: those tests skip there.
HAVE_GOOGLE = all(importlib.util.find_spec(m) for m in ("google", "googleapiclient", "google_auth_oauthlib"))
HAVE_FEEDS = all(importlib.util.find_spec(m) for m in ("requests", "bs4", "feedparser"))


@unittest.skipUnless(HAVE_GOOGLE, "needs the Google API libraries")
class CalendarSignInSaysWhatHappened(unittest.TestCase):
    """in a chat turn gcal.py looks for the user's own token. When it
    is missing it starts Google's browser sign-in, which fails at once on a
    machine with no browser for the bot, and the message claimed the sign-in
    "did not complete within 5 minutes" without saying which token was
    missing or why it failed."""

    def test_the_failure_names_the_token_and_the_cause(self):
        import contextlib
        import importlib.util
        import io
        import tempfile
        import webbrowser
        from unittest import mock
        spec = importlib.util.spec_from_file_location(
            "gcal_sweep", ROOT / "skills" / "calendar" / "scripts" / "gcal.py")
        gcal = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"JARVIS_USER_DIR": ""}):
            spec.loader.exec_module(gcal)
        with tempfile.TemporaryDirectory() as d:
            secrets = Path(d, "client.json")
            secrets.write_text("{}", encoding="utf-8")
            token = Path(d, "user", "google", "calendar_token.json")
            flow = mock.Mock()
            flow.run_local_server.side_effect = webbrowser.Error("could not locate runnable browser")
            err = io.StringIO()
            # the whole name, not an attribute of Google's class: another test
            # module stubs that class, and this one must not depend on it
            app_flow = mock.Mock(from_client_secrets_file=mock.Mock(return_value=flow))
            with mock.patch.object(gcal, "TOKEN_FILE", token), \
                    mock.patch.object(gcal, "CREDENTIALS_FILE", secrets), \
                    mock.patch.object(gcal, "InstalledAppFlow", app_flow), \
                    contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit):
                gcal.get_credentials()
        text = err.getvalue()
        self.assertIn("could not locate runnable browser", text)
        self.assertIn(str(token), text)
        self.assertNotIn("within 5 minutes", text)


def _load_gcal():
    import importlib.util
    from unittest import mock
    spec = importlib.util.spec_from_file_location(
        "gcal_sweep_ids", ROOT / "skills" / "calendar" / "scripts" / "gcal.py")
    gcal = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"JARVIS_USER_DIR": ""}):
        spec.loader.exec_module(gcal)
    return gcal


def _fake_calendar(items):
    """A Calendar service stand-in: events().list().execute() pages the
    items, events().delete(...) records what was deleted."""
    from unittest import mock
    deleted = []
    service = mock.Mock()
    service.events.return_value.list.return_value.execute.return_value = {"items": items}

    def delete(calendarId, eventId):
        deleted.append(eventId)
        return mock.Mock(execute=lambda: None)

    service.events.return_value.delete.side_effect = delete
    return service, deleted


WEEKLY = [
    {"id": "abc12345xyz67890_20261008T090000Z", "recurringEventId": "abc12345xyz67890",
     "summary": "Lesson", "start": {"dateTime": "2026-10-08T12:00:00+03:00"}},
    {"id": "abc12345xyz67890_20261015T090000Z", "recurringEventId": "abc12345xyz67890",
     "summary": "Lesson", "start": {"dateTime": "2026-10-15T12:00:00+03:00"}},
    {"id": "zz998877other", "summary": "Dentist", "start": {"dateTime": "2026-10-09T10:00:00+03:00"}},
]


@unittest.skipUnless(HAVE_GOOGLE, "needs the Google API libraries")
class CalendarIdsNameOneEvent(unittest.TestCase):
    """list showed every occurrence of a repeating event with the same
    8 character id, and delete/show took the FIRST event whose id starts
    with what was given, so deleting next week's lesson by the id the list
    printed deleted this week's."""

    def test_an_ambiguous_id_deletes_nothing(self):
        import contextlib
        import io
        from unittest import mock
        gcal = _load_gcal()
        service, deleted = _fake_calendar(WEEKLY)
        err = io.StringIO()
        with mock.patch.object(gcal, "get_service", return_value=service), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            gcal.delete_event("abc12345")
        self.assertEqual(deleted, [])
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("abc12345xyz67890_20261015T090000Z", err.getvalue())

    def test_a_unique_prefix_still_works(self):
        import contextlib
        import io
        from unittest import mock
        gcal = _load_gcal()
        service, deleted = _fake_calendar(WEEKLY)
        with mock.patch.object(gcal, "get_service", return_value=service), \
                contextlib.redirect_stdout(io.StringIO()):
            gcal.delete_event("zz998877")
        self.assertEqual(deleted, ["zz998877other"])

    def test_the_list_prints_an_id_that_names_one_occurrence(self):
        import contextlib
        import io
        from unittest import mock
        gcal = _load_gcal()
        service, _ = _fake_calendar(WEEKLY)
        out = io.StringIO()
        with mock.patch.object(gcal, "get_service", return_value=service), \
                contextlib.redirect_stdout(out):
            gcal.list_events(days=14)
        self.assertIn("[id:abc12345xyz67890_20261015T090000Z]", out.getvalue())
        self.assertIn("[id:zz998877]", out.getvalue())


def _load_weather():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "weather_sweep", ROOT / "skills" / "weather" / "scripts" / "weather.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Json:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


FORECAST = {
    "current": {"temperature_2m": 21.0, "relative_humidity_2m": 60, "apparent_temperature": 21.5,
                "weather_code": 1, "wind_speed_10m": 9.0},
    "current_units": {"temperature_2m": "°C", "relative_humidity_2m": "%",
                      "apparent_temperature": "°C", "wind_speed_10m": "km/h"},
    "daily": {"time": ["2026-10-07", "2026-10-08"], "weather_code": [1, 61],
              "temperature_2m_max": [24.0, 22.0], "temperature_2m_min": [15.0, 14.0],
              "precipitation_probability_max": [0, 70]},
}


class WeatherGreekNamesAndApiErrors(unittest.TestCase):
    """a Greek place name found nothing (the geocoder was asked in
    English; Θεσσαλονίκη, Ηράκλειο and Πειραιάς all answered "Could not find
    location", live 2026-10-07, while language=el finds them), an error
    answer from the forecast API ended in KeyError 'current', and tomorrow's
    range used a dash."""

    def _run(self, argv, answers):
        import contextlib
        import io
        from unittest import mock
        weather = _load_weather()
        seen = []

        def fake_get(url, params=None, timeout=None):
            seen.append((url, params))
            return answers.pop(0)

        out = io.StringIO()
        with mock.patch.object(weather.httpx, "get", side_effect=fake_get), \
                mock.patch.object(sys, "argv", ["weather.py", *argv]), \
                contextlib.redirect_stdout(out):
            rc = weather.main()
        return rc, out.getvalue(), seen

    def test_a_greek_name_is_looked_up_in_greek(self):
        geo = _Json({"results": [{"latitude": 40.64, "longitude": 22.94, "name": "Θεσσαλονίκη"}]})
        rc, out, seen = self._run(["Θεσσαλονίκη"], [geo, _Json(FORECAST)])
        self.assertEqual(rc, 0)
        self.assertEqual(seen[0][1]["language"], "el")
        self.assertIn("Θεσσαλονίκη", out)

    def test_a_forecast_error_is_reported_not_a_traceback(self):
        bad = _Json({"error": True, "reason": "Latitude must be in range of -90 to 90°. Given: 100.0."}, 400)
        rc, out, _ = self._run(["--lat", "100", "--lon", "0"], [bad])
        self.assertEqual(rc, 1)
        self.assertIn("Latitude must be in range", out)

    def test_tomorrow_reads_as_a_range_in_words(self):
        rc, out, _ = self._run([], [_Json(FORECAST)])
        self.assertEqual(rc, 0)
        self.assertIn("Tomorrow: Slight rain, 14.0 to 22.0°C", out)


class JsonKeepsGreekReadable(unittest.TestCase):
    """JSON printed by skills escaped every non-ASCII letter
    (\u039a\u03b1...), six characters each, for Greek page text, product
    names and file names. The browser's eval is the most used of them."""

    def test_browser_eval_prints_greek_as_greek(self):
        import contextlib
        import importlib.util
        import io
        from unittest import mock
        spec = importlib.util.spec_from_file_location(
            "browser_sweep_json", ROOT / "skills" / "browser" / "scripts" / "browser.py")
        browser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(browser)
        out = io.StringIO()
        with mock.patch.object(browser, "ensure_daemon", return_value=True), \
                mock.patch.object(browser, "send_command", return_value={"result": "Καλημέρα κόσμε"}), \
                mock.patch.object(sys, "argv", ["browser.py", "eval", "document.title"]), \
                contextlib.redirect_stdout(out):
            browser.main()
        self.assertIn("Καλημέρα κόσμε", out.getvalue())


def _load_stems():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "stems_sweep", ROOT / "skills" / "stems" / "scripts" / "separate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StemsRunInsideAMemoryCap(unittest.TestCase):
    """demucs ran inside the bot's service with no cap; it holds the whole
    input and four float32 stems, about 1.4 MB a second, so an hour long
    recording needs 7 to 8 GB, and on Linux an OOM kill inside the service
    stops the whole service. macOS keeps its old behaviour."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = Path(self.tmp.name, "song.wav")
        self.audio.write_bytes(b"RIFF")

    def test_demucs_is_started_inside_the_capped_scope(self):
        from unittest import mock
        stems = _load_stems()
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(list(cmd))
            ok = cmd[-1] == "true"           # the scope probe succeeds here
            return SimpleNamespace(returncode=0 if ok else 1, stdout="", stderr="stop here")

        with mock.patch.object(stems, "pick_device", return_value="cpu"), \
                mock.patch.object(stems.shutil, "which", return_value="/usr/bin/systemd-run"), \
                mock.patch.object(stems.subprocess, "run", side_effect=fake_run):
            stems.separate_stems(str(self.audio), self.tmp.name)
        demucs = [c for c in seen if "-c" in c]
        self.assertEqual(len(demucs), 1, seen)
        self.assertIn("--scope", demucs[0])
        self.assertIn(f"MemoryMax={stems.MEM_MAX}", demucs[0])

    def test_a_kill_by_the_cap_is_explained(self):
        from unittest import mock
        stems = _load_stems()
        die = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)", "--"]
        with mock.patch.object(stems, "pick_device", return_value="cpu"), \
                mock.patch.object(stems, "_scope_prefix", return_value=die):
            result = stems.separate_stems(str(self.audio), self.tmp.name)
        self.assertIn("memory cap", result.get("error", ""))

    def test_no_scope_and_a_long_input_is_refused_on_linux(self):
        from unittest import mock
        stems = _load_stems()
        with mock.patch.object(stems.sys, "platform", "linux"), \
                mock.patch.object(stems, "pick_device", return_value="cpu"), \
                mock.patch.object(stems, "_scope_prefix", return_value=None), \
                mock.patch.object(stems, "_duration", return_value=3600.0), \
                mock.patch.object(stems.subprocess, "run", side_effect=AssertionError("demucs started")):
            result = stems.separate_stems(str(self.audio), self.tmp.name)
        self.assertIn("60 minutes", result.get("error", ""))

    def test_macos_runs_a_long_input_as_before(self):
        from unittest import mock
        stems = _load_stems()
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(list(cmd))
            return SimpleNamespace(returncode=1, stdout="", stderr="stop here")

        with mock.patch.object(stems.sys, "platform", "darwin"), \
                mock.patch.object(stems, "pick_device", return_value="cpu"), \
                mock.patch.object(stems, "_scope_prefix", return_value=None), \
                mock.patch.object(stems, "_duration", return_value=3600.0), \
                mock.patch.object(stems.subprocess, "run", side_effect=fake_run):
            result = stems.separate_stems(str(self.audio), self.tmp.name)
        self.assertEqual(len(seen), 1, "demucs was not started")
        self.assertEqual(seen[0][0], sys.executable)
        self.assertIn("Demucs failed", result.get("error", ""))


def _load_analyze():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "analyze_sweep", ROOT / "skills" / "audio-analysis" / "scripts" / "analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AudioAnalysisInsideAMemoryCap(unittest.TestCase):
    """the plain analysis of a 34 minute file passed 6 GB (OOM-killed in a
    capped scope) and ran inside the bot's service. macOS keeps its old
    behaviour."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = Path(self.tmp.name, "album.mp3")
        self.audio.write_bytes(b"ID3")

    def _main(self, argv, env=None):
        import contextlib
        import io
        from unittest import mock
        mod = self.mod
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["analyze.py", *argv]), \
                mock.patch.dict(os.environ, env or {"AUDIO_ANALYSIS_ISOLATED": ""}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main()
        return rc, out.getvalue(), err.getvalue()

    def test_it_runs_again_inside_the_scope(self):
        from unittest import mock
        self.mod = _load_analyze()
        record = Path(self.tmp.name, "argv.txt")
        code = ("import os, sys; open(%r, 'w').write(repr((sys.argv[1:], os.environ.get('AUDIO_ANALYSIS_ISOLATED'))))"
                % str(record))
        with mock.patch.object(self.mod, "_scope_prefix", return_value=[sys.executable, "-c", code, "--"]):
            rc, _, _ = self._main([str(self.audio), "--bpm"])
        self.assertEqual(rc, 0)
        argv, flag = eval(record.read_text())
        # python -c keeps the "--" that ends the scope prefix: ['--', python, script, *args]
        self.assertEqual(argv[1], sys.executable)
        self.assertTrue(argv[2].endswith("analyze.py"), argv)
        self.assertEqual(argv[3:], [str(self.audio), "--bpm"])
        self.assertEqual(flag, "1")

    def test_macos_analyses_a_long_file_whole_as_before(self):
        from unittest import mock
        self.mod = _load_analyze()
        fake = {"success": True, "file": "x", "duration_seconds": 60, "duration_formatted": "1:00",
                "sample_rate": 44100, "bpm": 120.0, "key": "C", "key_confidence": 0.5,
                "avg_loudness_rms": 0.1, "brightness_hz": 1000.0}
        with mock.patch.object(self.mod.sys, "platform", "darwin"), \
                mock.patch.object(self.mod, "_scope_prefix", return_value=None), \
                mock.patch.object(self.mod, "_length", return_value=3600.0), \
                mock.patch.object(self.mod, "analyze_audio", return_value=fake) as analyze:
            rc, _, _ = self._main([str(self.audio)])
        self.assertEqual(rc, 0)
        analyze.assert_called_once_with(str(self.audio), offset=0.0, duration=None)

    def test_a_kill_by_the_cap_is_explained(self):
        from unittest import mock
        self.mod = _load_analyze()
        die = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)", "--"]
        with mock.patch.object(self.mod, "_scope_prefix", return_value=die):
            rc, _, err = self._main([str(self.audio)])
        self.assertEqual(rc, 1)
        self.assertIn("memory cap", err)

    def test_without_a_scope_a_long_file_needs_a_section_on_linux(self):
        from unittest import mock
        self.mod = _load_analyze()
        linux = mock.patch.object(self.mod.sys, "platform", "linux")
        linux.start()
        self.addCleanup(linux.stop)
        fake = {"success": True, "file": "x", "duration_seconds": 60, "duration_formatted": "1:00",
                "sample_rate": 44100, "bpm": 120.0, "key": "C", "key_confidence": 0.5,
                "avg_loudness_rms": 0.1, "brightness_hz": 1000.0}
        with mock.patch.object(self.mod, "_scope_prefix", return_value=None), \
                mock.patch.object(self.mod, "_length", return_value=3600.0), \
                mock.patch.object(self.mod, "analyze_audio", return_value=fake) as analyze:
            rc, _, err = self._main([str(self.audio)])
            self.assertEqual(rc, 1)
            self.assertIn("--duration", err)
            analyze.assert_not_called()
            rc, _, _ = self._main([str(self.audio), "--start", "120", "--duration", "60"])
            self.assertEqual(rc, 0)
            analyze.assert_called_once_with(str(self.audio), offset=120.0, duration=60.0)


@unittest.skipUnless(os.environ.get("BLENDER_TESTS") == "1", "renders with Blender; set BLENDER_TESTS=1")
class BlenderRender(unittest.TestCase):
    """skills/blender/scripts/render.py."""

    def test_frames_sets_the_length_of_every_scene(self):
        """only 'spinning_cube' set the scene's frame range, so every
        other scene rendered Blender's default 250 frames whatever --frames
        said."""
        import json
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "sphere.mp4")
            run = subprocess.run(
                ["blender", "--background", "--python", str(ROOT / "skills/blender/scripts/render.py"),
                 "--", "--scene", "glass_sphere", "--animation", "--frames", "3", "--engine", "CYCLES",
                 "--samples", "1", "--width", "64", "--height", "36", "--output", str(out)],
                capture_output=True, text=True, timeout=600)
            self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
            probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
                                    "-show_entries", "stream=nb_read_frames", "-of", "json", str(out)],
                                   capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(probe.stdout)["streams"][0]["nb_read_frames"], "3")

    def test_relative_output_is_written(self):
        """with no .blend file Blender cannot anchor a relative path,
        so a still rendered in full and then failed to save."""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            run = subprocess.run(
                ["blender", "--background", "--python", str(ROOT / "skills/blender/scripts/render.py"),
                 "--", "--scene", "glass_sphere", "--engine", "CYCLES", "--samples", "1",
                 "--width", "64", "--height", "36", "--output", "still.png"],
                capture_output=True, text=True, timeout=600, cwd=d)
            self.assertEqual(run.returncode, 0, run.stdout[-1500:] + run.stderr[-1500:])
            self.assertGreater(Path(d, "still.png").stat().st_size, 0)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ImageGenMainKeepsTheShape(unittest.TestCase):
    def setUp(self):
        self.gen = _load("generate_sweep", "skills/image-gen/scripts/generate.py")
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        crumbs = mock.patch.object(self.gen, "LAST_GEN_DIR", Path(self.td.name) / "crumbs")
        crumbs.start()
        self.addCleanup(crumbs.stop)

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["generate.py", *argv]), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                self.gen.main()
            except SystemExit:
                pass
        return out.getvalue()

    def test_a_wide_pollinations_picture_stays_wide(self):
        png = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(png, format="PNG")
        client = mock.Mock()
        client.get.return_value = SimpleNamespace(status_code=200, content=png.getvalue(),
                                                  headers={"content-type": "image/png"}, text="")
        target = Path(self.td.name) / "wide.jpg"
        with mock.patch.object(self.gen.httpx, "Client") as factory:
            factory.return_value.__enter__.return_value = client
            self._main("a field", "-o", str(target), "--backend", "pollinations", "-a", "16:9")
        self.assertIn("width=768&height=432", client.get.call_args.args[0])
        self.assertTrue(target.exists())

    def test_a_refusal_that_names_a_token_is_not_a_sign_in_failure(self):
        def refuse(stderr):
            return mock.patch.object(self.gen.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stdout="", stderr=stderr))

        with refuse("Error: upload token for media 3f2 is no longer valid"):
            out = self.gen.generate_higgsfield("p", str(Path(self.td.name) / "x.jpg"))
        self.assertEqual(out["error"], "Higgsfield error: Error: upload token for media 3f2 is no longer valid")
        with refuse("Not authenticated.\n  higgsfield auth login"):
            out = self.gen.generate_video("p", str(Path(self.td.name) / "x.mp4"))
        self.assertTrue(out["error"].startswith("Higgsfield not authenticated. Run: higgsfield auth login"))

    def test_brand_and_soul_keep_a_refusals_words_too(self):
        refusal = SimpleNamespace(returncode=1, stdout="",
                                  stderr="Error: upload token for media 3f2 is no longer valid")
        for rel, call in (("skills/image-gen/scripts/brand.py",
                           lambda m: m._run(["higgsfield"], False, "p", Path(self.td.name))),
                          ("skills/image-gen/scripts/soul_id.py",
                           lambda m: m._run(["higgsfield"]))):
            mod = _load("sweep_" + Path(rel).stem, rel)
            with mock.patch.object(mod.subprocess, "run", return_value=refusal):
                self.assertEqual(call(mod)["error"], refusal.stderr, rel)


def _part(mime, text, charset="utf-8", filename=""):
    data = base64.urlsafe_b64encode(text.encode(charset)).decode()
    return {"mimeType": mime, "filename": filename, "body": {"data": data},
            "headers": [{"name": "Content-Type", "value": f'{mime}; charset="{charset}"'}]}


@unittest.skipUnless(HAVE_GOOGLE, "needs the Google API libraries")
class GmailDraftsAndBodies(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_USER_DIR", None)
            self.gmail = _load("gmail_sweep", "skills/email/scripts/gmail.py")

    def test_a_greek_name_keeps_a_real_address(self):
        service = mock.Mock()
        service.users().drafts().create().execute.return_value = {"id": "d1"}
        with mock.patch.object(self.gmail, "get_gmail_service", return_value=service):
            self.gmail.create_draft("anna@example.com, Ελένη Παπά <eleni@example.com>",
                                    "Καλημέρα", "Γεια σου")
        raw = service.users().drafts().create.call_args.kwargs["body"]["message"]["raw"]
        sent = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
        self.assertEqual([(a.display_name, a.addr_spec) for a in sent["to"].addresses],
                         [("", "anna@example.com"), ("Ελένη Παπά", "eleni@example.com")])
        self.assertEqual(sent["to"].defects, ())
        self.assertEqual(sent["subject"], "Καλημέρα")
        self.assertEqual(sent.get_content().strip(), "Γεια σου")

    def test_send_keeps_a_real_address_too(self):
        # MOM keeps the send command, which built its mail the same way.
        service = mock.Mock()
        service.users().messages().send().execute.return_value = {"id": "m1"}
        with mock.patch.object(self.gmail, "get_gmail_service", return_value=service):
            self.gmail.send_email("Ελένη Παπά <eleni@example.com>", "Καλημέρα", "Γεια σου")
        raw = service.users().messages().send.call_args.kwargs["body"]["raw"]
        sent = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
        self.assertEqual([(a.display_name, a.addr_spec) for a in sent["to"].addresses],
                         [("Ελένη Παπά", "eleni@example.com")])
        self.assertEqual(sent["to"].defects, ())

    def test_an_html_only_body_with_an_attachment_reads(self):
        payload = {"mimeType": "multipart/mixed", "parts": [
            _part("text/html", "<html><head><style>p{}</style></head><body>"
                               "<p>Your invoice is attached.</p><p>Total: 120&nbsp;&euro;</p></body></html>"),
            {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "a"}},
        ]}
        self.assertEqual(self.gmail._extract_body(payload), "Your invoice is attached.\n\nTotal: 120\xa0€")

    def test_an_attached_text_file_is_not_the_body(self):
        payload = {"mimeType": "multipart/mixed", "parts": [
            _part("text/html", "<p>The real body</p>"),
            _part("text/plain", "notes in the attachment", filename="notes.txt"),
        ]}
        self.assertEqual(self.gmail._extract_body(payload), "The real body")

    def test_a_greek_mail_in_its_own_charset(self):
        payload = {"mimeType": "multipart/alternative", "parts": [
            _part("text/plain", "Καλημέρα σας", charset="windows-1253")]}
        self.assertEqual(self.gmail._extract_body(payload), "Καλημέρα σας")

    def test_a_cut_body_says_it_was_cut(self):
        service = mock.Mock()
        service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/plain", "headers": [],
                        "body": {"data": base64.urlsafe_b64encode(b"x" * 6000).decode()}}}
        with mock.patch.object(self.gmail, "get_gmail_service", return_value=service), \
                mock.patch.object(self.gmail, "_resolve_message_id", return_value="m1"):
            body = self.gmail.read_email("m1")["body"]
        self.assertTrue(body.endswith("[cut here: the mail goes on for 1000 more characters]"))


BROWSER_HARNESS = r"""
import importlib.util, json, os, socketserver, stat, sys, threading, time, http.server
tmp, script = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("browser_sweep", script)
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
for name in ("SOCKET_PATH", "PID_FILE", "STATE_FILE", "STORAGE_FILE", "REFS_FILE"):
    setattr(b, name, os.path.join(tmp, os.path.basename(getattr(b, name))))
b.BROWSER_LOCK_DIR = os.path.join(tmp, "locks")

class Slow(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/slow.js":
            time.sleep(7)  # the network stays busy past the old 5 s wait
            body = b"1"
        else:
            body = b"<html><head><title>slow</title></head><body><script src='/slow.js'></script></body></html>"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Slow); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % srv.server_address[1]
sys.argv = ["browser.py", "goto", url]
outcome = "ok"
try:
    b.main()
except BaseException as e:
    outcome = type(e).__name__
finally:
    deadline = time.time() + 30
    while time.time() < deadline and not b.daemon_is_running():
        time.sleep(0.5)  # an old daemon may still be starting: wait, then stop it
    if b.daemon_is_running():
        b.send_command({"action": "stop"})
        time.sleep(2)
mode = oct(stat.S_IMODE(os.stat(b.STORAGE_FILE).st_mode)) if os.path.exists(b.STORAGE_FILE) else None
print("HARNESS", json.dumps({"outcome": outcome, "url": url, "storage_mode": mode}))
"""


@unittest.skipUnless(importlib.util.find_spec("playwright"), "playwright not installed")
class BrowserFirstGoto(unittest.TestCase):
    def test_a_first_goto_to_a_slow_page_lands(self):
        with tempfile.TemporaryDirectory() as td:
            run = subprocess.run([sys.executable, "-c", BROWSER_HARNESS, td,
                                  str(ROOT / "skills/browser/scripts/browser.py")],
                                 capture_output=True, text=True, timeout=180)
        line = next(ln for ln in run.stdout.splitlines() if ln.startswith("HARNESS "))
        result = json.loads(line[len("HARNESS "):])
        self.assertEqual(result["outcome"], "ok", run.stdout[-800:] + run.stderr[-800:])
        self.assertIn(f'"url": "{result["url"]}"', run.stdout)
        self.assertEqual(result["storage_mode"], "0o600")



def _load_research():
    spec = importlib.util.spec_from_file_location(
        "research_sweep", ROOT / "skills" / "research" / "scripts" / "research.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_FEEDS, "needs requests, bs4 and feedparser")
class FeedFetchIsBounded(unittest.TestCase):
    """`rss add` and `rss check` fetched each feed with feedparser.parse(url),
    which has no timeout, so one feed server that accepts and never answers
    held the turn forever. A local socket that accepts and stays silent
    stands in for it."""

    def test_a_silent_feed_server_does_not_hang_the_check(self):
        import shutil
        import socket
        import threading
        research = _load_research()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        held = []

        def accept_and_stay_silent():
            try:
                conn, _ = srv.accept()
                held.append(conn)
            except OSError:
                pass

        threading.Thread(target=accept_and_stay_silent, daemon=True).start()
        tmp = Path(tempfile.mkdtemp(prefix="research-feed-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        url = f"http://127.0.0.1:{srv.getsockname()[1]}/feed.xml"
        done = []
        with mock.patch.object(research, "DATA_DIR", tmp), \
                mock.patch.object(research, "DB_FILE", tmp / "r.db"), \
                mock.patch.object(research, "FEED_TIMEOUT", 1, create=True), \
                mock.patch("builtins.print"):
            conn = research.init_db()
            conn.execute("INSERT INTO feeds (name, url, added) VALUES ('slow', ?, 'now')", (url,))
            conn.commit()
            conn.close()
            worker = threading.Thread(target=lambda: done.append(research.fetch_feed("slow")), daemon=True)
            worker.start()
            worker.join(timeout=8)
        for c in held:
            c.close()
        self.assertFalse(worker.is_alive(), "fetch_feed is still waiting on a silent server")


if __name__ == "__main__":
    unittest.main()
