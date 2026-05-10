"""Tests for service.py cleanup-on-install behaviour.

Regression coverage for the cross-mode plist conflict on macOS and the
stale telegram-bot-api unit on Linux. Both bite users who switch from
multi-user back to single-user — the previous service is left in place,
referencing a user that may no longer exist or .env keys that were
stripped during conversion.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import service  # noqa: E402


def _proc(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["mock"], returncode=rc, stdout=stdout, stderr=stderr,
    )


class _FakeRepo:
    """Minimal layout so service.py finds its venv + template."""

    def __init__(self, base: Path):
        self.root = base
        (self.root / ".venv" / "bin").mkdir(parents=True)
        (self.root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
        (self.root / ".venv" / "bin" / "python").chmod(0o755)
        (self.root / "data" / "logs").mkdir(parents=True)
        (self.root / "install" / "templates").mkdir(parents=True)
        # Minimal plist template with all placeholders the renderer touches
        (self.root / "install" / "templates"
         / "com.myoldmachine.bot.plist").write_text(
            "<plist>{{PYTHON}} {{WORKING_DIR}} {{BOT_PY}} {{LOG_DIR}} "
            "{{ENV_FILE}} {{VENV_BIN}} {{HOME}} {{CLI_BIN_PATHS}}</plist>"
        )
        # Minimal systemd template
        (self.root / "install" / "templates"
         / "myoldmachine.service").write_text(
            "[Service]\nUser={{USER}}\nWorkingDirectory={{WORKING_DIR}}\n"
            "ExecStart={{PYTHON}} bot.py\n"
            "EnvironmentFile={{WORKING_DIR}}/.env\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            "Environment=PATH={{WORKING_DIR}}/.venv/bin:{{CLI_BIN_PATHS}}/usr/bin\n"
            "StandardOutput=append:{{LOG_DIR}}/bot.log\n"
            "StandardError=append:{{LOG_DIR}}/bot.log\n"
        )


class MacosLaunchAgentRemovesStaleDaemonTests(unittest.TestCase):
    """Single-user install on macOS must tear down a pre-existing
    multi-user bot LaunchDaemon at /Library/LaunchDaemons/com.myoldmachine.bot.plist
    Both share Label "com.myoldmachine.bot" so leaving the daemon behind
    would race with our agent.
    """

    def test_unloads_existing_bot_launchdaemon(self):
        with TemporaryDirectory() as td:
            _FakeRepo(Path(td))

            real_exists = Path.exists
            seen_paths: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                seen_paths.append(str(cmd))
                return _proc(0)

            def fake_exists(self):
                if str(self) == "/Library/LaunchDaemons/com.myoldmachine.bot.plist":
                    return True
                return real_exists(self)

            with patch.object(Path, "home", return_value=Path(td) / "home"), \
                 patch.object(Path, "exists", new=fake_exists), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo), \
                 patch.object(service, "_launchctl_load"), \
                 patch.object(service,
                              "_remove_stale_telegram_bot_api_plist_macos"), \
                 patch.object(service, "get_sudo_password", return_value=None):
                (Path(td) / "home" / "Library" / "LaunchAgents").mkdir(
                    parents=True, exist_ok=True,
                )
                ok = service._setup_macos_launch_agent(Path(td), os_info=None)

            self.assertTrue(ok)
            joined = " ".join(seen_paths)
            self.assertIn("com.myoldmachine.bot.plist", joined)
            self.assertIn("system/com.myoldmachine.bot", joined)

    def test_no_op_when_no_stale_daemons(self):
        with TemporaryDirectory() as td:
            _FakeRepo(Path(td))

            real_exists = Path.exists
            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                return _proc(0)

            def fake_exists(self):
                if str(self).startswith("/Library/LaunchDaemons/"):
                    return False
                return real_exists(self)

            with patch.object(Path, "home", return_value=Path(td) / "home"), \
                 patch.object(Path, "exists", new=fake_exists), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo), \
                 patch.object(service, "_launchctl_load"), \
                 patch.object(service,
                              "_remove_stale_telegram_bot_api_plist_macos"), \
                 patch.object(service, "get_sudo_password", return_value=None):
                (Path(td) / "home" / "Library" / "LaunchAgents").mkdir(
                    parents=True, exist_ok=True,
                )
                ok = service._setup_macos_launch_agent(Path(td), os_info=None)
            self.assertTrue(ok)
            joined = " ".join(sudo_calls)
            self.assertNotIn("launchctl bootout", joined)


class MacosStaleTelegramBotApiPlistTests(unittest.TestCase):
    """_remove_stale_telegram_bot_api_plist_macos must tear down a plist
    only when its UserName= references a user other than the current bot
    user. A working single-user Bot API install (UserName=install_user)
    must be left alone.
    """

    def _write_plist(self, path: Path, user: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "<?xml version=\"1.0\"?>\n<plist><dict>\n"
            "<key>UserName</key>\n"
            f"<string>{user}</string>\n"
            "<key>Label</key>\n<string>com.telegram-bot-api</string>\n"
            "</dict></plist>\n",
            encoding="utf-8",
        )

    def test_removes_when_user_differs(self):
        with TemporaryDirectory() as td:
            plist = Path(td) / "com.telegram-bot-api.plist"
            self._write_plist(plist, "mom_orchestrator")

            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                return _proc(0)

            class _Path:
                def __new__(cls, p):
                    return Path(plist) if str(p).endswith(
                        "com.telegram-bot-api.plist"
                    ) else Path(p)

            with patch.object(service, "Path", _Path), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo):
                service._remove_stale_telegram_bot_api_plist_macos(
                    "alice", password="pw",
                )

            joined = " ".join(sudo_calls)
            self.assertIn("launchctl bootout system/com.telegram-bot-api",
                          joined)
            self.assertIn("launchctl unload", joined)
            self.assertIn("rm -f", joined)

    def test_leaves_alone_when_user_matches(self):
        with TemporaryDirectory() as td:
            plist = Path(td) / "com.telegram-bot-api.plist"
            self._write_plist(plist, "alice")

            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                return _proc(0)

            class _Path:
                def __new__(cls, p):
                    return Path(plist) if str(p).endswith(
                        "com.telegram-bot-api.plist"
                    ) else Path(p)

            with patch.object(service, "Path", _Path), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo):
                service._remove_stale_telegram_bot_api_plist_macos(
                    "alice", password="pw",
                )
            self.assertEqual(sudo_calls, [])
            # Plist file still on disk
            self.assertTrue(plist.exists())

    def test_no_op_when_plist_absent(self):
        sudo_calls: list[str] = []

        def fake_sudo(cmd, password=None, timeout=30):
            sudo_calls.append(str(cmd))
            return _proc(0)

        with patch.object(Path, "exists", return_value=False), \
             patch.object(service, "sudo_run", side_effect=fake_sudo):
            service._remove_stale_telegram_bot_api_plist_macos(
                "alice", password=None,
            )
        self.assertEqual(sudo_calls, [])

    def test_no_op_when_plist_has_no_username(self):
        # Without a UserName key we cannot tell — leave the plist alone.
        with TemporaryDirectory() as td:
            plist = Path(td) / "com.telegram-bot-api.plist"
            plist.write_text(
                "<?xml version=\"1.0\"?>\n<plist><dict>\n"
                "<key>Label</key>\n<string>com.telegram-bot-api</string>\n"
                "</dict></plist>\n",
                encoding="utf-8",
            )

            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                return _proc(0)

            class _Path:
                def __new__(cls, p):
                    return Path(plist) if str(p).endswith(
                        "com.telegram-bot-api.plist"
                    ) else Path(p)

            with patch.object(service, "Path", _Path), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo):
                service._remove_stale_telegram_bot_api_plist_macos(
                    "alice", password=None,
                )
            self.assertEqual(sudo_calls, [])


class LinuxStaleTelegramBotApiTests(unittest.TestCase):
    """_remove_stale_telegram_bot_api_unit_linux only removes the unit when
    its User= differs from the bot user we are about to register.
    Otherwise leaves it alone (the unit is healthy)."""

    def _write_unit(self, path: Path, user: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[Unit]\nDescription=tba\n[Service]\n"
            f"User={user}\nExecStart=/bin/true\n",
            encoding="utf-8",
        )

    def test_removes_when_user_differs(self):
        with TemporaryDirectory() as td:
            unit_path = Path(td) / "telegram-bot-api.service"
            self._write_unit(unit_path, "mom_orchestrator")

            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                # Treat `rm -f <unit_path>` as success.
                if "rm -f" in str(cmd):
                    unit_path.unlink(missing_ok=True)
                return _proc(0)

            class _Path:
                """Patch Path('/etc/systemd/system/telegram-bot-api.service')
                so it points at our tempfile instead."""
                def __new__(cls, p):
                    return Path(unit_path) if str(p).endswith(
                        "/telegram-bot-api.service"
                    ) else Path(p)

            with patch.object(service, "Path", _Path), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo):
                service._remove_stale_telegram_bot_api_unit_linux(
                    "alice", password="pw",
                )

            joined = " ".join(sudo_calls)
            self.assertIn("systemctl stop telegram-bot-api", joined)
            self.assertIn("systemctl disable telegram-bot-api", joined)
            self.assertIn("rm -f", joined)
            self.assertIn("daemon-reload", joined)

    def test_leaves_alone_when_user_matches(self):
        with TemporaryDirectory() as td:
            unit_path = Path(td) / "telegram-bot-api.service"
            self._write_unit(unit_path, "alice")

            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                return _proc(0)

            class _Path:
                def __new__(cls, p):
                    return Path(unit_path) if str(p).endswith(
                        "/telegram-bot-api.service"
                    ) else Path(p)

            with patch.object(service, "Path", _Path), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo):
                service._remove_stale_telegram_bot_api_unit_linux(
                    "alice", password="pw",
                )

            self.assertEqual(sudo_calls, [],
                             f"unexpected sudo calls: {sudo_calls}")
            # Unit file untouched
            self.assertTrue(unit_path.exists())

    def test_no_op_when_unit_absent(self):
        sudo_calls: list[str] = []

        def fake_sudo(cmd, password=None, timeout=30):
            sudo_calls.append(str(cmd))
            return _proc(0)

        with patch.object(Path, "exists", return_value=False), \
             patch.object(service, "sudo_run", side_effect=fake_sudo):
            service._remove_stale_telegram_bot_api_unit_linux(
                "alice", password=None,
            )
        self.assertEqual(sudo_calls, [])

    def test_no_op_when_unit_has_no_user_line(self):
        # If the unit has no User= line, we cannot tell — leave it alone
        # rather than guess wrong and break a working install.
        with TemporaryDirectory() as td:
            unit_path = Path(td) / "telegram-bot-api.service"
            unit_path.write_text(
                "[Unit]\nDescription=no user line here\n"
                "[Service]\nExecStart=/bin/true\n",
                encoding="utf-8",
            )

            sudo_calls: list[str] = []

            def fake_sudo(cmd, password=None, timeout=30):
                sudo_calls.append(str(cmd))
                return _proc(0)

            class _Path:
                def __new__(cls, p):
                    return Path(unit_path) if str(p).endswith(
                        "/telegram-bot-api.service"
                    ) else Path(p)

            with patch.object(service, "Path", _Path), \
                 patch.object(service, "sudo_run", side_effect=fake_sudo):
                service._remove_stale_telegram_bot_api_unit_linux(
                    "alice", password=None,
                )

            self.assertEqual(sudo_calls, [])


class NoClaudeLoginInUserFacingStringsTests(unittest.TestCase):
    """`claude login` is not a CLI subcommand — it is interpreted as a
    natural-language prompt by the Claude Code CLI, which produces an
    answer instead of an OAuth flow. The correct command is
    `claude auth login`. This test guards against regressions where a
    docstring, comment, or printf gets that wrong again.
    """

    def _scan(self, path: Path) -> list[str]:
        bad: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            # Match the bare phrase but not "claude auth login" and not the
            # CLI-internal slash-command "/login" reference.
            stripped = line.replace("claude auth login", "")
            if "claude login" in stripped:
                bad.append(f"{path.name}: {line.strip()}")
        return bad

    def test_no_unqualified_claude_login_in_install_or_docs(self):
        targets = [
            ROOT / "install" / "wizard.py",
            ROOT / ".env.example",
            ROOT / "README.md",
        ]
        offences: list[str] = []
        for path in targets:
            if path.exists():
                offences.extend(self._scan(path))
        self.assertEqual(
            offences, [],
            "Found bare `claude login` (not a real subcommand). "
            "Use `claude auth login`:\n  " + "\n  ".join(offences),
        )


if __name__ == "__main__":
    unittest.main()
