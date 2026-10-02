"""The nightly repair of Python tool kits a Homebrew Python update broke.

Homebrew installs every Python release in a folder named after its exact
version and deletes it when the next release lands. A kit (virtual
environment) linked into that folder dies with it, exit 127. On the reference
Mac the 04:00 update killed six kits that way on 14 Aug 2026 and two more on
2 Oct 2026. utils/venv_repair.py moves them onto Homebrew's stable path.

Everything here runs against a fake Homebrew and fake kits in a temporary
folder. Nothing reads the real home folder: a walk of it from a bot session
raised two macOS consent boxes on 2 Oct 2026. The one test that starts a real
Python builds its own kits and runs only where the test Python is itself a
Homebrew one.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import venv_repair as vr  # noqa: E402

CELLAR_314 = "/opt/homebrew/Cellar/python@3.12/3.12.14"
FRAMEWORK = "/Frameworks/Python.framework/Versions/3.12"


def _fake_python(path: Path, prints_minor: str = "3.12", fails: bool = False) -> None:
    """A stand-in interpreter that answers the start test the way Python does.

    Run through a kit's bin/python3.12 link, $0 is the link, so the kit's own
    folder is two levels up: that is what a real kit reports as sys.prefix.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if fails:
        body = 'echo "Fatal Python error: init_fs_encoding" >&2\nexit 1\n'
    else:
        body = f'echo {prints_minor}\ncd "$(dirname "$0")/.." && pwd -P\n'
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class FakeBrew:
    """A Homebrew prefix holding python@3.12 releases, opt pointing at one."""

    def __init__(self, base: Path, current: str = "3.12.15", others=(), **python):
        self.prefix = base / "brew"
        self.current = current
        for version in (current, *others):
            _fake_python(self.keg(version) / "bin" / "python3.12", **python)
        opt = self.prefix / "opt"
        opt.mkdir(parents=True, exist_ok=True)
        (opt / "python@3.12").symlink_to(Path("..") / "Cellar" / "python@3.12" / current)

    def keg(self, version: str) -> Path:
        return self.prefix / "Cellar" / "python@3.12" / version

    def python(self, version: str) -> str:
        return str(self.keg(version) / "bin" / "python3.12")

    @property
    def stable_python(self) -> str:
        return str(self.prefix / "opt" / "python@3.12" / "bin" / "python3.12")

    @property
    def stable_home(self) -> str:
        return str(self.prefix / "opt" / "python@3.12" / "bin")


def make_kit(where: Path, interpreter: str, home: str | None = None) -> Path:
    """A kit laid out as `python -m venv` lays it out."""
    bindir = where / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python3.12").symlink_to(interpreter)
    (bindir / "python3").symlink_to("python3.12")
    (bindir / "python").symlink_to("python3.12")
    (where / "pyvenv.cfg").write_text(
        f"home = {home if home is not None else os.path.dirname(interpreter)}\n"
        "include-system-site-packages = false\n"
        "version = 3.12.14\n"
        f"executable = {interpreter}\n"
        f"command = /some/venv/bin/python3 -m venv {where}\n"
    )
    return where


class TempCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # macOS hands out /var/folders/... while resolved paths read
        # /private/var/folders/...: resolve once so both sides agree.
        self.tmp = Path(tmp.name).resolve()


class StablePathTests(unittest.TestCase):
    def test_apple_silicon_framework_interpreter(self):
        self.assertEqual(
            vr.stable_path(f"{CELLAR_314}{FRAMEWORK}/bin/python3.12"),
            f"/opt/homebrew/opt/python@3.12{FRAMEWORK}/bin/python3.12")

    def test_home_folder_and_revision_suffix(self):
        self.assertEqual(
            vr.stable_path("/opt/homebrew/Cellar/python@3.12/3.12.13_4/bin"),
            "/opt/homebrew/opt/python@3.12/bin")

    def test_intel_and_linuxbrew_prefixes(self):
        self.assertEqual(
            vr.stable_path("/usr/local/Cellar/python@3.11/3.11.9/bin/python3.11"),
            "/usr/local/opt/python@3.11/bin/python3.11")
        self.assertEqual(
            vr.stable_path("/home/linuxbrew/.linuxbrew/Cellar/python@3.13/3.13.1/bin/python3.13"),
            "/home/linuxbrew/.linuxbrew/opt/python@3.13/bin/python3.13")

    def test_never_another_minor_version(self):
        # A 3.13 release in a python@3.12 folder would move compiled packages
        # across an ABI. Not something Homebrew does, so refuse it outright.
        self.assertIsNone(vr.stable_path("/opt/homebrew/Cellar/python@3.12/3.13.0/bin/python3.12"))
        self.assertIsNone(vr.stable_path("/opt/homebrew/Cellar/python@3.12/3.120.1/bin/python3.12"))

    def test_paths_that_are_not_a_versioned_python(self):
        for path in (
            "/opt/homebrew/opt/python@3.12/bin/python3.12",
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/Cellar/node/22.1.0/bin/node",
            "/usr/bin/python3",
            "relative/Cellar/python@3.12/3.12.1/bin/python3.12",
            "",
            None,
        ):
            self.assertIsNone(vr.stable_path(path), path)


class InspectTests(TempCase):
    def test_kit_on_the_stable_path_is_ok(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.stable_python, brew.stable_home)
        self.assertEqual(vr.inspect(kit).status, vr.OK)

    def test_kit_built_from_another_kit_is_pinned(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.python("3.12.15"))
        found = vr.inspect(kit)
        self.assertEqual(found.status, vr.PINNED)
        self.assertEqual(found.minor, "3.12")
        self.assertEqual(
            {(str(w), new) for w, _, new in found.changes},
            {(str(kit / "bin" / "python3.12"), brew.stable_python),
             ("home", brew.stable_home)})

    def test_kit_whose_release_was_deleted_is_dead(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        found = vr.inspect(kit)
        self.assertEqual(found.status, vr.DEAD)
        self.assertIn("3.12.14", found.detail)

    def test_dead_kit_whose_python_was_uninstalled_is_broken(self):
        brew = FakeBrew(self.tmp)
        (brew.prefix / "opt" / "python@3.12").unlink()
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        found = vr.inspect(kit)
        self.assertEqual(found.status, vr.BROKEN)
        self.assertEqual(found.detail, "Python 3.12 is no longer installed")
        self.assertEqual(found.changes, [])

    def test_dead_kit_from_a_python_that_is_not_homebrews_is_broken(self):
        kit = make_kit(self.tmp / "k", "/nonexistent/pyenv/versions/3.11.4/bin/python3.11")
        found = vr.inspect(kit)
        self.assertEqual(found.status, vr.BROKEN)
        self.assertIn("/nonexistent/pyenv", found.detail)

    def test_kit_on_an_older_release_still_installed_is_left_alone(self):
        # The stable path would move it to another release. It works, and it
        # will be repaired the night that release is deleted.
        brew = FakeBrew(self.tmp, others=("3.12.14",))
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        self.assertEqual(vr.inspect(kit).status, vr.KEPT)

    def test_only_home_pinned_and_dead(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.stable_python,
                       home=str(brew.keg("3.12.14") / "bin"))
        found = vr.inspect(kit)
        self.assertEqual(found.status, vr.DEAD)
        self.assertEqual([w for w, _, _ in found.changes], ["home"])

    def test_a_stale_executable_line_is_not_a_fault(self):
        # Healthy kits keep naming a deleted release in `executable` after
        # every update (Python never reads it), so it must not count.
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.stable_python, brew.stable_home)
        cfg = kit / "pyvenv.cfg"
        cfg.write_text(cfg.read_text().replace(
            f"executable = {brew.stable_python}", f"executable = {brew.python('3.12.14')}"))
        self.assertEqual(vr.inspect(kit).status, vr.OK)


class ApplyTests(TempCase):
    def _snapshot(self, kit: Path) -> tuple:
        bindir = kit / "bin"
        links = {p.name: os.readlink(p) for p in bindir.iterdir() if p.is_symlink()}
        cfg = kit / "pyvenv.cfg"
        return links, cfg.read_bytes(), cfg.stat().st_mode

    def _leftovers(self, kit: Path) -> list[str]:
        names = [p.name for p in (kit / "bin").iterdir()] + [p.name for p in kit.iterdir()]
        return [n for n in names if "venv-repair" in n]

    def test_dead_kit_is_repaired_and_starts(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        (kit / "pyvenv.cfg").chmod(0o640)
        before = (kit / "pyvenv.cfg").read_text()

        done = vr.apply(vr.inspect(kit))

        self.assertEqual(done.status, vr.REPAIRED, done.detail)
        self.assertEqual(os.readlink(kit / "bin" / "python3.12"), brew.stable_python)
        after = (kit / "pyvenv.cfg").read_text()
        self.assertIn(f"home = {brew.stable_home}\n", after)
        # Every other line byte for byte, including the stale `executable`.
        self.assertEqual(
            [line for line in before.splitlines() if not line.startswith("home")],
            [line for line in after.splitlines() if not line.startswith("home")])
        self.assertEqual((kit / "pyvenv.cfg").stat().st_mode & 0o777, 0o640)
        self.assertEqual(self._leftovers(kit), [])
        ran = subprocess.run([str(kit / "bin" / "python"), "-c", "pass"],
                             capture_output=True, text=True)
        self.assertEqual(ran.stdout.splitlines(), ["3.12", str(kit)])

    def test_leftovers_of_an_interrupted_run_are_replaced_not_followed(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        sentinel = self.tmp / "sentinel"
        sentinel.write_text("untouched")
        (kit / ".pyvenv.cfg.venv-repair").symlink_to(sentinel)
        (kit / "bin" / ".python3.12.venv-repair").symlink_to(sentinel)
        self.assertEqual(vr.apply(vr.inspect(kit)).status, vr.REPAIRED)
        self.assertEqual(sentinel.read_text(), "untouched")
        self.assertEqual(self._leftovers(kit), [])

    def test_pinned_kit_is_secured(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.python("3.12.15"))
        done = vr.apply(vr.inspect(kit))
        self.assertEqual(done.status, vr.SECURED, done.detail)
        self.assertEqual(os.readlink(kit / "bin" / "python3.12"), brew.stable_python)
        # And it survives the next release, which is the point.
        _fake_python(brew.keg("3.12.16") / "bin" / "python3.12")
        opt = brew.prefix / "opt" / "python@3.12"
        opt.unlink()
        opt.symlink_to(Path("..") / "Cellar" / "python@3.12" / "3.12.16")
        shutil.rmtree(brew.keg("3.12.15"))
        self.assertEqual(vr.inspect(kit).status, vr.OK)

    def test_dead_kit_that_still_does_not_start_is_put_back(self):
        brew = FakeBrew(self.tmp, fails=True)
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        before = self._snapshot(kit)
        done = vr.apply(vr.inspect(kit))
        self.assertEqual(done.status, vr.BROKEN)
        self.assertIn("init_fs_encoding", done.detail)
        self.assertIn("left as it was", done.detail)
        self.assertEqual(self._snapshot(kit), before)
        self.assertEqual(self._leftovers(kit), [])

    def test_pinned_kit_that_fails_the_start_test_keeps_its_working_link(self):
        brew = FakeBrew(self.tmp, prints_minor="3.11")
        kit = make_kit(self.tmp / "k", brew.python("3.12.15"))
        before = self._snapshot(kit)
        done = vr.apply(vr.inspect(kit))
        self.assertEqual(done.status, vr.KEPT)
        self.assertIn("3.11", done.detail)
        self.assertEqual(self._snapshot(kit), before)

    def test_start_test_rejects_an_interpreter_outside_the_kit(self):
        brew = FakeBrew(self.tmp)
        kit = make_kit(self.tmp / "k", brew.python("3.12.14"))
        found = vr.inspect(kit)
        with patch.object(vr.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "3.12\n/elsewhere\n", "")
            done = vr.apply(found)
        self.assertEqual(done.status, vr.BROKEN)
        self.assertIn("outside the kit", done.detail)

    def test_kits_that_need_nothing_are_not_touched(self):
        brew = FakeBrew(self.tmp, others=("3.12.14",))
        kits = [make_kit(self.tmp / "ok", brew.stable_python, brew.stable_home),
                make_kit(self.tmp / "kept", brew.python("3.12.14")),
                make_kit(self.tmp / "broken", "/nonexistent/python3.11")]
        before = [self._snapshot(k) for k in kits]
        with patch.object(vr.subprocess, "run") as run:
            results = [vr.apply(vr.inspect(k)) for k in kits]
        run.assert_not_called()
        self.assertEqual([r.status for r in results], [vr.OK, vr.KEPT, vr.BROKEN])
        self.assertEqual([self._snapshot(k) for k in kits], before)


class RealPythonTests(TempCase):
    """The same two repairs on kits built by the real interpreter.

    Runs only where the Python running the tests is a Homebrew one, as on the
    reference Mac; elsewhere there is no versioned folder to be tied to.
    """

    def setUp(self):
        super().setUp()
        base = os.path.realpath(getattr(sys, "_base_executable", "") or sys.executable)
        stable = vr.stable_path(base)
        if not stable or not os.path.exists(stable):
            self.skipTest(f"the test Python is not a Homebrew release ({base})")
        self.base = base

    def _kit(self, name: str) -> Path:
        kit = self.tmp / name
        # Built from the running Python, itself usually a venv: exactly how
        # `python3 -m venv` inside a bot session produces a pinned kit.
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(kit)],
                       check=True, capture_output=True)
        site = next(kit.glob("lib/python3.*/site-packages"))
        (site / "kitprobe.py").write_text("VALUE = 42\n")
        return kit

    def _imports_its_package(self, kit: Path) -> str:
        try:
            ran = subprocess.run([str(kit / "bin" / "python"), "-c",
                                  "import kitprobe, sys; print(kitprobe.VALUE, sys.prefix)"],
                                 capture_output=True, text=True)
        except OSError as e:    # a dead kit cannot even be started
            return str(e)
        return ran.stdout.strip() or ran.stderr.strip()

    def test_a_kit_built_from_a_kit_is_secured_for_real(self):
        kit = self._kit("pinned")
        self.assertEqual(vr.inspect(kit).status, vr.PINNED)
        done = vr.apply(vr.inspect(kit))
        self.assertEqual(done.status, vr.SECURED, done.detail)
        self.assertEqual(self._imports_its_package(kit), f"42 {kit}")

    def test_a_dead_kit_is_repaired_for_real(self):
        kit = self._kit("dead")
        m = vr._KEG.fullmatch(self.base)
        gone = f"{m['prefix']}/Cellar/{m['formula']}/{m['minor']}.0-gone{m['rest']}"
        link = kit / "bin" / f"python{m['minor']}"
        link.unlink()
        link.symlink_to(gone)
        cfg = kit / "pyvenv.cfg"
        cfg.write_text(vr._with_home(cfg.read_bytes(), os.path.dirname(gone)).decode())
        self.assertNotEqual(self._imports_its_package(kit), f"42 {kit}")
        self.assertEqual(vr.inspect(kit).status, vr.DEAD)
        done = vr.apply(vr.inspect(kit))
        self.assertEqual(done.status, vr.REPAIRED, done.detail)
        self.assertEqual(self._imports_its_package(kit), f"42 {kit}")


class WalkTests(TempCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.bot = self.home / "MyOldMachine"
        self.brew = FakeBrew(self.tmp)
        self.scanned = []
        real_scandir = os.scandir

        def scandir(path="."):
            self.scanned.append(os.path.normpath(str(path)))
            return real_scandir(path)

        for p in (patch.object(vr.os, "scandir", scandir),
                  patch.object(vr, "BOT_DIR", self.bot),
                  patch.object(vr.Path, "home", classmethod(lambda cls: self.home))):
            p.start()
            self.addCleanup(p.stop)

    def kit(self, rel: str) -> Path:
        return make_kit(self.home / rel, self.brew.stable_python, self.brew.stable_home)

    def found(self) -> list[str]:
        return [str(k.path.relative_to(self.home)) for k in vr.run()]

    def test_finds_kits_in_the_home_folder_and_the_bot_folder(self):
        self.kit(".venvs/post")
        self.kit("venvs/rembg")
        self.kit(".testenv")
        self.kit("MyOldMachine/data/stt/venv")
        self.kit("MyOldMachine/data/memory/projects/p/phase2/.tools_venv")
        self.assertEqual(self.found(), [
            ".testenv", ".venvs/post",
            "MyOldMachine/data/memory/projects/p/phase2/.tools_venv",
            "MyOldMachine/data/stt/venv", "venvs/rembg"])

    def test_never_reads_a_folder_macos_guards(self):
        for name in vr.HOME_SKIP:
            self.kit(f"{name}/project/venv")
        self.kit(".venvs/post")
        self.assertEqual(self.found(), [".venvs/post"])
        for name in vr.HOME_SKIP:
            guarded = str(self.home / name)
            self.assertFalse([p for p in self.scanned if p == guarded or p.startswith(guarded + os.sep)],
                             f"{name} was listed")

    def test_the_guarded_folders_stay_on_the_list(self):
        # 2 Oct 2026: a sweep from a bot session put up a box for Documents and
        # one for Downloads, and the answer to each is now a stored refusal.
        for name in ("Desktop", "Documents", "Downloads", "Library", "Movies",
                     "Music", "Pictures", ".Trash"):
            self.assertIn(name, vr.HOME_SKIP)

    def test_never_follows_a_symbolic_link(self):
        outside = self.tmp / "drive" / "PROJECTS"
        make_kit(outside / "x" / "venv", self.brew.stable_python)
        (self.home).mkdir(parents=True, exist_ok=True)
        (self.home / "projects").symlink_to(outside)
        self.kit(".venvs/post")
        self.assertEqual(self.found(), [".venvs/post"])
        self.assertFalse([p for p in self.scanned
                          if p.startswith(str(self.tmp / "drive")) or p == str(self.home / "projects")])

    def test_a_home_folder_reached_through_a_link_still_skips_what_it_must(self):
        # The bot's folder is a resolved path, so an unresolved home would walk
        # it a second time under the link's name, where data/users no longer
        # matches the skip list.
        self.kit("Documents/x/venv")
        self.kit("MyOldMachine/data/users/111/venv")
        self.kit(".venvs/post")
        link = self.tmp / "homelink"
        link.symlink_to(self.home)
        with patch.object(vr.Path, "home", classmethod(lambda cls: link)):
            kits = vr.run()
        self.assertEqual([k.path for k in kits], [self.home / ".venvs" / "post"])
        self.assertFalse([p for p in self.scanned if p.startswith(str(link))
                          or "/Documents" in p or "/data/users" in p])

    def test_other_users_and_the_bots_own_venv_are_never_read(self):
        self.kit("MyOldMachine/data/users/111/work/venv")
        self.kit("MyOldMachine/.venv")
        self.kit("MyOldMachine/data/stt/venv")
        self.assertEqual(self.found(), ["MyOldMachine/data/stt/venv"])
        for private in ("data/users", ".venv"):
            self.assertNotIn(str(self.bot / private), self.scanned)

    def test_the_bots_own_venv_is_never_changed_even_from_an_explicit_root(self):
        # Reached by a path the skip list cannot match: a link to the bot's
        # folder, and the .venv itself given as the folder to look in.
        pinned = make_kit(self.bot / ".venv", self.brew.python("3.12.15"))
        other = make_kit(self.bot / "data" / "stt" / "venv", self.brew.python("3.12.15"))
        link = self.tmp / "botlink"
        link.symlink_to(self.bot)
        for roots in ([link], [pinned]):
            kits = vr.run(repair=True, roots=roots, skip=[])
            self.assertFalse([k for k in kits if os.path.realpath(k.path) == str(pinned)], roots)
        self.assertEqual(os.readlink(pinned / "bin" / "python3.12"), self.brew.python("3.12.15"))
        self.assertEqual(os.readlink(other / "bin" / "python3.12"), self.brew.stable_python)

    def test_depth_limit_and_folders_never_entered(self):
        self.kit("a/b/c/d/e/venv")             # depth 6: found
        self.kit("a/b/c/d/e/f/venv")           # depth 7: not
        self.kit("proj/node_modules/x/venv")
        self.kit("proj/.git/venv")
        self.kit("lib/python3.12/site-packages/thing/venv")
        self.assertEqual(self.found(), ["a/b/c/d/e/venv"])

    def test_does_not_look_inside_a_kit(self):
        outer = self.kit("outer")
        make_kit(outer / "lib" / "nested", self.brew.stable_python)
        self.assertEqual(self.found(), ["outer"])

    def test_bot_folder_is_walked_once(self):
        self.kit("MyOldMachine/data/stt/venv")
        self.assertEqual(self.found(), ["MyOldMachine/data/stt/venv"])
        self.assertEqual(self.scanned.count(str(self.bot)), 1)

    def test_one_odd_kit_does_not_stop_the_rest(self):
        self.kit(".venvs/a")
        self.kit(".venvs/b")
        real = vr.inspect

        def inspect(venv):
            if venv.name == "a":
                raise RuntimeError("odd")
            return real(venv)

        with patch.object(vr, "inspect", inspect):
            kits = vr.run()
        self.assertEqual([(k.path.name, k.status) for k in kits],
                         [("a", vr.BROKEN), ("b", vr.OK)])
        self.assertIn("odd", kits[0].detail)


class RecordTests(TempCase):
    def setUp(self):
        super().setUp()
        self.file = self.tmp / "data" / "venv_repair.json"
        p = patch.object(vr, "RECORD_FILE", self.file)
        p.start()
        self.addCleanup(p.stop)
        self.night = datetime(2026, 10, 3, 4, 0, 21)

    def kits(self, **statuses):
        return [vr.Kit(Path(f"/h/{name}"), status, f"why {name}")
                for name, status in statuses.items()]

    def state(self):
        return json.loads(self.file.read_text())

    def test_records_what_changed_and_what_is_broken(self):
        vr.record(self.kits(a=vr.REPAIRED, b=vr.SECURED, c=vr.BROKEN, d=vr.OK, e=vr.KEPT),
                  now=self.night)
        state = self.state()
        self.assertEqual(state["checked_at"], "2026-10-03T04:00:21")
        self.assertEqual(state["error"], "")
        self.assertEqual(state["kits"], 5)
        self.assertEqual(state["broken"], ["/h/c"])
        self.assertEqual([(e["event"], e["path"]) for e in state["events"]],
                         [("repaired", "/h/a"), ("secured", "/h/b"), ("broken", "/h/c")])

    def test_a_kit_already_broken_is_not_news_again(self):
        vr.record(self.kits(c=vr.BROKEN), now=self.night)
        vr.record(self.kits(c=vr.BROKEN), now=self.night + timedelta(days=1))
        self.assertEqual(len(self.state()["events"]), 1)
        # Fixed by hand, then broken again: that is news.
        vr.record(self.kits(c=vr.OK), now=self.night + timedelta(days=2))
        vr.record(self.kits(c=vr.BROKEN), now=self.night + timedelta(days=3))
        self.assertEqual(len(self.state()["events"]), 2)

    def test_old_and_malformed_events_are_dropped(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"events": [
            {"at": "2026-09-01T04:00:00", "event": "repaired", "path": "/old"},
            {"at": "not a date", "event": "repaired", "path": "/bad"},
            "junk",
            {"at": "2026-10-02T12:00:00", "event": "secured", "path": "/noon"},
        ]}))
        vr.record([], now=self.night)
        self.assertEqual([e["path"] for e in self.state()["events"]], ["/noon"])

    def test_a_failure_keeps_what_was_recorded(self):
        vr.record(self.kits(a=vr.REPAIRED, c=vr.BROKEN), now=self.night)
        vr.record_failure("it ran longer than 600 s", now=self.night + timedelta(minutes=5))
        state = self.state()
        self.assertEqual(state["error"], "it ran longer than 600 s")
        self.assertEqual(state["checked_at"], "2026-10-03T04:05:21")
        self.assertEqual(len(state["events"]), 2)
        self.assertEqual(state["broken"], ["/h/c"])
        # The next sweep that finishes clears it.
        vr.record([], now=self.night + timedelta(days=1))
        self.assertEqual(self.state()["error"], "")

    def test_failure_record_never_raises(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("x")
        vr.record_failure("boom", path=blocker / "sub" / "venv_repair.json")


class ReportSectionTests(TempCase):
    def setUp(self):
        super().setUp()
        self.file = self.tmp / "venv_repair.json"
        self.home = self.tmp / "home"
        for p in (patch.object(vr, "RECORD_FILE", self.file),
                  patch.object(vr.Path, "home", classmethod(lambda cls: self.home))):
            p.start()
            self.addCleanup(p.stop)
        self.report_at = datetime(2026, 10, 3, 4, 45)

    def write(self, **state):
        self.file.write_text(json.dumps(state))

    def event(self, event, rel, at="2026-10-03T04:00:21", detail=""):
        return {"at": at, "event": event, "path": str(self.home / rel), "detail": detail}

    def test_silent_without_a_record_or_without_news(self):
        self.assertEqual(vr.report_section(now=self.report_at), [])
        self.write(checked_at="2026-10-03T04:00:21", error="", events=[])
        self.assertEqual(vr.report_section(now=self.report_at), [])
        self.file.write_text("[1, 2]")
        self.assertEqual(vr.report_section(now=self.report_at), [])

    def test_names_what_the_night_changed(self):
        self.write(checked_at="2026-10-03T04:00:21", error="", events=[
            self.event("repaired", ".venvs/post"),
            self.event("repaired", ".venvs/gdown"),
            self.event("secured", "venvs/rembg", at="2026-10-02T12:10:00"),
            self.event("broken", "old/venv", detail="Python 3.11 is no longer installed"),
        ])
        self.assertEqual(vr.report_section(now=self.report_at), [
            "Python tool kits",
            "  Repaired after a Python update: ~/.venvs/post, ~/.venvs/gdown",
            "  Protected from the next Python update: ~/venvs/rembg",
            "  Broken, not repaired: ~/old/venv (Python 3.11 is no longer installed)",
        ])

    def test_yesterdays_news_is_not_repeated(self):
        self.write(checked_at="2026-10-03T04:00:21", error="", events=[
            self.event("repaired", ".venvs/post", at="2026-10-02T04:00:20")])
        self.assertEqual(vr.report_section(now=self.report_at), [])

    def test_a_check_that_did_not_finish_says_so(self):
        self.write(checked_at="2026-10-03T04:10:21", error="it ran longer than 600 s", events=[])
        self.assertEqual(vr.report_section(now=self.report_at), [
            "Python tool kits", "  The check did not finish: it ran longer than 600 s"])
        self.write(checked_at="2026-10-01T04:10:21", error="old", events=[])
        self.assertEqual(vr.report_section(now=self.report_at), [])


class NightlyCommandTests(TempCase):
    def setUp(self):
        super().setUp()
        self.file = self.tmp / "venv_repair.json"
        for p in (patch.object(vr, "RECORD_FILE", self.file),
                  patch("sys.stdout", new_callable=io.StringIO)):
            p.start()
            self.addCleanup(p.stop)

    def test_obeys_the_switch(self):
        with patch("utils.maintenance.load_config", return_value={"venv_repair": False}), \
             patch.object(vr, "run") as run:
            self.assertEqual(vr.main(["--nightly"]), 0)
        run.assert_not_called()
        self.assertFalse(self.file.exists())

    def test_repairs_and_records(self):
        kits = [vr.Kit(Path("/h/a"), vr.REPAIRED)]
        with patch("utils.maintenance.load_config", return_value={"venv_repair": True}), \
             patch.object(vr, "run", return_value=kits) as run:
            self.assertEqual(vr.main(["--nightly"]), 0)
        run.assert_called_once_with(repair=True)
        self.assertEqual(json.loads(self.file.read_text())["events"][0]["path"], "/h/a")

    def test_a_listing_changes_and_records_nothing(self):
        with patch.object(vr, "run", return_value=[]) as run:
            vr.main([])
        run.assert_called_once_with(repair=False, roots=None, skip=None)
        self.assertFalse(self.file.exists())

    def test_a_repair_in_one_folder_does_not_replace_the_broken_list(self):
        with patch.object(vr, "run", return_value=[]):
            vr.main(["--repair", "--root", str(self.tmp)])
        self.assertFalse(self.file.exists())

    def test_on_by_default(self):
        from utils.maintenance import DEFAULT_CONFIG
        self.assertIs(DEFAULT_CONFIG["venv_repair"], True)


class SystemUpdateWiringTests(TempCase):
    """utils/system_update.py runs the repair in a new process and survives the
    Python it runs on being deleted underneath it."""

    def setUp(self):
        super().setUp()
        from utils import system_update as su
        self.su = su
        self.file = self.tmp / "venv_repair.json"
        self.logged = []
        for p in (patch.object(vr, "RECORD_FILE", self.file),
                  patch.object(su, "LOG_DIR", self.tmp / "logs"),
                  patch.object(su, "PENDING_APPS_STATE", self.tmp / "pending.json")):
            p.start()
            self.addCleanup(p.stop)

    def _run(self, **completed):
        with patch.object(self.su.subprocess, "run") as run:
            if "side_effect" in completed:
                run.side_effect = completed["side_effect"]
            else:
                run.return_value = subprocess.CompletedProcess(
                    [], completed.get("rc", 0), completed.get("out", ""), completed.get("err", ""))
            self.su._run_venv_repair(self.logged.append)
        return run

    def test_runs_the_script_in_a_new_process(self):
        run = self._run(out="Python tool kits: 13 checked, 2 repaired, 11 ok\n")
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[1]).resolve(), ROOT / "utils" / "venv_repair.py")
        self.assertEqual(argv[2:], ["--nightly"])
        self.assertEqual(self.logged, ["Python tool kits: 13 checked, 2 repaired, 11 ok"])
        self.assertFalse(self.file.exists())

    def test_a_crash_is_recorded_for_the_report(self):
        self._run(rc=1, err="Traceback (most recent call last):\nKeyError: 'x'\n")
        self.assertEqual(json.loads(self.file.read_text())["error"], "KeyError: 'x'")
        self.assertIn("KeyError: 'x'", self.logged[-1])

    def test_a_hang_or_a_missing_python_is_recorded(self):
        self._run(side_effect=subprocess.TimeoutExpired("x", 600))
        self.assertIn("longer than", json.loads(self.file.read_text())["error"])
        self._run(side_effect=FileNotFoundError(2, "No such file or directory"))
        self.assertIn("could not start", json.loads(self.file.read_text())["error"])

    def test_works_with_no_module_left_to_import(self):
        # The night it matters, Homebrew has just deleted the standard library
        # the update was started from. What the update had imported by then
        # still works and anything else fails, as 'tarfile' did on 14 Aug and
        # 2 Oct. So allow exactly what a fresh run of the update has loaded.
        loaded = set(subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import utils.system_update; "
             "print('\\n'.join(sys.modules))", str(ROOT)],
            capture_output=True, text=True, check=True).stdout.split())
        self.assertIn("utils.venv_repair", loaded)
        real_import = builtins.__import__

        def gone(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name not in loaded:
                raise ModuleNotFoundError(f"No module named {name!r}")
            return real_import(name, globals, locals, fromlist, level)

        with patch.object(self.su.subprocess, "run") as run:
            for completed in (subprocess.CompletedProcess([], 0, "Python tool kits: 1 checked\n", ""),
                              subprocess.CompletedProcess([], 1, "", "boom\n")):
                run.return_value = completed
                with patch.object(builtins, "__import__", gone):
                    self.su._run_venv_repair(self.logged.append)
        self.assertEqual(self.logged[0], "Python tool kits: 1 checked")
        self.assertEqual(json.loads(self.file.read_text())["error"], "boom")

    def test_runs_after_the_upgrade_even_a_failed_one(self):
        su = self.su
        order = []
        for errored in (False, True):
            order.clear()
            with patch.object(su, "_run_pkg_manager_update",
                              side_effect=lambda **kw: order.append("pkg") or su.PkgUpdateResult("x", errored)), \
                 patch.object(su, "_run_venv_repair", side_effect=lambda **kw: order.append("kits")), \
                 patch.object(su, "_maybe_run_macos_softwareupdate",
                              side_effect=lambda **kw: order.append("apple") or ""), \
                 patch.object(su, "_maybe_run_app_update_check",
                              side_effect=lambda **kw: order.append("apps") or su.AppCheckResult()):
                su.run_system_update()
            self.assertEqual(order, ["pkg", "kits"] if errored else ["pkg", "kits", "apple", "apps"])


class NightlyReportWiringTests(unittest.TestCase):
    def _report(self, kits_patch):
        from utils import nightly_report
        with patch.object(nightly_report, "_backup_section", return_value=["Backup", "  stub"]), \
             patch.object(nightly_report, "_jobs_section", return_value=["Maintenance jobs", "  stub"]), \
             patch.object(nightly_report, "_miniapp_section", return_value=[]), \
             patch.object(nightly_report, "_permissions_section", return_value=[]), \
             patch.object(nightly_report, "_system_section", return_value=["System", "  stub"]), \
             kits_patch:
            return nightly_report.build_report()

    def test_section_included_when_there_is_news(self):
        report = self._report(patch.object(
            vr, "report_section",
            return_value=["Python tool kits", "  Repaired after a Python update: ~/.venvs/post"]))
        self.assertIn("Python tool kits\n  Repaired after a Python update: ~/.venvs/post\n\nSystem",
                      report)

    def test_section_omitted_on_a_quiet_night(self):
        self.assertNotIn("Python tool kits", self._report(patch.object(vr, "report_section", return_value=[])))

    def test_a_broken_record_cannot_stop_the_report(self):
        report = self._report(patch.object(vr, "report_section", side_effect=ValueError("bad")))
        self.assertIn("Could not read the 04:00 check: bad", report)
        self.assertIn("System", report)


if __name__ == "__main__":
    unittest.main()
