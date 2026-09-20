#!/usr/bin/env python3
"""Interactive HTML system maps through the vendored archify renderer.

archify (skills/diagram/archify, MIT, pinned in VENDOR.md there) compiles a
small typed JSON specification into one self contained HTML page: an
explorable map with search, focus, route tracing, guided chapters, dark and
light themes, and PNG, SVG and WebM export built into the page itself.

This wrapper is the bot's one entry point to it, because three things have to
be right on every call and a bare `node bin/archify.mjs` gets none of them:

- the packaged update checker must never phone home or write reminder state
  (ARCHIFY_UPDATE_CHECK_DISABLED=1);
- the browser check needs a Chrome binary, which here is the one Puppeteer
  keeps for mermaid-cli rather than anything on PATH (ARCHIFY_CHROME);
- that Chrome has to run without its sandbox on Ubuntu 24.04, where AppArmor
  blocks unprivileged user namespaces (ARCHIFY_CHROME_NO_SANDBOX=1). It is
  the same reason scripts/puppeteer.json passes --no-sandbox to mmdc.

Telegram cannot show HTML inline, so `deliver --preview` also writes a PNG of
the rendered page for the chat: the 1440x900 capture that archify's own
browser check takes, in the requested theme.

Exit codes for deliver: 0 delivered (and previewed when asked); 1 the spec
failed a check and nothing was written for it; 3 the HTML was written but the
browser check did not pass, so the preview is missing or shows a defect.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ARCHIFY_ROOT = SCRIPT_DIR.parent / "archify"
ARCHIFY_CLI = ARCHIFY_ROOT / "bin" / "archify.mjs"

TYPES = ("architecture", "workflow", "sequence", "dataflow", "lifecycle")

# One render is sub second and the browser check about five; a hung Chrome
# must not hold a bot turn forever.
ARCHIFY_TIMEOUT = 300

# Same rule the auto updater applies to its gate: the child gets the bot's
# environment minus anything credential shaped. archify needs none of it.
_SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASSWORD", re.IGNORECASE)
QUALITIES = ("showcase", "standard")
THEMES = ("dark", "light")

# The capture archify's visual-check writes beside the artifact. One run
# produces both themes at this size; the wrapper keeps the requested one.
PREVIEW_VIEWPORT = "1440x900"
PREVIEW_STEM = "preview"

# Puppeteer's cache holds one folder per downloaded build, named like
# linux-152.0.7977.54 or mac_arm-152.0.7977.54, with the executable under an
# arch folder inside it. Both the full browser and the headless shell drive
# the DevTools pipe visual-check uses (measured 2026-09-20 on both platforms);
# the full one is preferred.
#
# macOS needs this lookup as much as Linux does. Upstream only knows the two
# /Applications bundles on a Mac, and a machine can easily have neither while
# still holding the Chrome Puppeteer downloaded for mermaid-cli. Without the
# cache layouts below the preview is skipped on every Mac run and the PNG the
# skill promises is quietly missing (measured 2026-09-20).
_CHROME_LAYOUTS = {
    "linux": (
        ("chrome", (("chrome-linux64", "chrome"),)),
        ("chrome-headless-shell", (("chrome-headless-shell-linux64", "chrome-headless-shell"),)),
    ),
    "darwin": (
        ("chrome", (
            ("chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"),
            ("chrome-mac-x64", "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"),
        )),
        ("chrome-headless-shell", (
            ("chrome-headless-shell-mac-arm64", "chrome-headless-shell"),
            ("chrome-headless-shell-mac-x64", "chrome-headless-shell"),
        )),
    ),
}
# PATH names are Linux only on purpose: upstream searches PATH there too, and
# on macOS it searches /Applications instead, which is the better lookup to
# fall through to once the cache has nothing.
_PATH_CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _build_version(name: str) -> tuple[int, ...]:
    """linux-152.0.7977.54 -> (152, 0, 7977, 54); anything else sorts last."""
    _, _, version = name.partition("-")
    parts = version.split(".")
    if not version or not all(part.isdigit() for part in parts):
        return (-1,)
    return tuple(int(part) for part in parts)


def puppeteer_cache_dir(env: dict) -> Path:
    override = env.get("PUPPETEER_CACHE_DIR")
    if override:
        return Path(override)
    return Path(env.get("HOME") or Path.home()) / ".cache" / "puppeteer"


def _chrome_layouts(platform: str) -> tuple:
    """The Puppeteer cache layouts to try, or an empty tuple for a platform
    whose layout is not known here (Windows), where upstream looks itself."""
    for prefix, layouts in _CHROME_LAYOUTS.items():
        if platform.startswith(prefix):
            return layouts
    return ()


def find_chrome(env: dict | None = None, platform: str | None = None) -> Path | None:
    """The Chrome the browser check should use, or None to let archify look itself.

    ARCHIFY_CHROME wins when set. Otherwise the newest full Chrome in
    Puppeteer's cache, then the newest headless shell there, then on Linux the
    usual names on PATH. None means archify does its own lookup.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    explicit = env.get("ARCHIFY_CHROME")
    if explicit:
        return Path(explicit)
    layouts = _chrome_layouts(platform)
    if not layouts:
        return None
    cache = puppeteer_cache_dir(env)
    for family, tails in layouts:
        root = cache / family
        if not root.is_dir():
            continue
        builds = []
        for build in root.iterdir():
            for tail in tails:
                exe = build.joinpath(*tail)
                if exe.is_file() and os.access(exe, os.X_OK):
                    builds.append((_build_version(build.name), exe))
                    break
        if builds:
            return max(builds)[1]
    if platform.startswith("linux"):
        for name in _PATH_CHROME_NAMES:
            found = shutil.which(name, path=env.get("PATH"))
            if found:
                return Path(found)
    return None


def build_env(env: dict | None = None, platform: str | None = None) -> dict:
    """The environment every archify call runs with."""
    base = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    out = {k: v for k, v in base.items() if not _SECRET_NAME.search(k)}
    # Forced, not defaulted: the bot must never let the package phone home.
    out["ARCHIFY_UPDATE_CHECK_DISABLED"] = "1"
    chrome = find_chrome(out, platform)
    if chrome is not None:
        out["ARCHIFY_CHROME"] = str(chrome)
    if platform.startswith("linux"):
        out.setdefault("ARCHIFY_CHROME_NO_SANDBOX", "1")
    return out


def node_binary() -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node not found. archify needs Node.js 18 or newer on PATH.")
    return node


def run_archify(args, env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    if not ARCHIFY_CLI.is_file():
        raise RuntimeError(f"{ARCHIFY_CLI} is missing; the vendored archify folder is incomplete")
    cmd = [node_binary(), str(ARCHIFY_CLI), *map(str, args)]
    kwargs.setdefault("timeout", ARCHIFY_TIMEOUT)
    return subprocess.run(cmd, env=build_env() if env is None else env, **kwargs)


def _parse_receipt(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def deliver(kind: str, spec: Path, output: Path, quality: str = "showcase", env: dict | None = None):
    """Run archify deliver. Returns (returncode, receipt or None, stdout, stderr)."""
    proc = run_archify(
        ["deliver", kind, spec, output, "--quality", quality, "--json"],
        env=env, capture_output=True, text=True,
    )
    return proc.returncode, _parse_receipt(proc.stdout), proc.stdout, proc.stderr


def visual_check(html: Path, workdir: Path, env: dict | None = None):
    """Run archify's browser check on a copy of html inside workdir.

    Returns (status, receipt, pngs): status is pass, fail or skipped (archify's
    exit codes 0, 1 and 2), pngs maps theme to the 1440x900 capture that
    exists. Checking a copy keeps the sidecars out of the delivery directory;
    the receipt binds to the bytes, and a copy has the same bytes.
    """
    copy = workdir / f"{PREVIEW_STEM}.html"
    shutil.copyfile(html, copy)
    proc = run_archify(["visual-check", copy, "--json"], env=env, capture_output=True, text=True)
    receipt = _parse_receipt(proc.stdout) or {
        "status": "fail",
        "error": (proc.stderr or proc.stdout).strip()[-2000:] or f"exit {proc.returncode}",
    }
    status = {0: "pass", 1: "fail", 2: "skipped"}.get(proc.returncode, "fail")
    pngs = {}
    for theme in THEMES:
        png = workdir / f"{PREVIEW_STEM}.visual-check.{PREVIEW_VIEWPORT}.{theme}.png"
        if png.is_file():
            pngs[theme] = png
    return status, receipt, pngs


def _diagnostic_lines(receipt: dict | None) -> list[str]:
    if not receipt:
        return []
    lines = []
    if receipt.get("error"):
        lines.append(str(receipt["error"]))
    for diag in receipt.get("diagnostics") or []:
        if isinstance(diag, dict):
            lines.append(json.dumps(diag, ensure_ascii=False))
        else:
            lines.append(str(diag))
    return lines


def make_preview(html: Path, png: Path, theme: str = "dark", env: dict | None = None) -> tuple[bool, str]:
    """Write a PNG of the rendered page. Returns (ok, message).

    A failed browser check still hands over its capture when one exists, so
    the defect it found can be looked at; the message says which it is.
    """
    with tempfile.TemporaryDirectory(prefix="archify-preview-") as tmp:
        status, receipt, pngs = visual_check(html, Path(tmp), env)
        capture = pngs.get(theme)
        if capture is not None:
            png.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(capture, png)
    if status == "pass" and capture is not None:
        return True, f"Wrote {png} ({PREVIEW_VIEWPORT}, {theme})"
    if status == "skipped":
        return False, "Preview skipped: no Chrome or Chromium found. Set ARCHIFY_CHROME to one."
    detail = "; ".join(_diagnostic_lines(receipt)) or "no capture written"
    if capture is not None:
        return False, f"Browser check failed: {detail}\nWrote {png} anyway so the defect can be seen."
    return False, f"Browser check failed: {detail}"


def cmd_deliver(a: argparse.Namespace) -> int:
    spec = Path(a.spec).resolve()
    output = Path(a.output).resolve()
    if not spec.is_file():
        print(f"Spec not found: {spec}", file=sys.stderr)
        return 2
    if output.suffix.lower() != ".html":
        print("The output path must end in .html", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    existed = output.exists()
    env = build_env()

    rc, receipt, out, err = deliver(a.type, spec, output, a.quality, env)
    if rc != 0 or not (receipt or {}).get("ok"):
        print(f"archify deliver failed (code {rc}).", file=sys.stderr)
        lines = _diagnostic_lines(receipt)
        print("\n".join(lines) if lines else (out + err).strip(), file=sys.stderr)
        if existed and output.exists():
            print(f"{output} is the PREVIOUS delivery, left untouched; nothing was written for this spec.",
                  file=sys.stderr)
        return 1

    validation = receipt.get("validation", {})
    artifact = receipt.get("artifact", {})
    summary = (
        f"{a.type}, {validation.get('compositionProfile', a.quality)} "
        f"{validation.get('checksPassed')}/{validation.get('checkCount')} checks, "
        f"{validation.get('errors')} errors, {validation.get('warnings')} warnings, "
        f"{artifact.get('bytes')} bytes"
    )

    preview_ok, preview_msg = (None, "")
    if a.preview:
        preview_ok, preview_msg = make_preview(output, Path(a.preview).resolve(), a.theme, env)

    if a.json:
        payload = {"html": str(output), "receipt": receipt}
        if a.preview:
            payload["preview"] = {
                "path": str(Path(a.preview).resolve()),
                "theme": a.theme,
                "ok": preview_ok,
                "message": preview_msg,
            }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote {output} ({summary})")
        if a.preview:
            print(preview_msg)
    return 0 if preview_ok in (None, True) else 3


def cmd_validate(a: argparse.Namespace) -> int:
    spec = Path(a.spec).resolve()
    if not spec.is_file():
        print(f"Spec not found: {spec}", file=sys.stderr)
        return 2
    args = ["validate", a.type, spec, "--quality", a.quality]
    if a.json:
        args.append("--json")
    if a.layout_json:
        args.append("--layout-json")
    return run_archify(args).returncode


def cmd_doctor(a: argparse.Namespace) -> int:
    return run_archify(["doctor"]).returncode


def cmd_run(a: argparse.Namespace) -> int:
    if not a.args:
        print("run needs an upstream command, e.g. run guide 'ETL pipeline' --json", file=sys.stderr)
        return 2
    if a.args[0] == "examples":
        # Upstream's examples command renders five 800 KB pages INTO the
        # vendored tree. demo writes one page where it is told to.
        print("refusing 'examples': it writes rendered pages into the vendored tree. "
              "Use: run demo <output-directory>", file=sys.stderr)
        return 2
    return run_archify(a.args).returncode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Interactive HTML system maps (archify) for the diagram skill.",
        epilog=(
            "deliver exit codes: 0 delivered; 1 the spec failed a check, nothing written for it; "
            "3 HTML written but the browser check did not pass."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("deliver", help="check the spec, write one self contained HTML page, optionally a PNG preview")
    d.add_argument("type", choices=TYPES)
    d.add_argument("spec", help="path to the JSON specification")
    d.add_argument("-o", "--output", required=True, help="HTML path to write; must end in .html")
    d.add_argument("--preview", help="also write a PNG of the rendered page here (needs Chrome)")
    d.add_argument("--theme", choices=THEMES, default="dark", help="theme of the preview (default: dark)")
    d.add_argument("--quality", choices=QUALITIES, default="showcase", help="check profile (default: showcase)")
    d.add_argument("--json", action="store_true", help="print the receipt as JSON instead of prose")
    d.set_defaults(func=cmd_deliver)

    v = sub.add_parser("validate", help="check a specification without writing anything")
    v.add_argument("type", choices=TYPES)
    v.add_argument("spec", help="path to the JSON specification")
    v.add_argument("--quality", choices=QUALITIES, default="showcase")
    v.add_argument("--json", action="store_true", help="machine readable diagnostics")
    v.add_argument("--layout-json", action="store_true", help="workflow v2 layout receipt")
    v.set_defaults(func=cmd_validate)

    doc = sub.add_parser("doctor", help="prove Node and the vendored bundle are whole")
    doc.set_defaults(func=cmd_doctor)

    r = sub.add_parser(
        "run",
        help="any other upstream command with the same environment, e.g. run guide 'ETL pipeline' --json, run demo /tmp/demo",
    )
    r.add_argument("args", nargs=argparse.REMAINDER)
    r.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as exc:
        print(f"archify timed out after {exc.timeout:.0f} s: {' '.join(map(str, exc.cmd[2:]))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
