"""Guards on the MemPalace source install contract.

The shared venv installs the upstream repo editable from `data/mempalace/src`
rather than a published wheel, so two things have to stay true: every extra
upstream declares is either installed or refused with a reason, and never more
than one hardware accelerator is installed (upstream: "install exactly one").

Nothing here touches the network, the real venv, or anyone's palace.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parent.parent
SETUP_PATH = ROOT / "skills" / "mempalace" / "scripts" / "mempalace_setup.py"


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


setup = _load("mp_setup_install_mod", SETUP_PATH)

PLATFORMS = ("darwin", "linux", "win32")


class ExtrasPartitionTests(unittest.TestCase):
    """Every upstream extra is a deliberate yes or a documented no."""

    def test_every_upstream_extra_is_installed_or_refused_with_a_reason(self):
        for platform in PLATFORMS:
            for accel in ("auto", "none"):
                with self.subTest(platform=platform, accel=accel):
                    installed = set(setup.extras_for(platform, accel))
                    skipped = setup.skipped_extras(platform, accel)
                    for extra in setup.UPSTREAM_EXTRAS:
                        self.assertTrue(
                            extra in installed or extra in skipped,
                            f"upstream extra '{extra}' is neither installed nor refused "
                            f"on {platform}; add it to COMMON_EXTRAS or _ALWAYS_SKIPPED",
                        )
                    for extra, reason in skipped.items():
                        self.assertTrue(reason.strip(), f"'{extra}' refused with no reason")

    def test_installed_and_skipped_never_overlap(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                installed = set(setup.extras_for(platform))
                self.assertFalse(installed & set(setup.skipped_extras(platform)))

    def test_common_extras_are_installed_everywhere(self):
        for platform in PLATFORMS:
            with self.subTest(platform=platform):
                self.assertLessEqual(set(setup.COMMON_EXTRAS), set(setup.extras_for(platform)))


class AcceleratorTests(unittest.TestCase):
    """Upstream pyproject: 'Install exactly one'."""

    def test_at_most_one_accelerator_per_platform(self):
        for platform in PLATFORMS:
            for accel in ("auto", "none", *setup.ACCELERATORS):
                with self.subTest(platform=platform, accel=accel):
                    chosen = set(setup.extras_for(platform, accel)) & set(setup.ACCELERATORS)
                    self.assertLessEqual(len(chosen), 1, f"more than one accelerator: {chosen}")

    def test_platform_defaults(self):
        self.assertEqual(setup.accelerator_for("darwin"), "coreml")
        self.assertEqual(setup.accelerator_for("win32"), "dml")
        # Linux stays CPU unless someone asks for CUDA: onnxruntime CPU already
        # arrives with chromadb, and NVIDIA hardware is not an assumption to make.
        self.assertIsNone(setup.accelerator_for("linux"))

    def test_none_installs_no_accelerator_and_explains_all_three(self):
        extras = setup.extras_for("darwin", "none")
        self.assertFalse(set(extras) & set(setup.ACCELERATORS))
        skipped = setup.skipped_extras("darwin", "none")
        for name in setup.ACCELERATORS:
            self.assertIn(name, skipped)

    def test_explicit_accelerator_overrides_platform_default(self):
        extras = setup.extras_for("linux", "gpu")
        self.assertIn("gpu", extras)
        self.assertNotIn("coreml", extras)
        self.assertNotIn("dml", extras)


class InterpreterPinTests(unittest.TestCase):
    """A venv built from a versioned Cellar path dies on the next brew bump."""

    def test_prefers_the_version_stable_homebrew_symlink(self):
        stable = "/opt/homebrew/opt/python@3.12/bin/python3.12"
        got = setup.stable_base_interpreter(
            version=(3, 12),
            executable="/opt/homebrew/Cellar/python@3.12/3.12.13/bin/python3.12",
            exists=lambda p: p == stable,
        )
        self.assertEqual(got, stable)

    def test_falls_back_to_the_running_interpreter_when_absent(self):
        running = "/usr/bin/python3.12"
        got = setup.stable_base_interpreter(
            version=(3, 12), executable=running, exists=lambda p: False,
        )
        self.assertEqual(got, running)

    def test_apple_silicon_prefix_wins_over_intel(self):
        got = setup.stable_base_interpreter(
            version=(3, 11), executable="/anything", exists=lambda p: True,
        )
        self.assertEqual(got, "/opt/homebrew/opt/python@3.11/bin/python3.11")

    def test_never_returns_a_versioned_cellar_path_when_a_stable_one_exists(self):
        got = setup.stable_base_interpreter(
            version=(3, 12),
            executable="/opt/homebrew/Cellar/python@3.12/3.12.13/bin/python3.12",
            exists=lambda p: p.startswith("/opt/homebrew/opt/"),
        )
        self.assertNotIn("/Cellar/", got)


class CheckoutContractTests(unittest.TestCase):
    def test_pins_a_release_tag_of_the_upstream_repo(self):
        self.assertEqual(setup.UPSTREAM_REPO, "https://github.com/MemPalace/mempalace.git")
        self.assertRegex(setup.PINNED_REF, r"^v\d+\.\d+\.\d+$")

    def test_checkout_lives_beside_the_venv_it_backs(self):
        # The venv installs src/ editable, so the two must not drift apart:
        # relocating either one strands the install.
        self.assertEqual(setup.SHARED_SRC_DIR.parent, setup.SHARED_VENV_DIR.parent)
        self.assertEqual(setup.SHARED_SRC_DIR.name, "src")

    def test_dirty_checkout_is_detected(self):
        if not which("git"):
            self.skipTest("git not available")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60,
                )

            git("init", "-q")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            (repo / "f.txt").write_text("one")
            git("add", "-A")
            git("commit", "-qm", "init")
            self.assertFalse(setup.checkout_is_dirty(repo))
            (repo / "f.txt").write_text("two")
            self.assertTrue(setup.checkout_is_dirty(repo))


if __name__ == "__main__":
    unittest.main()
