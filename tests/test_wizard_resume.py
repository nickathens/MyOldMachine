"""Regression tests for install.wizard._load_config_from_env on resume.

The wizard stores the sudo password in ~/.sudo_pass (mode 0600) instead of
.env. On a resume re-run (env_valid path), `config["sudo_pass"]` was once
missing so install steps that needed sudo fell back to `sudo -n` and failed
on machines that require a password. Loading it from disk closes that gap.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


class LoadConfigFromEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mom_wizard_test_"))
        self.fake_home = Path(tempfile.mkdtemp(prefix="mom_fakehome_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.fake_home, ignore_errors=True)

    def _write_env(self, content: str) -> None:
        (self.tmpdir / ".env").write_text(content, encoding="utf-8")

    def _write_sudo_pass(self, password: str) -> Path:
        sudo_file = self.fake_home / ".sudo_pass"
        sudo_file.write_text(password + "\n", encoding="utf-8")
        sudo_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return sudo_file

    def test_loads_sudo_pass_when_present(self):
        self._write_env(
            "TELEGRAM_BOT_TOKEN=tok\n"
            "LLM_PROVIDER=openrouter\n"
            "LLM_MODEL=mini\n"
        )
        self._write_sudo_pass("hunter2")

        with patch.object(wizard.Path, "home", return_value=self.fake_home):
            cfg = wizard._load_config_from_env(self.tmpdir)

        self.assertEqual(cfg.get("sudo_pass"), "hunter2")
        self.assertEqual(cfg.get("telegram_token"), "tok")

    def test_missing_sudo_pass_file_is_silent(self):
        self._write_env("TELEGRAM_BOT_TOKEN=tok\n")
        self.assertFalse((self.fake_home / ".sudo_pass").exists())

        with patch.object(wizard.Path, "home", return_value=self.fake_home):
            cfg = wizard._load_config_from_env(self.tmpdir)

        self.assertNotIn("sudo_pass", cfg)
        self.assertEqual(cfg.get("telegram_token"), "tok")

    def test_strips_trailing_newline_from_sudo_pass(self):
        # store_sudo_password writes `password + "\n"`; loader must strip.
        self._write_env("TELEGRAM_BOT_TOKEN=tok\n")
        sudo_file = self.fake_home / ".sudo_pass"
        sudo_file.write_text("with-newline\n\n", encoding="utf-8")
        sudo_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

        with patch.object(wizard.Path, "home", return_value=self.fake_home):
            cfg = wizard._load_config_from_env(self.tmpdir)

        self.assertEqual(cfg.get("sudo_pass"), "with-newline")

    def test_unreadable_sudo_pass_does_not_crash(self):
        self._write_env("TELEGRAM_BOT_TOKEN=tok\n")
        sudo_file = self.fake_home / ".sudo_pass"
        sudo_file.write_text("secret", encoding="utf-8")
        # Drop all read permissions so read_text raises PermissionError.
        sudo_file.chmod(0)
        try:
            with patch.object(wizard.Path, "home", return_value=self.fake_home):
                cfg = wizard._load_config_from_env(self.tmpdir)
        finally:
            # Restore so tearDown can remove the file.
            os.chmod(sudo_file, stat.S_IRUSR | stat.S_IWUSR)

        # Loader swallows OSError and just leaves sudo_pass unset.
        self.assertNotIn("sudo_pass", cfg)
        self.assertEqual(cfg.get("telegram_token"), "tok")


if __name__ == "__main__":
    unittest.main()
