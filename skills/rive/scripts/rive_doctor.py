#!/usr/bin/env python3
"""Is this machine ready for the rive skill, and does the CLI still behave?

Run it after installing, and after every Rive CLI update. The CLI is a
technical preview that renames flags between releases (--frame became
--advance), so a new version is not trusted until this passes:

  1  tools        rive, ffmpeg/ffprobe, python; the Rive editor app on macOS
  2  flags        every flag the skill's scripts pass must still be in --help
  3  build        a bundled sample builds, inspects clean, and renders pixels
  4  timing       a data dump confirms the 1/60 s stepping and gesture costs
                  the render timeline is built on
  5  web          (--web) the pinned web runtime loads and draws in headless
                  Chromium
  6  account      (--account) who this session is logged in as

  rive_doctor.py                 checks 1-4
  rive_doctor.py --web --account everything
  rive_doctor.py --install       install the CLI the supported way, then check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402

# Every flag the scripts in this folder pass to the CLI.
FLAGS_USED = ["--verify", "--once", "--publish", "--screenshot", "--advance", "--viewport", "--fit",
              "--data", "--data-dump", "--data-dump-every", "--pointer", "--key", "--gamepad",
              "--semantics", "--semantic-action", "--artboard", "--format=json", "--quiet", "--rev"]
SUBCOMMANDS_USED = ["create", "inspect", "schema", "docs", "samples", "doctor", "login", "whoami", "push", "pull"]
EDITOR_APP = Path("/Applications/Rive.app")
RELEASES = "https://releases.rive.app/cli"


def check(results: list, name: str, ok: bool, detail: str, fatal: bool = True) -> bool:
    results.append({"check": name, "ok": ok, "detail": detail, "fatal": fatal})
    return ok


def tools(results: list) -> None:
    binary = L.find_rive()
    version = L.cli_version(binary) if binary else None
    check(results, "rive cli", bool(binary), f"{binary} ({version})" if binary else
          "not installed: rive_doctor.py --install")
    if binary:
        note = L.version_note(version)
        check(results, "tested version", note is None, note or f"{version} is a measured version", fatal=False)
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        detail = path or f"missing ({'brew' if platform.system() == 'Darwin' else 'apt'} install ffmpeg)"
        if path:
            first = subprocess.run([path, "-version"], capture_output=True, text=True).stdout.splitlines()[:1]
            detail = f"{path} {first[0] if first else ''}"
        check(results, tool, bool(path), detail)
    if platform.system() == "Darwin":
        plist = EDITOR_APP / "Contents" / "Info.plist"
        if plist.is_file():
            info = plistlib.loads(plist.read_bytes())
            check(results, "rive editor app", True,
                  f"{info.get('CFBundleShortVersionString')} (build {info.get('CFBundleVersion')}); needs a "
                  "Rive account signed in at the screen before it does anything", fatal=False)
        else:
            check(results, "rive editor app", False, "not installed (brew install --cask rive); optional",
                  fatal=False)


def defines(help_text: str, flag: str) -> bool:
    """Does --help define this flag on a line of its own?

    A plain substring test passes a flag that is gone: --data lives on inside
    --data-dump, and most flags are named again in other flags' descriptions.
    """
    name = re.escape(flag.split("=")[0])
    return re.search(rf"(?m)^[ \t]+{name}(?=[=\[ \t]|$)", help_text) is not None


def flags(results: list) -> None:
    helped = L.run_rive(["--help"], timeout=30)
    help_text = helped.stdout + helped.stderr
    missing = [f for f in FLAGS_USED if not defines(help_text, f)]
    check(results, "flags", not missing,
          "all present" if not missing else f"no longer in --help: {', '.join(missing)} -- read the release "
          "notes and fix the scripts before rendering")
    missing_cmds = [c for c in SUBCOMMANDS_USED if not re.search(rf"(?m)^  {re.escape(c)}(?=\s|$)", help_text)]
    check(results, "subcommands", not missing_cmds,
          "all present" if not missing_cmds else f"missing: {', '.join(missing_cmds)}")


def build_and_render(results: list, keep: Path | None) -> None:
    samples = L.run_rive(["samples", "--path"], timeout=30).stdout.strip()
    sample = Path(samples) / "rml_triangle"
    if not sample.is_dir():
        check(results, "sample", False, f"no rml_triangle sample under {samples!r}")
        return
    work = Path(tempfile.mkdtemp(prefix="rive_skill_doctor_"))
    try:
        proj = work / "triangle"
        shutil.copytree(sample, proj)
        verify = L.run_rive([str(proj), "--verify", "--format=json"], timeout=120)
        env = L.parse_envelope(verify.stdout) or {}
        if not check(results, "verify", verify.returncode == 0 and env.get("success", False),
                     f"exit {verify.returncode}, envelope keys {sorted(env)}"):
            return
        inspect = L.inspect_project(proj)
        board = L.pick_artboard(inspect)
        check(results, "inspect", not L.problems(inspect) and board.width > 0,
              f"artboard {board.name} {board.size}, default state machine {board.state_machine}, "
              f"problems {L.problems(inspect)}")
        png0 = work / "rest.png"
        png1 = work / "later.png"
        L.run_rive([str(proj), "--quiet", f"--screenshot={png0}", L.advance_arg(L.FIRST_FRAME_EPSILON)], timeout=120)
        L.run_rive([str(proj), "--quiet", f"--screenshot={png1}", "--advance=30"], timeout=120)
        if not check(results, "screenshot", png0.is_file() and png1.is_file(), "captures written"):
            return
        blank = L.blank_reason(png1)
        check(results, "pixels", blank is None, blank or "the capture shows the scene")
        check(results, "animation", L.sha256_file(png0) != L.sha256_file(png1),
              "frame 30 differs from frame 0 (the spin runs)" if L.sha256_file(png0) != L.sha256_file(png1)
              else "frame 0 and frame 30 are identical: the state machine did not run")
        dump = work / "d.jsonl"
        L.run_rive([str(proj), "--quiet", f"--data-dump={dump}", "--data-dump-every=1", "--advance=6",
                    "--pointer=click@250,250", "--advance=6"], timeout=120)
        last = None
        if dump.is_file():
            lines = [json.loads(x) for x in dump.read_text().splitlines() if x.strip()]
            last = lines[-1] if lines else None
        frame = last.get("frame") if isinstance(last, dict) else None
        check(results, "gesture timing", frame == 15,
              f"6 frames + click + 6 frames ended on frame {frame} (expected 15: a click costs 3 frames); "
              "if this moved, rivetimeline.py's costs are stale" if frame != 15 else
              "a click costs 3 frames, as rivetimeline.py assumes")
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy2(png1, keep / "doctor_triangle.png")
    finally:
        L.remove_tree(work)


def web(results: list) -> None:
    try:
        import riveweb as W
    except Exception as exc:  # pragma: no cover - import guard
        check(results, "web runtime", False, f"cannot import riveweb: {exc}")
        return
    try:
        runtime = W.ensure_runtime()
    except L.RiveError as exc:
        check(results, "web runtime", False, str(exc))
        return
    check(results, "web runtime", True, f"{W.runtime_version()} at {runtime}")
    samples = L.run_rive(["samples", "--path"], timeout=30).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="rive_skill_doctor_web_"))
    try:
        proj = work / "triangle"
        shutil.copytree(Path(samples) / "rml_triangle", proj)
        L.run_rive([str(proj), "--once", "--quiet"], timeout=120)
        rivs = list((proj / "build").glob("*.riv"))
        if not check(results, "web build", bool(rivs), "built an unsigned .riv"):
            return
        with W.WebSession(rivs[0], width=500, height=500) as session:
            session.setup(width=500, height=500)
            png, _ = session.frame_png(0.5)
            out = work / "web.png"
            out.write_bytes(png)
            blank = L.blank_reason(out)
            check(results, "web pixels", blank is None,
                  f"{session.renderer_string}; " + (blank or "the web runtime drew the scene"))
    except L.RiveError as exc:
        check(results, "web engine", False, str(exc))
    finally:
        L.remove_tree(work)


def account(results: list) -> None:
    config = L.account_config_home()
    if config:
        Path(config).mkdir(parents=True, exist_ok=True, mode=0o700)
    who = L.run_rive(["whoami"], timeout=60)
    text = (who.stdout + who.stderr).strip()
    where = L.account_config_home() or "~/.config (no JARVIS_USER_DIR: the machine-wide login)"
    check(results, "account", who.returncode == 0 and "Not logged in" not in text,
          f"{text or 'no answer'} [login stored under {where}]", fatal=False)


def safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract only regular files and folders that stay inside dest."""
    root = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"refusing a path outside the archive folder: {member.name}")
        if not (member.isfile() or member.isdir()):
            continue
        tar.extract(member, dest, set_attrs=False)


def _safe_segment(value) -> bool:
    return isinstance(value, str) and ".." not in value and re.fullmatch(r"[0-9A-Za-z._-]{1,64}", value) is not None


def manifest_artifact(manifest: dict, key: str = "linux-x64") -> tuple[str, str, str]:
    """(version, path, sha256) from Rive's release manifest.

    The same shape checks Rive's install.sh makes: the version becomes a
    folder name here and the path a URL, so neither may climb out.
    """
    version = manifest.get("version")
    art = (manifest.get("artifacts") or {}).get(key) or {}
    path, sha = art.get("path"), str(art.get("sha256") or "").lower()
    if not _safe_segment(version):
        raise ValueError("the release manifest names an unsafe version")
    prefix = f"v{version}/"
    if not (isinstance(path, str) and path.startswith(prefix) and _safe_segment(path[len(prefix):])):
        raise ValueError("the release manifest names an unsafe download path")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("the release manifest has no SHA-256 for this build")
    return version, path, sha


def lay_out(blob: bytes, version: str, home: Path) -> Path:
    """Install a verified tarball the way Rive's install.sh lays it out.

    The binary goes to <home>/versions/<version>/rive with docs/ and samples/
    beside it, which is where `rive docs` and `rive samples` look (measured,
    CLI 1.1.1 on Linux: from anywhere else both fail with "not found beside
    the binary"). <home>/bin/rive is a hard link to it, or a copy where the
    filesystem cannot link, and `current` and `default` name the version.
    """
    payload_dir = home / "versions" / version
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x"
        out.mkdir()
        tar_path = Path(tmp) / "rive.tgz"
        tar_path.write_bytes(blob)
        with tarfile.open(tar_path) as tar:
            safe_extract(tar, out)
        binary = out / "rive"
        if binary.is_symlink() or not binary.is_file():
            raise RuntimeError("the archive held no rive binary")
        payload_dir.mkdir(parents=True, exist_ok=True)
        payload = payload_dir / "rive"
        staged = payload_dir / "rive.new"
        shutil.copy2(binary, staged)
        staged.chmod(0o755)
        os.replace(staged, payload)
        for extra in ("docs", "samples"):
            if (out / extra).is_dir():
                shutil.rmtree(payload_dir / extra, ignore_errors=True)
                shutil.copytree(out / extra, payload_dir / extra)
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    muxer, staged = bin_dir / "rive", bin_dir / "rive.new"
    staged.unlink(missing_ok=True)
    try:
        os.link(payload, staged)
    except OSError:
        shutil.copy2(payload, staged)
        staged.chmod(0o755)
    os.replace(staged, muxer)
    for name in ("current", "default"):
        pointer = home / f"{name}.new"
        pointer.write_text(version + "\n", encoding="utf-8")
        os.replace(pointer, home / name)
    return muxer


def install() -> int:
    system, machine = platform.system(), platform.machine()
    if system == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            print("Homebrew is missing; install it first, or use the official installer: "
                  "curl -fsSL https://releases.rive.app/cli/install.sh | sh")
            return 1
        for cmd in ([brew, "tap", "rive-app/tap"], [brew, "install", "--cask", "rive-app/tap/rive-cli"]):
            print("+", " ".join(cmd))
            if subprocess.run(cmd).returncode != 0:
                return 1
        print("Installed with Homebrew: update with `brew upgrade --cask rive-cli` "
              "(`rive update` does not work on a brew install).")
        return 0
    if system == "Linux" and machine in ("x86_64", "amd64"):
        with urllib.request.urlopen(f"{RELEASES}/latest/manifest.json", timeout=60) as resp:
            manifest = json.loads(resp.read())
        try:
            version, path, sha = manifest_artifact(manifest)
        except ValueError as exc:
            print(f"{exc}; nothing installed")
            return 1
        with urllib.request.urlopen(f"{RELEASES}/{path}", timeout=300) as resp:
            blob = resp.read()
        if hashlib.sha256(blob).hexdigest() != sha:
            print("the download failed its SHA-256 check; nothing installed")
            return 1
        try:
            muxer = lay_out(blob, version, Path.home() / ".rive")
        except RuntimeError as exc:
            print(f"{exc}; nothing installed")
            return 1
        print(f"Installed Rive CLI {version} to {muxer} (verified SHA-256, laid out as Rive's install.sh does). "
              "Add ~/.rive/bin to PATH. On Mesa GPUs captures come out blank until "
              "rive-app/rive-runtime#92 is fixed: see references/rendering.md, Linux.")
        return 0
    print(f"No Rive CLI build for {system} {machine} (Rive ships macOS arm64, Linux x64, Windows x64).")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web", action="store_true", help="also check the web engine")
    ap.add_argument("--account", action="store_true", help="also report this session's Rive login")
    ap.add_argument("--install", action="store_true", help="install the CLI first")
    ap.add_argument("--keep", help="copy the sample capture here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.install:
        rc = install()
        if rc:
            return rc
    results: list = []
    tools(results)
    if L.find_rive():
        try:
            flags(results)
            build_and_render(results, Path(args.keep) if args.keep else None)
            if args.web:
                web(results)
            if args.account:
                account(results)
        except L.RiveError as exc:
            check(results, "cli", False, str(exc))
    failed = [r for r in results if not r["ok"] and r["fatal"]]
    if args.json:
        print(json.dumps({"ok": not failed, "results": results}, indent=2))
    else:
        for r in results:
            mark = "ok  " if r["ok"] else ("FAIL" if r["fatal"] else "note")
            print(f"{mark} {r['check']}: {r['detail']}")
        print("READY" if not failed else f"NOT READY: {len(failed)} check(s) failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
