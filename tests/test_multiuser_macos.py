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


if __name__ == "__main__":
    unittest.main()
