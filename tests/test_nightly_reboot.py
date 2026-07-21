"""Unit tests for utils.nightly_reboot.

Every test drives the decision logic with the reboot execution replaced by a
recording fake, so no test ever reboots (or even shells out to) the real
machine. The four gates -- config, misfire window, boot recency, stranding --
are each exercised in isolation, plus the cross-platform reboot command,
uptime parsing, the stranding guard on Linux and macOS, and alert throttling.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import nightly_reboot as nr  # noqa: E402


def _completed(returncode=0, stderr=""):
    """A stand-in for subprocess.CompletedProcess without rebooting."""
    m = MagicMock()
    m.returncode = returncode
    m.stderr = stderr
    return m


# A datetime squarely inside the default 05:00 window, and one well outside it.
IN_WINDOW = datetime(2026, 7, 21, 5, 0, 0)
OUT_WINDOW = datetime(2026, 7, 21, 12, 0, 0)


class RunGateTests(unittest.TestCase):
    """The four gates in utils.nightly_reboot.run(), each in isolation."""

    def setUp(self):
        # Silence disk logging for every run() test.
        self._log_patch = patch.object(nr, "_log")
        self._log_patch.start()
        self.reboot_fn = MagicMock(return_value=_completed(0))

    def tearDown(self):
        self._log_patch.stop()

    def _run(self, config=None, **kwargs):
        cfg = {"nightly_reboot": True, "nightly_reboot_hour": 5,
               "nightly_reboot_minute": 0}
        if config:
            cfg.update(config)
        with patch("utils.maintenance.load_config", return_value=cfg):
            kwargs.setdefault("now", IN_WINDOW)
            kwargs.setdefault("reboot_fn", self.reboot_fn)
            return nr.run(**kwargs)

    # Gate 1 -------------------------------------------------------------
    def test_disabled_config_never_reboots(self):
        code = self._run(config={"nightly_reboot": False})
        self.assertEqual(code, "disabled")
        self.reboot_fn.assert_not_called()

    # Gate 2 -------------------------------------------------------------
    def test_misfire_outside_window_skips(self):
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "ok")):
            code = self._run(now=OUT_WINDOW)
        self.assertEqual(code, "misfire")
        self.reboot_fn.assert_not_called()

    def test_force_bypasses_window(self):
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "ok")):
            code = self._run(now=OUT_WINDOW, force=True)
        self.assertEqual(code, "rebooted")
        self.reboot_fn.assert_called_once()

    # Gate 3 -------------------------------------------------------------
    def test_recent_boot_skips(self):
        with patch.object(nr, "_uptime_seconds", return_value=600), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "ok")):
            code = self._run()
        self.assertEqual(code, "recent-boot")
        self.reboot_fn.assert_not_called()

    def test_unreadable_uptime_fails_open(self):
        # None uptime must NOT block the reboot (fail open, like production).
        with patch.object(nr, "_uptime_seconds", return_value=None), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "ok")):
            code = self._run()
        self.assertEqual(code, "rebooted")
        self.reboot_fn.assert_called_once()

    # Gate 4 -------------------------------------------------------------
    def test_stranding_guard_refuses_and_alerts(self):
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot",
                          return_value=(False, "service not enabled")), \
             patch.object(nr, "_should_alert", return_value=True) as should_alert, \
             patch.object(nr, "_alert") as alert:
            code = self._run(user_id=42)
        self.assertEqual(code, "stranded")
        self.reboot_fn.assert_not_called()
        should_alert.assert_called_once()
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0], 42)

    def test_stranding_alert_throttled(self):
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot",
                          return_value=(False, "no auto-login")), \
             patch.object(nr, "_should_alert", return_value=False), \
             patch.object(nr, "_alert") as alert:
            code = self._run(user_id=42)
        self.assertEqual(code, "stranded")
        alert.assert_not_called()  # throttled: still refuses, just doesn't ping

    # Happy path + failure ----------------------------------------------
    def test_all_gates_pass_reboots(self):
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "enabled")):
            code = self._run()
        self.assertEqual(code, "rebooted")
        self.reboot_fn.assert_called_once()

    def test_reboot_failure_alerts(self):
        self.reboot_fn.return_value = _completed(returncode=1, stderr="no sudo")
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "enabled")), \
             patch.object(nr, "_should_alert", return_value=True), \
             patch.object(nr, "_alert") as alert:
            code = self._run(user_id=7)
        self.assertEqual(code, "reboot-failed")
        alert.assert_called_once()

    def test_dry_run_never_reboots_even_when_gates_pass(self):
        with patch.object(nr, "_uptime_seconds", return_value=10 ** 6), \
             patch.object(nr, "_bot_returns_after_reboot", return_value=(True, "ok")):
            code = self._run(dry_run=True, now=OUT_WINDOW)
        self.assertEqual(code, "dry-run")
        self.reboot_fn.assert_not_called()


class WindowTests(unittest.TestCase):
    """_within_window: default target 05:00, window [04:55, 05:30]."""

    def test_inside(self):
        for t in (datetime(2026, 1, 1, 4, 55), datetime(2026, 1, 1, 5, 0),
                  datetime(2026, 1, 1, 5, 30)):
            self.assertTrue(nr._within_window(t, 5, 0), t)

    def test_outside(self):
        for t in (datetime(2026, 1, 1, 4, 54), datetime(2026, 1, 1, 5, 31),
                  datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 0, 0)):
            self.assertFalse(nr._within_window(t, 5, 0), t)


class RebootCommandTests(unittest.TestCase):
    def test_linux(self):
        with patch.object(nr.platform, "system", return_value="Linux"):
            self.assertEqual(nr._reboot_command(), "systemctl reboot")

    def test_darwin(self):
        with patch.object(nr.platform, "system", return_value="Darwin"):
            self.assertEqual(nr._reboot_command(), "shutdown -r now")


class StrandingGuardLinuxTests(unittest.TestCase):
    """_bot_returns_after_reboot on Linux keys off `systemctl is-enabled`."""

    def test_enabled_is_safe(self):
        with patch.object(nr.platform, "system", return_value="Linux"), \
             patch.object(nr, "_run", return_value=(0, "enabled\n")):
            safe, why = nr._bot_returns_after_reboot()
        self.assertTrue(safe)
        self.assertIn(nr.BOT_SERVICE, why)

    def test_disabled_is_unsafe(self):
        with patch.object(nr.platform, "system", return_value="Linux"), \
             patch.object(nr, "_run", return_value=(1, "disabled\n")):
            safe, why = nr._bot_returns_after_reboot()
        self.assertFalse(safe)

    def test_missing_unit_is_unsafe(self):
        with patch.object(nr.platform, "system", return_value="Linux"), \
             patch.object(nr, "_run", return_value=(1, "")):
            safe, _ = nr._bot_returns_after_reboot()
        self.assertFalse(safe)

    def test_enabled_runtime_is_unsafe(self):
        # 'enabled-runtime' does not persist across a reboot -> must refuse.
        with patch.object(nr.platform, "system", return_value="Linux"), \
             patch.object(nr, "_run", return_value=(0, "enabled-runtime\n")):
            safe, _ = nr._bot_returns_after_reboot()
        self.assertFalse(safe)


class StrandingGuardMacTests(unittest.TestCase):
    """macOS: LaunchDaemon boots regardless; a LaunchAgent needs auto-login."""

    def test_launchdaemon_present_is_safe(self):
        with patch.object(nr.platform, "system", return_value="Darwin"), \
             patch.object(nr.Path, "exists", return_value=True):
            safe, why = nr._bot_returns_after_reboot()
        self.assertTrue(safe)
        self.assertIn("LaunchDaemon", why)

    def test_launchagent_with_autologin_is_safe(self):
        with patch.object(nr.platform, "system", return_value="Darwin"), \
             patch.object(nr.Path, "exists", return_value=False), \
             patch.object(nr, "_run", return_value=(0, "localadmin\n")):
            safe, why = nr._bot_returns_after_reboot()
        self.assertTrue(safe)
        self.assertIn("auto-login", why)

    def test_launchagent_without_autologin_is_unsafe(self):
        with patch.object(nr.platform, "system", return_value="Darwin"), \
             patch.object(nr.Path, "exists", return_value=False), \
             patch.object(nr, "_run", return_value=(1, "")):
            safe, why = nr._bot_returns_after_reboot()
        self.assertFalse(safe)
        self.assertIn("login", why)


class UptimeTests(unittest.TestCase):
    def test_linux_parses_proc_uptime(self):
        with patch.object(nr.platform, "system", return_value="Linux"), \
             patch.object(nr.Path, "read_text", return_value="12345.67 9999.0\n"):
            self.assertAlmostEqual(nr._uptime_seconds(), 12345.67, places=1)

    def test_linux_unreadable_returns_none(self):
        with patch.object(nr.platform, "system", return_value="Linux"), \
             patch.object(nr.Path, "read_text", side_effect=OSError):
            self.assertIsNone(nr._uptime_seconds())

    def test_darwin_parses_sysctl(self):
        boot = datetime.now().timestamp() - 4000
        out = "{ sec = %d, usec = 123456 } Tue Jul 21 01:00:00 2026" % int(boot)
        with patch.object(nr.platform, "system", return_value="Darwin"), \
             patch.object(nr, "_run", return_value=(0, out)):
            up = nr._uptime_seconds()
        self.assertIsNotNone(up)
        self.assertGreater(up, 3000)


class AlertThrottleTests(unittest.TestCase):
    """_should_alert pings once, then suppresses within the throttle window."""

    def test_first_true_then_false(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            with patch.object(nr, "STATE_FILE", state), \
                 patch.object(nr, "DATA_DIR", Path(d)):
                now = datetime(2026, 7, 21, 5, 0)
                self.assertTrue(nr._should_alert("stranded", now))
                # Same key, one hour later: throttled.
                self.assertFalse(nr._should_alert("stranded", now + timedelta(hours=1)))
                # Past the throttle window: alerts again.
                later = now + timedelta(days=nr.ALERT_THROTTLE_DAYS + 1)
                self.assertTrue(nr._should_alert("stranded", later))

    def test_distinct_keys_independent(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            with patch.object(nr, "STATE_FILE", state), \
                 patch.object(nr, "DATA_DIR", Path(d)):
                now = datetime(2026, 7, 21, 5, 0)
                self.assertTrue(nr._should_alert("stranded", now))
                self.assertTrue(nr._should_alert("reboot-failed", now))


class MaintenanceConfigTests(unittest.TestCase):
    """The maintenance.py edit: new keys default off, status reflects them."""

    def test_defaults_present_and_off(self):
        from utils.maintenance import DEFAULT_CONFIG
        self.assertIs(DEFAULT_CONFIG["nightly_reboot"], False)
        self.assertEqual(DEFAULT_CONFIG["nightly_reboot_hour"], 5)
        self.assertEqual(DEFAULT_CONFIG["nightly_reboot_minute"], 0)

    def test_status_off_and_on(self):
        import utils.maintenance as m
        with patch.object(m, "load_config", return_value=dict(m.DEFAULT_CONFIG)):
            self.assertIn("Nightly reboot: OFF", m.get_status_report())
        cfg = dict(m.DEFAULT_CONFIG)
        cfg["nightly_reboot"] = True
        with patch.object(m, "load_config", return_value=cfg):
            self.assertIn("Nightly reboot: ON", m.get_status_report())


if __name__ == "__main__":
    unittest.main()
