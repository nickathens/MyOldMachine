"""Regression tests for core.scheduler.

Focused on the _execute_command UnboundLocalError that occurred when
asyncio.create_subprocess_shell raised before `success` was assigned.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
