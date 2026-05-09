"""Regression tests for `write_user_profile` resilience.

Bug guarded against (Nick's Mac, MyOldMachine dcbfd00):

  1. First install completed `_provision_multiuser`, which chowned
     data/memory/ to mom_orchestrator with mode 0700.
  2. User wiped ~/MyOldMachine without sudo. rm couldn't traverse the
     mode-0700 dirs, so they survived as zombies.
  3. Fresh `curl | bash` re-cloned on top. Wizard ran write_user_profile,
     hit `(data/memory/projects).mkdir(parents=True, exist_ok=True)` and
     crashed with PermissionError because data/memory/ was still
     orchestrator-owned mode 0700.

Fix: write_user_profile catches PermissionError on the memory subdir
mkdir and continues. _provision_multiuser later rebuilds these dirs
under the right ownership; the bot's memory system also creates dirs
on demand at runtime.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


class WriteUserProfileResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mom_user_profile_test_"))
        (self.tmpdir / "data").mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return {
            "telegram_user_id": 1234,
            "user_name": "TestUser",
        }

    def _machine_specs(self):
        return {
            "hostname": "test-host",
            "cpu": "test-cpu",
            "ram_gb": 16,
            "disk_gb": 256,
        }

    def test_creates_memory_subdirs_on_clean_install(self):
        """The happy path: nothing exists, mkdir succeeds."""
        wizard.write_user_profile(self.tmpdir, self._config(), self._machine_specs())
        memory = self.tmpdir / "data" / "memory"
        self.assertTrue((memory / "projects").is_dir())
        self.assertTrue((memory / "topics").is_dir())
        self.assertTrue((memory / "decisions").is_dir())

    def test_users_json_written(self):
        wizard.write_user_profile(self.tmpdir, self._config(), self._machine_specs())
        users_json = self.tmpdir / "data" / "users.json"
        self.assertTrue(users_json.exists())

    def test_memories_json_written(self):
        wizard.write_user_profile(self.tmpdir, self._config(), self._machine_specs())
        memories = self.tmpdir / "data" / "users" / "1234" / "memories.json"
        self.assertTrue(memories.exists())

    def test_skips_memory_subdir_on_permission_error(self):
        """The bug: data/memory/ exists but is orchestrator-owned mode 0700.
        mkdir raises PermissionError. write_user_profile must not crash."""
        # Pre-create data/memory/ so subdir mkdir is the failing point.
        (self.tmpdir / "data" / "memory").mkdir()

        original_mkdir = Path.mkdir
        memory_dir = (self.tmpdir / "data" / "memory").resolve()

        def mock_mkdir(self, *args, **kwargs):
            try:
                resolved_self = self.resolve()
            except OSError:
                resolved_self = self
            try:
                resolved_parent = resolved_self.parent.resolve()
            except OSError:
                resolved_parent = resolved_self.parent
            if resolved_parent == memory_dir:
                raise PermissionError(13, "Permission denied", str(self))
            return original_mkdir(self, *args, **kwargs)

        with patch.object(Path, "mkdir", mock_mkdir):
            wizard.write_user_profile(
                self.tmpdir, self._config(), self._machine_specs()
            )

        # Other writes still succeeded.
        self.assertTrue((self.tmpdir / "data" / "users.json").exists())
        self.assertTrue(
            (self.tmpdir / "data" / "users" / "1234" / "memories.json").exists()
        )

    def test_partial_memory_subdir_failure_does_not_block_remaining(self):
        """If mkdir fails for one subdir, the loop continues for the others.
        Simulated by raising on 'topics' only."""
        (self.tmpdir / "data" / "memory").mkdir()
        original_mkdir = Path.mkdir

        def mock_mkdir(self, *args, **kwargs):
            if self.name == "topics" and self.parent.name == "memory":
                raise PermissionError(13, "Permission denied", str(self))
            return original_mkdir(self, *args, **kwargs)

        with patch.object(Path, "mkdir", mock_mkdir):
            wizard.write_user_profile(
                self.tmpdir, self._config(), self._machine_specs()
            )

        memory = self.tmpdir / "data" / "memory"
        self.assertTrue((memory / "projects").is_dir())
        self.assertTrue((memory / "decisions").is_dir())
        self.assertFalse((memory / "topics").is_dir())

    def test_non_permission_errors_still_propagate(self):
        """OSError that isn't PermissionError must still surface (e.g., EROFS,
        ENOSPC). Don't silently swallow real failures."""
        (self.tmpdir / "data" / "memory").mkdir()
        original_mkdir = Path.mkdir

        def mock_mkdir(self, *args, **kwargs):
            if self.parent.name == "memory":
                raise OSError(28, "No space left on device", str(self))
            return original_mkdir(self, *args, **kwargs)

        with patch.object(Path, "mkdir", mock_mkdir), self.assertRaises(OSError):
            wizard.write_user_profile(
                self.tmpdir, self._config(), self._machine_specs()
            )


if __name__ == "__main__":
    unittest.main()
