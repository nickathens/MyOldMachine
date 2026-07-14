"""Tests for the Mini App installer's cross-platform (Linux + macOS) support.

Covers the launchd LaunchAgent path added alongside the original systemd path:
plist rendering, platform-aware `is_miniapp_configured`, cross-platform LAN IP
detection, the unsupported-OS early return, and the wizard's `applies_to` gate.

Everything here is host-agnostic: platform, filesystem, and socket are mocked so
the suite passes identically on the Ubuntu and macOS CI runners.
"""
from __future__ import annotations

import contextlib
import io
import plistlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import miniapp_setup  # noqa: E402

TEMPLATE = ROOT / "install" / "templates" / "com.myoldmachine.miniapp.plist"


class TemplateTests(unittest.TestCase):
    def test_template_file_exists(self):
        self.assertTrue(TEMPLATE.exists(), f"missing plist template: {TEMPLATE}")


class RenderTests(unittest.TestCase):
    def _render(self, port: int = 8090) -> str:
        return miniapp_setup._render_miniapp_plist(
            TEMPLATE.read_text(encoding="utf-8"),
            Path("/opt/mom"),
            port,
            Path("/Users/tester"),
        )

    def test_no_placeholders_remain(self):
        out = self._render()
        self.assertNotIn("{{", out)
        self.assertNotIn("}}", out)

    def test_rendered_plist_is_valid_and_labeled(self):
        parsed = plistlib.loads(self._render().encode("utf-8"))
        self.assertEqual(parsed["Label"], "com.myoldmachine.miniapp")
        self.assertTrue(parsed["RunAtLoad"])
        self.assertTrue(parsed["KeepAlive"])
        # The exec line is the last ProgramArguments entry (bash -c "<cmd>").
        cmd = parsed["ProgramArguments"][-1]
        self.assertIn("uvicorn miniapp.server:app", cmd)
        self.assertIn("--host 127.0.0.1", cmd)
        self.assertIn("--port 8090", cmd)
        self.assertIn("/opt/mom", parsed["WorkingDirectory"])

    def test_custom_port_is_applied(self):
        cmd = plistlib.loads(self._render(9099).encode("utf-8"))["ProgramArguments"][-1]
        self.assertIn("--port 9099", cmd)
        self.assertNotIn("8090", cmd)


class IsConfiguredTests(unittest.TestCase):
    def test_linux_checks_systemd_unit(self):
        with patch("install.miniapp_setup.platform.system", return_value="Linux"):
            with patch("pathlib.Path.exists", return_value=True):
                self.assertTrue(miniapp_setup.is_miniapp_configured())
            with patch("pathlib.Path.exists", return_value=False):
                self.assertFalse(miniapp_setup.is_miniapp_configured())

    def test_darwin_checks_launch_agent_plist(self):
        with TemporaryDirectory() as td:
            home = Path(td)
            with patch("install.miniapp_setup.platform.system", return_value="Darwin"), \
                 patch("install.miniapp_setup.Path.home", return_value=home):
                self.assertFalse(miniapp_setup.is_miniapp_configured())
                plist = home / "Library" / "LaunchAgents" / "com.myoldmachine.miniapp.plist"
                plist.parent.mkdir(parents=True)
                plist.write_text("<plist/>", encoding="utf-8")
                self.assertTrue(miniapp_setup.is_miniapp_configured())

    def test_unsupported_os_is_false(self):
        with patch("install.miniapp_setup.platform.system", return_value="Windows"):
            self.assertFalse(miniapp_setup.is_miniapp_configured())


class LanIpTests(unittest.TestCase):
    def test_returns_valid_ip_or_none(self):
        ip = miniapp_setup._detect_lan_ip()
        if ip is not None:
            self.assertEqual(ip.count("."), 3)
            self.assertFalse(ip.startswith("127."))

    def test_socket_failure_returns_none(self):
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.connect.side_effect = OSError("no route")
            self.assertIsNone(miniapp_setup._detect_lan_ip())


class RunStepGuardTests(unittest.TestCase):
    def test_unsupported_os_returns_before_asking(self):
        calls: list = []

        def fake_ask(*a, **k):
            calls.append((a, k))
            return "y"

        config: dict = {}
        with patch("install.miniapp_setup.platform.system", return_value="Windows"):
            with contextlib.redirect_stdout(io.StringIO()):
                miniapp_setup.run_miniapp_setup_step(config, ask=fake_ask)
        self.assertEqual(calls, [], "should skip before prompting on unsupported OS")
        self.assertNotIn("miniapp_enabled", config)


class WizardGateTests(unittest.TestCase):
    def test_applies_to_covers_linux_and_macos(self):
        sys.argv = ["x"]
        from install import wizard
        entry = next(f for f in wizard.OPTIONAL_FEATURES if f["key"] == "miniapp")
        self.assertNotIn("Linux-only", entry["summary"])
        for os_name, expected in (("Darwin", True), ("Linux", True), ("Windows", False)):
            with patch("install.wizard.platform.system", return_value=os_name):
                self.assertEqual(entry["applies_to"](), expected, os_name)


if __name__ == "__main__":
    unittest.main()
