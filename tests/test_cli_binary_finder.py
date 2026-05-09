"""Tests for CLI binary discovery helpers.

`_find_cli_binary` (core/llm.py) and `_discover_cli_bin_paths` (install/service.py)
locate the `claude` / `codex` executables when launchd or systemd inherit a
PATH that excludes `~/.local/bin`. This file covers both helpers end-to-end
without touching the real filesystem outside a tmpdir.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import llm  # noqa: E402
from install import service  # noqa: E402


class FindCliBinaryTests(unittest.TestCase):
    """`_find_cli_binary` should prefer PATH, then fall back to known dirs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake_home = Path(self._tmp.name)
        # Touch the directory tree so HOME exists.
        (self.fake_home / ".local" / "bin").mkdir(parents=True)
        (self.fake_home / ".npm-global" / "bin").mkdir(parents=True)
        (self.fake_home / ".bun" / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _patch_home(self):
        return patch.dict(os.environ, {"HOME": str(self.fake_home)})

    def test_prefers_path_when_which_succeeds(self) -> None:
        with patch.object(llm.shutil, "which", return_value="/usr/local/bin/claude"):
            self.assertEqual(llm._find_cli_binary("claude"), "/usr/local/bin/claude")

    def test_falls_back_to_local_bin(self) -> None:
        target = self.fake_home / ".local" / "bin" / "claude"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        with self._patch_home(), patch.object(llm.shutil, "which", return_value=None):
            self.assertEqual(llm._find_cli_binary("claude"), str(target))

    def test_falls_back_to_homebrew(self) -> None:
        with self._patch_home(), \
             patch.object(llm.shutil, "which", return_value=None), \
             patch.object(llm.Path, "exists", autospec=True) as exists, \
             patch.object(llm.Path, "is_dir", autospec=True) as is_dir:
            # Make only /opt/homebrew/bin/claude exist.
            def exists_side(self):
                return str(self) == "/opt/homebrew/bin/claude"
            def is_dir_side(self):
                return False
            exists.side_effect = exists_side
            is_dir.side_effect = is_dir_side
            self.assertEqual(llm._find_cli_binary("claude"), "/opt/homebrew/bin/claude")

    def test_falls_back_to_nvm(self) -> None:
        nvm_node = self.fake_home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
        nvm_node.mkdir(parents=True)
        target = nvm_node / "claude"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        with self._patch_home(), patch.object(llm.shutil, "which", return_value=None):
            result = llm._find_cli_binary("claude")
            self.assertEqual(result, str(target))

    def test_picks_highest_nvm_version(self) -> None:
        for ver in ("v18.0.0", "v20.0.0", "v22.0.0"):
            d = self.fake_home / ".nvm" / "versions" / "node" / ver / "bin"
            d.mkdir(parents=True)
            (d / "claude").write_text("ok")
        with self._patch_home(), patch.object(llm.shutil, "which", return_value=None):
            result = llm._find_cli_binary("claude")
            self.assertIn("v22.0.0", result)

    def test_returns_literal_when_nothing_found(self) -> None:
        with self._patch_home(), patch.object(llm.shutil, "which", return_value=None):
            self.assertEqual(llm._find_cli_binary("claude"), "claude")

    def test_directory_with_same_name_is_skipped(self) -> None:
        # If a stale directory at ~/.local/bin/claude exists, don't return it.
        bogus = self.fake_home / ".local" / "bin" / "claude"
        bogus.mkdir()
        with self._patch_home(), patch.object(llm.shutil, "which", return_value=None):
            self.assertEqual(llm._find_cli_binary("claude"), "claude")

    def test_codex_uses_same_logic(self) -> None:
        target = self.fake_home / ".local" / "bin" / "codex"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        with self._patch_home(), patch.object(llm.shutil, "which", return_value=None):
            self.assertEqual(llm._find_cli_binary("codex"), str(target))


class DiscoverCliBinPathsTests(unittest.TestCase):
    """`_discover_cli_bin_paths` builds the PATH suffix injected into plists."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake_home = Path(self._tmp.name)
        (self.fake_home / ".local" / "bin").mkdir(parents=True)
        (self.fake_home / ".npm-global" / "bin").mkdir(parents=True)
        (self.fake_home / ".bun" / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_when_no_cli_found(self) -> None:
        self.assertEqual(service._discover_cli_bin_paths(self.fake_home), "")

    def test_includes_local_bin_when_claude_present(self) -> None:
        (self.fake_home / ".local" / "bin" / "claude").write_text("ok")
        out = service._discover_cli_bin_paths(self.fake_home)
        self.assertEqual(out, f"{self.fake_home / '.local' / 'bin'}:")

    def test_includes_local_bin_when_codex_present(self) -> None:
        (self.fake_home / ".local" / "bin" / "codex").write_text("ok")
        out = service._discover_cli_bin_paths(self.fake_home)
        self.assertEqual(out, f"{self.fake_home / '.local' / 'bin'}:")

    def test_combines_multiple_dirs(self) -> None:
        (self.fake_home / ".local" / "bin" / "claude").write_text("ok")
        (self.fake_home / ".npm-global" / "bin" / "codex").write_text("ok")
        out = service._discover_cli_bin_paths(self.fake_home)
        # Order should follow candidate_dirs (~/.local/bin first).
        local = str(self.fake_home / ".local" / "bin")
        npm = str(self.fake_home / ".npm-global" / "bin")
        self.assertEqual(out, f"{local}:{npm}:")

    def test_dedups_when_same_dir_has_both(self) -> None:
        d = self.fake_home / ".local" / "bin"
        (d / "claude").write_text("ok")
        (d / "codex").write_text("ok")
        out = service._discover_cli_bin_paths(self.fake_home)
        # Single segment + trailing colon, no duplicate.
        self.assertEqual(out, f"{d}:")

    def test_includes_nvm_node_dir(self) -> None:
        nvm_node = self.fake_home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
        nvm_node.mkdir(parents=True)
        (nvm_node / "claude").write_text("ok")
        out = service._discover_cli_bin_paths(self.fake_home)
        self.assertIn(str(nvm_node), out)
        self.assertTrue(out.endswith(":"))

    def test_skips_non_existent_home(self) -> None:
        self.assertEqual(
            service._discover_cli_bin_paths(Path("/no/such/dir/anywhere")), ""
        )

    def test_trailing_colon_for_safe_splice(self) -> None:
        (self.fake_home / ".local" / "bin" / "claude").write_text("ok")
        out = service._discover_cli_bin_paths(self.fake_home)
        # Must always end with a colon when non-empty so the plist's
        # PATH="...:{{CLI_BIN_PATHS}}/opt/homebrew/bin..." doesn't produce ::
        self.assertTrue(out.endswith(":"))
        self.assertNotIn("::", out)


class HealthCheckRediscoveryTests(unittest.TestCase):
    """If the binary appears after init (user runs `claude install` mid-session),
    health_check should re-probe and succeed."""

    def test_claude_provider_rediscovers_binary(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6")
        # Simulate init having found nothing.
        provider._cli_binary = "claude"

        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            target = fake_home / ".local" / "bin" / "claude"
            target.parent.mkdir(parents=True)
            target.write_text("ok")
            target.chmod(0o755)
            with patch.dict(os.environ, {"HOME": str(fake_home)}), \
                 patch.object(llm.shutil, "which", return_value=None), \
                 patch.object(llm.asyncio, "create_subprocess_exec",
                               new=AsyncMock(side_effect=FileNotFoundError())):
                ok_, msg = asyncio.run(provider.health_check())
                self.assertFalse(ok_)
                self.assertIn("cannot be executed", msg)
                # After re-probe, _cli_binary should now point at the rediscovered path.
                self.assertEqual(provider._cli_binary, str(target))

    def test_claude_health_message_mentions_native_install(self) -> None:
        import asyncio
        provider = llm.ClaudeCLIProvider("claude-sonnet-4-6")
        provider._cli_binary = ""
        with patch.dict(os.environ, {"HOME": "/no/such/dir"}), \
             patch.object(llm.shutil, "which", return_value=None):
            ok_, msg = asyncio.run(provider.health_check())
            self.assertFalse(ok_)
            self.assertIn("claude install", msg)


if __name__ == "__main__":
    unittest.main()
