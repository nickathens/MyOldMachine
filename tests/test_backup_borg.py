"""Tests for the borg backup backend.

Covers:
- passphrase generation properties
- passphrase file IO (read/write/permissions)
- archive name builder
- create_backup output summary parser
- is_repo() structural detection
- borg subprocess argv shape (init, create, prune, list, extract)
- env injection (BORG_PASSPHRASE never in argv)
- absent borg binary handling
- dispatcher routing in utils/backup.py

All borg subprocess calls are mocked. No real borg invocations.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from utils import backup_borg  # noqa: E402


# ---- generate_passphrase ----

class GeneratePassphraseTests(unittest.TestCase):
    def test_default_length(self):
        p = backup_borg.generate_passphrase()
        self.assertEqual(len(p), backup_borg.PASSPHRASE_LENGTH)

    def test_alphanumeric_only(self):
        for _ in range(20):
            p = backup_borg.generate_passphrase()
            self.assertTrue(p.isalnum())

    def test_uniqueness(self):
        a = backup_borg.generate_passphrase()
        b = backup_borg.generate_passphrase()
        self.assertNotEqual(a, b)


# ---- write_passphrase / read_passphrase ----

class PassphraseIOTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "subdir" / "borg-passphrase"

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_creates_parent_and_file(self):
        ok = backup_borg.write_passphrase(self.path, "abc123")
        self.assertTrue(ok)
        self.assertTrue(self.path.exists())

    def test_write_mode_0600(self):
        backup_borg.write_passphrase(self.path, "abc123")
        mode = self.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_read_after_write(self):
        backup_borg.write_passphrase(self.path, "abc123")
        self.assertEqual(backup_borg.read_passphrase(self.path), "abc123")

    def test_write_strips_trailing_newline(self):
        # write_passphrase appends a single \n; read should strip it.
        backup_borg.write_passphrase(self.path, "abc\n\n")
        # Reader strips whitespace, so trailing newlines (whether one or many)
        # both come back as 'abc'.
        self.assertEqual(backup_borg.read_passphrase(self.path), "abc")

    def test_read_missing_returns_none(self):
        self.assertIsNone(backup_borg.read_passphrase(self.path))

    def test_overwrite_replaces(self):
        backup_borg.write_passphrase(self.path, "first")
        backup_borg.write_passphrase(self.path, "second")
        self.assertEqual(backup_borg.read_passphrase(self.path), "second")


# ---- _build_archive_name ----

class ArchiveNameTests(unittest.TestCase):
    def test_format(self):
        from datetime import datetime
        when = datetime(2026, 5, 10, 2, 5, 30)
        name = backup_borg._build_archive_name("mom", "host", when)
        self.assertEqual(name, "mom-host-2026-05-10_020530")

    def test_unsafe_hostname_chars_replaced(self):
        from datetime import datetime
        when = datetime(2026, 5, 10, 2, 5, 30)
        name = backup_borg._build_archive_name("mom", "host name/with$weird", when)
        self.assertNotIn("/", name)
        self.assertNotIn("$", name)
        self.assertNotIn(" ", name)

    def test_empty_hostname_falls_back(self):
        from datetime import datetime
        when = datetime(2026, 5, 10, 2, 5, 30)
        name = backup_borg._build_archive_name("mom", "", when)
        self.assertIn("host", name)


# ---- _summarize_create_output ----

class SummaryParserTests(unittest.TestCase):
    def test_parses_files_orig_dedup_duration(self):
        # Sample borg --stats output (paraphrased).
        sample = """
Archive name: mom-test-2026-05-10_020530
Number of files: 1234
Original size:  500 MB
Deduplicated size:  120 MB
Duration: 45.6 seconds
"""
        out = backup_borg._summarize_create_output(sample, "mom-test-2026-05-10_020530")
        self.assertIn("files=1234", out)
        self.assertIn("orig=500 MB", out)
        self.assertIn("dedup=120 MB", out)
        self.assertIn("45.6 seconds", out)

    def test_handles_empty(self):
        out = backup_borg._summarize_create_output("", "archive-x")
        self.assertEqual(out, "Backup created: archive-x")


# ---- _human_bytes ----

class HumanBytesTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(backup_borg._human_bytes(0), "0.0 B")
        self.assertEqual(backup_borg._human_bytes(512), "512.0 B")

    def test_kb(self):
        self.assertEqual(backup_borg._human_bytes(2048), "2.0 KB")

    def test_gb(self):
        self.assertEqual(backup_borg._human_bytes(2 * 1024 ** 3), "2.0 GB")


# ---- is_repo ----

class IsRepoTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_dir_is_not_repo(self):
        self.assertFalse(backup_borg.is_repo(self.path))

    def test_dir_without_data_is_not_repo(self):
        (self.path / "config").write_text("x")
        self.assertFalse(backup_borg.is_repo(self.path))

    def test_full_structure_is_repo(self):
        (self.path / "config").write_text("x")
        (self.path / "data").mkdir()
        self.assertTrue(backup_borg.is_repo(self.path))

    def test_nonexistent_path_is_not_repo(self):
        self.assertFalse(backup_borg.is_repo(self.path / "missing"))


# ---- _borg_env ----

class BorgEnvTests(unittest.TestCase):
    def test_passphrase_in_env(self):
        env = backup_borg._borg_env("hunter2")
        self.assertEqual(env["BORG_PASSPHRASE"], "hunter2")

    def test_relocated_repo_set(self):
        env = backup_borg._borg_env("x")
        self.assertEqual(env["BORG_RELOCATED_REPO_ACCESS_IS_OK"], "yes")

    def test_passphrase_never_empty_when_supplied(self):
        env = backup_borg._borg_env("")
        # Empty string is acceptable but key must exist (borg can be unencrypted).
        self.assertIn("BORG_PASSPHRASE", env)


# ---- subprocess argv shape (mocked) ----

class CreateBackupArgvTests(unittest.TestCase):
    """Verify the exact argv borg would be called with, without actually
    running borg."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name) / "repo"
        # Make it look like a repo so create_backup proceeds past is_repo.
        self.repo_dir.mkdir()
        (self.repo_dir / "config").write_text("x")
        (self.repo_dir / "data").mkdir()

        self.source = Path(self.tmp.name) / "src"
        self.source.mkdir()
        (self.source / "file.txt").write_text("hi")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_completed(self, stdout="", stderr="", rc=0):
        return subprocess.CompletedProcess(
            args="", returncode=rc, stdout=stdout, stderr=stderr,
        )

    def test_create_argv(self):
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            run.return_value = self._make_completed(stderr="Number of files: 1\n")
            ok, msg = backup_borg.create_backup(
                repo_path=str(self.repo_dir),
                passphrase="secret",
                sources=[str(self.source)],
                hostname="testhost",
            )
            self.assertTrue(ok, msg=msg)
            args, kwargs = run.call_args
            cmd = args[0]
            # Must start with 'borg create'
            self.assertEqual(cmd[0], "borg")
            self.assertEqual(cmd[1], "create")
            # Must have stats + compression flags
            self.assertIn("--stats", cmd)
            self.assertIn("--compression", cmd)
            self.assertIn("zstd,3", cmd)
            # Archive ref ends with ::archive-name
            archive_ref = next(c for c in cmd if "::" in c)
            self.assertTrue(archive_ref.startswith(str(self.repo_dir) + "::"))
            self.assertIn("mom-testhost-", archive_ref)
            # Source path is included
            self.assertIn(str(self.source), cmd)
            # Excludes are present
            self.assertIn("--exclude", cmd)
            # Passphrase is in env, never in argv
            self.assertNotIn("secret", " ".join(cmd))
            self.assertEqual(kwargs["env"]["BORG_PASSPHRASE"], "secret")

    def test_create_skips_missing_sources(self):
        missing = Path(self.tmp.name) / "does-not-exist"
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            run.return_value = self._make_completed()
            ok, msg = backup_borg.create_backup(
                repo_path=str(self.repo_dir),
                passphrase="secret",
                sources=[str(self.source), str(missing)],
            )
            self.assertTrue(ok, msg=msg)
            cmd = run.call_args[0][0]
            self.assertIn(str(self.source), cmd)
            self.assertNotIn(str(missing), cmd)

    def test_create_no_existing_sources_fails_cleanly(self):
        missing = Path(self.tmp.name) / "does-not-exist"
        with mock.patch.object(backup_borg, "_have_borg", return_value=True):
            ok, msg = backup_borg.create_backup(
                repo_path=str(self.repo_dir),
                passphrase="secret",
                sources=[str(missing)],
            )
            self.assertFalse(ok)
            self.assertIn("nothing to back up", msg)

    def test_create_not_a_repo(self):
        not_repo = Path(self.tmp.name) / "not-a-repo"
        not_repo.mkdir()
        with mock.patch.object(backup_borg, "_have_borg", return_value=True):
            ok, msg = backup_borg.create_backup(
                repo_path=str(not_repo),
                passphrase="x",
                sources=[str(self.source)],
            )
            self.assertFalse(ok)
            self.assertIn("not a borg repo", msg)

    def test_create_no_borg_binary(self):
        with mock.patch.object(backup_borg, "_have_borg", return_value=False):
            ok, msg = backup_borg.create_backup(
                repo_path=str(self.repo_dir),
                passphrase="x",
                sources=[str(self.source)],
            )
            self.assertFalse(ok)
            self.assertIn("not found", msg.lower())


class PruneArgvTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "config").write_text("x")
        (self.repo / "data").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _ok(self, stderr=""):
        return subprocess.CompletedProcess(args="", returncode=0, stdout="", stderr=stderr)

    def test_prune_argv_uses_correct_keep_flags(self):
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            run.return_value = self._ok(stderr="Pruning archive: a\nPruning archive: b\n")
            ok, msg = backup_borg.prune(
                str(self.repo), passphrase="p",
                keep_daily=7, keep_weekly=4, keep_monthly=6,
            )
            self.assertTrue(ok)
            cmd = run.call_args[0][0]
            self.assertIn("--keep-daily", cmd)
            self.assertEqual(cmd[cmd.index("--keep-daily") + 1], "7")
            self.assertIn("--keep-weekly", cmd)
            self.assertEqual(cmd[cmd.index("--keep-weekly") + 1], "4")
            self.assertIn("--keep-monthly", cmd)
            self.assertEqual(cmd[cmd.index("--keep-monthly") + 1], "6")
            self.assertIn("--glob-archives", cmd)
            self.assertEqual(cmd[cmd.index("--glob-archives") + 1], "mom-*")

    def test_prune_counts_pruned_lines(self):
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            run.return_value = self._ok(stderr="Pruning archive: a\nPruning archive: b\n")
            ok, msg = backup_borg.prune(str(self.repo), passphrase="p")
            self.assertTrue(ok)
            self.assertIn("2 archive", msg)


class InitRepoTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "newrepo"

    def tearDown(self):
        self.tmp.cleanup()

    def _ok(self):
        return subprocess.CompletedProcess(args="", returncode=0, stdout="", stderr="")

    def test_init_argv(self):
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            run.return_value = self._ok()
            ok, msg = backup_borg.init_repo(str(self.repo), "secret")
            self.assertTrue(ok)
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[:2], ["borg", "init"])
            self.assertIn("--encryption", cmd)
            self.assertEqual(cmd[cmd.index("--encryption") + 1], "repokey-blake2")
            self.assertIn("--make-parent-dirs", cmd)
            self.assertIn(str(self.repo), cmd)

    def test_init_idempotent_for_existing_repo(self):
        # Pre-existing repo
        self.repo.mkdir(parents=True)
        (self.repo / "config").write_text("x")
        (self.repo / "data").mkdir()
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            ok, msg = backup_borg.init_repo(str(self.repo), "secret")
            self.assertTrue(ok)
            self.assertIn("already initialized", msg)
            run.assert_not_called()

    def test_init_refuses_nonempty_nonrepo_dir(self):
        self.repo.mkdir(parents=True)
        (self.repo / "junk.txt").write_text("not a repo")
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            ok, msg = backup_borg.init_repo(str(self.repo), "secret")
            self.assertFalse(ok)
            self.assertIn("non-empty", msg)
            run.assert_not_called()


class ListBackupsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "config").write_text("x")
        (self.repo / "data").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_argv_and_format(self):
        list_out = "mom-host-2026-05-10_020000\nmom-host-2026-05-09_020000\n"
        info_out = json.dumps({"cache": {"stats": {"unique_size": 5 * 1024 ** 2}}})

        def runner(*args, **kwargs):
            cmd = args[0]
            if "info" in cmd:
                return subprocess.CompletedProcess(
                    args="", returncode=0, stdout=info_out, stderr=""
                )
            return subprocess.CompletedProcess(
                args="", returncode=0, stdout=list_out, stderr=""
            )

        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run", side_effect=runner):
            out = backup_borg.list_backups(str(self.repo), passphrase="p")
            self.assertIn("Borg backups in", out)
            self.assertIn("mom-host-2026-05-10_020000", out)
            self.assertIn("Total: 2 archive", out)
            self.assertIn("Repo size on disk: 5.0 MB", out)


class RestoreBackupTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "config").write_text("x")
        (self.repo / "data").mkdir()
        self.target = Path(self.tmp.name) / "restore"

    def tearDown(self):
        self.tmp.cleanup()

    def test_restore_argv(self):
        out = "file/a\nfile/b\nfile/c\n"
        with mock.patch.object(backup_borg, "_have_borg", return_value=True), \
             mock.patch.object(backup_borg.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess(
                args="", returncode=0, stdout=out, stderr=""
            )
            ok, msg = backup_borg.restore_backup(
                str(self.repo), passphrase="p",
                archive_name="mom-host-x", target_dir=str(self.target),
            )
            self.assertTrue(ok)
            self.assertIn("3 entries", msg)
            cmd, kwargs = run.call_args[0][0], run.call_args[1]
            self.assertEqual(cmd[:2], ["borg", "extract"])
            self.assertIn("--list", cmd)
            archive = next(c for c in cmd if "::" in c)
            self.assertEqual(archive, f"{self.repo}::mom-host-x")
            self.assertEqual(kwargs["cwd"], str(self.target))


# ---- dispatcher routing in utils/backup.py ----

class DispatcherRoutingTests(unittest.TestCase):
    """Verify utils.backup correctly routes to tarball vs borg backends."""

    def setUp(self):
        # Force a clean import of utils.backup so tests don't see stale state.
        for k in list(sys.modules.keys()):
            if k.startswith("utils.backup"):
                del sys.modules[k]

    def _import(self):
        from utils import backup as _b
        return _b

    def test_default_is_tarball(self):
        b = self._import()
        self.assertEqual(b._backup_tool({}), "tarball")
        self.assertEqual(b._backup_tool({"backup_tool": "tarball"}), "tarball")

    def test_borg_routing(self):
        b = self._import()
        self.assertEqual(b._backup_tool({"backup_tool": "borg"}), "borg")
        self.assertEqual(b._backup_tool({"backup_tool": "BORG"}), "borg")
        # Trim whitespace
        self.assertEqual(b._backup_tool({"backup_tool": "  borg  "}), "borg")

    def test_unknown_tool_falls_back_tarball(self):
        b = self._import()
        self.assertEqual(b._backup_tool({"backup_tool": "rsync"}), "tarball")

    def test_create_dispatches_to_borg_branch(self):
        b = self._import()
        with mock.patch.object(b, "get_maintenance_config",
                               return_value={"backup_tool": "borg"}), \
             mock.patch.object(b, "_create_borg_backup", return_value="borg!"), \
             mock.patch.object(b, "_create_tarball_backup", return_value="tar!"):
            self.assertEqual(b.create_backup("/tmp/x"), "borg!")

    def test_create_dispatches_to_tarball_branch(self):
        b = self._import()
        with mock.patch.object(b, "get_maintenance_config", return_value={}), \
             mock.patch.object(b, "_create_borg_backup", return_value="borg!"), \
             mock.patch.object(b, "_create_tarball_backup", return_value="tar!"):
            self.assertEqual(b.create_backup("/tmp/x"), "tar!")

    def test_list_dispatches_to_borg_branch(self):
        b = self._import()
        with mock.patch.object(b, "get_maintenance_config",
                               return_value={"backup_tool": "borg"}), \
             mock.patch.object(b, "_list_borg_backups", return_value="borg-list"), \
             mock.patch.object(b, "_list_tarball_backups", return_value="tar-list"):
            self.assertEqual(b.list_backups("/tmp"), "borg-list")

    def test_restore_dispatches_to_borg_branch(self):
        b = self._import()
        with mock.patch.object(b, "get_maintenance_config",
                               return_value={"backup_tool": "borg",
                                             "backup_path": "/tmp/repo"}), \
             mock.patch.object(b, "_restore_borg_backup", return_value="borg-restore"), \
             mock.patch.object(b, "_restore_tarball_backup", return_value="tar-restore"):
            self.assertEqual(b.restore_backup("archive-name"), "borg-restore")


# ---- borg_setup ----

class BorgSetupTests(unittest.TestCase):
    def setUp(self):
        for k in list(sys.modules.keys()):
            if k.startswith("install.borg_setup"):
                del sys.modules[k]
        from install import borg_setup
        self.bs = borg_setup

    def test_passphrase_path_for_single_user(self):
        repo = Path("/opt/MOM")
        p = self.bs.passphrase_path_for(repo, multiuser=False)
        self.assertEqual(p, repo / "data" / "borg-passphrase")

    def test_passphrase_path_for_multiuser(self):
        repo = Path("/opt/MOM")
        p = self.bs.passphrase_path_for(repo, multiuser=True)
        self.assertEqual(p, repo / "data" / "orchestrator" / "borg-passphrase")

    def test_install_borg_no_op_when_present(self):
        with mock.patch.object(self.bs, "have_borg", return_value=True):
            ok, msg = self.bs.install_borg()
            self.assertTrue(ok)
            self.assertIn("already", msg)

    def test_detect_pkg_manager_apt(self):
        def which(cmd):
            return "/usr/bin/apt" if cmd == "apt" else None
        with mock.patch.object(self.bs.shutil, "which", side_effect=which):
            self.assertEqual(self.bs._detect_pkg_manager(), "apt")

    def test_detect_pkg_manager_none(self):
        with mock.patch.object(self.bs.shutil, "which", return_value=None):
            self.assertIsNone(self.bs._detect_pkg_manager())

    def test_install_borg_macos_falls_through_to_pip(self):
        # Simulate: not installed, brew fails, pip succeeds.
        with mock.patch.object(self.bs, "have_borg",
                               side_effect=[False, True, True]):
            with mock.patch.object(self.bs, "_install_via_brew",
                                   return_value=(False, "brew failed")), \
                 mock.patch.object(self.bs, "_try_pip_install_borg",
                                   return_value=(True, "pip ok")), \
                 mock.patch.object(self.bs.sys, "platform", "darwin"):
                ok, msg = self.bs.install_borg()
                self.assertTrue(ok)
                self.assertIn("pip ok", msg)

    def test_install_borg_linux_pkg_first(self):
        with mock.patch.object(self.bs, "have_borg",
                               side_effect=[False, True]):
            with mock.patch.object(self.bs, "_install_via_linux_pkg",
                                   return_value=(True, "apt ok")), \
                 mock.patch.object(self.bs.sys, "platform", "linux"):
                ok, msg = self.bs.install_borg()
                self.assertTrue(ok)
                self.assertEqual(msg, "apt ok")


if __name__ == "__main__":
    unittest.main()
