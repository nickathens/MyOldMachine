"""The program launchd starts in front of the bot's Python on macOS.

macOS gives its privacy grants (files on a removable volume, Accessibility)
to the program *responsible* for a request. For a launchd job that is the
first program the job runs that is not part of macOS, and every process the
job starts inherits it. Read on this project's Mac on 2 Oct 2026 with
`sudo launchctl procinfo <bot pid>`, while the job ran
`/bin/bash -c "... exec .venv/bin/python bot.py"`:

    responsible path = /opt/homebrew/Cellar/python@3.12/3.12.15/Frameworks/
                       Python.framework/Versions/3.12/bin/python3.12

That is Homebrew's Python, and it is ad hoc signed: the signature is a hash of
the binary, so every Python update is a new program to macOS. The 04:00
nightly update moved 3.12.14 to 3.12.15, the grants stopped applying, and the
bot's first look at the storage drive after the 05:00 restart raised a consent
box. Until a person answered it at the screen, macOS held every read of that
drive behind it, and the update also took away the bot's screen control.

The fix is a program at the top of the job that does not change:
`macos_starter.c`, built into `~/Library/Application Support/MyOldMachine/`.
It starts the bot as its child and stays, so the grants are given to it once
and survive every Python update.

**It is built only when it is missing.** Its signature is a hash of its bytes
as well, so rebuilding it, even from the same source with a newer compiler,
would make a new program and void the grants exactly as a Python update does.
An existing copy is kept whatever the source in the repo says now.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = Path(__file__).with_name("macos_starter.c")

# The name macOS shows on the consent box and in System Settings, so it is the
# project's name rather than something a person has to decode.
NAME = "MyOldMachine"
SIGNING_IDENTIFIER = "com.myoldmachine.starter"


class StarterError(Exception):
    """A build that did not produce a starter that works."""


def starter_path(home: Path | None = None) -> Path:
    """Where the starter lives. Outside the repo, so a fresh clone keeps it."""
    base = home if home is not None else Path.home()
    return base / "Library" / "Application Support" / "MyOldMachine" / NAME


def usable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_compiler() -> str | None:
    """`cc`, or None when there is none.

    On macOS `/usr/bin/cc` is always present, but without the Command Line
    Tools it does not fail: it opens a dialog offering to install them, which
    on an unattended install nobody answers. `xcode-select -p` tells the two
    apart without opening anything.
    """
    if platform.system() == "Darwin":
        try:
            probe = subprocess.run(["xcode-select", "-p"], capture_output=True,
                                   timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if probe.returncode != 0:
            return None
    return shutil.which("cc")


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StarterError(f"{Path(cmd[0]).name}: {exc}") from exc


def build(dest: Path, cc: str, source: Path | None = None) -> None:
    """Compile, sign, prove and move into place, or raise StarterError.

    The copy is proved before it is moved in: it has to start a command and
    hand back that command's exit status. Nothing is left at `dest` unless it
    passed.
    """
    source = source or SOURCE
    dest.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".build-", dir=dest.parent))
    try:
        built = scratch / dest.name
        made = _run([cc, "-O2", "-Wall", "-o", str(built), str(source)])
        if made.returncode != 0:
            raise StarterError(f"cc failed: {(made.stderr or made.stdout).strip()[:300]}")
        if platform.system() == "Darwin":
            signed = _run(["codesign", "--force", "--sign", "-",
                           "--identifier", SIGNING_IDENTIFIER, str(built)])
            if signed.returncode != 0:
                raise StarterError(f"codesign failed: {signed.stderr.strip()[:300]}")
            checked = _run(["codesign", "--verify", "--strict", str(built)])
            if checked.returncode != 0:
                raise StarterError(f"its signature does not verify: {checked.stderr.strip()[:300]}")
        proof = _run([str(built), "/bin/sh", "-c", "exit 7"], timeout=30)
        if proof.returncode != 7:
            raise StarterError(
                f"it ran a command that exits 7 and returned {proof.returncode}")
        built.chmod(0o755)
        os.replace(built, dest)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def ensure_starter(home: Path | None = None) -> tuple[Path | None, str]:
    """The starter, built if it is missing, and a line saying what happened.

    Returns (None, reason) when there is no starter to use. The LaunchAgent
    then starts the bot without one, as before, so nothing here can stop the
    bot from starting.
    """
    path = starter_path(home)
    if usable(path):
        return path, (f"Starter kept as it is, so the permissions macOS gave it "
                      f"still apply: {path}")
    if path.exists() or path.is_symlink():
        return None, (f"{path} exists but is not an executable file. It was "
                      f"left alone and the bot starts without its starter.")
    cc = find_compiler()
    if cc is None:
        return None, ("No C compiler, so the bot starts without its starter "
                      "and its macOS permissions stay tied to Python, which an "
                      "update can reset. Install the Command Line Tools "
                      "(xcode-select --install) and run this again.")
    try:
        build(path, cc)
    except (StarterError, OSError) as exc:
        return None, f"Could not build the starter ({exc}). The bot starts without it."
    return path, f"Built the starter the bot runs through: {path}"

