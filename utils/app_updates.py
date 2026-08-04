#!/usr/bin/env python3
"""Update checks for apps the OS package manager does not track.

utils/system_update.py covers whatever apt/dnf/brew/... knows about. That
leaves a blind spot: several apps this machine is *supposed* to have are
installed outside the package manager entirely, so nothing on the box has ever
looked at their versions. Measured on the reference Mac mini, 2026-08-04:

    DaVinci Resolve   21.0.2 installed, 21.0.3 published (cask retired, so the
                      davinci-resolve skill ships its own installer)
    Claude Code       2.1.217 installed, 2.1.221 published — the bot's own
                      runtime, 13 days stale, self-update switched off
    @higgsfield/cli   0.1.40 installed, 1.1.20 published — a whole major
                      version behind, and it is what image-gen spends money on
    surge             0.27.3 installed, 0.41.2 published — presentations are
                      delivered with it

None of those five gaps could ever surface, because none of these apps appear
on any list the nightly job reads.

Everything here is a *check*. Installing is opt-in per family and deliberately
narrow (see AUTO_INSTALLABLE): an unattended nightly job is the wrong place to
swap a 3.5 GB GUI application, but exactly the right place to move a CLI
forward by a patch release.
"""

from __future__ import annotations

import json
import logging
import platform
import plistlib
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

logger = logging.getLogger(__name__)

# Anthropic's release-channel files for the native Claude Code build: plain
# text, one version string. "latest" is what `claude update` on this install
# actually follows (measured 2026-08-04: it moved 2.1.217 -> 2.1.221 while the
# stable channel sat on 2.1.220), so comparing against stable would report a
# permanent false "outdated" that updating could never clear.
CLAUDE_DIST = (
    "https://storage.googleapis.com/"
    "claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"
)
CLAUDE_CHANNEL = "latest"
# Same version, second opinion, used only when the channel file is unreachable.
CLAUDE_NPM = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"

# Blackmagic's own downloads feed — the one skills/davinci-resolve's installer
# already reads to find what to fetch. Public, no registration (registration is
# only needed for the download itself, not for the version list).
BMD_DOWNLOADS = "https://www.blackmagicdesign.com/api/support/us/downloads.json"

# Where a macOS Resolve install reports its version. Studio and free use
# different bundle names and a machine may carry either.
RESOLVE_BUNDLES = (
    "/Applications/DaVinci Resolve/DaVinci Resolve.app",
    "/Applications/DaVinci Resolve Studio/DaVinci Resolve Studio.app",
    "/opt/resolve/bin/resolve",  # Linux install, version read separately
)

# Global npm CLIs that skills in this repo install and then never revisit.
# Keyed by package name so an unrelated global package a user installed by hand
# is reported but never auto-touched.
NPM_SKILL_CLIS = {
    "@higgsfield/cli": "image-gen",
    "@mermaid-js/mermaid-cli": "diagram",
    "@googleworkspace/cli": "google-workspace",
    "lighthouse": "lighthouse",
    "surge": "presentations",
    "afterwriting": "screenplay",
}

# npm is excluded from every npm upgrade path on purpose. Node here comes from
# Homebrew and Homebrew owns the files under its prefix, npm's own included.
# `npm install -g npm@latest` overwrites them in place, and the next
# `brew upgrade node` then collides with files brew did not put there. brew
# already upgrades npm as part of node, so leaving it alone loses nothing.
NPM_NEVER_TOUCH = frozenset({"npm", "npx", "node", "corepack"})

# Families the nightly job may install without a human. A CLI moves by
# replacing a file and old versions stay on disk; a GUI app bundle does not.
AUTO_INSTALLABLE = frozenset({"claude-code", "npm"})

_VERSION_PART = re.compile(r"\d+")


class AppStatus(NamedTuple):
    """One app's update state.

    state is one of:
      current    installed and up to date
      outdated   a newer version is published, nothing was installed
      updated    a newer version was published and this run installed it
      failed     an update was attempted and did not take
      unknown    installed, but the published version could not be read
    An app that is not installed is left out entirely — skills in this repo
    self-install on demand, so "absent" is a normal state, not a fault.
    """

    name: str
    family: str
    installed: str
    latest: str
    state: str
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state in ("outdated", "updated", "failed")


def _run(cmd: list[str], timeout: int = 60, merge_stderr: bool = True) -> tuple[int, str]:
    """Run a command with no shell. Returns (returncode, stdout+stderr).

    merge_stderr=False returns stdout alone. Callers that *parse* the output
    need that: a tool which prints a warning to stderr would otherwise corrupt
    its own JSON, and the parse failure reads as "nothing to report".
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr) if merge_stderr else r.stdout
        return r.returncode, out.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 1, str(e)


def _fetch(url: str, timeout: int = 20) -> str:
    """GET a URL, returning "" on any failure.

    Every caller treats a network failure as "cannot tell", never as "up to
    date" — a check that goes quiet when the internet hiccups is worse than no
    check, because it looks like good news.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace").strip()
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.debug(f"fetch failed for {url}: {e}")
        return ""


def version_tuple(v: str) -> tuple[int, ...]:
    """Numeric parts of a version string, for ordering.

    Deliberately ignores anything non-numeric: "2.1.221 (Claude Code)" and
    "v21.0.3-build4" both reduce to their numbers. Comparison is padded by the
    caller, so 21.0 and 21.0.3 order correctly.
    """
    return tuple(int(p) for p in _VERSION_PART.findall(v or ""))


def is_newer(candidate: str, current: str) -> bool:
    """True when candidate is a strictly higher version than current.

    Returns False when either side has no numbers at all: an unreadable
    version must not be reported as an update available.
    """
    a, b = version_tuple(candidate), version_tuple(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def is_major_jump(current: str, latest: str) -> bool:
    """True when the leading version number goes up.

    The line between "install it while everyone sleeps" and "ask a human".
    Under semver a major bump is a promise that something breaks, and this is
    not theoretical here: @higgsfield/cli sat on 0.1.40 against a published
    1.1.20 (2026-08-04), and image-gen spends real money through it. Moving
    that overnight could leave the skill broken with nobody watching, so a
    major jump is always reported rather than installed.

    A 0.x leading number is treated the same way. Pre-1.0 packages break on
    minor bumps too, and both of the badly-stale CLIs found on this machine
    were 0.x, so the cautious reading is the useful one.
    """
    a, b = version_tuple(current), version_tuple(latest)
    if not a or not b:
        return True  # cannot tell, so do not install unattended
    if b[0] != a[0]:
        return True
    # 0.x: treat a minor bump as breaking, since that is where 0.x puts its
    # breaking changes.
    return a[0] == 0 and len(a) > 1 and len(b) > 1 and b[1] != a[1]


# --------------------------------------------------------------------------
# Claude Code — the bot's own runtime
# --------------------------------------------------------------------------

def _claude_installed_version() -> str:
    """Installed Claude Code version, or "" when it is not on PATH."""
    if not shutil.which("claude"):
        return ""
    rc, out = _run(["claude", "--version"], timeout=30)
    if rc != 0:
        return ""
    # "2.1.221 (Claude Code)" -> "2.1.221"
    first = out.split()[0] if out.split() else ""
    return first if version_tuple(first) else ""


def _claude_latest_version() -> str:
    """Published Claude Code version from the release channel.

    Falls back to the npm registry, which publishes the same build, so a
    blocked storage domain does not silence the check.
    """
    text = _fetch(f"{CLAUDE_DIST}/{CLAUDE_CHANNEL}")
    if text and version_tuple(text) and len(text) < 40:
        return text.strip()
    raw = _fetch(CLAUDE_NPM)
    if raw:
        try:
            return str(json.loads(raw).get("version") or "")
        except (json.JSONDecodeError, AttributeError):
            return ""
    return ""


def check_claude_code(auto_update: bool = False) -> list[AppStatus]:
    """Version state of the Claude Code CLI this bot runs on.

    Updating is safe while a session is live: the native install writes a new
    file under versions/ and repoints a symlink, so a process already running
    keeps its own binary and previous versions stay on disk to roll back to.
    Verified during the 2.1.217 -> 2.1.221 update, 2026-08-04: the in-flight
    session survived untouched.
    """
    installed = _claude_installed_version()
    if not installed:
        return []
    latest = _claude_latest_version()
    if not latest:
        return [AppStatus("Claude Code", "claude-code", installed, "", "unknown",
                          "could not reach the release channel")]
    if not is_newer(latest, installed):
        return [AppStatus("Claude Code", "claude-code", installed, latest, "current")]
    if not auto_update:
        return [AppStatus("Claude Code", "claude-code", installed, latest, "outdated")]
    if is_major_jump(installed, latest):
        return [AppStatus("Claude Code", "claude-code", installed, latest, "outdated",
                          "major version, worth a look before installing")]

    rc, out = _run(["claude", "update"], timeout=600)
    now = _claude_installed_version()
    if rc == 0 and is_newer(now, installed):
        return [AppStatus("Claude Code", "claude-code", now, latest, "updated")]
    return [AppStatus("Claude Code", "claude-code", installed, latest, "failed",
                      out[-200:] if out else f"rc={rc}")]


# --------------------------------------------------------------------------
# DaVinci Resolve — installed by the skill's own script, never by a package
# manager (Blackmagic retired the Homebrew cask)
# --------------------------------------------------------------------------

def _resolve_installed_version() -> str:
    """Installed DaVinci Resolve version, free or Studio, or ""."""
    if platform.system() == "Darwin":
        for bundle in RESOLVE_BUNDLES:
            plist = Path(bundle) / "Contents" / "Info.plist"
            if not plist.is_file():
                continue
            try:
                with open(plist, "rb") as fh:
                    v = plistlib.load(fh).get("CFBundleShortVersionString", "")
                if version_tuple(v):
                    return str(v)
            except (OSError, plistlib.InvalidFileException, ValueError) as e:
                logger.debug(f"Resolve plist unreadable at {plist}: {e}")
        return ""
    # Linux: Blackmagic drops a plain-text version file next to the install.
    for marker in ("/opt/resolve/docs/version", "/opt/resolve/version"):
        p = Path(marker)
        if p.is_file():
            try:
                v = p.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if version_tuple(v):
                return v
    return ""


def _bmd_platform_key() -> str:
    """The key Blackmagic's feed uses for this OS."""
    return {"Darwin": "Mac OS X", "Linux": "Linux", "Windows": "Windows"}.get(
        platform.system(), ""
    )


def _resolve_latest_version() -> str:
    """Newest published Resolve version for this platform, or "".

    Same filter the davinci-resolve installer uses: the plain "DaVinci Resolve"
    product, excluding Studio and Server, whose version numbers track
    separately. The feed lists newest first, so the first hit wins.
    """
    key = _bmd_platform_key()
    if not key:
        return ""
    raw = _fetch(BMD_DOWNLOADS, timeout=30)
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    for entry in data.get("downloads", []) or []:
        name = entry.get("name", "")
        if "DaVinci Resolve" not in name or "Studio" in name or "Server" in name:
            continue
        for item in (entry.get("urls") or {}).get(key, []) or []:
            if item.get("product") != "davinci-resolve":
                continue
            try:
                return "%s.%s.%s" % (
                    item["major"], item["minor"], item["releaseNum"],
                )
            except KeyError:
                continue
    return ""


def check_davinci_resolve(auto_update: bool = False) -> list[AppStatus]:
    """Version state of DaVinci Resolve. Never installs, whatever the flag says.

    Three reasons an unattended install is wrong here, all hit for real:
    Blackmagic gate the download behind a name-and-email form, the installer
    needs admin rights and a GUI-mediated 3.5 GB package, and replacing the app
    under a colourist with a project open loses their work.
    """
    installed = _resolve_installed_version()
    if not installed:
        return []
    latest = _resolve_latest_version()
    if not latest:
        return [AppStatus("DaVinci Resolve", "resolve", installed, "", "unknown",
                          "could not read Blackmagic's download feed")]
    if is_newer(latest, installed):
        return [AppStatus("DaVinci Resolve", "resolve", installed, latest, "outdated",
                          "ask me to update it, never installed unattended")]
    return [AppStatus("DaVinci Resolve", "resolve", installed, latest, "current")]


# --------------------------------------------------------------------------
# Global npm CLIs that skills depend on
# --------------------------------------------------------------------------

def _npm_outdated_global() -> dict:
    """Parsed `npm outdated -g --json`, or {} when npm is absent or unhappy.

    npm exits 1 when anything is outdated, which is the case we care about, so
    the return code is not a usable success signal — the JSON is.

    stdout only, deliberately. npm writes its warnings to stderr and keeps the
    JSON on stdout, so folding the two together appends the warning text to the
    document and the parse fails. Reproduced on a Linux box with npm 10.8.2:
    one `npm warn config` line turned a correct report of two stale CLIs into
    an empty one, silently — the exact shape of failure this module exists to
    remove.
    """
    if not shutil.which("npm"):
        return {}
    _rc, out = _run(["npm", "outdated", "-g", "--json"], timeout=180,
                    merge_stderr=False)
    if not out:
        return {}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        logger.debug("npm outdated returned non-JSON output")
        return {}
    return data if isinstance(data, dict) else {}


def check_npm_clis(auto_update: bool = False) -> list[AppStatus]:
    """Version state of the global npm CLIs the skills in this repo install."""
    results: list[AppStatus] = []
    for pkg, info in sorted(_npm_outdated_global().items()):
        if pkg in NPM_NEVER_TOUCH or not isinstance(info, dict):
            continue
        current = str(info.get("current") or "")
        latest = str(info.get("latest") or "")
        if not current or not latest or not is_newer(latest, current):
            continue
        skill = NPM_SKILL_CLIS.get(pkg, "")
        detail = f"used by the {skill} skill" if skill else "installed by hand"
        # Only packages a skill in this repo put there are auto-installed. A
        # global package someone added themselves is reported, never moved.
        if not auto_update or pkg not in NPM_SKILL_CLIS:
            results.append(AppStatus(pkg, "npm", current, latest, "outdated", detail))
            continue
        if is_major_jump(current, latest):
            results.append(AppStatus(
                pkg, "npm", current, latest, "outdated",
                f"{detail}, major version so it needs a look before installing"))
            continue
        # An exact version is pinned rather than @latest so the return code is
        # a usable signal: npm either installed that version or failed. (The
        # Claude check cannot rely on rc alone — `claude update` exits 0 having
        # done nothing — so it re-reads the version instead.)
        rc, out = _run(["npm", "install", "-g", f"{pkg}@{latest}"], timeout=600)
        if rc == 0:
            results.append(AppStatus(pkg, "npm", latest, latest, "updated", detail))
        else:
            results.append(AppStatus(pkg, "npm", current, latest, "failed",
                                     out[-200:] if out else f"rc={rc}"))
    return results


# --------------------------------------------------------------------------
# Flatpak apps (Linux) — sandboxed, version-independent, and invisible to apt
# --------------------------------------------------------------------------

def check_flatpak(auto_update: bool = False) -> list[AppStatus]:
    """Flatpak apps with a pending update. Report only.

    install/compat.py falls back to Flatpak whenever a distro's own packages are
    too old for an app (Blender, GIMP, Inkscape, LibreOffice, Chromium), so on
    a Linux box the workstation apps can live here rather than in apt — where
    the nightly job would never see them.

    Report only: a flatpak update pulls whole runtimes and can run to gigabytes,
    which is not something to start unattended on a metered or small-disk box.
    """
    if platform.system() != "Linux" or not shutil.which("flatpak"):
        return []
    rc, out = _run(
        ["flatpak", "remote-ls", "--updates", "--app", "--columns=application,version"],
        timeout=180,
    )
    if rc != 0 or not out:
        return []
    results = []
    for line in out.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        app_id = parts[0].strip() if parts else ""
        # Skip headers and any chatter flatpak prints on stderr.
        if not app_id or "." not in app_id or " " in app_id:
            continue
        latest = parts[1].strip() if len(parts) > 1 else ""
        results.append(AppStatus(app_id, "flatpak", "", latest, "outdated",
                                 "run flatpak update"))
    return results


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

# (family, checker). The family name is what decides whether auto_update is
# allowed to reach that checker at all, so the gate is data rather than a
# property of the function object.
CHECKS: tuple[tuple[str, Callable[[bool], list[AppStatus]]], ...] = (
    ("claude-code", check_claude_code),
    ("resolve", check_davinci_resolve),
    ("npm", check_npm_clis),
    ("flatpak", check_flatpak),
)


def collect(auto_update: bool = False, log_fn=None) -> list[AppStatus]:
    """Run every check. One failing check never takes the others down.

    A family outside AUTO_INSTALLABLE is called with auto_update forced off.
    Belt and braces: check_davinci_resolve refuses to install internally too,
    but gating here means an accidental unattended 3.5 GB application install
    takes two independent mistakes rather than one.
    """
    out: list[AppStatus] = []
    for family, check in CHECKS:
        try:
            out.extend(check(auto_update and family in AUTO_INSTALLABLE))
        except Exception as e:  # a version check must never break the nightly job
            logger.warning(f"{family} app check failed: {e}")
            if log_fn:
                log_fn(f"app check for {family} failed: {e}")
    return out


def summarize(statuses: list[AppStatus]) -> str:
    """One line for the nightly digest, "" when there is nothing to say.

    Silent when everything is current: the digest is a Telegram message, and a
    nightly "all fine" from every subsystem trains people to stop reading it.
    """
    updated = [s for s in statuses if s.state == "updated"]
    outdated = [s for s in statuses if s.state == "outdated"]
    failed = [s for s in statuses if s.state == "failed"]
    parts = []
    if updated:
        parts.append(
            "App updates installed: "
            + ", ".join(f"{s.name} {s.latest}" for s in updated) + "."
        )
    if outdated:
        parts.append(
            f"{len(outdated)} app update(s) waiting on you: "
            + ", ".join(f"{s.name} {s.installed} to {s.latest}" for s in outdated)
            + ". Ask me to update them."
        )
    if failed:
        parts.append(
            "App update failed: " + ", ".join(s.name for s in failed) + "."
        )
    return " ".join(parts)


class AppCheckResult(NamedTuple):
    """Outcome of one out-of-band check round.

    summary: the digest line, "" when there is nothing to say.
    news:    something moved or failed to move, which is worth a ping tonight.
    waiting: apps needing a human. Split out from news because these persist
             night after night — Resolve can sit a version behind for weeks —
             and a nightly repeat of the same line is how a digest gets muted.
             The caller throttles these; see system_update._should_remind_pending.
    """

    summary: str = ""
    news: bool = False
    waiting: tuple[str, ...] = ()


def run_app_update_check(auto_update: bool = False, log_fn=None) -> AppCheckResult:
    """Run every out-of-band check and report what it found.

    auto_update installs only what AUTO_INSTALLABLE allows and only when the
    version move is not a major jump; everything else is reported for a human.
    """
    def log(msg: str):
        if log_fn:
            log_fn(msg)

    statuses = collect(auto_update=auto_update, log_fn=log_fn)
    for s in statuses:
        log(f"app check: {s.name} {s.installed or '?'} -> {s.latest or '?'} [{s.state}]"
            + (f" {s.detail}" if s.detail else ""))
    if not statuses:
        log("app check: nothing installed outside the package manager")
        return AppCheckResult()
    summary = summarize(statuses)
    if not summary:
        log("app check: everything outside the package manager is current")
    return AppCheckResult(
        summary=summary,
        news=any(s.state in ("updated", "failed") for s in statuses),
        waiting=tuple(sorted(s.name for s in statuses if s.state == "outdated")),
    )


def main() -> int:
    """CLI entry point: python utils/app_updates.py [--update]"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Check apps the package manager does not track"
    )
    parser.add_argument("--update", action="store_true",
                        help="install the updates that are safe unattended")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    statuses = collect(auto_update=args.update)
    if not statuses:
        print("Nothing installed outside the package manager.")
        return 0
    width = max(len(s.name) for s in statuses)
    for s in statuses:
        print(f"{s.name:<{width}}  {s.installed or '?':>10} -> {s.latest or '?':<10} "
              f"{s.state}" + (f"  ({s.detail})" if s.detail else ""))
    summary = summarize(statuses)
    if summary:
        print()
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
