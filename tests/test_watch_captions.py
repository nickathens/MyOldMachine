"""watch: captions in the video's own language, and whisper found beside Python.

Linux bot review 2026-09-27.
S049: download.py asked yt-dlp for English captions only, so a Greek video's
"captions" were YouTube's machine translation into English (on the Greek
lyric talk D8Dg3ORQIOc the tracks are en, el-orig and el). The caption track
is now chosen by the video's language, a missed track is fetched again
through the android_vr client, and the transcript says which kind it read.
The whisper CLI is also found beside sys.executable, where a service's PATH
often lacks the venv's bin. Also: WebVTT cues without hours parsed to
nothing, and entities stayed raw.
"""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"watch_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dl = _load("download")
tr = _load("transcribe")
wh = _load("whisper")

GREEK_TALK = {"language": "el", "subtitles": {},
              "automatic_captions": {"en": [], "el-orig": [], "el": []},
              "title": "Μαθήματα Στιχουργικής #1", "duration": 464}
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nΚαλησπέρα\n"


class CaptionChoice(unittest.TestCase):
    def test_original_language_first_translation_last(self):
        plan = dl.caption_plan(GREEK_TALK)
        self.assertEqual(plan[0], ("el-orig", "automatic, original language"))
        self.assertEqual(plan[-1][0], "en")
        self.assertIn("machine translated from el", plan[-1][1])

    def test_manual_track_in_the_video_language_wins(self):
        raw = dict(GREEK_TALK, subtitles={"el": [], "en": [], "live_chat": []})
        plan = dl.caption_plan(raw)
        self.assertEqual(plan[0], ("el", "manual"))
        self.assertNotIn("live_chat", [c for c, _ in plan])

    def test_english_video_prefers_its_original_track(self):
        raw = {"language": "en", "subtitles": {}, "automatic_captions": {"en": [], "en-orig": []}}
        self.assertEqual(dl.caption_plan(raw)[0][0], "en-orig")

    def test_pick_reads_the_plan_not_the_alphabet(self):
        out = Path(tempfile.mkdtemp(prefix="watch-"))
        for code in ("en", "el-orig"):
            (out / f"video.{code}.vtt").write_text(VTT, encoding="utf-8")
        path, kind = dl._pick_subtitle(out, GREEK_TALK)
        self.assertEqual(path.name, "video.el-orig.vtt")
        self.assertIn("original language", kind)


class DownloadRetry(unittest.TestCase):
    def test_missed_track_is_fetched_again_with_android_vr(self):
        out = Path(tempfile.mkdtemp(prefix="watch-"))
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "--skip-download" not in cmd:
                # First pass: the video and the English translation arrive,
                # the Greek original answers 429.
                (out / "video.mp4").write_bytes(b"x")
                (out / "video.info.json").write_text(json.dumps(GREEK_TALK), encoding="utf-8")
                (out / "video.en.vtt").write_text(VTT, encoding="utf-8")
            else:
                (out / "video.el-orig.vtt").write_text(VTT, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        with mock.patch.object(dl.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(dl.shutil, "which", return_value="/usr/bin/yt-dlp"):
            result = dl.download_url("https://www.youtube.com/watch?v=D8Dg3ORQIOc", out)
        self.assertTrue(result["subtitle_path"].endswith("video.el-orig.vtt"), result)
        self.assertIn("original language", result["subtitle_kind"])
        first, retry = calls
        self.assertIn(".*-orig", first[first.index("--sub-langs") + 1])
        self.assertIn("youtube:player_client=android_vr", retry)
        self.assertEqual(retry[retry.index("--sub-langs") + 1], "el-orig")

    def test_no_retry_when_the_best_track_arrived(self):
        out = Path(tempfile.mkdtemp(prefix="watch-"))
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            (out / "video.mp4").write_bytes(b"x")
            (out / "video.info.json").write_text(json.dumps(GREEK_TALK), encoding="utf-8")
            (out / "video.el-orig.vtt").write_text(VTT, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(dl.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(dl.shutil, "which", return_value="/usr/bin/yt-dlp"):
            dl.download_url("https://www.youtube.com/watch?v=D8Dg3ORQIOc", out)
        self.assertEqual(len(calls), 1)


# YouTube's rolling auto captions, in the exact layout measured on
# D8Dg3ORQIOc (2026-09-27): a word-timed cue opens with a line holding one
# space, then a 10 ms cue repeats the new line alone, then the next cue shows
# that line above the next one.
ROLLING = (
    "WEBVTT\nKind: captions\nLanguage: el\n\n"
    "00:00:00.890 --> 00:00:19.189 align:start position:0%\n \n[music]\n\n"
    "00:00:19.189 --> 00:00:19.199 align:start position:0%\n \n \n\n"
    "00:00:19.199 --> 00:00:22.910 align:start position:0%\n \n"
    "hello<00:00:19.520><c> there</c><00:00:20.359><c> in</c><00:00:20.680><c> 2009</c>\n\n"
    "00:00:22.910 --> 00:00:22.920 align:start position:0%\nhello there in 2009\n \n\n"
    "00:00:22.920 --> 00:00:25.310 align:start position:0%\nhello there in 2009\n"
    "I<00:00:23.199><c> wrote</c><00:00:23.439><c> a</c><00:00:23.880><c> text</c>\n\n"
    "00:00:25.310 --> 00:00:25.320 align:start position:0%\nI wrote a text\n \n\n"
)


class VttParse(unittest.TestCase):
    def test_rolling_captions_give_each_line_once_at_its_start(self):
        path = Path(tempfile.mkdtemp(prefix="vtt-")) / "roll.vtt"
        path.write_text(ROLLING, encoding="utf-8")
        segs = tr.parse_vtt(str(path))
        self.assertEqual([(s["start"], s["text"]) for s in segs],
                         [(0.89, "[music]"), (19.2, "hello there in 2009"),
                          (22.92, "I wrote a text")])

    def test_cues_without_hours_and_entities(self):
        path = Path(tempfile.mkdtemp(prefix="vtt-")) / "a.vtt"
        path.write_text("WEBVTT\n\n00:01.000 --> 00:04.500\nTom &amp; Jerry\n\n"
                        "01:02:03.000 --> 01:02:05.000\n<c>Later</c>\n", encoding="utf-8")
        segs = tr.parse_vtt(str(path))
        self.assertEqual(segs[0], {"start": 1.0, "end": 4.5, "text": "Tom & Jerry"})
        self.assertEqual(segs[1]["start"], 3723.0)


class LocalWhisperLoad(unittest.TestCase):
    def test_whisper_found_beside_the_interpreter(self):
        tmp = tempfile.TemporaryDirectory(prefix="venv-")
        self.addCleanup(tmp.cleanup)
        venv = Path(tmp.name) / "bin"
        venv.mkdir()
        exe = venv / "whisper"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        with mock.patch.object(wh.shutil, "which", return_value=None), \
                mock.patch.object(wh.sys, "executable", str(venv / "python")):
            self.assertEqual(wh.whisper_bin(), str(exe))


class SweepRest20261007(unittest.TestCase):
    """Linux bot sweep 2026-10-07. The config file setup.py writes says to uncomment
    WATCH_LOCAL_WHISPER_MODEL / _DEVICE there, but whisper.py read them from
    the environment only, so the file's setting did nothing.
    And transcript stamps past an hour read [75:30] while the frame list
    beside them says t=1:15:30."""

    def _model_used(self, env, file_text):
        tmp = tempfile.TemporaryDirectory(prefix="watchcfg-")
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / ".env"
        cfg.write_text(file_text, encoding="utf-8")
        audio = Path(tmp.name) / "audio.mp3"
        audio.write_bytes(b"x")
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "stop here")

        clean = {k: v for k, v in wh.os.environ.items() if not k.startswith("WATCH_LOCAL_WHISPER_")}
        clean.update(env)
        with mock.patch.object(wh, "CONFIG_ENV", cfg), \
                mock.patch.object(wh, "whisper_bin", return_value="/venv/bin/whisper"), \
                mock.patch.object(wh, "_isolate_or_refuse", side_effect=lambda c, m, d: c), \
                mock.patch.object(wh.subprocess, "run", side_effect=fake_run), \
                mock.patch.dict(wh.os.environ, clean, clear=True), \
                mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                wh._transcribe_local(audio)
        cmd = seen[0]
        return cmd[cmd.index("--model") + 1], cmd[cmd.index("--device") + 1]

    def test_the_config_file_setting_is_honoured(self):
        self.assertEqual(self._model_used({}, "WATCH_LOCAL_WHISPER_MODEL=tiny\n"), ("tiny", "cpu"))

    def test_the_environment_wins_over_the_file(self):
        self.assertEqual(self._model_used({"WATCH_LOCAL_WHISPER_MODEL": "small"},
                                          "WATCH_LOCAL_WHISPER_MODEL=tiny\n"), ("small", "cpu"))

    def test_a_commented_line_is_ignored(self):
        self.assertEqual(self._model_used({}, "#   WATCH_LOCAL_WHISPER_MODEL=tiny   # note\n"), ("base", "cpu"))

    def test_hour_long_stamps_match_the_frame_list(self):
        out = tr.format_transcript([{"start": 4530.4, "end": 4533.0, "text": "late"},
                                    {"start": 65.0, "end": 66.0, "text": "early"}])
        self.assertIn("[1:15:30] late", out)
        self.assertIn("[01:05] early", out)


if __name__ == "__main__":
    unittest.main()
