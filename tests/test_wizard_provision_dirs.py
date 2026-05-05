"""Regression tests for `_provision_multiuser` directory creation.

The fresh-install bug this guards against:

  1. wizard.py mkdir's data/users/ as the install user (parent data/ is
     install-user-owned, so this works).
  2. Wizard then chown's data/users/ to mom_orchestrator and chmod's it
     to 0755. The install user now has only r-x on data/users/ as "other".
  3. Wizard tries `(data/users/userN).mkdir(parents=True, exist_ok=True)`.
     This raises PermissionError because creating a directory inside
     data/users/ requires write permission.

Maria (Catalina, MyOldMachine 638cf6c) hit this on her first install. Fix:
go through `mkdir_as_root` (sudo mkdir -p) for the slot dirs only —
their parent is owned by the orchestrator, so plain mkdir cannot work.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


class ProvisionMultiuserSlotDirsTests(unittest.TestCase):
    """The slot-dir creation path must go through sudo, not Path.mkdir."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mom_provision_test_"))
        # The wizard expects data/ to exist (it's created elsewhere in the
        # flow). Pre-create it so the test focuses on the bug under scrutiny.
        (self.tmpdir / "data").mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_multiuser_helpers(self, *, find_cli_returns: Path):
        """Patch every install.multiuser symbol the wizard imports.

        Returns the dict of MagicMocks keyed by name so tests can assert
        on call counts, args, etc.
        """
        mocks = {
            "create_system_user": MagicMock(return_value=True),
            "find_cli_binary": MagicMock(return_value=find_cli_returns),
            "grant_sudo": MagicMock(return_value=(True, "ok")),
            "mkdir_as_root": MagicMock(return_value=True),
            "set_owner": MagicMock(return_value=True),
            "set_perms": MagicMock(return_value=True),
            "slot_user": MagicMock(side_effect=lambda i: f"mom_user{i}"),
        }
        # The wizard does a local `from install.multiuser import ...` inside
        # _provision_multiuser, so patches must target install.multiuser.
        from install import multiuser as mu
        patches = [patch.object(mu, name, mock) for name, mock in mocks.items()]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return mocks

    def test_slot_dirs_go_through_mkdir_as_root_not_path_mkdir(self):
        mocks = self._patch_multiuser_helpers(
            find_cli_returns=Path("/usr/local/bin/claude"),
        )
        # Stub out the users.json write step (sudo cat / sudo cp). Without
        # this, _provision_multiuser would try to invoke real sudo against
        # a path inside the test tempdir.
        with patch("subprocess.run") as run_mock, \
             patch("getpass.getuser", return_value="installer"):
            run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
            success, _msg = wizard._provision_multiuser(
                self.tmpdir,
                {"sudo_pass": "pw", "multiuser_num_slots": 2,
                 "telegram_user_id": "12345", "user_name": "Admin"},
            )
        self.assertTrue(success)

        # Each slot must call mkdir_as_root exactly once with the right path.
        # 2 slots → 2 calls.
        self.assertEqual(mocks["mkdir_as_root"].call_count, 2)
        called_paths = [c.args[0] for c in mocks["mkdir_as_root"].call_args_list]
        self.assertEqual(called_paths[0], self.tmpdir / "data" / "users" / "user1")
        self.assertEqual(called_paths[1], self.tmpdir / "data" / "users" / "user2")
        # Password must propagate so the helper doesn't fall back to sudo -n.
        for call in mocks["mkdir_as_root"].call_args_list:
            self.assertEqual(call.kwargs.get("password"), "pw")

    def test_provisioning_aborts_when_mkdir_as_root_fails(self):
        # When sudo mkdir fails (e.g. denied by sudoers, ENOSPC), the wizard
        # must surface the failure rather than silently proceeding.
        mocks = self._patch_multiuser_helpers(
            find_cli_returns=Path("/usr/local/bin/claude"),
        )
        # First slot mkdir fails — the wizard must abort before chown/chmod.
        mocks["mkdir_as_root"].return_value = False

        success, msg = wizard._provision_multiuser(
            self.tmpdir,
            {"sudo_pass": "pw", "multiuser_num_slots": 2,
             "telegram_user_id": "12345", "user_name": "Admin"},
        )
        self.assertFalse(success)
        self.assertIn("user1", msg)
        # set_owner must NOT have been called for the slot dir whose mkdir
        # failed (we'd be chowning a path that doesn't exist).
        slot_owner_calls = [
            c for c in mocks["set_owner"].call_args_list
            if "user1" in str(c.args[0])
        ]
        self.assertEqual(slot_owner_calls, [])

    def test_users_json_written_when_provisioning_succeeds(self):
        # End-to-end smoke: the wizard reaches the users.json write step
        # only if everything before it (including the slot mkdir) succeeded.
        # If the mkdir bug regresses, this never runs.
        self._patch_multiuser_helpers(
            find_cli_returns=Path("/usr/local/bin/claude"),
        )
        captured_writes: list[tuple[str, str]] = []

        def fake_run(cmd, *args, **kwargs):
            # Capture the temp-file → orchestrator/users.json copy. The
            # wizard writes users.json via `sudo cp <tmp> <target>`.
            if cmd[:2] == ["sudo", "-S"] and cmd[3] == "cat":
                # First "does users.json exist?" probe — say no.
                return MagicMock(returncode=1, stdout="", stderr="")
            if cmd[:2] == ["sudo", "-S"] and cmd[3] == "cp":
                src = cmd[4]
                dst = cmd[5]
                content = Path(src).read_text(encoding="utf-8")
                captured_writes.append((dst, content))
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run), \
             patch("getpass.getuser", return_value="installer"):
            success, _msg = wizard._provision_multiuser(
                self.tmpdir,
                {"sudo_pass": "pw", "multiuser_num_slots": 2,
                 "telegram_user_id": "12345", "user_name": "Admin"},
            )

        self.assertTrue(success)
        # Exactly one users.json written, contents include slot 1 admin binding.
        self.assertEqual(len(captured_writes), 1)
        target, payload = captured_writes[0]
        self.assertTrue(target.endswith("/users.json"))
        parsed = json.loads(payload)
        self.assertEqual(parsed["slots"]["1"]["telegram_id"], "12345")
        self.assertTrue(parsed["slots"]["1"]["is_admin"])


if __name__ == "__main__":
    unittest.main()
