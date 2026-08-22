"""Tests for the opt-in external heartbeat (utils/heartbeat.py).

Covers the safe-by-default contract: no URL means no ping and a clean exit, a
configured URL pings, and any network failure is swallowed (the script never
raises and always exits 0 so a scheduled run does not error-spam).

Also covers --require-service, the gate that turns a machine-is-alive monitor
into a bot-is-alive one by skipping the ping while the bot is down.
"""
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make MOM importable without spinning up the bot
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils import heartbeat
from core import config


def _mock_urlopen_cm(code=200):
    """A context manager whose response.getcode() returns `code`."""
    resp = MagicMock()
    resp.getcode.return_value = code
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


class SendPingTests(unittest.TestCase):

    def test_empty_url_returns_false_without_network(self):
        with patch("utils.heartbeat.urllib.request.urlopen") as mock_open:
            self.assertFalse(heartbeat.send_ping(""))
        mock_open.assert_not_called()

    def test_2xx_is_success(self):
        with patch("utils.heartbeat.urllib.request.urlopen",
                   return_value=_mock_urlopen_cm(200)):
            self.assertTrue(heartbeat.send_ping("https://example.test/ping"))

    def test_500_is_failure(self):
        with patch("utils.heartbeat.urllib.request.urlopen",
                   return_value=_mock_urlopen_cm(500)):
            self.assertFalse(heartbeat.send_ping("https://example.test/ping"))

    def test_network_error_is_swallowed(self):
        with patch("utils.heartbeat.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("boom")):
            # Must not raise; a failed ping is a False return, not an exception.
            self.assertFalse(heartbeat.send_ping("https://example.test/ping"))


class MainTests(unittest.TestCase):

    def test_no_url_is_noop_and_exits_zero(self):
        with patch("utils.heartbeat.get_heartbeat_url", return_value=""), \
             patch("utils.heartbeat.send_ping") as mock_send:
            rc = heartbeat.main([])
        self.assertEqual(rc, 0)
        mock_send.assert_not_called()  # opt-in: no URL means no ping attempt

    def test_env_url_triggers_ping(self):
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.send_ping", return_value=True) as mock_send:
            rc = heartbeat.main([])
        self.assertEqual(rc, 0)
        mock_send.assert_called_once()

    def test_cli_url_overrides_env(self):
        with patch("utils.heartbeat.get_heartbeat_url", return_value=""), \
             patch("utils.heartbeat.send_ping", return_value=True) as mock_send:
            rc = heartbeat.main(["--url", "https://cli.test/ping"])
        self.assertEqual(rc, 0)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[0], "https://cli.test/ping")

    def test_exit_zero_even_when_ping_fails(self):
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.send_ping", return_value=False):
            rc = heartbeat.main([])
        self.assertEqual(rc, 0)  # never error-spam on a transient blip


class ServiceProbeTests(unittest.TestCase):
    """The two platform probes behind bot_is_running()."""

    @staticmethod
    def _completed(returncode=0, stdout=""):
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        return result

    def test_systemd_active_is_true(self):
        with patch("utils.heartbeat.subprocess.run",
                   return_value=self._completed(0)) as run:
            self.assertIs(heartbeat._systemd_unit_active("mom.service"), True)
        self.assertEqual(run.call_args.args[0],
                         ["systemctl", "is-active", "--quiet", "mom.service"])

    def test_systemd_inactive_is_false(self):
        # `is-active --quiet` exits 3 for inactive, 4 for an unknown unit.
        for rc in (1, 3, 4):
            with self.subTest(returncode=rc):
                with patch("utils.heartbeat.subprocess.run",
                           return_value=self._completed(rc)):
                    self.assertIs(heartbeat._systemd_unit_active("mom.service"), False)

    def test_systemd_missing_is_unknown(self):
        # No systemd on this host: unknown, which is not the same as down.
        with patch("utils.heartbeat.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(heartbeat._systemd_unit_active("mom.service"))

    def test_systemd_timeout_is_unknown(self):
        with patch("utils.heartbeat.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("systemctl", 10)):
            self.assertIsNone(heartbeat._systemd_unit_active("mom.service"))

    def test_launchd_running_job_is_true(self):
        listing = '{\n\t"Label" = "com.myoldmachine.bot";\n\t"PID" = 4242;\n};'
        with patch("utils.heartbeat.subprocess.run",
                   return_value=self._completed(0, listing)) as run:
            self.assertIs(heartbeat._launchd_job_running("com.myoldmachine.bot"), True)
        self.assertEqual(run.call_args.args[0],
                         ["launchctl", "list", "com.myoldmachine.bot"])

    def test_launchd_loaded_without_pid_is_false(self):
        # Loaded but crashed or throttled: no process, so not running.
        listing = '{\n\t"Label" = "com.myoldmachine.bot";\n\t"LastExitStatus" = 1;\n};'
        with patch("utils.heartbeat.subprocess.run",
                   return_value=self._completed(0, listing)):
            self.assertIs(heartbeat._launchd_job_running("com.myoldmachine.bot"), False)

    def test_launchd_not_loaded_is_false(self):
        with patch("utils.heartbeat.subprocess.run",
                   return_value=self._completed(113, "")):
            self.assertIs(heartbeat._launchd_job_running("com.myoldmachine.bot"), False)

    def test_launchd_missing_is_unknown(self):
        with patch("utils.heartbeat.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(heartbeat._launchd_job_running("com.myoldmachine.bot"))

    def test_bot_is_running_dispatches_on_platform(self):
        cases = [
            ("Linux", "_systemd_unit_active", "_launchd_job_running"),
            ("Darwin", "_launchd_job_running", "_systemd_unit_active"),
        ]
        for system, used, unused in cases:
            with self.subTest(platform=system):
                with patch("utils.heartbeat.platform.system", return_value=system), \
                     patch(f"utils.heartbeat.{used}", return_value=True) as chosen, \
                     patch(f"utils.heartbeat.{unused}") as other:
                    self.assertIs(heartbeat.bot_is_running("svc"), True)
                chosen.assert_called_once_with("svc")
                other.assert_not_called()

    def test_bot_is_running_unknown_platform_is_unknown(self):
        with patch("utils.heartbeat.platform.system", return_value="Windows"):
            self.assertIsNone(heartbeat.bot_is_running("svc"))

    def test_bot_is_running_empty_service_probes_nothing(self):
        with patch("utils.heartbeat.subprocess.run") as run:
            self.assertIsNone(heartbeat.bot_is_running(""))
        run.assert_not_called()


class RequireServiceGateTests(unittest.TestCase):
    """--require-service: the ping stops with the bot, and only with the bot."""

    def test_no_flag_means_no_probe(self):
        # Default behaviour must be untouched for existing schedules.
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.bot_is_running") as probe, \
             patch("utils.heartbeat.send_ping", return_value=True) as send:
            rc = heartbeat.main([])
        self.assertEqual(rc, 0)
        probe.assert_not_called()
        send.assert_called_once()

    def test_bot_down_skips_the_ping(self):
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.bot_is_running", return_value=False), \
             patch("utils.heartbeat.send_ping") as send:
            rc = heartbeat.main(["--require-service", "mom.service"])
        self.assertEqual(rc, 0)  # silent skip, never a failed unit
        send.assert_not_called()

    def test_bot_up_pings(self):
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.bot_is_running", return_value=True), \
             patch("utils.heartbeat.send_ping", return_value=True) as send:
            rc = heartbeat.main(["--require-service", "mom.service"])
        self.assertEqual(rc, 0)
        send.assert_called_once()

    def test_unknown_state_fails_open(self):
        """Pinging anyway degrades to machine-and-network monitoring. Staying
        silent would page the operator every interval on a host whose service
        manager cannot be read, and an alert that cries wolf gets muted."""
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.bot_is_running", return_value=None), \
             patch("utils.heartbeat.send_ping", return_value=True) as send:
            rc = heartbeat.main(["--require-service", "mom.service"])
        self.assertEqual(rc, 0)
        send.assert_called_once()

    def test_no_url_short_circuits_before_probing(self):
        # Nothing configured means nothing to gate; do not shell out for it.
        with patch("utils.heartbeat.get_heartbeat_url", return_value=""), \
             patch("utils.heartbeat.bot_is_running") as probe, \
             patch("utils.heartbeat.send_ping") as send:
            rc = heartbeat.main(["--require-service", "mom.service"])
        self.assertEqual(rc, 0)
        probe.assert_not_called()
        send.assert_not_called()

    def test_gate_is_probed_with_the_name_given(self):
        with patch("utils.heartbeat.get_heartbeat_url",
                   return_value="https://example.test/ping"), \
             patch("utils.heartbeat.bot_is_running", return_value=True) as probe, \
             patch("utils.heartbeat.send_ping", return_value=True):
            heartbeat.main(["--require-service", "com.myoldmachine.bot"])
        probe.assert_called_once_with("com.myoldmachine.bot")


class ConfigGetterTests(unittest.TestCase):

    def test_url_empty_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.get_heartbeat_url(), "")

    def test_url_is_stripped(self):
        with patch.dict(os.environ, {"HEARTBEAT_URL": "  https://x.test/p  "}, clear=True):
            self.assertEqual(config.get_heartbeat_url(), "https://x.test/p")

    def test_interval_default_and_floor(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.get_heartbeat_interval_minutes(), 2)
        with patch.dict(os.environ, {"HEARTBEAT_INTERVAL_MIN": "0"}, clear=True):
            self.assertEqual(config.get_heartbeat_interval_minutes(), 1)
        with patch.dict(os.environ, {"HEARTBEAT_INTERVAL_MIN": "5"}, clear=True):
            self.assertEqual(config.get_heartbeat_interval_minutes(), 5)


if __name__ == "__main__":
    unittest.main()
