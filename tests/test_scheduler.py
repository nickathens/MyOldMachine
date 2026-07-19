"""Regression tests for core.scheduler.

Focused on the _execute_command UnboundLocalError that occurred when
asyncio.create_subprocess_shell raised before `success` was assigned.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import scheduler  # noqa: E402


class ExecuteCommandSuccessPathInitializedTests(unittest.TestCase):
    """`success` must be defined on every exception path of _execute_command.

    The original bug: when the inner try-block raised before reaching
    `success = proc.returncode == 0`, the trailing `if not meta.get("repeat"):`
    branch hit `if success:` with no binding, raising UnboundLocalError and
    masking the real error in the logs.
    """

    def _meta(self, repeat: bool = False) -> dict:
        return {
            "user_id": 12345,
            "name": "test-job",
            "command": "echo hi",
            "weekdays": None,
            "timeout_seconds": 1,
            "repeat": repeat,
            "log_file": None,
            "notify": False,
            "message": "",
        }

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro) if sys.platform == "win32" \
            else asyncio.run(coro)

    def test_subprocess_creation_failure_no_unboundlocal(self):
        """If create_subprocess_shell raises, cleanup must not crash."""
        fake_scheduler = MagicMock()
        fake_scheduler.send_message = AsyncMock(return_value=True)

        with patch.object(scheduler, "get_scheduler", return_value=fake_scheduler), \
             patch.object(scheduler, "_get_meta", return_value=self._meta(repeat=False)), \
             patch.object(scheduler, "_log_execution"), \
             patch.object(scheduler, "_delete_meta") as mock_delete, \
             patch("asyncio.create_subprocess_shell",
                   side_effect=OSError("fork failed")):

            self._run(scheduler._execute_command("job-abc"))

            # Failure path: meta should be kept (NOT deleted) for recovery.
            mock_delete.assert_not_called()

    def test_timeout_no_unboundlocal(self):
        """asyncio.TimeoutError on wait_for must not crash on cleanup branch."""
        fake_scheduler = MagicMock()
        fake_scheduler.send_message = AsyncMock(return_value=True)

        async def _fake_create(*_args, **_kwargs):
            proc = MagicMock()
            proc.pid = 99999
            proc.returncode = -9

            async def _comm():
                await asyncio.sleep(10)
                return (b"", b"")

            proc.communicate = _comm
            proc.wait = AsyncMock()
            proc.terminate = MagicMock()
            proc.kill = MagicMock()
            return proc

        with patch.object(scheduler, "get_scheduler", return_value=fake_scheduler), \
             patch.object(scheduler, "_get_meta", return_value=self._meta(repeat=False)), \
             patch.object(scheduler, "_log_execution"), \
             patch.object(scheduler, "_delete_meta") as mock_delete, \
             patch("asyncio.create_subprocess_shell", side_effect=_fake_create), \
             patch("os.killpg"), patch("os.getpgid", return_value=12345):

            self._run(scheduler._execute_command("job-timeout"))
            # Failure path: meta retained for recovery
            mock_delete.assert_not_called()

    def test_repeat_job_does_not_delete_on_failure(self):
        """Repeat jobs should never delete meta even when success is False."""
        fake_scheduler = MagicMock()
        fake_scheduler.send_message = AsyncMock(return_value=True)

        with patch.object(scheduler, "get_scheduler", return_value=fake_scheduler), \
             patch.object(scheduler, "_get_meta", return_value=self._meta(repeat=True)), \
             patch.object(scheduler, "_log_execution"), \
             patch.object(scheduler, "_delete_meta") as mock_delete, \
             patch("asyncio.create_subprocess_shell",
                   side_effect=RuntimeError("boom")):

            self._run(scheduler._execute_command("job-repeat"))
            mock_delete.assert_not_called()

    def test_missing_meta_returns_early(self):
        """Job with no metadata short-circuits without error."""
        fake_scheduler = MagicMock()

        with patch.object(scheduler, "get_scheduler", return_value=fake_scheduler), \
             patch.object(scheduler, "_get_meta", return_value=None), \
             patch.object(scheduler, "_delete_meta") as mock_delete:
            self._run(scheduler._execute_command("ghost-job"))
            mock_delete.assert_not_called()


class ParseNaturalTimeTests(unittest.TestCase):
    """parse_natural_time covers the most-used inputs."""

    def test_in_minutes(self):
        result = scheduler.parse_natural_time("in 5 minutes")
        self.assertIsNotNone(result)

    def test_in_hours(self):
        result = scheduler.parse_natural_time("in 2 hours")
        self.assertIsNotNone(result)

    def test_iso_format(self):
        result = scheduler.parse_natural_time("2030-01-01T12:00:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2030)

    def test_garbage_returns_none(self):
        self.assertIsNone(scheduler.parse_natural_time("not a real time"))


class SendMessageTimeoutDedupTests(unittest.TestCase):
    """send_message must treat a post-send ReadTimeout as delivered (return True,
    no retry), while still reporting genuine non-delivery as failure (return
    False). This is what stops duplicate notifications when a slow ack times out
    after the request was already sent."""

    def _run(self, coro):
        return asyncio.run(coro)

    @staticmethod
    def _client_raising(exc):
        """Return a fake httpx.AsyncClient factory whose .post() raises `exc`."""
        class _FakeAsyncClient:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_a):
                return False

            async def post(self_inner, *_a, **_k):
                raise exc

        return lambda *_a, **_k: _FakeAsyncClient()

    def test_read_timeout_treated_as_delivered(self):
        sched = scheduler.Scheduler("fake-token")
        factory = self._client_raising(scheduler.httpx.ReadTimeout("slow ack"))
        with patch.object(scheduler.httpx, "AsyncClient", factory):
            result = self._run(sched.send_message(123, "hi"))
        self.assertTrue(result)

    def test_connect_error_reported_as_failure(self):
        sched = scheduler.Scheduler("fake-token")
        factory = self._client_raising(scheduler.httpx.ConnectError("refused"))
        with patch.object(scheduler.httpx, "AsyncClient", factory):
            result = self._run(sched.send_message(123, "hi"))
        self.assertFalse(result)

    def test_retry_loop_does_not_resend_when_delivered(self):
        # End-to-end contract: when send_message reports delivered (as it now
        # does on a post-send ReadTimeout), _send_with_retry must not re-send.
        sched = MagicMock()
        sched.send_message = AsyncMock(return_value=True)
        result = self._run(scheduler._send_with_retry(sched, 123, "hi"))
        self.assertTrue(result)
        sched.send_message.assert_called_once()


class ParseWeekdayTimeTests(unittest.TestCase):
    """A weekday anywhere in the text must win over the bare-time branch.

    'at 5pm on monday' used to resolve to today/tomorrow 17:00 with the
    weekday silently dropped, while 'monday at 5pm' parsed correctly.
    """

    def test_at_time_on_weekday_keeps_weekday(self):
        result = scheduler.parse_natural_time("at 5pm on monday")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 0)
        self.assertEqual((result.hour, result.minute), (17, 0))

    def test_bare_time_on_weekday_keeps_both_time_and_weekday(self):
        result = scheduler.parse_natural_time("5pm on friday")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 4)
        self.assertEqual((result.hour, result.minute), (17, 0))

    def test_hhmm_on_weekday(self):
        result = scheduler.parse_natural_time("at 17:30 on tuesday")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 1)
        self.assertEqual((result.hour, result.minute), (17, 30))

    def test_weekday_first_unchanged(self):
        result = scheduler.parse_natural_time("monday at 5pm")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 0)
        self.assertEqual((result.hour, result.minute), (17, 0))

    def test_weekday_with_bare_hhmm(self):
        result = scheduler.parse_natural_time("friday 17:30")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 4)
        self.assertEqual((result.hour, result.minute), (17, 30))

    def test_weekday_alone_defaults_to_9am(self):
        result = scheduler.parse_natural_time("on wednesday")
        self.assertIsNotNone(result)
        self.assertEqual(result.weekday(), 2)
        self.assertEqual((result.hour, result.minute), (9, 0))

    def test_plain_at_time_without_weekday_unchanged(self):
        result = scheduler.parse_natural_time("at 3pm")
        self.assertIsNotNone(result)
        self.assertEqual((result.hour, result.minute), (15, 0))


class SyncGiveUpTests(unittest.TestCase):
    """The sync loop must not re-schedule a failed one-shot forever.

    Chain being killed: one-shot delivery fails -> executor keeps meta 'for
    recovery' -> fired APS job is gone -> sync re-adds it 5s out -> fires,
    fails again -> repeat every tick until restart. There was no attempt
    counter and the >24h staleness cutoff existed only in startup recovery.
    """

    def _sch(self):
        sch = scheduler.Scheduler.__new__(scheduler.Scheduler)
        sch._aps = MagicMock()
        sch._aps.get_jobs.return_value = []
        sch._notify_admins = MagicMock()
        return sch

    def _meta(self, attempts=0, minutes_past=5, repeat=None):
        return {
            "job_id": "j1", "user_id": 42, "message": "hello",
            "job_type": "reminder", "name": "test-reminder", "notify": True,
            "command": None, "log_file": None, "repeat": repeat,
            "weekdays": None, "channel": "telegram",
            "created_at": datetime.now().isoformat(),
            "run_at": (datetime.now() - timedelta(minutes=minutes_past)).isoformat(),
            "raw_at": "", "created_context": "", "end_date": None,
            "timeout_seconds": None, "recovery_attempts": attempts,
        }

    def test_fresh_past_oneshot_rescheduled_and_counted(self):
        sch = self._sch()
        with patch.object(scheduler, "_get_all_meta", return_value=[self._meta(attempts=0)]), \
             patch.object(scheduler, "_bump_recovery_attempts") as bump, \
             patch.object(scheduler, "_delete_meta") as delete, \
             patch.object(scheduler, "_log_execution"):
            sch.sync_from_meta()
        sch._aps.add_job.assert_called_once()
        bump.assert_called_once_with("j1")
        delete.assert_not_called()
        sch._notify_admins.assert_not_called()

    def test_exhausted_oneshot_discarded_with_admin_alert(self):
        sch = self._sch()
        meta = self._meta(attempts=scheduler.MAX_RECOVERY_ATTEMPTS)
        with patch.object(scheduler, "_get_all_meta", return_value=[meta]), \
             patch.object(scheduler, "_bump_recovery_attempts") as bump, \
             patch.object(scheduler, "_delete_meta") as delete, \
             patch.object(scheduler, "_log_execution") as log:
            sch.sync_from_meta()
        sch._aps.add_job.assert_not_called()
        bump.assert_not_called()
        delete.assert_called_once_with("j1")
        sch._notify_admins.assert_called_once()
        self.assertFalse(log.call_args.args[3])  # logged as failure

    def test_stale_oneshot_discarded_without_alert(self):
        sch = self._sch()
        meta = self._meta(attempts=0, minutes_past=25 * 60)  # >24h past due
        with patch.object(scheduler, "_get_all_meta", return_value=[meta]), \
             patch.object(scheduler, "_bump_recovery_attempts"), \
             patch.object(scheduler, "_delete_meta") as delete, \
             patch.object(scheduler, "_log_execution"):
            sch.sync_from_meta()
        sch._aps.add_job.assert_not_called()
        delete.assert_called_once_with("j1")
        sch._notify_admins.assert_not_called()

    def test_recurring_job_never_hits_giveup(self):
        sch = self._sch()
        meta = self._meta(attempts=99, repeat="daily")
        with patch.object(scheduler, "_get_all_meta", return_value=[meta]), \
             patch.object(scheduler, "_bump_recovery_attempts") as bump, \
             patch.object(scheduler, "_delete_meta") as delete, \
             patch.object(scheduler, "_log_execution"):
            sch.sync_from_meta()
        sch._aps.add_job.assert_called_once()
        bump.assert_not_called()
        delete.assert_not_called()


class HistoryCapTests(unittest.TestCase):
    """History is the only forensic record; the cap must hold at 2000 rows."""

    def test_cap_keeps_newest_2000(self):
        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "history.db"
            with patch.object(scheduler, "SCHEDULER_DIR", Path(td)), \
                 patch.object(scheduler, "HISTORY_DB_PATH", hist):
                scheduler._init_history_db()
                conn = sqlite3.connect(str(hist))
                conn.executemany(
                    "INSERT INTO history (job_id, user_id, message, executed_at, success, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(f"j{i}", 1, "m", "2026-01-01T00:00:00", 1, None) for i in range(2500)],
                )
                conn.commit()
                conn.close()
                scheduler._log_execution("newest", 1, "m", True)
                conn = sqlite3.connect(str(hist))
                count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
                newest = conn.execute(
                    "SELECT job_id FROM history ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                conn.close()
        self.assertEqual(count, scheduler.HISTORY_MAX_ROWS)
        self.assertEqual(newest, "newest")


if __name__ == "__main__":
    unittest.main()
