"""Tests for the opt-in external heartbeat (utils/heartbeat.py).

Covers the safe-by-default contract: no URL means no ping and a clean exit, a
configured URL pings, and any network failure is swallowed (the script never
raises and always exits 0 so a scheduled run does not error-spam).
"""
import os
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
