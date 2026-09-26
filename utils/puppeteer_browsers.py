#!/usr/bin/env python3
"""The browser a global npm CLI drives through Puppeteer, fetched as this user.

@mermaid-js/mermaid-cli draws every diagram in a headless Chrome. Its
dependency Puppeteer downloads that browser from an install script during
`npm install`, into the installing user's cache (~/.cache/puppeteer). Two ways
that leaves the bot without a browser while npm still exits 0:

  * Linux installs global npm packages with `sudo npm install -g` (the
    skill's own install message, and the nightly update where npm's folder
    is root's), so the script runs as root and the browser lands
    in /root/.cache/puppeteer. The bot's own mmdc never looks there: "Could
    not find chrome-headless-shell" on every render, hit on the Linux box on
    3 Sep 2026 until Chrome was installed as the bot's user.
  * The script catches its own download failure and exits 0 (Puppeteer's
    install.mjs: `console.warn('Browser download failed', error)`). Measured
    on the Mac, 25 Sep 2026, npm 11.19.1 and Puppeteer 25.12.0, with the
    download host unreachable: npm exit 0, an empty cache, every render
    failing. A nightly update would have called that "updated".

npm 11.19's allowScripts warning is not a third way. It names Puppeteer's
script as "not yet covered", but runs it: npm skips a script only when a
package is denied outright, or fails the whole install under
strict-allow-scripts, which is off by default.

ensure_browsers() runs the same download again as the current user, through
the package's own Puppeteer, so it fetches exactly the builds that package
pins, with Puppeteer's own CLI, which exits 1 when a download fails. Where
they are already cached it returns in about a tenth of a second (measured).

  python3 utils/puppeteer_browsers.py @mermaid-js/mermaid-cli
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def npm_global_root(prefix: Path | None = None) -> Path | None:
    """Where `npm install -g` puts packages (under `prefix` when given, as
    for a trial install), or None when npm cannot say."""
    npm = shutil.which("npm")
    if not npm:
        return None
    cmd = [npm, "root", "-g"] + (["--prefix", str(prefix)] if prefix else [])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    root = result.stdout.strip()
    return Path(root) if result.returncode == 0 and root else None


def package_name(spec: str) -> str:
    """The name in an install spec: '@scope/name@1.2' is '@scope/name'."""
    at = spec.find("@", 1)
    return spec if at == -1 else spec[:at]


def bundled_puppeteer(package: str, root: Path | None = None) -> Path | None:
    """The Puppeteer a global package installed for itself, or None.

    Only the full `puppeteer` package downloads browsers. `puppeteer-core`,
    which lighthouse uses, never does, so it is not looked for.
    """
    root = root if root is not None else npm_global_root()
    if root is None:
        return None
    found = root / package_name(package) / "node_modules" / "puppeteer"
    return found if (found / "package.json").is_file() else None


def ensure_browsers(package: str, root: Path | None = None, timeout: int = 900) -> tuple[bool, str]:
    """Fetch the browsers `package`'s Puppeteer pins into this user's cache.

    `root` is the node_modules the package sits in, the global one by
    default. Returns (ok, detail). A package with no Puppeteer needs nothing,
    so it is (True, "") without a download.
    """
    puppeteer = bundled_puppeteer(package, root)
    if puppeteer is None:
        return True, ""
    node = shutil.which("node")
    if not node:
        return False, "node is not on PATH"
    try:
        meta = json.loads((puppeteer / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"cannot read Puppeteer's package.json: {e}"
    cli = meta.get("bin")
    if isinstance(cli, dict):
        cli = cli.get("puppeteer")
    if not isinstance(cli, str) or not (puppeteer / cli).is_file():
        return False, f"Puppeteer {meta.get('version', '?')} has no browsers command"
    # No browser named: the same set the install script fetches (Chrome and
    # its headless shell by default). The package folder as cwd, as for the
    # install script, so any Puppeteer config file is found the same way.
    try:
        result = subprocess.run([node, str(puppeteer / cli), "browsers", "install"],
                                capture_output=True, text=True, timeout=timeout, cwd=puppeteer)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"the browser download did not finish: {e}"
    out = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, f"the browser download failed: {error_lines(out) or f'rc={result.returncode}'}"
    return True, out[-300:]


def error_lines(out: str) -> str:
    """What went wrong, from a failed browsers command.

    The command prints its whole usage text before the error and a stack
    after it, so the tail alone reads like "CLI.js:181:21". Measured with a
    read-only cache: "Error: All providers failed for chrome-headless-shell
    154.0.8037.57:" and then "DefaultProvider: EACCES: permission denied".
    """
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Error"):
            rest = [s.strip() for s in lines[i + 1:i + 3] if s.strip() and not s.strip().startswith("at ")]
            return " ".join([line.strip(), *rest])[:300]
    return out[-300:]


def main(argv: list[str] | None = None) -> int:
    packages = sys.argv[1:] if argv is None else argv
    if not packages:
        print("usage: puppeteer_browsers.py PACKAGE [PACKAGE...]", file=sys.stderr)
        return 2
    failed = 0
    for package in packages:
        ok, detail = ensure_browsers(package)
        print(f"{package}: {'ok' if ok else 'FAILED'}" + (f"\n{detail}" if detail else ""))
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
