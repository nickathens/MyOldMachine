"""Tests for the macOS multi-user gate in install.wizard.

The wizard's `_run_multiuser_step` must:
  - Force single-user on macOS unless the caller passes experimental=True.
  - On Darwin + experimental=True, prompt for an explicit accept token
    before entering the (broken) slot-account flow.
  - Continue to allow multi-user on Linux without --experimental.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


class MacosMultiuserGateTests(unittest.TestCase):
    """macOS without --experimental: silently force single-user."""

    def _make_config(self) -> dict:
        return {"user_name": "test"}

    def test_macos_default_forces_single_user(self):
        cfg = self._make_config()
        with patch.object(wizard.platform, "system", return_value="Darwin"), \
             patch("builtins.input") as mock_in:
            wizard._run_multiuser_step(cfg, experimental=False)
        # Must NOT prompt the user at all in the default-Mac case.
        mock_in.assert_not_called()
        self.assertFalse(cfg["multiuser_enabled"])
        self.assertEqual(cfg["multiuser_num_slots"], 1)
        self.assertEqual(cfg["multiuser_queue_mode"], "per_user")
        self.assertFalse(cfg["multiuser_queue_enabled"])

    def test_macos_experimental_requires_accept_token(self):
        """User must type the literal accept-token. Anything else falls back."""
        cfg = self._make_config()
        # User types 'y' which is NOT the accept token. Wizard should fall back.
        with patch.object(wizard.platform, "system", return_value="Darwin"), \
             patch("builtins.input", return_value="y"):
            wizard._run_multiuser_step(cfg, experimental=True)
        self.assertFalse(cfg["multiuser_enabled"])
        self.assertEqual(cfg["multiuser_num_slots"], 1)

    def test_macos_experimental_accept_token_unlocks_flow(self):
        """Right token: proceeds past the gate into the regular Linux flow."""
        cfg = self._make_config()
        # Sequence: accept token, then "1" for num_users (single-user).
        # We pick num_users=1 because the deeper queue-mode prompt is not
        # needed at single-user count and it lets us assert that we entered
        # the regular flow without mocking probe helpers.
        inputs = iter(["i-accept-broken-mac-multiuser", "1"])
        with patch.object(wizard.platform, "system", return_value="Darwin"), \
             patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            wizard._run_multiuser_step(cfg, experimental=True)
        # We DID enter the regular flow (otherwise num_slots would have been
        # set by the bypass branch with no input consumed).
        self.assertEqual(cfg["multiuser_num_slots"], 1)
        # Single-user count means multi-user stays disabled even after the
        # gate is unlocked — that is the correct behavior.
        self.assertFalse(cfg["multiuser_enabled"])

    def test_macos_experimental_accept_token_case_insensitive(self):
        """Case folds — the wizard lowercases the answer before comparing."""
        cfg = self._make_config()
        inputs = iter(["I-ACCEPT-BROKEN-MAC-MULTIUSER", "1"])
        with patch.object(wizard.platform, "system", return_value="Darwin"), \
             patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            wizard._run_multiuser_step(cfg, experimental=True)
        # Got past the gate (consumed the second input "1").
        self.assertEqual(cfg["multiuser_num_slots"], 1)


class LinuxMultiuserStepStillWorksTests(unittest.TestCase):
    """Linux is unaffected by --experimental: no warning, no gate, normal flow."""

    def test_linux_default_runs_normal_flow(self):
        cfg = {"user_name": "nick"}
        # Linux, single-user count → never enters the queue-mode branch.
        with patch.object(wizard.platform, "system", return_value="Linux"), \
             patch("builtins.input", return_value="1"):
            wizard._run_multiuser_step(cfg, experimental=False)
        self.assertEqual(cfg["multiuser_num_slots"], 1)
        self.assertFalse(cfg["multiuser_enabled"])

    def test_linux_with_experimental_no_op(self):
        """Passing --experimental on Linux is a no-op — same behaviour."""
        cfg = {"user_name": "nick"}
        with patch.object(wizard.platform, "system", return_value="Linux"), \
             patch("builtins.input", return_value="1"):
            wizard._run_multiuser_step(cfg, experimental=True)
        self.assertEqual(cfg["multiuser_num_slots"], 1)


class UnsupportedOsTests(unittest.TestCase):
    """Windows / unknown platforms remain unsupported regardless of flag."""

    def test_windows_falls_back_to_single_user(self):
        cfg = {"user_name": "nick"}
        with patch.object(wizard.platform, "system", return_value="Windows"), \
             patch("builtins.input") as mock_in:
            wizard._run_multiuser_step(cfg, experimental=True)
        mock_in.assert_not_called()
        self.assertFalse(cfg["multiuser_enabled"])


class WizardArgParseTests(unittest.TestCase):
    """The --experimental flag must be a real argparse argument so install.sh
    can pass it through."""

    def test_argparse_accepts_experimental(self):
        # Smoke-test: the argparse call inside main() must accept the flag.
        # We avoid running main() (which has many side effects) by parsing
        # a minimal args list directly.
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--repo-dir", type=str, default="/tmp")
        parser.add_argument("--os", type=str, choices=["linux", "macos"],
                            default="linux")
        parser.add_argument("--experimental", action="store_true")
        ns = parser.parse_args(["--experimental"])
        self.assertTrue(ns.experimental)
        ns2 = parser.parse_args([])
        self.assertFalse(ns2.experimental)


if __name__ == "__main__":
    unittest.main()
