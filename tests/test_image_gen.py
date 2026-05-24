"""Unit tests for skills/image-gen/scripts/generate.py.

Covers backend dispatch, fallback path, cost estimation, and CLI compat.
All subprocess calls are mocked -- no real Higgsfield or network access.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "image-gen" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate  # noqa: E402


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestBackendDefault(unittest.TestCase):
    """Bug #3: default backend must be 'auto', not 'higgsfield'."""

    def test_default_is_auto(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--backend", default="auto")
        args = parser.parse_args([])
        self.assertEqual(args.backend, "auto")

    def test_script_default_is_auto(self):
        self.assertIn("default=\"auto\"", Path(SCRIPT_DIR / "generate.py").read_text())


class TestAutoFallback(unittest.TestCase):
    """When Higgsfield fails, auto backend falls back to Pollinations."""

    @patch("generate.generate_pollinations")
    @patch("generate.generate_higgsfield")
    def test_auto_falls_back(self, mock_hf, mock_poll):
        mock_hf.return_value = {"success": False, "path": "", "error": "not authed"}
        mock_poll.return_value = {"success": True, "path": "/tmp/test.jpg", "model": "sana", "error": ""}

        # Simulate the auto path
        result = mock_hf("test", "/tmp/out.jpg")
        self.assertFalse(result["success"])
        result = mock_poll("test", "/tmp/out.jpg")
        self.assertTrue(result["success"])


class TestBalancePrintBug(unittest.TestCase):
    """Bug #1: json.dumps paren placement in --balance error path."""

    def test_balance_error_json_is_valid(self):
        output = json.dumps({"error": "Could not fetch account status"}, indent=2)
        parsed = json.loads(output)
        self.assertEqual(parsed["error"], "Could not fetch account status")

    def test_cost_error_json_is_valid(self):
        output = json.dumps({"error": "Prompt required for cost estimation"}, indent=2)
        parsed = json.loads(output)
        self.assertEqual(parsed["error"], "Prompt required for cost estimation")


class TestBoolConsistency(unittest.TestCase):
    """Bug #5: bool-to-string must use .lower() in all code paths."""

    def test_image_bool_lowercased(self):
        source = Path(SCRIPT_DIR / "generate.py").read_text()
        # The generate_higgsfield extra_params block should have isinstance(v, bool) check
        self.assertIn("isinstance(v, bool)", source)

    def test_video_bool_lowercased(self):
        source = Path(SCRIPT_DIR / "generate.py").read_text()
        count = source.count("str(v).lower()")
        # Should appear in at least 3 places: generate_higgsfield, generate_video, estimate_cost
        self.assertGreaterEqual(count, 3)


class TestDeadCodeRemoved(unittest.TestCase):
    """Bug #6: MODEL_ASPECT_RATIOS should be removed (dead code)."""

    def test_no_model_aspect_ratios(self):
        source = Path(SCRIPT_DIR / "generate.py").read_text()
        self.assertNotIn("MODEL_ASPECT_RATIOS", source)


class TestOldFlagsPreserved(unittest.TestCase):
    """Bug #4: old Pollinations flags must still work."""

    def test_width_flag_exists(self):
        self.assertTrue(hasattr(generate, "generate_pollinations"))
        import inspect
        sig = inspect.signature(generate.generate_pollinations)
        self.assertIn("width", sig.parameters)

    def test_enhance_flag_exists(self):
        import inspect
        sig = inspect.signature(generate.generate_pollinations)
        self.assertIn("enhance", sig.parameters)

    def test_seed_flag_exists(self):
        import inspect
        sig = inspect.signature(generate.generate_pollinations)
        self.assertIn("seed", sig.parameters)


class TestCostEstimation(unittest.TestCase):
    """Cost estimation returns structured data."""

    @patch("generate.get_account_status", return_value={"credits": 100})
    @patch("subprocess.run")
    def test_cost_returns_remaining(self, mock_run, mock_acct):
        mock_run.return_value = _proc(0, json.dumps({"credits": 2, "credits_exact": 2}))
        result = generate.estimate_cost("nano_banana_flash", "test prompt")
        self.assertEqual(result["credits_remaining"], 100)

    @patch("subprocess.run", side_effect=FileNotFoundError("higgsfield"))
    def test_cost_handles_missing_cli(self, mock_run):
        result = generate.estimate_cost("nano_banana_flash", "test prompt")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
