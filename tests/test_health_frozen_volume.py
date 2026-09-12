#!/usr/bin/env python3
"""Regression tests for the frozen external drive guard in core.health.

Run: python3 -m unittest tests.test_health_frozen_volume  (from repo root)

What happened, 12 Sep 2026, on a Mac mini running this bot: the nightly 5 AM
reboot relaunched Adobe Illustrator, which asked macOS for access to files on
a removable volume. The consent box sat on a screen nobody was looking at.
macOS serialises those approvals, so every later request to read that drive,
from Finder, Spotlight, a root shell and this bot alike, queued behind the
unanswered box: 216 blocked requests by the evening. Each of the bot's turns
that touched the drive produced no output until the 30 minute idle timeout
killed it, and the user was told "the task may have been too complex". Four
turns died that way before anyone knew the drive was involved. The disk itself
was fine throughout, and restarting the notification agent cleared it in ten
milliseconds.

Three things had to change, and each has tests here:

(1) DETECT IT. A listing of every mounted external volume, run from a child
    process that can be abandoned, with a timeout. The bot's own process never
    lists a drive itself, because that is exactly the call that never returns.
(2) SAY IT. An alert names the drive within minutes rather than hours, names
    the consent box when one is on the screen, shows the drive's state in
    /health, and follows up with a recovery line when it answers again.
(3) STOP THE BLEEDING. The system prompt tells the assistant not to touch the
    drive at all while it is frozen, and the idle-timeout message names the
    drive as the likely cause instead of blaming the task.

The probes run a real child process with a stand-in snippet; everything that
reads the machine (mounts, memory, network, the screen) is stubbed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.health as health  # noqa: E402
from core import llm  # noqa: E402


_OK_DISK = {"total_gb": 460.0, "used_gb": 200.0, "free_gb": 260.0, "percent": 43.0}
_OK_MEM = {"total_gb": 24.0, "used_gb": 7.1, "free_gb": 16.9, "percent": 29.5}
_NO_SWAP = {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0}

_HANG = "import time; time.sleep(30)"
_DIALOG = "“Adobe Illustrator 2026” would like to access files on a removable volume."


def _reset():
    health._volume_probe_cache["checked"] = 0.0
    health._volume_probe_cache["frozen"] = []
    health._frozen_reported.clear()
    health._alert_cooldowns.clear()


class ProbeTests(unittest.TestCase):
    """(1) The listing runs in a child that can be abandoned."""

    def setUp(self):
        _reset()

    def test_listing_that_hangs_reads_frozen_within_the_timeout(self):
        with patch.object(health, "_PROBE_SNIPPET", _HANG):
            started = time.monotonic()
            state = health.probe_volume("/irrelevant", timeout=0.3)
            elapsed = time.monotonic() - started
        self.assertEqual(state, health.VOLUME_FROZEN)
        # The timeout plus the grace given to the kill, never the child's sleep.
        self.assertLess(elapsed, 5.0)

    def test_listing_that_returns_reads_ok(self):
        with patch.object(health, "_PROBE_SNIPPET", "pass"):
            self.assertEqual(health.probe_volume("/irrelevant", timeout=10), health.VOLUME_OK)

    def test_not_a_separate_volume_reads_skip(self):
        with patch.object(health, "_PROBE_SNIPPET", "import sys; sys.exit(3)"):
            self.assertEqual(health.probe_volume("/irrelevant", timeout=10), health.VOLUME_SKIP)

    def test_stock_snippet_skips_the_system_disk_and_a_missing_path(self):
        # A directory on the system disk is not a separate volume: skip, not
        # frozen, not ok. A path that does not exist: skip, never frozen.
        self.assertEqual(health.probe_volume(str(ROOT), timeout=15), health.VOLUME_SKIP)
        self.assertEqual(health.probe_volume("/definitely/not/here", timeout=15), health.VOLUME_SKIP)

    def test_volumes_are_probed_together_not_one_after_another(self):
        roots = ["/a", "/b", "/c", "/d"]
        with patch.object(health, "_PROBE_SNIPPET", _HANG):
            started = time.monotonic()
            frozen = health.get_frozen_volumes(timeout=0.4, roots=roots)
            elapsed = time.monotonic() - started
        self.assertEqual(frozen, roots)
        # Four sequential probes would cost at least 1.6 s; together they cost one.
        self.assertLess(elapsed, 1.5)
        self.assertEqual(health.frozen_volumes_known(), roots)

    def test_known_is_a_cache_read_and_never_probes(self):
        health._volume_probe_cache.update(checked=time.time(), frozen=["/Volumes/X"])
        with patch.object(health, "probe_volume", side_effect=AssertionError("probed")):
            self.assertEqual(health.frozen_volumes_known(), ["/Volumes/X"])
            self.assertEqual(health.frozen_volumes_cached(max_age=600), ["/Volumes/X"])
        # Stale: cached() probes again, known() still does not.
        health._volume_probe_cache["checked"] = time.time() - 10_000
        with patch.object(health, "external_volume_roots", return_value=["/Volumes/X"]), \
                patch.object(health, "probe_volume", return_value=health.VOLUME_OK):
            self.assertEqual(health.frozen_volumes_cached(max_age=600), [])
        with patch.object(health, "probe_volume", side_effect=AssertionError("probed")):
            self.assertEqual(health.frozen_volumes_known(), [])


class VolumeRootsTests(unittest.TestCase):
    """The parent process enumerates mount points without ever stat-ing one."""

    def test_macos_lists_volumes_without_touching_them(self):
        listing = {"/Volumes": ["Macintosh HD", "CooCooStorage", "TimeMachine"]}
        with patch.object(health.platform, "system", return_value="Darwin"), \
                patch.object(health.os, "listdir", side_effect=lambda p: listing[p]), \
                patch.object(health.os, "stat", side_effect=AssertionError("stat on a mount point")), \
                patch.object(health.os, "lstat", side_effect=AssertionError("lstat on a mount point")):
            roots = health.external_volume_roots()
        # Sorted, unfiltered: the child decides what is a real volume.
        self.assertEqual(roots, ["/Volumes/CooCooStorage", "/Volumes/Macintosh HD", "/Volumes/TimeMachine"])

    def test_linux_lists_media_and_mnt_mounts(self):
        listing = {"/media": ["nick"], "/media/nick": ["USB2", "USB1"], "/mnt": ["nas"]}

        def listdir(path):
            if path in listing:
                return listing[path]
            raise FileNotFoundError(path)

        with patch.object(health.platform, "system", return_value="Linux"), \
                patch.object(health.os, "listdir", side_effect=listdir):
            roots = health.external_volume_roots()
        self.assertEqual(roots, ["/media/nick/USB1", "/media/nick/USB2", "/mnt/nas"])

    def test_no_mount_bases_means_no_roots(self):
        with patch.object(health.platform, "system", return_value="Linux"), \
                patch.object(health.os, "listdir", side_effect=FileNotFoundError):
            self.assertEqual(health.external_volume_roots(), [])


class AlertTests(unittest.TestCase):
    """(2) check_critical and the cooldown key."""

    def setUp(self):
        _reset()
        health._consecutive_cpu_breaches = 0
        health._consecutive_net_failures = 0
        patchers = [
            patch.object(health, "get_disk_usage", return_value=_OK_DISK),
            patch.object(health, "get_memory_usage", return_value=_OK_MEM),
            patch.object(health, "get_memory_pressure", return_value=health.PRESSURE_NORMAL),
            patch.object(health, "get_swap_usage", return_value=_NO_SWAP),
            patch.object(health, "get_cpu_usage", return_value=5.0),
            patch.object(health, "get_network_status", return_value=True),
            patch.object(health, "get_system_uptime_seconds",
                         return_value=health._SETTLING_WINDOW_SECONDS + 60),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_check_critical_speaks_when_a_drive_is_frozen_and_not_otherwise(self):
        quiet = [a for a in health.check_critical() if "not responding" in a]
        self.assertEqual(quiet, [])
        health._volume_probe_cache.update(checked=time.time(), frozen=["/Volumes/X"])
        hits = [a for a in health.check_critical() if "Storage drive not responding" in a]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].startswith("CRITICAL"), hits[0])
        self.assertIn("/Volumes/X", hits[0])

    def test_check_critical_reads_the_cache_and_never_probes(self):
        # It runs inside the event loop's executor; a probe's timeout on top
        # of its own work is not a price it may pay.
        with patch.object(health, "probe_volume", side_effect=AssertionError("probed")), \
                patch.object(health, "external_volume_roots", side_effect=AssertionError("enumerated")):
            health.check_critical()

    def test_cooldown_key_ignores_the_measured_seconds_but_not_the_drive(self):
        with patch.object(health, "_VOLUME_PROBE_TIMEOUT", 5.0):
            five = health.volume_alert("/Volumes/X")
        with patch.object(health, "_VOLUME_PROBE_TIMEOUT", 7.0):
            seven = health.volume_alert("/Volumes/X")
        self.assertNotEqual(five, seven)
        self.assertEqual(health._alert_key(five), health._alert_key(seven))
        self.assertNotEqual(health._alert_key(five), health._alert_key(health.volume_alert("/Volumes/Y")))

    def test_alert_says_what_to_do(self):
        text = health.volume_alert("/Volumes/X").lower()
        self.assertIn("screen", text)
        self.assertIn("replug", text)
        self.assertIn("restart", text)


class RunVolumeCheckTests(unittest.TestCase):
    """(2) The five minute check: alert, cooldown, recovery, re-alert."""

    def setUp(self):
        _reset()

    def _run(self, sequence, prompt=None):
        sent = []

        async def send(uid, text):
            sent.append((uid, text))
            return True

        answers = iter(sequence)
        with patch.object(health, "get_frozen_volumes", side_effect=lambda *a, **k: next(answers)), \
                patch.object(health, "pending_removable_volume_prompt", return_value=prompt):
            for _ in sequence:
                asyncio.run(health.run_volume_check(send, [7]))
        return sent

    def test_alert_once_then_silence_then_recovery_then_alert_again(self):
        sent = self._run([["/Volumes/X"], ["/Volumes/X"], [], ["/Volumes/X"]], prompt=_DIALOG)
        self.assertEqual([uid for uid, _ in sent], [7, 7, 7])
        first, recovery, again = (text for _, text in sent)
        self.assertIn("Health Alert", first)
        self.assertIn("/Volumes/X", first)
        self.assertIn("Adobe Illustrator", first)
        self.assertIn("responding again", recovery)
        self.assertIn("/Volumes/X", recovery)
        self.assertNotIn("Adobe Illustrator", recovery)
        self.assertIn("not responding", again)

    def test_silent_when_nothing_is_frozen(self):
        self.assertEqual(self._run([[], []]), [])

    def test_alert_without_a_dialog_still_says_what_to_do(self):
        sent = self._run([["/Volumes/X"]], prompt=None)
        self.assertEqual(len(sent), 1)
        self.assertIn("screen", sent[0][1].lower())
        self.assertNotIn("On the screen right now", sent[0][1])

    def test_every_admin_hears_it(self):
        sent = []

        async def send(uid, text):
            sent.append(uid)
            return True

        with patch.object(health, "get_frozen_volumes", return_value=["/Volumes/X"]), \
                patch.object(health, "pending_removable_volume_prompt", return_value=None):
            asyncio.run(health.run_volume_check(send, [1, 2]))
        self.assertEqual(sent, [1, 2])


class NoticeTests(unittest.TestCase):
    """(3) What the assistant is told."""

    def test_paths_leading_to_reads_symlinks_without_touching_the_volume(self):
        with TemporaryDirectory() as home:
            os.symlink("/Volumes/X/PROJECTS", os.path.join(home, "projects"))
            os.symlink("/Volumes/X", os.path.join(home, "storage"))
            os.symlink("/Volumes/XY/other", os.path.join(home, "lookalike"))
            os.symlink("/usr/local", os.path.join(home, "elsewhere"))
            os.mkdir(os.path.join(home, "plain"))
            self.assertEqual(health.paths_leading_to("/Volumes/X", home), ["~/projects", "~/storage"])

    def test_notice_names_the_drive_the_paths_and_the_rule(self):
        with TemporaryDirectory() as home:
            os.symlink("/Volumes/X/CODE", os.path.join(home, "Code"))
            text = health.frozen_volume_notice(["/Volumes/X"], home)
            self.assertEqual(health.frozen_volume_notice([], home), "")
        self.assertIn("STORAGE DRIVE NOT RESPONDING", text)
        self.assertIn("/Volumes/X", text)
        self.assertIn("~/Code", text)
        self.assertIn("Do NOT touch", text)

    def test_prompt_carries_the_notice_only_while_a_drive_is_frozen(self):
        os.environ["MOM_TEST"] = "1"
        import bot as botmod
        # Same fixture as test_active_projects_context: the prompt reads and
        # creates per-user state under DATA_DIR, so point it at a temp tree.
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "memory" / "projects").mkdir(parents=True)
            # The session and user-dir helpers resolve through core.config, not
            # bot.DATA_DIR, and create the user's folders as a side effect; keep
            # that inside the temp tree too so the repo's data/ stays untouched.
            with patch.object(botmod, "DATA_DIR", data), \
                    patch.object(botmod, "get_user_dir",
                                 side_effect=lambda uid: data / "users" / str(uid)), \
                    patch.object(botmod, "get_session") as session:
                session.return_value.load_summary.return_value = None
                with patch.object(botmod, "frozen_volumes_known", return_value=["/Volumes/X"]):
                    text = botmod.build_system_prompt(user_id=111111111)
                with patch.object(botmod, "frozen_volumes_known", return_value=[]):
                    quiet = botmod.build_system_prompt(user_id=111111111)
        self.assertIn("STORAGE DRIVE NOT RESPONDING", text)
        self.assertIn("/Volumes/X", text)
        self.assertNotIn("STORAGE DRIVE NOT RESPONDING", quiet)


class TimeoutMessageTests(unittest.TestCase):
    """(3) The idle timeout names the drive instead of blaming the task."""

    def setUp(self):
        _reset()

    def test_names_the_drive_instead_of_blaming_the_task(self):
        health._volume_probe_cache.update(checked=time.time(), frozen=["/Volumes/X"])
        msg = llm._idle_timeout_message("Claude", 30, "Bash", "")
        self.assertIn("Claude stopped responding after 30 minutes", msg)
        self.assertIn("Was running: Bash", msg)
        self.assertIn("/Volumes/X", msg)
        self.assertNotIn("too complex", msg)

    def test_without_a_frozen_drive_the_old_wording_stands(self):
        msg = llm._idle_timeout_message("Claude", 30, "Bash", "")
        self.assertIn("too complex", msg)
        self.assertNotIn("/Volumes", msg)
        saved = llm._idle_timeout_message("Codex", 30, None, "some partial text")
        self.assertIn("/recover", saved)
        self.assertNotIn("too complex", saved)
        self.assertNotIn("Was running", saved)

    def test_timeout_path_reads_the_cache_and_never_probes(self):
        with patch.object(health, "probe_volume", side_effect=AssertionError("probed")), \
                patch.object(health, "external_volume_roots", side_effect=AssertionError("enumerated")):
            llm._idle_timeout_message("Claude", 30, "Bash", "")


if __name__ == "__main__":
    unittest.main()
