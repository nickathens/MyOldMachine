"""Tests for install.convert_to_single_user.

The conversion script tears down a multi-user install: stops services,
deletes slot accounts, revokes the sudoers fragment, restores data/
ownership, rewrites .env, and re-registers a single-user service.

All side-effecting helpers (subprocess.run, sudo, multiuser deletion,
service registration) are mocked. Tests run on any host without
touching the actual filesystem outside a tempdir.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import convert_to_single_user as cts  # noqa: E402


def _completed(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["mock"], returncode=rc, stdout=stdout, stderr=stderr,
    )


# ─── Helpers ────────────────────────────────────────────────────────────


class _Repo:
    """Minimal repo layout for tests."""

    def __init__(self, base: Path):
        self.root = base
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "orchestrator").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "users").mkdir(parents=True, exist_ok=True)
        # A faux .venv/bin/python so reregister_single_user_service finds it.
        (self.root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        (self.root / ".venv" / "bin" / "python").write_text("#!/bin/sh\nexit 0\n")
        (self.root / ".venv" / "bin" / "python").chmod(0o755)
        (self.root / "install").mkdir(exist_ok=True)
        (self.root / "install" / "service.py").write_text("# stub")

    def write_env(self, **kv) -> None:
        lines = [f"{k}={v}" for k, v in kv.items()]
        (self.root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_users_json(self, num_slots: int) -> None:
        data = {
            "version": 1,
            "num_slots": num_slots,
            "max_slots": 8,
            "orchestrator_user": "mom_orchestrator",
            "queue_mode": "per_user",
            "slots": {},
        }
        (self.root / "data" / "orchestrator" / "users.json").write_text(
            json.dumps(data), encoding="utf-8",
        )


# ─── read_env / rewrite_env ────────────────────────────────────────────


class ReadEnvTests(unittest.TestCase):
    def test_returns_empty_when_missing(self):
        with TemporaryDirectory() as td:
            self.assertEqual(cts.read_env(Path(td)), {})

    def test_parses_keys(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_ENABLED="1", LLM_PROVIDER="claude")
            env = cts.read_env(Path(td))
        self.assertEqual(env["MULTIUSER_ENABLED"], "1")
        self.assertEqual(env["LLM_PROVIDER"], "claude")

    def test_ignores_comments_and_blanks(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            (r.root / ".env").write_text(
                "# comment\nLLM_PROVIDER=claude\n\nFOO=bar\n", encoding="utf-8",
            )
            env = cts.read_env(Path(td))
        self.assertEqual(env, {"LLM_PROVIDER": "claude", "FOO": "bar"})


class RewriteEnvToSingleUserTests(unittest.TestCase):
    def test_drops_all_multiuser_keys_and_sets_disabled(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(
                LLM_PROVIDER="claude",
                MULTIUSER_ENABLED="1",
                MULTIUSER_NUM_SLOTS="4",
                MULTIUSER_ORCHESTRATOR_USER="mom_orchestrator",
                QUEUE_MODE="universal",
                CONCURRENT_REQUESTS="1",
                BOT_NAME="MyBot",
            )
            self.assertTrue(cts.rewrite_env_to_single_user(Path(td)))
            env = cts.read_env(Path(td))
        # Single-user disabled flag stays
        self.assertEqual(env.get("MULTIUSER_ENABLED"), "0")
        # Other multi-user keys gone
        self.assertNotIn("MULTIUSER_NUM_SLOTS", env)
        self.assertNotIn("MULTIUSER_ORCHESTRATOR_USER", env)
        self.assertNotIn("QUEUE_MODE", env)
        self.assertNotIn("CONCURRENT_REQUESTS", env)
        # Unrelated keys untouched
        self.assertEqual(env["LLM_PROVIDER"], "claude")
        self.assertEqual(env["BOT_NAME"], "MyBot")

    def test_appends_disabled_when_no_existing_key(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(LLM_PROVIDER="claude")
            self.assertTrue(cts.rewrite_env_to_single_user(Path(td)))
            env = cts.read_env(Path(td))
        self.assertEqual(env["MULTIUSER_ENABLED"], "0")
        self.assertEqual(env["LLM_PROVIDER"], "claude")

    def test_missing_env_is_a_noop_success(self):
        with TemporaryDirectory() as td:
            self.assertTrue(cts.rewrite_env_to_single_user(Path(td)))

    def test_preserves_comment_lines(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            (r.root / ".env").write_text(
                "# header\nLLM_PROVIDER=claude\n# trailing\n", encoding="utf-8",
            )
            self.assertTrue(cts.rewrite_env_to_single_user(Path(td)))
            text = (r.root / ".env").read_text(encoding="utf-8")
        self.assertIn("# header", text)
        self.assertIn("# trailing", text)
        self.assertIn("MULTIUSER_ENABLED=0", text)


# ─── detect_slot_users ─────────────────────────────────────────────────


class DetectSlotUsersTests(unittest.TestCase):
    def test_from_env_only(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_NUM_SLOTS="3")
            with patch.object(cts._mu, "system_user_exists", return_value=False):
                got = cts.detect_slot_users(Path(td), {"MULTIUSER_NUM_SLOTS": "3"})
        self.assertEqual(got, ["mom_user1", "mom_user2", "mom_user3"])

    def test_orchestrator_users_json_more_authoritative(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_NUM_SLOTS="2")
            r.write_users_json(num_slots=4)
            with patch.object(cts._mu, "system_user_exists", return_value=False):
                got = cts.detect_slot_users(Path(td), {"MULTIUSER_NUM_SLOTS": "2"})
        # Union of env (1..2) + users.json (1..4) = 1..4
        self.assertEqual(got, ["mom_user1", "mom_user2", "mom_user3", "mom_user4"])

    def test_picks_up_orphan_system_users(self):
        # Env says 0 slots, users.json missing, but a stale system user lurks.
        seen = {"mom_user5": True}
        with TemporaryDirectory() as td:
            with patch.object(cts._mu, "system_user_exists",
                              side_effect=lambda n: seen.get(n, False)):
                got = cts.detect_slot_users(Path(td), {})
        self.assertEqual(got, ["mom_user5"])

    def test_invalid_num_slots_is_treated_as_zero(self):
        with TemporaryDirectory() as td:
            with patch.object(cts._mu, "system_user_exists", return_value=False):
                got = cts.detect_slot_users(Path(td), {"MULTIUSER_NUM_SLOTS": "not-a-number"})
        self.assertEqual(got, [])

    def test_corrupt_users_json_does_not_crash(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_NUM_SLOTS="1")
            (r.root / "data" / "orchestrator" / "users.json").write_text(
                "not json", encoding="utf-8",
            )
            with patch.object(cts._mu, "system_user_exists", return_value=False):
                got = cts.detect_slot_users(Path(td), {"MULTIUSER_NUM_SLOTS": "1"})
        self.assertEqual(got, ["mom_user1"])


# ─── service stop ──────────────────────────────────────────────────────


class StopServiceMacosTests(unittest.TestCase):
    def test_no_op_when_plist_missing(self):
        with patch.object(Path, "exists", return_value=False), \
             patch.object(cts, "_sudo_run") as m:
            self.assertTrue(cts.stop_service_macos(password="x"))
        m.assert_not_called()

    def test_unloads_and_removes_when_plist_present(self):
        with patch.object(Path, "exists", return_value=True), \
             patch.object(cts, "_sudo_run", return_value=_completed(0)) as m:
            self.assertTrue(cts.stop_service_macos(password=None))
        # bootout, unload, rm — at least 3 calls
        self.assertGreaterEqual(m.call_count, 3)

    def test_returns_false_when_rm_fails(self):
        responses = [_completed(0), _completed(0), _completed(1, stderr="permission denied")]
        with patch.object(Path, "exists", return_value=True), \
             patch.object(cts, "_sudo_run", side_effect=responses):
            self.assertFalse(cts.stop_service_macos(password=None))


class StopServiceLinuxTests(unittest.TestCase):
    def test_stops_disables_and_removes(self):
        with patch.object(Path, "exists", return_value=True), \
             patch.object(cts, "_sudo_run", return_value=_completed(0)) as m:
            self.assertTrue(cts.stop_service_linux(password="x"))
        # stop, disable, rm, daemon-reload
        self.assertGreaterEqual(m.call_count, 4)

    def test_no_unit_no_problem(self):
        with patch.object(Path, "exists", return_value=False), \
             patch.object(cts, "_sudo_run", return_value=_completed(0)):
            self.assertTrue(cts.stop_service_linux(password=None))


class StopServiceDispatchTests(unittest.TestCase):
    def test_dispatches_to_macos(self):
        with patch.object(cts, "_is_macos", return_value=True), \
             patch.object(cts, "_is_linux", return_value=False), \
             patch.object(cts, "stop_service_macos", return_value=True) as m:
            self.assertTrue(cts.stop_service(password="x"))
        m.assert_called_once_with("x")

    def test_dispatches_to_linux(self):
        with patch.object(cts, "_is_macos", return_value=False), \
             patch.object(cts, "_is_linux", return_value=True), \
             patch.object(cts, "stop_service_linux", return_value=True) as m:
            self.assertTrue(cts.stop_service(password=None))
        m.assert_called_once_with(None)

    def test_unknown_os_warns_but_continues(self):
        with patch.object(cts, "_is_macos", return_value=False), \
             patch.object(cts, "_is_linux", return_value=False):
            self.assertTrue(cts.stop_service(password=None))


# ─── reregister_single_user_service ────────────────────────────────────


class ReregisterTests(unittest.TestCase):
    def test_calls_service_py_with_repo_dir(self):
        with TemporaryDirectory() as td:
            _Repo(Path(td))
            with patch.object(cts.subprocess, "run",
                              return_value=_completed(0)) as m:
                self.assertTrue(cts.reregister_single_user_service(Path(td)))
        cmd = m.call_args[0][0]
        # Shape: [python, install/service.py, --repo-dir, <td>]
        self.assertTrue(cmd[1].endswith("install/service.py"))
        self.assertIn("--repo-dir", cmd)
        # NO --orchestrator-user flag — that's the whole point
        self.assertNotIn("--orchestrator-user", cmd)

    def test_missing_venv_python_returns_false(self):
        with TemporaryDirectory() as td:
            self.assertFalse(cts.reregister_single_user_service(Path(td)))

    def test_service_py_failure_returns_false(self):
        with TemporaryDirectory() as td:
            _Repo(Path(td))
            with patch.object(cts.subprocess, "run", return_value=_completed(1)):
                self.assertFalse(cts.reregister_single_user_service(Path(td)))


# ─── delete_slot_and_orchestrator_users ────────────────────────────────


class DeleteUsersTests(unittest.TestCase):
    def test_deletes_each_slot_then_orchestrator(self):
        deleted: list[str] = []

        def fake_exists(name):
            return True

        def fake_delete(name, *, password=None):
            deleted.append(name)
            return True

        with patch.object(cts._mu, "system_user_exists",
                          side_effect=fake_exists), \
             patch.object(cts._mu, "delete_system_user",
                          side_effect=fake_delete):
            count, errors = cts.delete_slot_and_orchestrator_users(
                ["mom_user1", "mom_user2"], password="x",
            )
        self.assertEqual(deleted, ["mom_user1", "mom_user2", "mom_orchestrator"])
        self.assertEqual(count, 3)
        self.assertEqual(errors, [])

    def test_skips_missing_users(self):
        with patch.object(cts._mu, "system_user_exists", return_value=False), \
             patch.object(cts._mu, "delete_system_user") as m:
            count, errors = cts.delete_slot_and_orchestrator_users(
                ["mom_user1"], password=None,
            )
        m.assert_not_called()
        self.assertEqual(count, 0)
        self.assertEqual(errors, [])

    def test_collects_failures(self):
        def fake_delete(name, *, password=None):
            return name != "mom_user1"

        with patch.object(cts._mu, "system_user_exists", return_value=True), \
             patch.object(cts._mu, "delete_system_user", side_effect=fake_delete):
            count, errors = cts.delete_slot_and_orchestrator_users(
                ["mom_user1", "mom_user2"], password=None,
            )
        self.assertEqual(count, 2)
        self.assertEqual(errors, ["mom_user1"])


# ─── filesystem cleanup ────────────────────────────────────────────────


class ChownDataBackTests(unittest.TestCase):
    def test_invokes_chown_recursive(self):
        with TemporaryDirectory() as td:
            _Repo(Path(td))
            with patch.object(cts, "_sudo_run",
                              return_value=_completed(0)) as m:
                self.assertTrue(cts.chown_data_back_to_install_user(
                    Path(td), "nick", password="x"))
        cmd = m.call_args[0][0]
        self.assertEqual(cmd[0], "chown")
        self.assertEqual(cmd[1], "-R")
        self.assertEqual(cmd[2], "nick:")
        self.assertTrue(cmd[3].endswith("/data"))

    def test_falls_back_with_explicit_group_on_failure(self):
        with TemporaryDirectory() as td:
            _Repo(Path(td))
            calls = [_completed(1, stderr="invalid group"), _completed(0)]
            with patch.object(cts, "_sudo_run", side_effect=calls) as m:
                self.assertTrue(cts.chown_data_back_to_install_user(
                    Path(td), "root", password="x"))
        self.assertEqual(m.call_count, 2)
        # Second call should have explicit group, not just "user:"
        cmd = m.call_args_list[1][0][0]
        self.assertNotEqual(cmd[2], "root:")

    def test_no_data_dir_no_op(self):
        with TemporaryDirectory() as td:
            with patch.object(cts, "_sudo_run") as m:
                self.assertTrue(cts.chown_data_back_to_install_user(
                    Path(td), "nick", password=None))
        m.assert_not_called()

    def test_chowns_env_file_when_present(self):
        """The .env file lives outside data/ but multi-user provisioning
        chowned it to install_user:mom_orchestrator. After conversion the
        orchestrator group is gone, so we must restore install_user's
        primary group on .env too — not just data/."""
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_ENABLED="1", LLM_PROVIDER="claude")
            with patch.object(cts, "_sudo_run",
                              return_value=_completed(0)) as m:
                self.assertTrue(cts.chown_data_back_to_install_user(
                    Path(td), "nick", password=None))
        # We expect calls for both data/ and .env, plus a chmod 600 on .env.
        targets = [c.args[0][-1] for c in m.call_args_list]
        self.assertTrue(any(t.endswith("/data") for t in targets))
        self.assertTrue(any(t.endswith("/.env") for t in targets))
        # chmod 600 .env should be among the calls
        chmod_calls = [c.args[0] for c in m.call_args_list if c.args[0][0] == "chmod"]
        self.assertTrue(any(c[1] == "600" for c in chmod_calls))


class RemoveOrchestratorDirTests(unittest.TestCase):
    def test_no_op_when_dir_missing(self):
        with TemporaryDirectory() as td:
            with patch.object(cts, "_sudo_run") as m:
                self.assertTrue(cts.remove_orchestrator_dir(Path(td), None))
        m.assert_not_called()

    def test_invokes_sudo_rm(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            (r.root / "data" / "orchestrator").mkdir(exist_ok=True)
            with patch.object(cts, "_sudo_run",
                              return_value=_completed(0)) as m:
                self.assertTrue(cts.remove_orchestrator_dir(Path(td), None))
        cmd = m.call_args[0][0]
        self.assertEqual(cmd[:3], ["rm", "-rf"][:2] + [str(r.root / "data" / "orchestrator")])


# ─── checkpoint cleanup ────────────────────────────────────────────────


class RemoveMultiuserCheckpointTests(unittest.TestCase):
    def test_removes_only_multiuser_lines(self):
        with TemporaryDirectory() as td:
            ckpt = Path(td) / "checkpoints"
            ckpt.write_text(
                "wizard_config\nmultiuser_v2\nprovisioning\nmultiuser\nservice\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ",
                            {"MYOLDMACHINE_CHECKPOINT_FILE": str(ckpt)}):
                self.assertTrue(cts.remove_multiuser_checkpoint())
            text = ckpt.read_text(encoding="utf-8")
        self.assertNotIn("multiuser_v2", text)
        self.assertNotIn("multiuser\n", text.replace("multiuser_v2", ""))
        self.assertIn("wizard_config", text)
        self.assertIn("provisioning", text)
        self.assertIn("service", text)

    def test_missing_checkpoint_file_is_ok(self):
        with TemporaryDirectory() as td:
            ckpt = Path(td) / "missing"
            with patch.dict("os.environ",
                            {"MYOLDMACHINE_CHECKPOINT_FILE": str(ckpt)}):
                self.assertTrue(cts.remove_multiuser_checkpoint())


# ─── End-to-end convert() ──────────────────────────────────────────────


class ConvertEndToEndTests(unittest.TestCase):
    def test_already_single_user_is_zero_exit_no_op(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_ENABLED="0", LLM_PROVIDER="claude")
            rc = cts.convert(Path(td), force=True)
        self.assertEqual(rc, 0)

    def test_missing_repo_dir_aborts(self):
        rc = cts.convert(Path("/nonexistent-myoldmachine"), force=True)
        self.assertEqual(rc, 2)

    def test_full_conversion_calls_each_step(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(
                MULTIUSER_ENABLED="1", MULTIUSER_NUM_SLOTS="2",
                LLM_PROVIDER="claude",
            )
            r.write_users_json(num_slots=2)

            mocks = {
                "stop_service": MagicMock(return_value=True),
                "revoke_orchestrator_sudo": MagicMock(return_value=True),
                "delete_slot_and_orchestrator_users": MagicMock(return_value=(3, [])),
                "chown_data_back_to_install_user": MagicMock(return_value=True),
                "remove_orchestrator_dir": MagicMock(return_value=True),
                "relax_slot_dir_perms": MagicMock(return_value=None),
                "rewrite_env_to_single_user": MagicMock(return_value=True),
                "remove_multiuser_checkpoint": MagicMock(return_value=True),
                "reregister_single_user_service": MagicMock(return_value=True),
            }
            patches = [patch.object(cts, n, m) for n, m in mocks.items()]
            with patch.object(cts._mu, "system_user_exists", return_value=False):
                for p in patches:
                    p.start()
                try:
                    rc = cts.convert(Path(td), force=True)
                finally:
                    for p in patches:
                        p.stop()
        self.assertEqual(rc, 0)
        for name, m in mocks.items():
            self.assertGreater(m.call_count, 0,
                               f"{name} should have been called once")

    def test_skip_confirmation_aborts_when_user_says_no(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_ENABLED="1", MULTIUSER_NUM_SLOTS="1")
            with patch.object(cts._mu, "system_user_exists", return_value=False), \
                 patch("builtins.input", return_value="n"):
                rc = cts.convert(Path(td), force=False)
            # Verify .env untouched while td still exists.
            env = cts.read_env(Path(td))
        self.assertEqual(rc, 1)
        self.assertEqual(env["MULTIUSER_ENABLED"], "1")

    def test_eof_on_confirmation_aborts(self):
        """Closed stdin → safe abort, not crash."""
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(MULTIUSER_ENABLED="1")
            with patch.object(cts._mu, "system_user_exists", return_value=False), \
                 patch("builtins.input", side_effect=EOFError):
                rc = cts.convert(Path(td), force=False)
        self.assertEqual(rc, 1)


# ─── Confirm helper ────────────────────────────────────────────────────


class ConfirmTests(unittest.TestCase):
    def test_yes_variants(self):
        for s in ("y", "Y", "yes", "YES"):
            with patch("builtins.input", return_value=s):
                self.assertTrue(cts._confirm("?", default="n"))

    def test_no_variants(self):
        for s in ("n", "no", ""):
            with patch("builtins.input", return_value=s):
                self.assertFalse(cts._confirm("?", default="n"))

    def test_eof_returns_false(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(cts._confirm("?", default="n"))

    def test_keyboard_interrupt_returns_false(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertFalse(cts._confirm("?", default="n"))


# ─── Install user resolution ───────────────────────────────────────────


class ResolveInstallUserTests(unittest.TestCase):
    def test_prefers_sudo_user(self):
        with patch.dict("os.environ", {"SUDO_USER": "nick"}):
            self.assertEqual(cts._resolve_install_user(), "nick")

    def test_falls_back_to_getpass(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("SUDO_USER", None)
            with patch.object(cts.getpass, "getuser", return_value="alice"):
                self.assertEqual(cts._resolve_install_user(), "alice")

    def test_ignores_root_sudo_user(self):
        with patch.dict("os.environ", {"SUDO_USER": "root"}), \
             patch.object(cts.getpass, "getuser", return_value="alice"):
            self.assertEqual(cts._resolve_install_user(), "alice")


class TelegramBotApiCleanupTests(unittest.TestCase):
    """Conversion must take down the telegram-bot-api unit too.

    The Bot API server was registered with UserName=mom_orchestrator (Mac)
    or User=mom_orchestrator (Linux). Once the orchestrator user is
    deleted, the unit crash-loops with EX_CONFIG (78) on Mac and a
    User= unknown error on Linux. The conversion stops + removes the
    unit; the user can opt back in via ./install.sh later.
    """

    def test_macos_unloads_and_removes_telegram_bot_api_plist(self):
        target_paths: list[str] = []

        def fake_sudo(cmd, password=None, *, timeout=60):
            for arg in cmd:
                if "com.telegram-bot-api" in str(arg):
                    target_paths.append(str(arg))
            return _completed(0)

        with patch.object(Path, "exists", return_value=True), \
             patch.object(cts, "_sudo_run", side_effect=fake_sudo):
            self.assertTrue(cts.stop_service_macos(password="x"))
        # bootout system/com.telegram-bot-api + unload <plist> + rm <plist>
        self.assertTrue(any(
            "com.telegram-bot-api" in p for p in target_paths
        ))
        self.assertTrue(any(
            p.endswith("com.telegram-bot-api.plist") for p in target_paths
        ))

    def test_macos_skips_telegram_bot_api_when_plist_missing(self):
        # First Path.exists() call (bot plist) returns True, second (tba) False.
        side = iter([True, False])

        def fake_exists(self):
            try:
                return next(side)
            except StopIteration:
                return False

        with patch.object(Path, "exists", new=fake_exists), \
             patch.object(cts, "_sudo_run", return_value=_completed(0)) as m:
            self.assertTrue(cts.stop_service_macos(password="x"))
        # Ensure no `com.telegram-bot-api` argument was passed to sudo
        for call in m.call_args_list:
            for arg in call[0][0]:
                self.assertNotIn("com.telegram-bot-api", str(arg))

    def test_linux_stops_and_removes_telegram_bot_api_unit(self):
        seen_units: list[str] = []

        def fake_sudo(cmd, password=None, *, timeout=60):
            for arg in cmd:
                if "telegram-bot-api" in str(arg):
                    seen_units.append(str(arg))
            return _completed(0)

        with patch.object(Path, "exists", return_value=True), \
             patch.object(cts, "_sudo_run", side_effect=fake_sudo):
            self.assertTrue(cts.stop_service_linux(password="x"))
        self.assertTrue(any(
            arg == "telegram-bot-api" for arg in seen_units
        ), f"expected systemctl arg 'telegram-bot-api' in {seen_units}")
        self.assertTrue(any(
            arg.endswith("/telegram-bot-api.service") for arg in seen_units
        ), f"expected unit-path arg in {seen_units}")

    def test_linux_skips_telegram_bot_api_when_unit_missing(self):
        # Bot myoldmachine.service exists, telegram-bot-api.service does not.
        side = iter([True, False])

        def fake_exists(self):
            try:
                return next(side)
            except StopIteration:
                return False

        with patch.object(Path, "exists", new=fake_exists), \
             patch.object(cts, "_sudo_run", return_value=_completed(0)) as m:
            self.assertTrue(cts.stop_service_linux(password=None))
        # No call should reference the telegram-bot-api unit
        for call in m.call_args_list:
            for arg in call[0][0]:
                self.assertNotIn("telegram-bot-api", str(arg))


class RewriteEnvStripsBotApiKeysTests(unittest.TestCase):
    """The conversion must drop TELEGRAM_API_BASE/ID/HASH so the bot stops
    trying to talk to the (now-dead) local Bot API server. The user can
    re-enable via ./install.sh's optional-features prompt afterwards."""

    def test_strips_all_three_keys(self):
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(
                LLM_PROVIDER="claude",
                MULTIUSER_ENABLED="1",
                TELEGRAM_API_BASE="http://localhost:8081",
                TELEGRAM_API_ID="12345",
                TELEGRAM_API_HASH="0123456789abcdef0123456789abcdef",
            )
            self.assertTrue(cts.rewrite_env_to_single_user(Path(td)))
            env = cts.read_env(Path(td))
        self.assertNotIn("TELEGRAM_API_BASE", env)
        self.assertNotIn("TELEGRAM_API_ID", env)
        self.assertNotIn("TELEGRAM_API_HASH", env)
        self.assertEqual(env["LLM_PROVIDER"], "claude")
        self.assertEqual(env["MULTIUSER_ENABLED"], "0")

    def test_preserves_non_bot_api_telegram_keys(self):
        # TELEGRAM_BOT_TOKEN must NOT be stripped — that's the user's
        # actual bot identity, completely unrelated to the Bot API server.
        with TemporaryDirectory() as td:
            r = _Repo(Path(td))
            r.write_env(
                LLM_PROVIDER="claude",
                MULTIUSER_ENABLED="1",
                TELEGRAM_BOT_TOKEN="123456:abcdef",
                TELEGRAM_API_BASE="http://localhost:8081",
            )
            self.assertTrue(cts.rewrite_env_to_single_user(Path(td)))
            env = cts.read_env(Path(td))
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "123456:abcdef")
        self.assertNotIn("TELEGRAM_API_BASE", env)


if __name__ == "__main__":
    unittest.main()
