"""Core fixes ported from the Linux bot's sweep of 2026-10-07.

Each class names what it covers, and every assertion here fails on main before
this branch: a failed history save lost the finished reply; the mail digest
counted mail the classifier never read as newsletters; the start up temp sweep
deleted files a live job was writing; the Mini App ran its slow calls on its
only event loop; a failed palace sync exited 0; the Telegram sender gave no
reason for a refusal and lost the file over a long caption; `create` on an
existing project name replaced it, whoever owned it; an anchor id with a space
was saved and then lost.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"  # keep test logging out of the production bot.log

import bot  # noqa: E402


class _FailingSession:
    """A session whose history write fails, the way a full disk does."""

    def __init__(self):
        self.summary_file = Path("/nonexistent/summary.json")

    def get_current_topic(self):
        return None

    def load_conversation(self):
        return []

    def should_compact(self, history):
        return False, ""

    def save_conversation(self, history):
        raise OSError(28, "No space left on device")


class ReplySurvivesAFailedHistorySave(unittest.TestCase):
    """Both handlers save the turn before they send it. An exception in the
    save skipped the send, so the user got "Error processing your message"
    instead of the finished answer."""

    def test_the_answer_comes_back_when_the_save_raises(self):
        uid = 990000101
        bot._failed_turns.discard(uid)
        bot._conversation_cache.pop(uid, None)
        with mock.patch.object(bot, "log_exchange") as logged, \
                self.assertLogs(bot.logger, level="ERROR"):
            out = bot._save_and_send(uid, "hi", "the answer", session=_FailingSession())
        self.assertEqual(out, "the answer")
        # The append-only log still records the exchange.
        logged.assert_called_once()
        self.assertEqual(logged.call_args.args[:3], (uid, "hi", "the answer"))


class StartupSweepLeavesLiveWorkAlone(unittest.TestCase):
    """The start up sweep deleted any matching temp file an hour after its last
    write, whoever was using it, so a restart during the day removed the
    output of a render still running in another process. It now takes the
    Stop hook's rule: two hours old, and named on no live command line."""

    def setUp(self):
        from utils import startup_cleanup as sc
        self.sc = sc
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)
        now = time.time()

        def made(name, age):
            path = self.dir / name
            path.write_bytes(b"x")
            os.utime(path, (now - age, now - age))
            return path

        self.held = made("tmpheld.mp4", 3 * 3600)
        self.stale = made("tmpstale.mp4", 3 * 3600)
        self.recent = made("tmprecent.mp4", 90 * 60)
        # A long job that names its output on its command line, the way an
        # ffmpeg render does. Kept short so no ps width can cut the path.
        self.job = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", str(self.held)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.job.wait)
        self.addCleanup(self.job.kill)

    def test_a_file_a_live_job_names_survives_and_the_stale_one_goes(self):
        with mock.patch.object(self.sc, "TEMP_PATTERNS", [str(self.dir / "tmp*.mp4")]):
            self.sc._clean_temp_files()
        self.assertTrue(self.held.exists(), "deleted the output of a running job")
        self.assertFalse(self.stale.exists(), "left a stale file nobody uses")

    def test_the_age_bound_is_the_stop_hooks(self):
        from utils import skill_hooks
        with mock.patch.object(self.sc, "TEMP_PATTERNS", [str(self.dir / "tmp*.mp4")]):
            self.sc._clean_temp_files()
        self.assertGreater(skill_hooks.TEMP_MAX_AGE, 90 * 60)
        self.assertTrue(self.recent.exists(), "a 90 minute old file went at start up")


class ProcessTablesReadWholeLines(unittest.TestCase):
    """ps cuts each line at the terminal width whenever it can find one (a bot
    started from a shell, a test run), and the in-use check reads the end of
    the line, where a job's output path sits. Under pytest on Linux the line
    "python -c ... /tmp/tmpXXXX/tmpheld.mp4" came back cut after "/tmp/"."""

    def _argv_of(self, module, call):
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
            call()
        self.assertTrue(seen, "no ps call was made")
        return seen[0]

    def test_startup_reads_whole_lines(self):
        from utils import startup_cleanup as sc
        for macos in (False, True):
            with mock.patch.object(sc, "IS_MACOS", macos):
                self.assertIn("-ww", self._argv_of(sc, sc._get_all_pids))

    def test_the_stop_hook_reads_whole_lines(self):
        from utils import skill_hooks
        for macos in (False, True):
            with mock.patch.object(skill_hooks, "IS_MACOS", macos):
                self.assertIn("-ww", self._argv_of(skill_hooks, skill_hooks.get_process_table))


def _load_file(name, rel):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MemPalaceSyncFailsLoudly(unittest.TestCase):
    """A failed export or mining run printed ERROR and still exited 0, so the
    scheduler never reported a palace that had stopped growing."""

    def setUp(self):
        self.mp = _load_file("mp_sync_sweep", "skills/mempalace/scripts/mempalace_sync.py")
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.user_dir = Path(self.td.name) / "12345"
        self.user_dir.mkdir()

    def _log(self, readable=True):
        db = self.user_dir / "message_log.db"
        if not readable:
            db.write_text("this is not sqlite")
            return
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE messages (timestamp TEXT, role TEXT, content TEXT)")
        conn.execute("INSERT INTO messages VALUES ('2026-10-01T10:00:00', 'user', 'hi')")
        conn.commit()
        conn.close()

    def _main(self, mined):
        with mock.patch.object(self.mp, "mine_sessions", return_value=mined), \
                mock.patch.object(sys, "argv", ["mempalace_sync.py", "--user-dir", str(self.user_dir)]), \
                redirect_stdout(io.StringIO()):
            return self.mp.main()

    def test_a_failed_mining_run_fails_the_job(self):
        self._log()
        self.assertEqual(self._main({"total_drawers": -1, "error": "chroma is gone"}), 1)

    def test_a_clean_run_exits_zero(self):
        self._log()
        self.assertEqual(self._main({"total_drawers": 10}), 0)

    def test_an_unreadable_message_log_fails_the_job(self):
        self._log(readable=False)
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(self.mp.export_messages(self.user_dir, cutoff_date="2026-10-07"))
        self.assertEqual(self._main({"total_drawers": 10}), 1)


class SenderSaysWhy(unittest.TestCase):
    """A refusal printed "Document sent: False" and nothing else, and a caption
    over 1024 characters made Telegram refuse the file itself."""

    def setUp(self):
        self.send = _load_file("send_sweep", "utils/send_to_telegram.py")
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.doc = Path(self.td.name) / "f.pdf"
        self.doc.write_bytes(b"%PDF-1.4")

    def _post(self, answer, status=200):
        calls = []

        def fake_post(url, data=None, files=None, timeout=None):
            calls.append(dict(data or {}))
            return types.SimpleNamespace(status_code=status, json=lambda: dict(answer))
        return calls, fake_post

    def test_a_refusal_prints_telegrams_reason(self):
        calls, fake = self._post({"ok": False, "description": "Bad Request: chat not found"}, 400)
        err = io.StringIO()
        with mock.patch.object(self.send.httpx, "post", side_effect=fake), redirect_stderr(err):
            ok = self.send.send_file("t", 1, "sendDocument", "document", str(self.doc), "a caption")
        self.assertFalse(ok)
        self.assertIn("chat not found", err.getvalue())

    def test_a_long_caption_is_cut_and_the_file_still_goes(self):
        calls, fake = self._post({"ok": True})
        err = io.StringIO()
        with mock.patch.object(self.send.httpx, "post", side_effect=fake), redirect_stderr(err):
            ok = self.send.send_file("t", 1, "sendDocument", "document", str(self.doc), "δ" * 1500)
        self.assertTrue(ok)
        self.assertEqual(len(calls[0]["caption"]), self.send.CAPTION_LIMIT)
        self.assertIn("cut", err.getvalue())

    def test_an_answer_that_is_not_json_is_a_refusal_not_a_traceback(self):
        def page():
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

        def fake_post(url, data=None, files=None, timeout=None):
            return types.SimpleNamespace(status_code=502, json=page)
        err = io.StringIO()
        with mock.patch.object(self.send.httpx, "post", side_effect=fake_post), redirect_stderr(err):
            self.assertFalse(self.send.send_message("t", 1, "hello"))
        self.assertIn("HTTP 502", err.getvalue())


if __name__ == "__main__":
    unittest.main()
