#!/usr/bin/env python3
"""Keep Python tool kits working through Homebrew Python updates.

A virtual environment does not carry its own Python. Its `bin/python3.12` is a
link to the Python it was built from, and `home` in its pyvenv.cfg names that
Python's folder. Homebrew installs every Python release in a folder named after
the exact version (`Cellar/python@3.12/3.12.14`) and deletes that folder when
the next release lands. A kit linked into the versioned folder dies with it:
every command in it fails with "No such file or directory", exit 127. A kit
linked through Homebrew's stable path (`opt/python@3.12`, which Homebrew moves
to each new release) keeps working.

Nobody chooses the versioned link. A venv built from another venv takes the
parent's resolved interpreter, and inside a bot session `python3` is the bot's
own .venv, so `python3 -m venv ~/.venvs/post` produces exactly that link. The
nightly update's python@3.12 upgrades killed six kits that way on 14 Aug 2026,
two of which nobody noticed for seven weeks, and two more on 2 Oct 2026.

This finds those kits and moves them to the stable path: the same Python today,
and each release after it. A dead kit is repaired the same way. That is safe
inside one minor version, because a patch release keeps the ABI and the
packages installed in the kit keep loading, so nothing is reinstalled. Every
change is proved by starting the kit, and put back exactly as it was when the
kit does not start.

What it never does:
  - read Desktop, Documents, Downloads, Library or the other folders macOS
    guards. At 4 AM nobody is there to answer the box that would raise, and a
    box left open holds every later read of that folder (2 Oct 2026, a sweep
    from a bot session raised two);
  - follow a symbolic link, or even look at what one points to, so nothing
    outside the home folder and the bot's own folder is read, external drives
    included;
  - look inside another user's private folder (data/users);
  - change the bot's own .venv, which is what runs this;
  - move a kit to another minor version (3.12 to 3.13), where its compiled
    packages would stop loading.

Usage:
    python utils/venv_repair.py                # what would change; changes nothing
    python utils/venv_repair.py --repair       # make the changes
    python utils/venv_repair.py --root DIR     # look only in DIR (repeatable)
    python utils/venv_repair.py --nightly      # what the 4 AM system update runs

The system update runs it as a new process on purpose. The upgrade it has just
made can delete the Python that the update itself is running on, and every
module not yet imported goes with it; that is how the 14 Aug and 2 Oct runs
lost their Apple and app checks to "No module named 'tarfile'". A new process
starts on whichever Python is installed now.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from utils.safe_json import load_json, save_json  # noqa: E402

RECORD_FILE = BOT_DIR / "data" / "venv_repair.json"

# The deepest kit on the reference Mac sits six folders under the bot's folder
# (data/memory/projects/<project>/<phase>/.tools_venv).
MAX_DEPTH = 6

# Directly under the home folder. macOS guards the first eight, and the rest
# hold applications, caches and npm's store, none of which is where a kit is
# built.
HOME_SKIP = (
    "Desktop", "Documents", "Downloads", "Library", "Movies", "Music",
    "Pictures", ".Trash", "Applications", ".cache", ".npm",
)
# At any depth: large, and never the home of a kit.
NEVER_ENTER = frozenset({".git", "node_modules", "__pycache__", "site-packages"})

# How long one start test may take before the kit counts as not starting.
START_TIMEOUT = 60
# The report covers a day, and the record keeps a week for anyone asking later.
REPORT_WINDOW = timedelta(hours=24)
KEEP_EVENTS = timedelta(days=7)

OK = "ok"              # through the stable path, or not a Homebrew Python at all
PINNED = "pinned"      # works now, tied to one release, dies with the next
DEAD = "dead"          # its release is gone, and the stable path can bring it back
REPAIRED = "repaired"  # was dead, starts again
SECURED = "secured"    # was pinned, now on the stable path, still starts
KEPT = "kept"          # tied to an older release that is still installed; left alone
BROKEN = "broken"      # does not start, and this cannot fix it

# <prefix>/Cellar/python@3.12/3.12.14/<rest>. Homebrew's stable path to the
# same file is <prefix>/opt/python@3.12/<rest>.
_KEG = re.compile(
    r"(?P<prefix>/.+?)/Cellar/(?P<formula>python@(?P<minor>\d+\.\d+))"
    r"/(?P<version>[^/]+)(?P<rest>/.*)?"
)
_INTERPRETER = re.compile(r"python(\d+(\.\d+)?)?")
_PROBE = "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.prefix)"


@dataclass
class Kit:
    path: Path
    status: str
    detail: str = ""
    minor: str = ""
    # (where, old, new): where is a link in bin/, or "home" for pyvenv.cfg.
    changes: list = field(default_factory=list)


def stable_path(path: str) -> str | None:
    """The same file through Homebrew's stable path, or None.

    None unless `path` lies inside a versioned python@X.Y folder whose version
    belongs to X.Y, which is what makes the stable path the same Python one
    patch release on, never another minor version.
    """
    m = _KEG.fullmatch(path or "")
    if not m or not m["version"].startswith(m["minor"] + "."):
        return None
    return f"{m['prefix']}/opt/{m['formula']}{m['rest'] or ''}"


def _minor(path: str) -> str:
    m = _KEG.fullmatch(path or "")
    return m["minor"] if m else ""


def _home(cfg_text: str) -> str:
    """`home` from pyvenv.cfg, read the way Python's own startup reads it."""
    for line in cfg_text.splitlines():
        key, eq, value = line.partition("=")
        if eq and key.strip().lower() == "home":
            return value.strip()
    return ""


def _interpreter_links(venv: Path) -> list[tuple[Path, str]]:
    """bin/python, python3, python3.12 ... that link to an absolute path.

    The relative ones (python -> python3.12) follow whichever link they name.
    """
    try:
        entries = sorted(os.scandir(venv / "bin"), key=lambda e: e.name)
    except OSError:
        return []
    out = []
    for entry in entries:
        if not _INTERPRETER.fullmatch(entry.name) or not entry.is_symlink():
            continue
        try:
            target = os.readlink(entry.path)
        except OSError:
            continue
        if os.path.isabs(target):
            out.append((Path(entry.path), target))
    return out


def _walk(root: str, skip: frozenset, max_depth: int):
    """Yield every folder under root that holds a pyvenv.cfg.

    Not os.walk: that classifies a symbolic link by looking at its target,
    which for a link into a guarded folder or onto an external drive is
    already a read there. A DirEntry asked with follow_symlinks=False answers
    from the folder listing alone.
    """
    stack = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            continue
        if any(e.name == "pyvenv.cfg" and not e.is_dir(follow_symlinks=False)
               for e in entries):
            yield path
            continue
        if depth >= max_depth:
            continue
        for e in entries:
            if e.name in NEVER_ENTER or not e.is_dir(follow_symlinks=False):
                continue
            child = os.path.join(path, e.name)
            if child not in skip:
                stack.append((child, depth + 1))


def find_venvs(roots, skip=(), max_depth: int = MAX_DEPTH) -> list[Path]:
    skip = frozenset(os.path.normpath(str(p)) for p in skip)
    found = set()
    for root in roots:
        root = os.path.normpath(str(root))
        if os.path.isdir(root):
            found.update(_walk(root, skip, max_depth))
    return sorted(Path(p) for p in found)


def default_scan() -> tuple[list[Path], list[Path]]:
    """The bot's own folder and the home folder, minus what is never read.

    Both resolved, so a home folder reached through a link still matches the
    paths in the skip list.
    """
    home = Path.home().resolve()
    skip = [home / name for name in HOME_SKIP]
    # Walked as a root of its own, with its own depth.
    skip.append(BOT_DIR)
    return [BOT_DIR, home], skip


def _protected() -> list[Path]:
    return [BOT_DIR / ".venv", BOT_DIR / "data" / "users"]


def inspect(venv: Path) -> Kit:
    """Classify one kit. Changes nothing."""
    try:
        cfg = (venv / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return Kit(venv, BROKEN, f"its pyvenv.cfg cannot be read ({e.strerror or e})")
    links = _interpreter_links(venv)
    home = _home(cfg)

    changes = [(link, old, stable_path(old)) for link, old in links if stable_path(old)]
    if home and stable_path(home):
        changes.append(("home", home, stable_path(home)))
    minor = _minor(changes[0][1]) if changes else ""

    alive = (all(os.path.exists(link) for link, _ in links)
             and (not home or os.path.isdir(home)))

    if alive:
        if not changes:
            return Kit(venv, OK)
        if all(os.path.realpath(old) == os.path.realpath(new) for _, old, new in changes):
            return Kit(venv, PINNED, f"tied to Python {_release(changes)}", minor, changes)
        return Kit(venv, KEPT,
                   f"tied to Python {_release(changes)}, which is still installed "
                   "beside the current release", minor, changes)

    # Dead. Would every part exist once the planned changes are made?
    planned = {str(where): new for where, _, new in changes}
    for link, old in links:
        if not os.path.exists(planned.get(str(link), old)):
            return Kit(venv, BROKEN, _gone(planned.get(str(link), old), minor))
    if home and not os.path.isdir(planned.get("home", home)):
        return Kit(venv, BROKEN, _gone(planned.get("home", home), minor))
    return Kit(venv, DEAD, f"its Python {_release(changes)} is gone", minor, changes)


def _release(changes) -> str:
    m = _KEG.fullmatch(changes[0][1])
    return m["version"] if m else "?"


def _gone(path: str, minor: str) -> str:
    if minor and f"/opt/python@{minor}" in path:
        return f"Python {minor} is no longer installed"
    return f"its Python is gone ({path})"


def _fresh(tmp: Path) -> Path:
    """tmp, with whatever an interrupted run left there removed, never written through."""
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    return tmp


def _relink(link: Path, target: str) -> None:
    """Point link at target in one step: a reader sees the old link or the new."""
    tmp = _fresh(link.with_name(f".{link.name}.venv-repair"))
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _write_cfg(cfg_path: Path, data: bytes) -> None:
    tmp = _fresh(cfg_path.with_name(".pyvenv.cfg.venv-repair"))
    tmp.write_bytes(data)
    os.chmod(tmp, os.stat(cfg_path).st_mode & 0o7777)
    os.replace(tmp, cfg_path)


def _with_home(cfg: bytes, new_home: str) -> bytes:
    """pyvenv.cfg with every `home` line set to new_home, the rest byte for byte."""
    out = []
    for line in cfg.decode("utf-8").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        key, eq, _ = body.partition("=")
        if eq and key.strip().lower() == "home":
            line = f"{key.rstrip()} = {new_home}{line[len(body):]}"
        out.append(line)
    return "".join(out).encode("utf-8")


def _starts(kit: Kit) -> tuple[bool, str]:
    """Start the kit's own Python and check it is the kit, on the same minor version.

    Started the way any use of the kit starts it, minus the caller's
    environment (-I). Not -S: Python only takes pyvenv.cfg into account in
    site, so without it sys.prefix is the base Python's and the test could not
    tell the kit from the Python it links to.
    """
    links = [where for where, _, _ in kit.changes if where != "home"]
    exe = links[0] if links else kit.path / "bin" / "python3"
    try:
        p = subprocess.run([str(exe), "-I", "-c", _PROBE],
                           capture_output=True, text=True, timeout=START_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    lines = p.stdout.split("\n")
    if p.returncode != 0:
        err = (p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1]
        return False, err
    if lines[0].strip() != kit.minor:
        return False, f"it started Python {lines[0].strip()}, not {kit.minor}"
    prefix = lines[1].strip() if len(lines) > 1 else ""
    if os.path.realpath(prefix) != os.path.realpath(kit.path):
        return False, f"it started outside the kit ({prefix})"
    return True, ""


def apply(kit: Kit) -> Kit:
    """Make the planned changes, prove the kit starts, or put it back as it was."""
    if kit.status not in (PINNED, DEAD):
        return kit
    failed = BROKEN if kit.status == DEAD else KEPT
    cfg_path = kit.path / "pyvenv.cfg"
    try:
        original_cfg = cfg_path.read_bytes()
    except OSError as e:
        return replace(kit, status=failed, detail=f"its pyvenv.cfg cannot be read ({e})")
    done = []
    try:
        for where, old, new in kit.changes:
            if where == "home":
                _write_cfg(cfg_path, _with_home(original_cfg, new))
            else:
                _relink(where, new)
            done.append((where, old))
    except (OSError, UnicodeDecodeError) as e:
        note = _undo(cfg_path, original_cfg, done)
        return replace(kit, status=failed, detail=f"could not be changed ({e}){note}")
    ok, why = _starts(kit)
    if ok:
        status = REPAIRED if kit.status == DEAD else SECURED
        return replace(kit, status=status, detail=f"was tied to Python {_release(kit.changes)}")
    note = _undo(cfg_path, original_cfg, done)
    return replace(kit, status=failed,
                   detail=f"does not start after the repair ({why}), so it was left as it was{note}")


def _undo(cfg_path: Path, original_cfg: bytes, done) -> str:
    try:
        for where, old in reversed(done):
            if where == "home":
                _write_cfg(cfg_path, original_cfg)
            else:
                _relink(where, old)
    except OSError as e:
        return f"; putting it back failed too ({e})"
    return ""


def run(repair: bool = False, roots=None, skip=None) -> list[Kit]:
    """Every kit under the roots, classified, and repaired when asked."""
    if roots is None:
        roots, skip = default_scan()
    protected = [str(p) for p in _protected()]
    never = {os.path.realpath(BOT_DIR / ".venv")}
    kits = []
    for venv in find_venvs(roots, [*(skip or []), *protected]):
        if os.path.realpath(venv) in never:
            continue
        try:
            kit = inspect(venv)
            if repair:
                kit = apply(kit)
        except Exception as e:  # one odd kit must not stop the rest
            kit = Kit(venv, BROKEN, f"could not be checked ({e})")
        kits.append(kit)
    return kits


def summary(kits: list[Kit]) -> str:
    counts = {}
    for kit in kits:
        counts[kit.status] = counts.get(kit.status, 0) + 1
    parts = [f"{n} {status}" for status, n in sorted(counts.items())]
    return f"Python tool kits: {len(kits)} checked" + (f", {', '.join(parts)}" if parts else "")


def _now() -> datetime:
    return datetime.now()


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def record(kits: list[Kit], path: Path | None = None, now: datetime | None = None) -> dict:
    """Add what this sweep changed or found broken to the record.

    A kit that was already broken at the previous sweep is not news again, so
    the report names each one once. Events build up for a week, so the report
    finds a repair made at noon as well as one made at 4 AM.
    """
    path = path or RECORD_FILE
    now = now or _now()
    previous = load_json(path, {})
    if not isinstance(previous, dict):
        previous = {}
    stamp = now.isoformat(timespec="seconds")
    events = [e for e in previous.get("events") or []
              if isinstance(e, dict) and (_when(e.get("at")) or datetime.min) > now - KEEP_EVENTS]
    was_broken = set(previous.get("broken") or [])
    broken = []
    for kit in kits:
        if kit.status == BROKEN:
            broken.append(str(kit.path))
            if str(kit.path) in was_broken:
                continue
        elif kit.status not in (REPAIRED, SECURED):
            continue
        events.append({"at": stamp, "event": kit.status, "path": str(kit.path),
                       "detail": kit.detail})
    state = {"checked_at": stamp, "error": "", "kits": len(kits),
             "broken": broken, "events": events}
    save_json(path, state)
    return state


def record_failure(reason: str, path: Path | None = None, now: datetime | None = None) -> None:
    """The sweep itself did not finish. Keeps every event already recorded.

    Called by the system update after the upgrade, inside the process whose
    Python may just have been deleted, so it may only use what this module
    imported when it was loaded. Never raises.
    """
    try:
        path = path or RECORD_FILE
        state = load_json(path, {})
        if not isinstance(state, dict):
            state = {}
        state["checked_at"] = (now or _now()).isoformat(timespec="seconds")
        state["error"] = reason or "unknown error"
        save_json(path, state)
    except Exception:
        pass


def _short(path: str) -> str:
    home = str(Path.home().resolve())
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def report_section(path: Path | None = None, now: datetime | None = None) -> list[str]:
    """Lines for the 04:45 nightly report. Empty on a night with nothing to say."""
    now = now or _now()
    state = load_json(path or RECORD_FILE, {})
    if not isinstance(state, dict):
        return []
    recent = [e for e in state.get("events") or []
              if isinstance(e, dict) and (_when(e.get("at")) or datetime.min) > now - REPORT_WINDOW]
    lines = []
    repaired = [_short(e.get("path", "")) for e in recent if e.get("event") == REPAIRED]
    if repaired:
        lines.append(f"Repaired after a Python update: {', '.join(repaired)}")
    secured = [_short(e.get("path", "")) for e in recent if e.get("event") == SECURED]
    if secured:
        lines.append(f"Protected from the next Python update: {', '.join(secured)}")
    for e in recent:
        if e.get("event") == BROKEN:
            lines.append(f"Broken, not repaired: {_short(e.get('path', ''))} ({e.get('detail', '')})")
    checked = _when(state.get("checked_at"))
    if state.get("error") and checked and checked > now - REPORT_WINDOW:
        lines.append(f"The check did not finish: {state['error']}")
    if not lines:
        return []
    return ["Python tool kits", *[f"  {line}" for line in lines]]


def _print_table(kits: list[Kit]) -> None:
    for kit in kits:
        detail = f"  {kit.detail}" if kit.detail else ""
        print(f"{kit.status:9} {_short(str(kit.path))}{detail}")
    print(summary(kits))


def _nightly() -> int:
    from utils.maintenance import load_config
    if not load_config().get("venv_repair", True):
        print("Python tool kits: repair is off in maintenance.json")
        return 0
    kits = run(repair=True)
    record(kits)
    print(summary(kits))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repair", action="store_true",
                        help="re-point and repair kits (default: only list them)")
    parser.add_argument("--root", action="append", default=[],
                        help="look only in this folder; repeatable")
    parser.add_argument("--nightly", action="store_true",
                        help="the 4 AM run: obeys maintenance.json, repairs, records")
    args = parser.parse_args(argv)
    if args.nightly:
        return _nightly()
    roots = [Path(r).expanduser().resolve() for r in args.root] or None
    kits = run(repair=args.repair, roots=roots, skip=[] if roots else None)
    # Only a full sweep may replace the record's list of broken kits.
    if args.repair and not roots:
        record(kits)
    _print_table(kits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
