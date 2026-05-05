"""Unit tests for install.compat_check.

Covers cli_compat() across:
- non-CLI providers (always pass)
- macOS below / at / above the floor
- Linux glibc below / at the floor
- musl Linux (no glibc) — should refuse
- glibc_version() parsing of `ldd --version` output
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import compat_check as cc  # noqa: E402
from install import os_detect  # noqa: E402


def _make_os_info(
    *,
    os_type: str,
    version: str = "",
    version_major: int = 0,
    version_minor: int = 0,
    version_name: str = "",
):
    """Build a minimal OSInfo-like object for cli_compat()."""
    info = MagicMock()
    info.os_type = os_type
    info.version = version
    info.version_major = version_major
    info.version_minor = version_minor
    info.version_name = version_name
    return info


class CliCompatProviderGateTests(unittest.TestCase):
    """Non-CLI providers must always pass — they don't ship native binaries."""

    def test_claude_api_always_ok(self):
        info = _make_os_info(os_type="macos", version="10.15", version_major=10, version_minor=15)
        ok, reason = cc.cli_compat("claude-api", info)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_openrouter_always_ok(self):
        info = _make_os_info(os_type="macos", version="10.15", version_major=10, version_minor=15)
        ok, reason = cc.cli_compat("openrouter", info)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_ollama_always_ok(self):
        info = _make_os_info(os_type="linux")
        ok, reason = cc.cli_compat("ollama", info)
        self.assertTrue(ok)
        self.assertEqual(reason, "")


class CliCompatMacosTests(unittest.TestCase):
    """macOS gating — Claude CLI and Codex CLI both require macOS 13+."""

    def test_claude_refused_on_catalina(self):
        info = _make_os_info(
            os_type="macos", version="10.15.7",
            version_major=10, version_minor=15, version_name="Catalina",
        )
        ok, reason = cc.cli_compat("claude", info)
        self.assertFalse(ok)
        self.assertIn("Claude CLI", reason)
        self.assertIn("13.0", reason)
        self.assertIn("10.15.7", reason)
        self.assertIn("Catalina", reason)

    def test_codex_refused_on_catalina(self):
        info = _make_os_info(
            os_type="macos", version="10.15",
            version_major=10, version_minor=15, version_name="Catalina",
        )
        ok, reason = cc.cli_compat("codex", info)
        self.assertFalse(ok)
        self.assertIn("Codex CLI", reason)
        self.assertIn("13.0", reason)

    def test_claude_refused_on_big_sur(self):
        info = _make_os_info(
            os_type="macos", version="11.7", version_major=11, version_minor=7,
        )
        ok, reason = cc.cli_compat("claude", info)
        self.assertFalse(ok)

    def test_claude_refused_on_monterey(self):
        info = _make_os_info(
            os_type="macos", version="12.7", version_major=12, version_minor=7,
        )
        ok, _ = cc.cli_compat("claude", info)
        self.assertFalse(ok)

    def test_claude_ok_on_ventura(self):
        info = _make_os_info(
            os_type="macos", version="13.0", version_major=13, version_minor=0,
        )
        ok, reason = cc.cli_compat("claude", info)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_claude_ok_on_sonoma(self):
        info = _make_os_info(
            os_type="macos", version="14.4", version_major=14, version_minor=4,
        )
        ok, _ = cc.cli_compat("claude", info)
        self.assertTrue(ok)

    def test_claude_ok_on_sequoia(self):
        info = _make_os_info(
            os_type="macos", version="15.1", version_major=15, version_minor=1,
        )
        ok, _ = cc.cli_compat("claude", info)
        self.assertTrue(ok)


class CliCompatLinuxTests(unittest.TestCase):
    """Linux gating — Claude CLI and Codex CLI require glibc 2.31+, refuse musl."""

    def test_claude_refused_on_old_glibc(self):
        info = _make_os_info(os_type="linux")
        with patch.object(os_detect, "glibc_version", return_value=(2, 28)):
            ok, reason = cc.cli_compat("claude", info)
        self.assertFalse(ok)
        self.assertIn("glibc 2.31", reason)
        self.assertIn("2.28", reason)

    def test_codex_refused_on_old_glibc(self):
        info = _make_os_info(os_type="linux")
        with patch.object(os_detect, "glibc_version", return_value=(2, 27)):
            ok, reason = cc.cli_compat("codex", info)
        self.assertFalse(ok)
        self.assertIn("Codex CLI", reason)

    def test_claude_ok_at_glibc_floor(self):
        info = _make_os_info(os_type="linux")
        with patch.object(os_detect, "glibc_version", return_value=(2, 31)):
            ok, reason = cc.cli_compat("claude", info)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_claude_ok_above_glibc_floor(self):
        info = _make_os_info(os_type="linux")
        with patch.object(os_detect, "glibc_version", return_value=(2, 39)):
            ok, _ = cc.cli_compat("claude", info)
        self.assertTrue(ok)

    def test_claude_refused_on_musl(self):
        info = _make_os_info(os_type="linux")
        with patch.object(os_detect, "glibc_version", return_value=None):
            ok, reason = cc.cli_compat("claude", info)
        self.assertFalse(ok)
        self.assertIn("glibc-based Linux", reason)
        self.assertIn("musl", reason)


class CliCompatOtherOSTests(unittest.TestCase):
    """Unknown OS types should fall through to True (no gating)."""

    def test_unknown_os_passes(self):
        info = _make_os_info(os_type="freebsd")
        ok, reason = cc.cli_compat("claude", info)
        self.assertTrue(ok)
        self.assertEqual(reason, "")


class GlibcVersionTests(unittest.TestCase):
    """install.os_detect.glibc_version() parsing."""

    def test_returns_none_on_non_linux(self):
        with patch("install.os_detect.platform.system", return_value="Darwin"):
            self.assertIsNone(os_detect.glibc_version())

    def test_returns_none_on_musl(self):
        fake = MagicMock()
        fake.stdout = "musl libc (x86_64)\nVersion 1.2.4\n"
        fake.stderr = ""
        with patch("install.os_detect.platform.system", return_value="Linux"), \
             patch("install.os_detect.subprocess.run", return_value=fake):
            self.assertIsNone(os_detect.glibc_version())

    def test_returns_none_on_missing_ldd(self):
        with patch("install.os_detect.platform.system", return_value="Linux"), \
             patch("install.os_detect.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(os_detect.glibc_version())

    def test_parses_ubuntu_2204(self):
        fake = MagicMock()
        fake.stdout = "ldd (Ubuntu GLIBC 2.35-0ubuntu3.1) 2.35\nCopyright (C) 2022\n"
        fake.stderr = ""
        with patch("install.os_detect.platform.system", return_value="Linux"), \
             patch("install.os_detect.subprocess.run", return_value=fake):
            self.assertEqual(os_detect.glibc_version(), (2, 35))

    def test_parses_debian_old(self):
        fake = MagicMock()
        fake.stdout = "ldd (Debian GLIBC 2.28-10) 2.28\n"
        fake.stderr = ""
        with patch("install.os_detect.platform.system", return_value="Linux"), \
             patch("install.os_detect.subprocess.run", return_value=fake):
            self.assertEqual(os_detect.glibc_version(), (2, 28))

    def test_unparseable_output_returns_none(self):
        fake = MagicMock()
        fake.stdout = "no version here at all\n"
        fake.stderr = ""
        with patch("install.os_detect.platform.system", return_value="Linux"), \
             patch("install.os_detect.subprocess.run", return_value=fake):
            self.assertIsNone(os_detect.glibc_version())


if __name__ == "__main__":
    unittest.main()
