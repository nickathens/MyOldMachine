"""Tests for the down-alert installer (install/heartbeat_setup.py).

`utils/heartbeat.py` could always ping a monitor, but nothing scheduled it.
This covers the half that closes that gap: URL and interval validation, the
rendered systemd units and launchd plist, the wizard registry entry, and the
.env round trip that keeps HEARTBEAT_URL alive across a wizard re-run.

Everything here is host-agnostic: platform and filesystem are mocked, so the
suite passes identically on the Ubuntu and macOS CI runners. Nothing here
touches the network or the real service managers.
"""
from __future__ import annotations

import plistlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import heartbeat_setup  # noqa: E402

TEMPLATES = ROOT / "install" / "templates"
PLIST_TEMPLATE = TEMPLATES / "com.myoldmachine.heartbeat.plist"


class TemplateFileTests(unittest.TestCase):
    def test_all_three_templates_ship(self):
        for name in (
            "myoldmachine-heartbeat.service",
            "myoldmachine-heartbeat.timer",
            "com.myoldmachine.heartbeat.plist",
        ):
            with self.subTest(template=name):
                self.assertTrue((TEMPLATES / name).exists(),
                                f"missing template: {TEMPLATES / name}")


class UrlValidationTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        for url in ("https://hc-ping.com/abc-123", "http://10.0.0.5:8080/ping/x"):
            with self.subTest(url=url):
                self.assertEqual(heartbeat_setup.normalize_ping_url(url), url)

    def test_strips_whitespace_and_quotes(self):
        # Pasting from a monitor's UI commonly drags quotes or spaces along.
        self.assertEqual(
            heartbeat_setup.normalize_ping_url('  "https://hc-ping.com/x"  '),
            "https://hc-ping.com/x",
        )

    def test_rejects_non_urls(self):
        for bad in ("", "   ", None, "hc-ping.com/x", "ftp://hc-ping.com/x",
                    "https://", "just some words"):
            with self.subTest(value=bad):
                self.assertIsNone(heartbeat_setup.normalize_ping_url(bad))


class IntervalValidationTests(unittest.TestCase):
    def test_parses_positive_integers(self):
        self.assertEqual(heartbeat_setup.normalize_interval("5"), 5)
        self.assertEqual(heartbeat_setup.normalize_interval(" 10 "), 10)

    def test_falls_back_on_junk_and_non_positive(self):
        for bad in ("", "0", "-3", "abc", None, 0, -1, 2.5):
            with self.subTest(value=bad):
                self.assertEqual(
                    heartbeat_setup.normalize_interval(bad),
                    heartbeat_setup.DEFAULT_INTERVAL_MIN,
                )


class SystemdRenderTests(unittest.TestCase):
    def _render(self, interval: int = 2):
        return heartbeat_setup.render_systemd_units(
            (TEMPLATES / "myoldmachine-heartbeat.service").read_text(encoding="utf-8"),
            (TEMPLATES / "myoldmachine-heartbeat.timer").read_text(encoding="utf-8"),
            Path("/opt/mom"), interval, "tester",
        )

    def test_no_placeholders_remain(self):
        for text in self._render():
            self.assertNotIn("{{", text)
            self.assertNotIn("}}", text)

    def test_service_runs_the_gated_script_as_the_install_user(self):
        service, _ = self._render()
        self.assertIn("User=tester", service)
        self.assertIn("Type=oneshot", service)
        self.assertIn(
            "ExecStart=/opt/mom/.venv/bin/python /opt/mom/utils/heartbeat.py "
            "--require-service myoldmachine.service",
            service,
        )

    def test_service_carries_no_requisite_gate(self):
        """The gate must stay in the script, not the unit.

        Requisite=/BindsTo= makes this oneshot FAIL rather than skip whenever
        the bot is down, so every interval of an outage adds an entry to
        `systemctl --failed` during exactly the incident you want a clean
        signal from. docs/heartbeat.md used to recommend Requisite=.
        """
        service, _ = self._render()
        directives = [ln.strip() for ln in service.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
        for banned in ("Requisite=", "BindsTo=", "Requires=", "PartOf="):
            for line in directives:
                self.assertFalse(line.startswith(banned),
                                 f"unit must not carry {banned}: {line}")

    def test_service_has_a_bounded_start(self):
        # A wedged DNS lookup must never pile runs up on the timer.
        service, _ = self._render()
        self.assertIn("TimeoutStartSec=", service)

    def test_timer_uses_the_chosen_interval(self):
        _, timer = self._render(interval=7)
        self.assertIn("OnUnitActiveSec=7min", timer)
        self.assertIn("OnBootSec=7min", timer)
        self.assertIn("WantedBy=timers.target", timer)

    def test_only_the_timer_is_installable(self):
        # The oneshot must not carry [Install]; enabling it would make it fire
        # at boot outside the timer as well.
        service, timer = self._render()
        self.assertNotIn("[Install]", service)
        self.assertIn("[Install]", timer)


class LaunchdRenderTests(unittest.TestCase):
    def _render(self, interval: int = 2) -> str:
        return heartbeat_setup.render_heartbeat_plist(
            PLIST_TEMPLATE.read_text(encoding="utf-8"),
            Path("/opt/mom"),
            interval,
            Path("/Users/tester"),
        )

    def test_no_placeholders_remain(self):
        out = self._render()
        self.assertNotIn("{{", out)
        self.assertNotIn("}}", out)

    def test_rendered_plist_is_valid_and_labeled(self):
        parsed = plistlib.loads(self._render().encode("utf-8"))
        self.assertEqual(parsed["Label"], "com.myoldmachine.heartbeat")
        self.assertTrue(parsed["RunAtLoad"])
        self.assertEqual(parsed["WorkingDirectory"], "/opt/mom")

    def test_interval_is_expressed_in_seconds(self):
        parsed = plistlib.loads(self._render(interval=3).encode("utf-8"))
        self.assertEqual(parsed["StartInterval"], 180)

    def test_no_keepalive_on_a_oneshot(self):
        # KeepAlive on a script that exits after each ping relaunches it in a
        # tight loop, which would hammer the monitor and the CPU.
        parsed = plistlib.loads(self._render().encode("utf-8"))
        self.assertNotIn("KeepAlive", parsed)

    def test_argv_gates_on_the_bot_launchd_label(self):
        parsed = plistlib.loads(self._render().encode("utf-8"))
        argv = parsed["ProgramArguments"]
        self.assertEqual(argv[0], "/opt/mom/.venv/bin/python")
        self.assertEqual(argv[1], "/opt/mom/utils/heartbeat.py")
        self.assertIn("--require-service", argv)
        self.assertEqual(argv[argv.index("--require-service") + 1],
                         "com.myoldmachine.bot")


class IsConfiguredTests(unittest.TestCase):
    """Keyed on the schedule, never on HEARTBEAT_URL alone: a URL in .env with
    nothing firing it is the half-configured state this installer exists to
    finish, so it must still read as not configured."""

    def test_linux_checks_the_timer_unit(self):
        with patch("install.heartbeat_setup.platform.system", return_value="Linux"), \
             patch("install.heartbeat_setup.Path.exists", return_value=True):
            self.assertTrue(heartbeat_setup.is_heartbeat_configured(Path("/opt/mom")))
        with patch("install.heartbeat_setup.platform.system", return_value="Linux"), \
             patch("install.heartbeat_setup.Path.exists", return_value=False):
            self.assertFalse(heartbeat_setup.is_heartbeat_configured(Path("/opt/mom")))

    def test_macos_checks_the_launch_agent(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            plist = home / "Library" / "LaunchAgents" / "com.myoldmachine.heartbeat.plist"
            with patch("install.heartbeat_setup.platform.system", return_value="Darwin"), \
                 patch("install.heartbeat_setup.Path.home", return_value=home):
                self.assertFalse(heartbeat_setup.is_heartbeat_configured(Path("/opt/mom")))
                plist.parent.mkdir(parents=True)
                plist.write_text("x", encoding="utf-8")
                self.assertTrue(heartbeat_setup.is_heartbeat_configured(Path("/opt/mom")))

    def test_other_platforms_are_never_configured(self):
        with patch("install.heartbeat_setup.platform.system", return_value="Windows"):
            self.assertFalse(heartbeat_setup.is_heartbeat_configured(Path("/opt/mom")))


class SetupStepTests(unittest.TestCase):
    """The interactive step: what it writes, and what it refuses to write."""

    def _run(self, answers, *, platform_name="Linux", install_ok=True):
        config: dict = {}
        replies = list(answers)

        def fake_ask(prompt, default=None, required=True, secret=False):
            return replies.pop(0) if replies else (default or "")

        with patch("install.heartbeat_setup.platform.system", return_value=platform_name), \
             patch("install.heartbeat_setup._install_systemd_timer",
                   return_value=install_ok) as linux_mock, \
             patch("install.heartbeat_setup._install_launchd_agent",
                   return_value=install_ok) as mac_mock, \
             patch("install.heartbeat_setup._prove_the_ping") as ping_mock, \
             patch("install.service.get_sudo_password", return_value=None), \
             patch("sys.stdout"):
            heartbeat_setup.run_heartbeat_setup_step(config, ask=fake_ask)
        return config, linux_mock, mac_mock, ping_mock

    def test_blank_url_skips_everything(self):
        config, linux_mock, mac_mock, ping_mock = self._run([""])
        self.assertEqual(config, {})
        linux_mock.assert_not_called()
        mac_mock.assert_not_called()
        ping_mock.assert_not_called()

    def test_invalid_url_skips_everything(self):
        config, linux_mock, _, ping_mock = self._run(["hc-ping.com/oops"])
        self.assertEqual(config, {})
        linux_mock.assert_not_called()
        ping_mock.assert_not_called()

    def test_accepted_url_installs_the_timer_and_records_config(self):
        config, linux_mock, mac_mock, ping_mock = self._run(
            ["https://hc-ping.com/abc", "4"]
        )
        linux_mock.assert_called_once()
        self.assertEqual(linux_mock.call_args.args[1], 4)  # interval reaches the installer
        mac_mock.assert_not_called()
        self.assertEqual(config["heartbeat_url"], "https://hc-ping.com/abc")
        self.assertEqual(config["heartbeat_interval_min"], 4)
        ping_mock.assert_called_once_with("https://hc-ping.com/abc")

    def test_macos_takes_the_launchd_path(self):
        config, linux_mock, mac_mock, _ = self._run(
            ["https://hc-ping.com/abc", "2"], platform_name="Darwin"
        )
        mac_mock.assert_called_once()
        linux_mock.assert_not_called()
        self.assertEqual(config["heartbeat_interval_min"], 2)

    def test_failed_install_leaves_no_url_behind(self):
        """A URL in .env with no schedule advertises an alert that does not
        exist, which is worse than no alert at all: nobody goes looking."""
        config, _, _, ping_mock = self._run(
            ["https://hc-ping.com/abc", "2"], install_ok=False
        )
        self.assertNotIn("heartbeat_url", config)
        ping_mock.assert_not_called()

    def test_unsupported_platform_refuses_early(self):
        config, linux_mock, mac_mock, _ = self._run(
            ["https://hc-ping.com/abc"], platform_name="Windows"
        )
        self.assertEqual(config, {})
        linux_mock.assert_not_called()
        mac_mock.assert_not_called()


class WizardRegistryTests(unittest.TestCase):
    def setUp(self):
        sys.argv = ["x"]
        from install import wizard
        self.wizard = wizard
        self.entry = next(f for f in wizard.OPTIONAL_FEATURES
                          if f["key"] == "heartbeat")

    def test_registry_contains_heartbeat(self):
        self.assertIn("heartbeat", {f["key"] for f in self.wizard.OPTIONAL_FEATURES})

    def test_applies_to_covers_linux_and_macos_only(self):
        for os_name, expected in (("Linux", True), ("Darwin", True), ("Windows", False)):
            with patch("install.wizard.platform.system", return_value=os_name):
                self.assertEqual(self.entry["applies_to"](), expected, os_name)

    def test_is_configured_delegates_to_the_installer(self):
        with patch("install.heartbeat_setup.is_heartbeat_configured",
                   return_value=True) as check:
            self.assertTrue(self.entry["is_configured"]({}))
        check.assert_called_once()

    def test_configure_invokes_the_setup_step(self):
        config = {}
        with patch.object(self.wizard, "_run_heartbeat_setup_step") as step:
            self.entry["configure"](config)
        step.assert_called_once_with(config)


class EnvRoundTripTests(unittest.TestCase):
    """write_env rebuilds .env from a fixed list of keys, so anything it does
    not know about is destroyed on the next wizard re-run. These two functions
    have to agree or the alert silently switches itself off later."""

    BASE = {
        "telegram_token": "123:abc",
        "llm_provider": "claude",
        "llm_model": "claude-sonnet-5",
        "telegram_user_id": "42",
        "bot_name": "MyOldMachine",
        "timezone": "UTC",
    }

    def setUp(self):
        sys.argv = ["x"]
        from install import wizard
        self.wizard = wizard

    def _round_trip(self, extra: dict) -> dict:
        with TemporaryDirectory() as td, patch("sys.stdout"):
            repo = Path(td)
            self.wizard.write_env(repo, {**self.BASE, **extra})
            self.written = (repo / ".env").read_text(encoding="utf-8")
            return self.wizard._load_config_from_env(repo)

    def test_heartbeat_url_survives_a_rewrite(self):
        loaded = self._round_trip({
            "heartbeat_url": "https://hc-ping.com/abc",
            "heartbeat_interval_min": 5,
        })
        self.assertIn("HEARTBEAT_URL=https://hc-ping.com/abc", self.written)
        self.assertIn("HEARTBEAT_INTERVAL_MIN=5", self.written)
        self.assertEqual(loaded["heartbeat_url"], "https://hc-ping.com/abc")
        self.assertEqual(loaded["heartbeat_interval_min"], "5")

    def test_a_second_rewrite_still_keeps_it(self):
        # The real failure shape: install, re-run the wizard, lose the alert.
        loaded = self._round_trip({"heartbeat_url": "https://hc-ping.com/abc"})
        with TemporaryDirectory() as td, patch("sys.stdout"):
            repo = Path(td)
            self.wizard.write_env(repo, {**self.BASE, **loaded})
            again = self.wizard._load_config_from_env(repo)
        self.assertEqual(again["heartbeat_url"], "https://hc-ping.com/abc")

    def test_interval_defaults_to_two_when_only_a_url_is_set(self):
        self._round_trip({"heartbeat_url": "https://hc-ping.com/abc"})
        self.assertIn("HEARTBEAT_INTERVAL_MIN=2", self.written)

    def test_no_heartbeat_keys_when_disabled(self):
        loaded = self._round_trip({})
        self.assertNotIn("HEARTBEAT_URL", self.written)
        self.assertNotIn("HEARTBEAT_INTERVAL_MIN", self.written)
        self.assertNotIn("heartbeat_url", loaded)


if __name__ == "__main__":
    unittest.main()
