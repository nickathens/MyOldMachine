"""Failure controls for mount discovery, recovery and alert delivery."""
import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, mock_open, patch

from core import health


class VolumeReviewTests(unittest.TestCase):
    def setUp(self):
        health._volume_probe_cache.update(checked=0.0, frozen=[], states={})
        health._frozen_reported.clear()
        health._alert_cooldowns.clear()
        self.addCleanup(self._clear)

    def _clear(self):
        health._volume_probe_cache.update(checked=0.0, frozen=[], states={})
        health._frozen_reported.clear()
        health._alert_cooldowns.clear()

    def test_linux_discovery_never_lists_a_direct_media_mount(self):
        mounts = (
            "1 0 8:1 / / rw - ext4 /dev/sda1 rw\n"
            "2 1 8:2 / /media/USB1 rw - ext4 /dev/sdb1 rw\n"
            "3 1 8:3 / /media/alice/My\\040Disk rw - ext4 /dev/sdc1 rw\n"
            "4 1 8:4 / /mnt rw - ext4 /dev/sdd1 rw\n"
            "5 1 8:5 / /run/media/alice/USB2 rw - ext4 /dev/sde1 rw\n"
        )
        with patch.object(health.platform, "system", return_value="Linux"), \
                patch("builtins.open", mock_open(read_data=mounts)), \
                patch.object(health.os, "listdir", side_effect=AssertionError("unsafe listing")):
            roots = health.external_volume_roots()
        self.assertEqual(set(roots), {
            "/media/USB1", "/media/alice/My Disk", "/mnt", "/run/media/alice/USB2",
        })

    def test_numeric_drive_names_have_separate_cooldowns(self):
        self.assertNotEqual(health._alert_key(health.volume_alert("/mnt/USB1")),
                            health._alert_key(health.volume_alert("/mnt/USB2")))

    def test_both_numbered_drives_are_named_in_the_delivered_alert(self):
        send = AsyncMock(return_value=True)
        self._check({"/mnt/USB1": "frozen", "/mnt/USB2": "frozen"}, send)
        self.assertIn("/mnt/USB1", send.await_args.args[1])
        self.assertIn("/mnt/USB2", send.await_args.args[1])

    def test_discovery_failure_preserves_warning_and_returns_to_monitor(self):
        send = AsyncMock(return_value=True)
        self._check({"/mnt/USB": "frozen"}, send)
        with patch.object(health, "external_volume_roots", side_effect=OSError("unavailable")):
            asyncio.run(health.run_volume_check(send, [7]))
        self.assertEqual(health.frozen_volumes_known(), ["/mnt/USB"])
        self.assertEqual(send.await_count, 1)

    def test_regular_health_check_does_not_duplicate_volume_delivery(self):
        send = AsyncMock(return_value=True)
        self._check({"/mnt/USB": "frozen"}, send)
        with patch.object(health, "check_critical", return_value=[health.volume_alert("/mnt/USB")]):
            asyncio.run(health.run_health_check(send, [7]))
        self.assertEqual(send.await_count, 1)

    def test_drive_alert_repeats_after_four_hours(self):
        send = AsyncMock(return_value=True)
        with patch.object(health.time, "time", return_value=100_000):
            self._check({"/mnt/USB": "frozen"}, send)
        with patch.object(health.time, "time", return_value=100_300):
            self._check({"/mnt/USB": "frozen"}, send)
        self.assertEqual(send.await_count, 1)
        with patch.object(health.time, "time", return_value=114_400):
            self._check({"/mnt/USB": "frozen"}, send)
        self.assertEqual(send.await_count, 2)

    def test_failed_probe_launch_is_unknown_not_skipped(self):
        with patch.object(health.subprocess, "Popen", side_effect=OSError("no resources")):
            self.assertEqual(health.probe_volume("/mnt/USB"), "unknown")

    def test_failed_listing_is_unknown_not_skipped(self):
        with patch.object(health, "_PROBE_SNIPPET", "raise PermissionError('denied')"):
            self.assertEqual(health.probe_volume("/mnt/USB"), "unknown")

    def test_stock_probe_does_not_hide_permission_failure_during_stat(self):
        prefix = (
            "import os\n"
            "def denied(*a, **k): raise PermissionError('denied')\n"
            "os.stat = denied\n"
            "os.lstat = denied\n"
        )
        with patch.object(health, "_PROBE_SNIPPET", prefix + health._PROBE_SNIPPET):
            self.assertEqual(health.probe_volume("/mnt/USB"), "unknown")

    def _check(self, states, send, admins=(7,)):
        with patch.object(health, "probe_volumes", return_value=states), \
                patch.object(health, "pending_removable_volume_prompt", return_value=None):
            asyncio.run(health.run_volume_check(send, list(admins)))

    def test_removed_drive_is_not_reported_as_recovered(self):
        send = AsyncMock(return_value=True)
        self._check({"/mnt/USB": "frozen"}, send)
        self._check({}, send)
        self.assertEqual(send.await_count, 1)

    def test_failed_probe_does_not_clear_frozen_warning_or_claim_recovery(self):
        send = AsyncMock(return_value=True)
        self._check({"/mnt/USB": "frozen"}, send)
        self._check({"/mnt/USB": "unknown"}, send)
        self.assertEqual(health.frozen_volumes_known(), ["/mnt/USB"])
        self.assertEqual(send.await_count, 1)
        self._check({"/mnt/USB": "ok"}, send)
        self.assertIn("responding again", send.await_args.args[1])
        self.assertEqual(send.await_count, 2)

    def test_a_plain_directory_after_unmount_is_not_recovery(self):
        send = AsyncMock(return_value=True)
        self._check({"/mnt/USB": "frozen"}, send)
        self._check({"/mnt/USB": "skip"}, send)
        self.assertEqual(send.await_count, 1)

    def test_failed_alert_delivery_retries_without_waiting_four_hours(self):
        send = AsyncMock(side_effect=[False, True])
        self._check({"/mnt/USB": "frozen"}, send)
        self._check({"/mnt/USB": "frozen"}, send)
        self.assertEqual(send.await_count, 2)

    def test_failed_recovery_delivery_retries(self):
        send = AsyncMock(side_effect=[True, False, True])
        self._check({"/mnt/USB": "frozen"}, send)
        self._check({"/mnt/USB": "ok"}, send)
        self._check({"/mnt/USB": "ok"}, send)
        self.assertEqual(send.await_count, 3)
        self.assertIn("responding again", send.await_args.args[1])

    def test_one_admin_failure_retries_only_that_admin(self):
        send = AsyncMock(side_effect=[True, OSError("offline"), True])
        self._check({"/mnt/USB": "frozen"}, send, (7, 8))
        self._check({"/mnt/USB": "frozen"}, send, (7, 8))
        self.assertEqual([c.args[0] for c in send.await_args_list], [7, 8, 8])

    def test_never_send_recovery_to_admin_who_missed_the_alert(self):
        send = AsyncMock(side_effect=[True, False, True])
        self._check({"/mnt/USB": "frozen"}, send, (7, 8))
        self._check({"/mnt/USB": "ok"}, send, (7, 8))
        self.assertEqual([c.args[0] for c in send.await_args_list], [7, 8, 7])


class TimeoutStreamTests(unittest.IsolatedAsyncioTestCase):
    async def _reply(self, engine):
        from core import llm
        event = ({"type": "result", "result": "Checking files"} if engine == "Claude" else
                 {"type": "item.completed", "item": {
                     "type": "agent_message", "text": "Checking files"}})
        script = f"import time; print({json.dumps(event)!r}, flush=True); time.sleep(30)"
        spawn = asyncio.create_subprocess_exec
        read_line = llm._read_line_with_timeout

        async def fake_cli(*args, **kwargs):
            # A real process/pipe, with no provider login, network or hooks.
            kwargs["env"] = {"PATH": os.defpath}
            return await spawn(sys.executable, "-c", script, **kwargs)

        async def short_read(stream, timeout):
            return await read_line(stream, 0.05)

        cls = llm.ClaudeCLIProvider if engine == "Claude" else llm.CodexCLIProvider
        provider = cls("test-model")
        provider._cli_binary = sys.executable
        provider.IDLE_TIMEOUT = 0.2
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(health, "frozen_volumes_known", return_value=["/mnt/USB"]), \
                patch.object(llm, "_codex_feature_names", return_value=frozenset()), \
                patch("asyncio.create_subprocess_exec", side_effect=fake_cli), \
                patch.object(llm, "_read_line_with_timeout", side_effect=short_read):
            provider._bot_dir = Path(temp)
            return await asyncio.wait_for(provider.complete("test", []), 5)

    async def test_codex_partial_reply_still_names_the_frozen_drive(self):
        reply = await self._reply("Codex")
        self.assertIn("Checking files", reply.text)
        self.assertIn("/mnt/USB", reply.text)

    async def test_claude_result_followed_by_stall_names_the_frozen_drive(self):
        reply = await self._reply("Claude")
        self.assertIn("Checking files", reply.text)
        self.assertIn("/mnt/USB", reply.text)


class VolumeMonitorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_monitor_checks_drives_immediately_and_full_health_after_settling(self):
        with patch.dict(os.environ, {"MOM_TEST": "1"}):
            import bot
        clock = [0.0]
        seen = []

        async def volumes(*args):
            seen.append(("drive", clock[0]))

        async def full_health(*args):
            seen.append(("full", clock[0]))

        async def sleep(delay):
            clock[0] += delay
            if clock[0] >= 900:
                raise asyncio.CancelledError

        fake_asyncio = types.SimpleNamespace(
            get_running_loop=lambda: types.SimpleNamespace(time=lambda: clock[0]), sleep=sleep,
        )
        with patch.object(bot, "asyncio", fake_asyncio), \
                patch.object(bot, "run_volume_check", side_effect=volumes), \
                patch.object(bot, "run_health_check", side_effect=full_health), \
                patch.object(bot, "get_allowed_users", return_value=[7]), \
                patch.object(bot, "is_admin", return_value=True), \
                patch.object(bot, "_last_health_check", None):
            with self.assertRaises(asyncio.CancelledError):
                await bot._health_monitor_loop(types.SimpleNamespace(send_message=AsyncMock()))
        self.assertEqual(seen, [("drive", 0.0), ("drive", 300.0),
                                ("full", 300.0), ("drive", 600.0)])


if __name__ == "__main__":
    unittest.main()
