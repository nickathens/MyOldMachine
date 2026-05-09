"""Unit tests for the macOS group helpers in install.multiuser.

Linux paths are well-tested in production; the new code we ship is the
macOS group creation logic. These tests mock subprocess so the suite
can run on any host.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import multiuser as mu  # noqa: E402


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class SystemGroupExistsTests(unittest.TestCase):
    def test_macos_group_exists_via_dscl(self):
        with patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "_run", return_value=_proc(0, stdout="...")) as m:
            self.assertTrue(mu.system_group_exists("mom_orchestrator"))
        # dscl invocation shape
        args, _ = m.call_args
        self.assertEqual(args[0], ["dscl", ".", "-read", "/Groups/mom_orchestrator"])

    def test_macos_group_missing(self):
        with patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "_run", return_value=_proc(1)):
            self.assertFalse(mu.system_group_exists("mom_orchestrator"))

    def test_linux_uses_getent(self):
        with patch.object(mu, "_is_macos", return_value=False), \
             patch.object(mu, "_run", return_value=_proc(0)) as m:
            self.assertTrue(mu.system_group_exists("mom_user1"))
        args, _ = m.call_args
        self.assertEqual(args[0], ["getent", "group", "mom_user1"])

    def test_invalid_name_raises(self):
        with self.assertRaises(ValueError):
            mu.system_group_exists("Bad Name With Spaces")


class PickFreeMacosGidTests(unittest.TestCase):
    def test_picks_lowest_free(self):
        # 401 and 402 are taken; 403 should be picked
        used = "g1                401\ng2                402\n"
        with patch.object(mu, "_run", return_value=_proc(0, stdout=used)):
            gid = mu._pick_free_macos_gid()
        self.assertEqual(gid, 403)

    def test_returns_none_when_band_full(self):
        used_lines = "\n".join(f"g{i}                {i}"
                               for i in range(mu._MACOS_ROLE_GID_LO,
                                              mu._MACOS_ROLE_GID_HI + 1))
        with patch.object(mu, "_run", return_value=_proc(0, stdout=used_lines)):
            gid = mu._pick_free_macos_gid()
        self.assertIsNone(gid)

    def test_dscl_failure_treats_band_as_empty(self):
        # If dscl fails (e.g. on Linux), used set is empty -> picks LO
        with patch.object(mu, "_run", return_value=_proc(1)):
            gid = mu._pick_free_macos_gid()
        self.assertEqual(gid, mu._MACOS_ROLE_GID_LO)


class EnsureMacosGroupTests(unittest.TestCase):
    def test_noop_on_linux(self):
        with patch.object(mu, "_is_macos", return_value=False):
            self.assertTrue(mu._ensure_macos_group("mom_user1"))

    def test_idempotent_when_group_exists(self):
        with patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_group_exists", return_value=True), \
             patch.object(mu, "_sudo_run") as m:
            self.assertTrue(mu._ensure_macos_group("mom_orchestrator"))
        m.assert_not_called()

    def test_creates_group_via_four_dscl_steps(self):
        # group doesn't exist initially; after creation system_group_exists() returns True
        exists_results = iter([False, True])
        with patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_group_exists",
                          side_effect=lambda n: next(exists_results)), \
             patch.object(mu, "_pick_free_macos_gid", return_value=420), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as m:
            self.assertTrue(mu._ensure_macos_group("mom_user1", password="pw"))
        # Four dscl steps: -create, PrimaryGroupID, RealName, Password
        self.assertEqual(m.call_count, 4)
        commands = [c.args[0] for c in m.call_args_list]
        self.assertEqual(commands[0], ["dscl", ".", "-create", "/Groups/mom_user1"])
        self.assertIn("PrimaryGroupID", commands[1])
        self.assertIn("420", commands[1])
        self.assertIn("RealName", commands[2])
        self.assertIn("Password", commands[3])

    def test_returns_false_when_no_free_gid(self):
        with patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_group_exists", return_value=False), \
             patch.object(mu, "_pick_free_macos_gid", return_value=None), \
             patch.object(mu, "_sudo_run") as m:
            self.assertFalse(mu._ensure_macos_group("mom_user1"))
        m.assert_not_called()

    def test_returns_false_when_dscl_step_fails(self):
        exists_results = iter([False, False])  # never becomes True
        with patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_group_exists",
                          side_effect=lambda n: next(exists_results)), \
             patch.object(mu, "_pick_free_macos_gid", return_value=420), \
             patch.object(mu, "_sudo_run", return_value=_proc(1, stderr="boom")):
            self.assertFalse(mu._ensure_macos_group("mom_user1"))


class MacosUserMembershipTests(unittest.TestCase):
    def test_already_member_skips_dseditgroup_edit(self):
        with patch.object(mu, "_macos_user_is_member", return_value=True), \
             patch.object(mu, "_sudo_run") as m:
            self.assertTrue(mu._macos_add_user_to_group("mom_user1", "mom_user1"))
        m.assert_not_called()

    def test_adds_when_not_member(self):
        with patch.object(mu, "_macos_user_is_member", return_value=False), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as m:
            self.assertTrue(mu._macos_add_user_to_group(
                "mom_user1", "mom_user1", password="pw",
            ))
        cmd = m.call_args.args[0]
        self.assertEqual(
            cmd,
            ["dseditgroup", "-o", "edit", "-a", "mom_user1", "-t", "user", "mom_user1"],
        )

    def test_returns_false_on_dseditgroup_failure(self):
        with patch.object(mu, "_macos_user_is_member", return_value=False), \
             patch.object(mu, "_sudo_run", return_value=_proc(1, stderr="nope")):
            self.assertFalse(mu._macos_add_user_to_group("mom_user1", "mom_user1"))


class CreateSystemUserMacosTests(unittest.TestCase):
    def test_macos_creates_user_then_group_then_membership(self):
        # Order of calls: sysadminctl -addUser, then group ensure, then add to group
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists",
                          side_effect=[False, True]) as exist_mock, \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo_mock, \
             patch.object(mu, "_ensure_macos_group", return_value=True) as g_mock, \
             patch.object(mu, "_macos_add_user_to_group", return_value=True) as m_mock:
            ok = mu.create_system_user("mom_user1", password="pw")
        self.assertTrue(ok)
        # First call should be sysadminctl -addUser
        first_cmd = sudo_mock.call_args_list[0].args[0]
        self.assertEqual(first_cmd[0], "sysadminctl")
        self.assertIn("-addUser", first_cmd)
        g_mock.assert_called_once_with("mom_user1", "pw")
        m_mock.assert_called_once_with("mom_user1", "mom_user1", "pw")
        self.assertEqual(exist_mock.call_count, 2)

    def test_macos_existing_user_still_ensures_group(self):
        # User already exists; we must still create matching group for older
        # installs that ran before this fix landed.
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", return_value=True), \
             patch.object(mu, "_ensure_macos_group", return_value=True) as g_mock, \
             patch.object(mu, "_macos_add_user_to_group", return_value=True) as m_mock:
            ok = mu.create_system_user("mom_user1", password="pw")
        self.assertTrue(ok)
        g_mock.assert_called_once()
        m_mock.assert_called_once()

    def test_macos_user_creation_failure_short_circuits(self):
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", return_value=False), \
             patch.object(mu, "_sudo_run", return_value=_proc(1, stderr="bad")), \
             patch.object(mu, "_ensure_macos_group") as g_mock:
            ok = mu.create_system_user("mom_user1", password="pw")
        self.assertFalse(ok)
        g_mock.assert_not_called()


class DeleteSystemUserMacosTests(unittest.TestCase):
    def test_deletes_user_then_group(self):
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", return_value=True), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo_mock, \
             patch.object(mu, "_delete_macos_group", return_value=True) as g_mock:
            ok = mu.delete_system_user("mom_user1", password="pw")
        self.assertTrue(ok)
        first_cmd = sudo_mock.call_args_list[0].args[0]
        self.assertEqual(first_cmd[0], "sysadminctl")
        self.assertIn("-deleteUser", first_cmd)
        g_mock.assert_called_once_with("mom_user1", "pw")


class MkdirAsRootTests(unittest.TestCase):
    """Regression tests for the install-time helper that creates slot dirs.

    The wizard chowns data/users/ to mom_orchestrator with mode 0755 before
    creating data/users/userN/ inside it. Once that chown lands, the install
    user (a non-orchestrator account, hence "other") loses write permission
    on data/users/, so a plain Path.mkdir for the slot dir raises
    PermissionError. mkdir_as_root bypasses that by going through sudo.
    """

    def test_calls_sudo_mkdir_p_with_password(self):
        with patch.object(mu, "_sudo_run", return_value=_proc(0)) as m:
            self.assertTrue(
                mu.mkdir_as_root(Path("/data/users/user1"), password="hunter2")
            )
        cmd = m.call_args.args[0]
        self.assertEqual(cmd, ["mkdir", "-p", "/data/users/user1"])
        # Password must propagate so sudo doesn't fall back to -n and EACCES.
        self.assertEqual(m.call_args.args[1], "hunter2")

    def test_no_password_uses_passwordless_sudo(self):
        # When the install user is in NOPASSWD sudoers (CI, some setups),
        # the helper still works without a password.
        with patch.object(mu, "_sudo_run", return_value=_proc(0)) as m:
            self.assertTrue(mu.mkdir_as_root(Path("/data/users/user1")))
        # Second positional arg is the password parameter — should be None.
        self.assertIsNone(m.call_args.args[1])

    def test_returns_false_on_failure(self):
        with patch.object(mu, "_sudo_run",
                          return_value=_proc(1, stderr="EACCES boom")):
            self.assertFalse(mu.mkdir_as_root(Path("/etc/sudoers.d/whatever")))

    def test_idempotent_on_existing_directory(self):
        # `mkdir -p` exits 0 when the directory already exists; the helper
        # must not treat that as failure on a wizard resume.
        with patch.object(mu, "_sudo_run", return_value=_proc(0)) as m:
            self.assertTrue(mu.mkdir_as_root(Path("/already/here")))
        self.assertEqual(m.call_count, 1)


class MacosCreateUserSetsHomeTests(unittest.TestCase):
    """sysadminctl -roleAccount defaults NFSHomeDirectory to /var/empty.
    Slot users with that home cannot host ~/.claude/, breaking the bot. The
    create_system_user macOS path must call dscl to set NFSHomeDirectory to
    the requested home_dir so HOME resolves to a writable location.
    """

    def test_fresh_user_sets_nfshomedirectory(self):
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", side_effect=[False, True]), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo_mock, \
             patch.object(mu, "_ensure_macos_group", return_value=True), \
             patch.object(mu, "_macos_add_user_to_group", return_value=True):
            ok = mu.create_system_user(
                "mom_user1",
                password="pw",
                home_dir=Path("/repo/data/users/user1"),
            )
        self.assertTrue(ok)
        # Last sudo call should be the dscl NFSHomeDirectory write.
        last_cmd = sudo_mock.call_args_list[-1].args[0]
        self.assertEqual(last_cmd[0], "dscl")
        self.assertEqual(last_cmd[1], ".")
        self.assertEqual(last_cmd[2], "-create")
        self.assertEqual(last_cmd[3], "/Users/mom_user1")
        self.assertEqual(last_cmd[4], "NFSHomeDirectory")
        self.assertEqual(last_cmd[5], "/repo/data/users/user1")

    def test_fresh_user_without_home_dir_skips_dscl(self):
        # If the caller doesn't specify home_dir, leave macOS default in place.
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", side_effect=[False, True]), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo_mock, \
             patch.object(mu, "_ensure_macos_group", return_value=True), \
             patch.object(mu, "_macos_add_user_to_group", return_value=True):
            ok = mu.create_system_user("mom_user1", password="pw")
        self.assertTrue(ok)
        # Only sysadminctl call, no dscl
        sysadm_calls = [
            c for c in sudo_mock.call_args_list
            if c.args[0][0] == "sysadminctl"
        ]
        dscl_calls = [
            c for c in sudo_mock.call_args_list
            if c.args[0][0] == "dscl"
        ]
        self.assertEqual(len(sysadm_calls), 1)
        self.assertEqual(len(dscl_calls), 0)

    def test_existing_user_re_sets_home_dir_for_migration(self):
        # Re-running the wizard on a Mac where slot users were created
        # before this fix landed should repair the home dir, not leave them
        # pointing at /var/empty. Migration path.
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", return_value=True), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo_mock, \
             patch.object(mu, "_ensure_macos_group", return_value=True), \
             patch.object(mu, "_macos_add_user_to_group", return_value=True):
            ok = mu.create_system_user(
                "mom_user1",
                password="pw",
                home_dir=Path("/repo/data/users/user1"),
            )
        self.assertTrue(ok)
        dscl_calls = [
            c for c in sudo_mock.call_args_list
            if c.args[0][0] == "dscl"
        ]
        self.assertEqual(len(dscl_calls), 1)
        cmd = dscl_calls[0].args[0]
        self.assertEqual(
            cmd,
            ["dscl", ".", "-create", "/Users/mom_user1",
             "NFSHomeDirectory", "/repo/data/users/user1"],
        )

    def test_dscl_failure_short_circuits(self):
        with patch.object(mu, "_is_linux", return_value=False), \
             patch.object(mu, "_is_macos", return_value=True), \
             patch.object(mu, "system_user_exists", side_effect=[False, True]), \
             patch.object(mu, "_ensure_macos_group", return_value=True), \
             patch.object(mu, "_macos_add_user_to_group", return_value=True):
            # sysadminctl OK, dscl fails
            def fake_sudo(cmd, *_args, **_kw):
                if cmd[0] == "dscl":
                    return _proc(1, stderr="dscl error")
                return _proc(0)
            with patch.object(mu, "_sudo_run", side_effect=fake_sudo):
                ok = mu.create_system_user(
                    "mom_user1",
                    password="pw",
                    home_dir=Path("/repo/data/users/user1"),
                )
        self.assertFalse(ok)


class PropagateClaudeCredentialsTests(unittest.TestCase):
    """The bot dispatches `sudo -u mom_userN claude -p ...` and the CLI looks
    for ~/.claude/.credentials.json relative to HOME. Without this propagation
    step every slot returns 'Not logged in · Please run /login'.

    Tests use getpass.getuser() so Path("~user").expanduser() resolves on
    every CI host without mocking the pwd/dscl layer.
    """

    @classmethod
    def setUpClass(cls):
        import getpass
        cls.real_user = getpass.getuser()

    def test_source_missing_returns_zero_with_diagnostic(self):
        # Path("~nobody").expanduser() raises RuntimeError on Python 3.12+
        # when the user doesn't resolve. The function must catch this and
        # return a clean (0, errors) instead of crashing.
        count, errors = mu.propagate_claude_credentials(
            "definitelynotarealuser_xyz123",
            {"mom_user1": Path("/anywhere")},
            password="pw",
        )
        self.assertEqual(count, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("source missing", errors[0])

    def test_source_file_missing_returns_zero(self):
        # Real user, but the .credentials.json file genuinely doesn't exist.
        with patch.object(Path, "exists", return_value=False):
            count, errors = mu.propagate_claude_credentials(
                self.real_user,
                {"mom_user1": Path("/anywhere")},
                password="pw",
            )
        self.assertEqual(count, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("source missing", errors[0])

    def test_happy_path_copies_to_each_slot(self):
        slot_homes = {
            "mom_user1": Path("/data/users/user1"),
            "mom_user2": Path("/data/users/user2"),
        }
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root", return_value=True) as mk, \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo, \
             patch.object(mu, "set_owner", return_value=True) as so, \
             patch.object(mu, "set_perms", return_value=True) as sp:
            count, errors = mu.propagate_claude_credentials(
                self.real_user, slot_homes, password="pw",
            )
        self.assertEqual(count, 2)
        self.assertEqual(errors, [])
        self.assertEqual(mk.call_count, 2)  # one mkdir per slot
        cp_calls = [c for c in sudo.call_args_list if c.args[0][0] == "cp"]
        self.assertEqual(len(cp_calls), 2)
        # set_owner: one per slot (target_dir, recursive)
        self.assertEqual(so.call_count, 2)
        # set_perms: 2 per slot (dir 700, file 600)
        self.assertEqual(sp.call_count, 4)

    def test_partial_failure_preserves_count_and_errors(self):
        slot_homes = {
            "mom_user1": Path("/data/users/user1"),
            "mom_user2": Path("/data/users/user2"),
        }
        mkdir_results = iter([True, False])
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root",
                          side_effect=lambda *a, **kw: next(mkdir_results)), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)), \
             patch.object(mu, "set_owner", return_value=True), \
             patch.object(mu, "set_perms", return_value=True):
            count, errors = mu.propagate_claude_credentials(
                self.real_user, slot_homes, password="pw",
            )
        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("mom_user2", errors[0])
        self.assertIn("mkdir", errors[0])

    def test_cp_failure_records_stderr(self):
        slot_homes = {"mom_user1": Path("/data/users/user1")}
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root", return_value=True), \
             patch.object(mu, "_sudo_run",
                          return_value=_proc(1, stderr="permission denied")), \
             patch.object(mu, "set_owner", return_value=True), \
             patch.object(mu, "set_perms", return_value=True):
            count, errors = mu.propagate_claude_credentials(
                self.real_user, slot_homes, password="pw",
            )
        self.assertEqual(count, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("permission denied", errors[0])
        self.assertIn("mom_user1", errors[0])

    def test_set_owner_failure_logged(self):
        slot_homes = {"mom_user1": Path("/data/users/user1")}
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root", return_value=True), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)), \
             patch.object(mu, "set_owner", return_value=False), \
             patch.object(mu, "set_perms", return_value=True):
            count, errors = mu.propagate_claude_credentials(
                self.real_user, slot_homes, password="pw",
            )
        self.assertEqual(count, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("chown", errors[0])

    def test_chmod_target_dir_failure_logged(self):
        slot_homes = {"mom_user1": Path("/data/users/user1")}
        # set_perms succeeds for dir, fails for file -> the dir fail short
        # circuits but we want to make sure the failure is reported.
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root", return_value=True), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)), \
             patch.object(mu, "set_owner", return_value=True), \
             patch.object(mu, "set_perms", return_value=False):
            count, errors = mu.propagate_claude_credentials(
                self.real_user, slot_homes, password="pw",
            )
        self.assertEqual(count, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("chmod", errors[0])

    def test_idempotent_overwrites_existing_credentials(self):
        slot_homes = {"mom_user1": Path("/data/users/user1")}
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root", return_value=True), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)) as sudo, \
             patch.object(mu, "set_owner", return_value=True), \
             patch.object(mu, "set_perms", return_value=True):
            for _ in range(2):
                count, errors = mu.propagate_claude_credentials(
                    self.real_user, slot_homes, password="pw",
                )
                self.assertEqual(count, 1)
                self.assertEqual(errors, [])
        cp_calls = [c for c in sudo.call_args_list if c.args[0][0] == "cp"]
        self.assertEqual(len(cp_calls), 2)

    def test_empty_slot_dict_short_circuits(self):
        with patch.object(Path, "exists", return_value=True):
            count, errors = mu.propagate_claude_credentials(
                self.real_user, {}, password="pw",
            )
        self.assertEqual(count, 0)
        self.assertEqual(errors, [])

    def test_partial_failure_continues_to_remaining_slots(self):
        slot_homes = {
            "mom_user1": Path("/data/users/user1"),
            "mom_user2": Path("/data/users/user2"),
            "mom_user3": Path("/data/users/user3"),
        }
        # Only slot 2's chown fails - slots 1 and 3 still succeed.
        owner_results = iter([True, False, True])
        with patch.object(Path, "exists", return_value=True), \
             patch.object(mu, "mkdir_as_root", return_value=True), \
             patch.object(mu, "_sudo_run", return_value=_proc(0)), \
             patch.object(mu, "set_owner",
                          side_effect=lambda *a, **kw: next(owner_results)), \
             patch.object(mu, "set_perms", return_value=True):
            count, errors = mu.propagate_claude_credentials(
                self.real_user, slot_homes, password="pw",
            )
        self.assertEqual(count, 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("mom_user2", errors[0])


class FindCliBinaryFallbackTests(unittest.TestCase):
    """find_cli_binary must work even when shutil.which returns None.

    On macOS the install user's PATH for non-login shells does not include
    ``~/.local/bin`` — Anthropic's native installer (``claude install``)
    drops the binary there. Multi-user provisioning needs to find it
    anyway so the sudoers fragment can pin a real, existing path.
    """

    def test_falls_back_to_local_bin(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td_home:
            home = Path(td_home)
            local_bin = home / ".local" / "bin"
            local_bin.mkdir(parents=True)
            target = local_bin / "claude"
            target.write_text("#!/bin/sh\n")
            target.chmod(0o755)
            with patch("shutil.which", return_value=None), \
                 patch("install.multiuser.Path.home", return_value=home):
                got = mu.find_cli_binary("claude")
            self.assertIsNotNone(got)
            self.assertEqual(Path(got), target.absolute())

    def test_returns_path_when_which_succeeds(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            target = Path(td) / "claude"
            target.write_text("")
            target.chmod(0o755)
            with patch("shutil.which", return_value=str(target)):
                got = mu.find_cli_binary("claude")
            self.assertEqual(Path(got), target.absolute())

    def test_returns_none_when_nothing_found(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td_home:
            home = Path(td_home)
            with patch("shutil.which", return_value=None), \
                 patch("install.multiuser.Path.home", return_value=home):
                self.assertIsNone(mu.find_cli_binary("claude"))

    def test_skips_directory_with_same_name(self):
        # If ~/.local/bin/claude is a directory (corrupted install), we
        # must not return it; we'd hand sudo a directory path.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td_home:
            home = Path(td_home)
            (home / ".local" / "bin" / "claude").mkdir(parents=True)
            with patch("shutil.which", return_value=None), \
                 patch("install.multiuser.Path.home", return_value=home):
                self.assertIsNone(mu.find_cli_binary("claude"))


if __name__ == "__main__":
    unittest.main()
