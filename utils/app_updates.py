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
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
# `python utils/app_updates.py` puts utils/ on sys.path rather than the repo
# root, so the sibling import below needs the root added first.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import puppeteer_browsers  # noqa: E402

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

# The Codex CLI, the bot's second engine. It arrives two ways: the installer
# runs `npm install -g @openai/codex`, and the reference Mac has the Homebrew
# cask. Neither ever moved by itself. The nightly brew upgrade leaves every
# cask alone (system_update._UPGRADE_CMDS), and on npm is_major_jump would
# hold every release, because Codex numbers each one as a 0.x minor (0.155 to
# 0.159 in the week to 30 Sep 2026). So the cask sat in the digest night after
# night as "waiting on you: codex". check_codex_cli owns it now, and the cask
# list leaves out what is updated here.
CODEX_CASK = "codex"
CODEX_NPM = "@openai/codex"

# The Rive CLI, which the rive skill renders with, and the Rive editor app.
# Both are Homebrew casks on the Mac, and neither moved by itself either: the
# CLI has no updater on a brew install (`rive update` serves only Rive's own
# installer), and the editor's own updater runs only while the app is open,
# which on a machine nobody sits at is never. So the nightly run listed one
# or both as waiting on 10 of the 13 nights from 26 Sep to 8 Oct 2026, and
# each release waited for someone to ask. check_rive_cli and check_rive_editor
# own them now. brew outdated names a tap's cask by its bare token
# ("rive-cli"), so that is the name the cask list leaves out.
RIVE_CLI_CASK = "rive-app/tap/rive-cli"
RIVE_EDITOR_CASK = "rive"
RIVE_EDITOR_APP = Path("/Applications/Rive.app")
CASKS_UPDATED_HERE = frozenset({CODEX_CASK, RIVE_CLI_CASK.rsplit("/", 1)[-1], RIVE_EDITOR_CASK})

# Families the nightly job may install without a human. A CLI moves by
# replacing a file and old versions stay on disk; a GUI app bundle does not.
# Codex is the exception on the first count, since brew and npm both delete
# the old version, which is why it is tried before it is installed. Rive is
# the exception on both: brew deletes the old CLI and the old editor, and the
# editor is an app. So each is tried first, and the editor is replaced only
# while it is closed.
AUTO_INSTALLABLE = frozenset({"claude-code", "codex", "npm", "rive"})

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


def _run(cmd: list[str], timeout: int = 60, merge_stderr: bool = True,
         env: dict | None = None) -> tuple[int, str]:
    """Run a command with no shell. Returns (returncode, stdout+stderr).

    merge_stderr=False returns stdout alone. Callers that *parse* the output
    need that: a tool which prints a warning to stderr would otherwise corrupt
    its own JSON, and the parse failure reads as "nothing to report".
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
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


def _diagram_draws(prefix: Path | None = None) -> tuple[bool, str]:
    """mermaid-cli works: its browser is fetched as this user, and one diagram
    draws through the diagram skill's own script, the path every render takes.
    With a prefix, the copy trial-installed there is the one checked."""
    root = puppeteer_browsers.npm_global_root(prefix)
    ok, why = puppeteer_browsers.ensure_browsers("@mermaid-js/mermaid-cli", root=root)
    if not ok:
        return False, why
    env = None
    if prefix is not None:
        env = dict(os.environ, PATH=f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    script = ROOT / "skills" / "diagram" / "scripts" / "diagram.py"
    with tempfile.TemporaryDirectory(prefix="app_update_check_") as tmp:
        source, out = Path(tmp) / "check.mmd", Path(tmp) / "check.png"
        source.write_text("graph TD\n  A --> B\n", encoding="utf-8")
        rc, text = _run([sys.executable, str(script), str(source), "-o", str(out)], timeout=180, env=env)
        if rc == 0 and out.is_file() and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n":
            return True, ""
    return False, f"a test diagram did not draw: {text[-300:] or f'rc={rc}'}"


# npm's exit code is not proof that these work. Puppeteer inside mermaid-cli
# swallows a failed browser download and exits 0 (utils/puppeteer_browsers.py),
# and every diagram then fails the next morning. An update of one of these is
# tried in a scratch prefix first and installed only if its check passes
# there, then checked again where it lands. A CLI with no entry is taken at
# npm's word.
NPM_PROOF: dict[str, Callable[[Path | None], tuple[bool, str]]] = {
    "@mermaid-js/mermaid-cli": _diagram_draws,
}


def _trial_install(pkg: str, version: str,
                   proof: Callable[[Path | None], tuple[bool, str]]) -> tuple[bool, str]:
    """Install pkg@version into a scratch prefix and run its proof there.

    The live copy stays as it is unless this passes. Putting the old version
    back after a failed update would be no remedy: mermaid-cli takes Puppeteer
    as a peer dependency and npm resolves the newest match on every install,
    a rollback included. Measured: 11.16.0 with Puppeteer 25.3.0, updated to
    11.17.0 and then put back, kept 25.12.0 and the newer Chrome it pins.
    """
    scratch = Path(tempfile.mkdtemp(prefix="app_update_trial_"))
    try:
        rc, out = _run(["npm", "install", "-g", "--prefix", str(scratch), f"{pkg}@{version}"], timeout=600)
        if rc != 0:
            return False, f"the trial install failed: {out[-200:] if out else f'rc={rc}'}"
        return proof(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _npm_global_needs_sudo() -> bool:
    """True where npm's global folder is not writable by this user.

    Node from a Linux distro package or NodeSource keeps it under /usr, owned
    by root, and the nightly job runs as the bot's user: every update there
    failed with EACCES. Homebrew and nvm prefixes belong to the user. A folder
    that does not exist yet is judged by the nearest one that does, since npm
    creates it there.
    """
    if platform.system() == "Darwin":
        return False
    root = puppeteer_browsers.npm_global_root()
    if root is None:
        return False
    while not root.exists() and root != root.parent:
        root = root.parent
    return not os.access(root, os.W_OK)


def _npm_install_live(spec: str) -> tuple[int, str]:
    """npm install -g spec where the live copy is.

    Through sudo when the global folder is root's, with the password the
    installer stored (the same one the package manager upgrade uses). The
    first install of these CLIs is a sudo install too: the skills' own
    messages say so ("Install it with `sudo npm install -g ...`" in
    skills/diagram/scripts/diagram.py).
    Without a stored password, sudo -n: it works under a NOPASSWD rule and
    fails at once otherwise, rather than waiting on a prompt nobody sees.
    An argument list, never a shell string: the version comes from the npm
    registry.

    Under sudo, Puppeteer's install script runs as root and would fetch its
    browsers into root's cache, which nothing reads. mermaid-cli is the skill
    CLI that carries it, and the proof that runs after its install
    (_diagram_draws) fetches the bot's own copy as its user. So the sudo call
    skips that download. The variable is set by env inside the sudo call,
    because sudo resets the environment, and it never enters the bot's own
    environment, where Puppeteer's browsers command would honour it too and
    fetch nothing. A sudo rule that allows npm alone, and not env, refuses
    this call; the stored password covers both.
    """
    cmd = ["npm", "install", "-g", spec]
    if not _npm_global_needs_sudo():
        return _run(cmd, timeout=600)
    from install.sudo import get_sudo_password
    password = get_sudo_password()
    sudo = ["sudo", "-S", "-p", ""] if password else ["sudo", "-n"]
    sudo += ["env", "PUPPETEER_SKIP_DOWNLOAD=true"]
    try:
        r = subprocess.run(sudo + cmd, input=(password + "\n") if password else None,
                           capture_output=True, text=True, timeout=600)
    except (subprocess.TimeoutExpired, OSError) as e:
        return 1, str(e)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 and not password:
        out = (f"npm's global folder is owned by root and no sudo password is stored, "
               f"so the update could not be installed: {out[-200:]}")
    return r.returncode, out


def check_npm_clis(auto_update: bool = False) -> list[AppStatus]:
    """Version state of the global npm CLIs the skills in this repo install."""
    results: list[AppStatus] = []
    for pkg, info in sorted(_npm_outdated_global().items()):
        # Codex has its own check below, which reports it once.
        if pkg in NPM_NEVER_TOUCH or pkg == CODEX_NPM or not isinstance(info, dict):
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
        proof = NPM_PROOF.get(pkg)
        if proof:
            works, why = _trial_install(pkg, latest, proof)
            if not works:
                results.append(AppStatus(pkg, "npm", current, latest, "failed",
                                         f"{latest} failed a trial install, so {current} was kept: {why}"))
                continue
        # An exact version is pinned rather than @latest so the return code is
        # a usable signal: npm either installed that version or failed. (The
        # Claude check cannot rely on rc alone — `claude update` exits 0 having
        # done nothing — so it re-reads the version instead.)
        rc, out = _npm_install_live(f"{pkg}@{latest}")
        if rc != 0:
            results.append(AppStatus(pkg, "npm", current, latest, "failed",
                                     out[-200:] if out else f"rc={rc}"))
            continue
        if proof:
            works, why = proof(None)
            if not works:
                results.append(AppStatus(pkg, "npm", latest, latest, "failed",
                                         f"{latest} passed its trial but not where it was installed: {why}"))
                continue
        results.append(AppStatus(pkg, "npm", latest, latest, "updated", detail))
    return results


# --------------------------------------------------------------------------
# Codex CLI — the bot's second engine, from the Homebrew cask or from npm
# --------------------------------------------------------------------------

CODEX_NAME = "Codex CLI"

# Everything the bot hands `codex exec` on a turn (core/llm.py,
# CodexCLIProvider.complete), with a real value where a flag takes one, and
# `--disable <name>` added for each of CODEX_BOT_FEATURES. clap checks every
# flag and the --sandbox value before it acts on --help, so
# `codex exec <these> --help` exits 0 only on a build that takes the bot's
# command line, and starts no turn. Measured on 0.159.2: 0 for this list, 2
# for an unknown flag or an unknown sandbox mode.
CODEX_BOT_ARGV = (
    "exec", "--json", "--ephemeral", "--skip-git-repo-check",
    "--sandbox", "danger-full-access", "-m", "gpt-6-astra",
    "-c", 'model_reasoning_effort="max"', "--dangerously-bypass-hook-trust",
)
# The names the bot turns off (core.llm._CODEX_DISABLED_FEATURES). An unknown
# name is not caught with the flags above: it aborts the turn later, so
# `codex features list` is asked instead.
CODEX_BOT_FEATURES = ("multi_agent", "multi_agent_v2")


def _brew_env() -> dict:
    """brew's environment for the cask steps: no self-update on the way.

    The nightly run has already refreshed brew, and a refresh between the
    trial and the install could move the cask to a version nobody tried.
    """
    return dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1")


def _brew_cask(token: str) -> dict:
    """brew's record of one cask (`brew info --cask --json=v2`), or {}.

    "version" is what brew would install now and "installed" what it
    installed last, None for a cask it never installed.
    """
    rc, out = _run(["brew", "info", "--cask", "--json=v2", token],
                   timeout=120, merge_stderr=False, env=_brew_env())
    try:
        cask = json.loads(out)["casks"][0] if rc == 0 else {}
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        cask = {}
    return cask if isinstance(cask, dict) else {}


def _cask_version(cask: dict) -> str:
    """The version a cask record offers, or "" when it holds none."""
    version = str(cask.get("version") or "")
    return version if version_tuple(version) and len(version) < 40 else ""


def _linked_from_cask(binary: str, token: str) -> bool:
    """True when this command really points into Caskroom/<token>/."""
    parts = Path(os.path.realpath(binary)).parts
    return any(a == "Caskroom" and b == token for a, b in zip(parts, parts[1:]))


def _brew_fetch(token: str) -> tuple[Path | None, str]:
    """Download the file brew is about to install for a cask: (file, "") or (None, why).

    `brew fetch` puts it in brew's own cache and checks it against the cask's
    SHA-256, and the upgrade after it installs that same file, so the build
    tried from it is byte for byte the one that goes live.
    """
    env = _brew_env()
    rc, out = _run(["brew", "fetch", "--cask", token], timeout=900, env=env)
    if rc != 0:
        return None, f"brew could not download it: {out[-200:] or f'rc={rc}'}"
    rc, path = _run(["brew", "--cache", "--cask", token], timeout=60,
                    merge_stderr=False, env=env)
    download = Path(path) if rc == 0 and path else None
    if download is None or not download.is_file():
        return None, "brew downloaded it but did not say where"
    return download, ""


def _codex_version(binary: str, env: dict | None = None) -> str:
    """What `codex --version` says ("codex-cli 0.159.2" gives "0.159.2"), or ""."""
    rc, out = _run([binary, "--version"], timeout=30, merge_stderr=False, env=env)
    words = out.split() if rc == 0 else []
    return words[-1] if words and version_tuple(words[-1]) else ""


def _codex_source(binary: str) -> str:
    """How this codex was installed: "cask", "npm", or "" for anything else.

    Read off where the command really points. The cask links it to
    Caskroom/codex/<version>/bin/codex, npm to
    <npm root>/@openai/codex/bin/codex.js. A source build, bun, pnpm or the
    standalone installer is reported and never moved, because this check
    would not know how to put it back.
    """
    if _linked_from_cask(binary, CODEX_CASK):
        return "cask"
    real = Path(os.path.realpath(binary))
    root = puppeteer_browsers.npm_global_root()
    if root is not None and Path(os.path.realpath(root / CODEX_NPM)) in real.parents:
        return "npm"
    return ""


def _codex_latest(source: str) -> str:
    """Newest Codex that the install's own source offers, or "".

    Each source is asked for what IT would install. The cask can trail
    GitHub by hours (its bump is a pull request on Homebrew's side), so
    reading GitHub would report an update brew cannot fetch yet.
    """
    if source == "cask":
        version = _cask_version(_brew_cask(CODEX_CASK))
    elif source == "npm":
        rc, out = _run(["npm", "view", CODEX_NPM, "version"], timeout=120,
                       merge_stderr=False)
        version = out.strip() if rc == 0 else ""
    else:
        version = ""
    return version if version_tuple(version) and len(version) < 40 else ""


def _codex_running() -> bool:
    """True while any codex process is alive on the machine.

    A turn keeps starting helpers out of its own install folder (codex-path/rg
    for search, bin/codex-code-mode-host), and an update deletes that folder:
    brew purges the old version and npm replaces the package. So the update
    waits for a night when nobody is mid-turn. This only reads the process
    table and acts on nothing it finds (AGENTS.md, "Never reap by name across
    the machine").
    """
    rc, _out = _run(["pgrep", "-x", "codex"], timeout=15)
    return rc == 0


def _codex_takes_the_bot(binary: str, version: str) -> tuple[bool, str]:
    """Whether the bot's Codex turns would still run on this build.

    The running bot asks a codex build two things once and trusts the answers
    for the life of its process (core/llm.py, _codex_cached_probe): whether
    `exec --help` lists the hook-trust flag, and which names `features list`
    shows. An update swaps the build under those answers, and one that dropped
    either would abort every Codex turn until somebody restarted the bot. So
    a build has to take the bot's whole command line and still list every name
    the bot turns off, or it is not installed.

    Asked with CODEX_HOME in a scratch folder, so a build on trial never opens
    the live sign-in, config or session databases. Measured on 0.159.2: both
    answers are the same with an empty home.
    """
    with tempfile.TemporaryDirectory(prefix="codex_trial_home_") as home:
        env = dict(os.environ, CODEX_HOME=home)
        found = _codex_version(binary, env)
        if found != version:
            return False, f"it reports version {found or 'nothing'}, not {version}"
        argv = [binary, *CODEX_BOT_ARGV]
        for name in CODEX_BOT_FEATURES:
            argv += ["--disable", name]
        rc, out = _run(argv + ["--help"], timeout=60, env=env)
        if rc != 0:
            # clap's "error:" line says why. It is not always the first line:
            # on Linux the scratch home sits under /tmp, and every codex call
            # there opens with "WARNING: ... Refusing to create helper binaries
            # under temporary dir", which is harmless and not the reason
            # (measured on 0.159.2).
            lines = [line.strip() for line in out.splitlines() if line.strip()]
            first = next((line for line in lines if line.startswith("error:")),
                         lines[0] if lines else f"rc={rc}")
            return False, f"it refuses the bot's command line: {first[:200]}"
        rc, out = _run([binary, "features", "list"], timeout=60,
                       merge_stderr=False, env=env)
        if rc != 0:
            return False, f"`codex features list` failed: {out[-200:] or f'rc={rc}'}"
        listed = {line.split()[0] for line in out.splitlines() if line.split()}
        missing = [name for name in CODEX_BOT_FEATURES if name not in listed]
        if missing:
            return False, f"it no longer lists {', '.join(missing)}, which the bot turns off"
    return True, ""


def _codex_cask_trial(version: str) -> tuple[bool, str]:
    """Unpack the download brew is about to install (_brew_fetch) and put it
    to the bot's test."""
    archive, why = _brew_fetch(CODEX_CASK)
    if archive is None:
        return False, why
    scratch = Path(tempfile.mkdtemp(prefix="app_update_trial_"))
    try:
        rc, out = _run(["tar", "-xzf", str(archive), "-C", str(scratch)], timeout=300)
        binary = scratch / "bin" / "codex"
        if rc != 0 or not binary.is_file():
            return False, f"the download did not unpack to bin/codex: {out[-200:] or f'rc={rc}'}"
        return _codex_takes_the_bot(str(binary), version)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def check_codex_cli(auto_update: bool = False) -> list[AppStatus]:
    """Version state of the Codex CLI, updated through whatever installed it.

    Any release that keeps the leading number is installed, which for Codex
    so far is every one, but only after a copy of that exact release has
    taken the bot's command line (_codex_takes_the_bot). The 0.x rule in
    is_major_jump is not applied here on purpose: it would hold every Codex
    release, and asking the build says more about what breaks the bot than
    the version number does.
    """
    binary = shutil.which("codex")
    if not binary:
        return []
    installed = _codex_version(binary)
    if not installed:
        return []
    source = _codex_source(binary)
    if not source:
        return [AppStatus(CODEX_NAME, "codex", installed, "", "unknown",
                          "installed some other way than Homebrew or npm, so left alone")]
    where = "Homebrew" if source == "cask" else "npm"
    latest = _codex_latest(source)
    if not latest:
        return [AppStatus(CODEX_NAME, "codex", installed, "", "unknown",
                          f"could not read the newest version from {where}")]
    if not is_newer(latest, installed):
        return [AppStatus(CODEX_NAME, "codex", installed, latest, "current")]
    if not auto_update:
        return [AppStatus(CODEX_NAME, "codex", installed, latest, "outdated")]
    if version_tuple(latest)[0] != version_tuple(installed)[0]:
        return [AppStatus(CODEX_NAME, "codex", installed, latest, "outdated",
                          "major version, worth a look before installing")]
    in_use = AppStatus(CODEX_NAME, "codex", installed, latest, "outdated",
                       "Codex was in use, so the update waits for a quiet night")
    if _codex_running():
        return [in_use]

    if source == "cask":
        works, why = _codex_cask_trial(latest)
    else:
        works, why = _trial_install(
            CODEX_NPM, latest,
            lambda prefix: _codex_takes_the_bot(str(prefix / "bin" / "codex"), latest))
    if not works:
        return [AppStatus(CODEX_NAME, "codex", installed, latest, "failed",
                          f"{latest} failed its trial, so {installed} was kept: {why}")]
    # Asked again, because a turn can start while the trial downloads.
    if _codex_running():
        return [in_use]

    if source == "cask":
        rc, out = _run(["brew", "upgrade", "--cask", CODEX_CASK], timeout=900, env=_brew_env())
    else:
        rc, out = _npm_install_live(f"{CODEX_NPM}@{latest}")
    if rc != 0:
        return [AppStatus(CODEX_NAME, "codex", installed, latest, "failed",
                          out[-200:] if out else f"rc={rc}")]
    now = _codex_version(binary)
    if now != latest:
        return [AppStatus(CODEX_NAME, "codex", now or installed, latest, "failed",
                          f"{where} finished, but codex reports {now or 'nothing'}, not {latest}")]
    works, why = _codex_takes_the_bot(binary, latest)
    if not works:
        return [AppStatus(CODEX_NAME, "codex", latest, latest, "failed",
                          f"{latest} passed its trial but not where it was installed: {why}")]
    return [AppStatus(CODEX_NAME, "codex", latest, latest, "updated")]


# --------------------------------------------------------------------------
# Rive — the CLI the rive skill renders with, and the Rive editor app, both
# Homebrew casks on the Mac
# --------------------------------------------------------------------------

RIVE_CLI_NAME = "Rive CLI"
RIVE_EDITOR_NAME = "Rive editor"

# Rive's Apple developer team. Every CLI from 1.1.1 to 1.5.0 and every editor
# from 0.8.5940 to 0.9.157 installed here was signed by it and notarized by
# Apple. A build signed by anyone else is not installed.
RIVE_TEAM_ID = "NJ3JMFUNS9"

# How the rive skill's scripts run the CLI (rivelib.rive_env): no usage
# analytics, which nobody on this machine agreed to, and no terminal UI.
RIVE_QUIET = {"RIVE_ANALYTICS": "off", "RIVE_NO_TUI": "1", "NO_COLOR": "1"}

RIVE_DOCTOR = ROOT / "skills" / "rive" / "scripts" / "rive_doctor.py"


def _rive_version(binary: str) -> str:
    """What `rive --version` says ("rive 1.5.0" gives "1.5.0"), or ""."""
    rc, out = _run([binary, "--version"], timeout=30, merge_stderr=False,
                   env=dict(os.environ, **RIVE_QUIET))
    words = out.split() if rc == 0 else []
    return words[-1] if words and version_tuple(words[-1]) else ""


def _bundle_version(app: Path) -> str:
    """The version an app bundle carries, or "" when there is no bundle."""
    try:
        with open(app / "Contents" / "Info.plist", "rb") as fh:
            version = str(plistlib.load(fh).get("CFBundleShortVersionString", ""))
    except (OSError, plistlib.InvalidFileException, ValueError):
        return ""
    return version if version_tuple(version) else ""


def _signed_by_rive(path: Path) -> tuple[bool, str]:
    """Whether an app or a bare binary carries Rive's signature and Apple's
    notarization: the checks each Rive update by hand was proved with.

    codesign proves the signature is whole and names the team, and Gatekeeper
    (spctl) proves Apple notarized it, which is what lets a downloaded copy
    run at all. Gatekeeper's execute check refuses anything that is not an
    app, so a bare binary is asked as a file against its own signature.
    """
    rc, out = _run(["codesign", "--verify", "--deep", "--strict", str(path)], timeout=300)
    if rc != 0:
        return False, f"its signature does not hold: {out[-200:] or f'rc={rc}'}"
    _rc, out = _run(["codesign", "-dv", "--verbose=2", str(path)], timeout=60)
    team = re.search(r"(?m)^TeamIdentifier=(\S+)", out)
    if not team or team.group(1) != RIVE_TEAM_ID:
        return False, (f"it is signed by {team.group(1) if team else 'no team'}, "
                       f"not by Rive ({RIVE_TEAM_ID})")
    if path.suffix == ".app":
        assess = ["spctl", "--assess", "--type", "execute", str(path)]
    else:
        assess = ["spctl", "--assess", "--type", "open",
                  "--context", "context:primary-signature", str(path)]
    rc, out = _run(assess, timeout=120)
    if rc != 0:
        return False, f"Gatekeeper refuses it: {out[-200:] or f'rc={rc}'}"
    return True, ""


def _rive_cli_works(binary: str, version: str) -> tuple[bool, str]:
    """Whether the rive skill would still work on this build of the CLI.

    The skill's own doctor is the test, the one its SKILL.md asks for after
    every CLI update: every flag the skill's scripts pass must still be in
    --help, a bundled sample must build, inspect clean and draw, and a click
    must still cost the 3 frames the render timeline is built on. The CLI is
    a technical preview whose flags have moved before (--frame became
    --advance, and 1.4.0 dropped --immediate). RIVE_SKILL_CLI points the
    doctor at this build, the way the skill tries a second version beside
    the live one. The build also has to be the version meant, signed by
    Rive.

    The doctor's note that a version has not been compared frame by frame
    with the ones the skill was measured on is a note, not a failure.
    Measured 8 Oct 2026: it passes 1.5.0 unpacked in a scratch folder in
    0.6 s.
    """
    found = _rive_version(binary)
    if found != version:
        return False, f"it reports version {found or 'nothing'}, not {version}"
    if platform.system() == "Darwin":
        signed, why = _signed_by_rive(Path(binary))
        if not signed:
            return False, why
    rc, out = _run([sys.executable, str(RIVE_DOCTOR), "--json"], timeout=600,
                   merge_stderr=False,
                   env=dict(os.environ, **RIVE_QUIET, RIVE_SKILL_CLI=binary))
    try:
        report = json.loads(out)
    except json.JSONDecodeError:
        report = None
    if not isinstance(report, dict):
        return False, f"the rive skill's doctor gave no report (rc={rc})"
    failed = [f"{r.get('check')}: {r.get('detail')}" for r in report.get("results") or []
              if isinstance(r, dict) and not r.get("ok") and r.get("fatal")]
    if rc != 0 or report.get("ok") is not True or failed:
        return False, f"the rive skill's doctor fails it: {'; '.join(failed)[:300] or f'rc={rc}'}"
    return True, ""


def _rive_cli_trial(version: str) -> tuple[bool, str]:
    """Unpack the CLI brew is about to install (_brew_fetch) and run the
    skill's doctor on it.

    Unpacked whole: the binary's docs/ and samples/ sit beside it, the only
    place `rive docs` and `rive samples` look, and the doctor builds one of
    those samples.
    """
    archive, why = _brew_fetch(RIVE_CLI_CASK)
    if archive is None:
        return False, why
    scratch = Path(tempfile.mkdtemp(prefix="app_update_trial_"))
    try:
        rc, out = _run(["tar", "-xzf", str(archive), "-C", str(scratch)], timeout=300)
        binary = scratch / "rive"
        if rc != 0 or not binary.is_file():
            return False, f"the download did not unpack to rive: {out[-200:] or f'rc={rc}'}"
        return _rive_cli_works(str(binary), version)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _rive_cli_busy() -> bool:
    """True while the Rive CLI or one of the rive skill's scripts is running.

    A render starts the CLI once a frame and solves alpha and encodes in
    between, so in the middle of one there can be moments with no rive
    process at all; the skill's script driving it says more. The upgrade
    deletes the folder the CLI lives in, so a render running across it would
    fail on the frame captured while the link is missing, or finish with
    frames from two versions, whose edges can differ (1.3.0 against 1.2.0,
    skills/rive/references/rendering.md). Reads the process table only and
    acts on nothing it finds (AGENTS.md, "Never reap by name across the
    machine").
    """
    for probe in (["pgrep", "-x", "rive"], ["pgrep", "-f", "skills/rive/scripts/"]):
        rc, _out = _run(probe, timeout=15)
        if rc == 0:
            return True
    return False


def check_rive_cli(auto_update: bool = False) -> list[AppStatus]:
    """Version state of the Rive CLI, updated through its Homebrew cask.

    Any release that keeps the leading number is installed, once a copy of
    that exact release has passed the rive skill's doctor (_rive_cli_works).
    A CLI from Rive's own installer (~/.rive/bin, the Linux route) is
    reported and never moved.
    """
    binary = shutil.which("rive")
    if not binary:
        return []
    installed = _rive_version(binary)
    if not installed:
        return []
    if not _linked_from_cask(binary, RIVE_CLI_CASK.rsplit("/", 1)[-1]):
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, "", "unknown",
                          "installed some other way than Homebrew, so left alone")]
    latest = _cask_version(_brew_cask(RIVE_CLI_CASK))
    if not latest:
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, "", "unknown",
                          "could not read the newest version from Homebrew")]
    if not is_newer(latest, installed):
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, latest, "current")]
    if not auto_update:
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, latest, "outdated")]
    if version_tuple(latest)[0] != version_tuple(installed)[0]:
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, latest, "outdated",
                          "major version, worth a look before installing")]
    in_use = AppStatus(RIVE_CLI_NAME, "rive", installed, latest, "outdated",
                       "Rive was rendering, so the update waits for a quiet night")
    if _rive_cli_busy():
        return [in_use]

    works, why = _rive_cli_trial(latest)
    if not works:
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, latest, "failed",
                          f"{latest} failed its trial, so {installed} was kept: {why}")]
    # Asked again, because a render can start while the trial runs.
    if _rive_cli_busy():
        return [in_use]

    rc, out = _run(["brew", "upgrade", "--cask", RIVE_CLI_CASK], timeout=900, env=_brew_env())
    if rc != 0:
        return [AppStatus(RIVE_CLI_NAME, "rive", installed, latest, "failed",
                          out[-200:] if out else f"rc={rc}")]
    now = _rive_version(binary)
    if now != latest:
        return [AppStatus(RIVE_CLI_NAME, "rive", now or installed, latest, "failed",
                          f"Homebrew finished, but rive reports {now or 'nothing'}, not {latest}")]
    works, why = _rive_cli_works(binary, latest)
    if not works:
        return [AppStatus(RIVE_CLI_NAME, "rive", latest, latest, "failed",
                          f"{latest} passed its trial but not where it was installed: {why}")]
    return [AppStatus(RIVE_CLI_NAME, "rive", latest, latest, "updated")]


def _rive_editor_good(app: Path, version: str) -> tuple[bool, str]:
    """Whether this Rive.app is the version meant, signed by Rive and
    notarized.

    That is all that can be asked of the editor unattended. It does nothing
    until a person signs in at the screen, so it is never opened here.
    """
    if not app.is_dir():
        return False, f"there is no {app.name} in it"
    found = _bundle_version(app)
    if found != version:
        return False, f"it reports version {found or 'nothing'}, not {version}"
    return _signed_by_rive(app)


def _rive_editor_trial(version: str) -> tuple[bool, str]:
    """Open the disk image brew is about to install from (_brew_fetch) and
    check the app in it.

    Attached read-only and out of sight, and never launched. Measured 8 Oct
    2026 on 0.9.157 (93 MB): attach under 2 s, the checks 2 s, detach 0.1 to
    11 s.
    """
    dmg, why = _brew_fetch(RIVE_EDITOR_CASK)
    if dmg is None:
        return False, why
    mount = Path(tempfile.mkdtemp(prefix="app_update_trial_"))
    rc, out = _run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
                    "-mountpoint", str(mount), str(dmg)], timeout=300)
    try:
        if rc != 0:
            return False, f"its disk image would not open: {out[-200:] or f'rc={rc}'}"
        return _rive_editor_good(mount / RIVE_EDITOR_APP.name, version)
    finally:
        if rc == 0 and _run(["hdiutil", "detach", str(mount)], timeout=120)[0] != 0:
            _run(["hdiutil", "detach", "-force", str(mount)], timeout=120)
        try:
            mount.rmdir()  # empty once detached; never deletes into an image
        except OSError:
            pass


def _rive_editor_open() -> bool:
    """True while anything runs out of the Rive editor's app bundle.

    The upgrade swaps the bundle out from under it, and an open file with
    unsaved work would go with it. While the editor is open its own updater
    runs anyway. Reads the process table only.
    """
    rc, _out = _run(["pgrep", "-f", f"{RIVE_EDITOR_APP}/Contents/"], timeout=15)
    return rc == 0


def check_rive_editor(auto_update: bool = False) -> list[AppStatus]:
    """Version state of the Rive editor, updated through its Homebrew cask.

    The one application installed unattended, and on purpose: it is a 93 MB
    download from Rive's own server behind no form, it is what Rive's own
    updater would install the moment anyone opened the app, and nobody here
    opens it. A copy of the exact disk image is checked first
    (_rive_editor_trial), and the swap waits for a night the app is closed.

    The brew step leaves app casks alone because a Blender upgrade hung in
    the nightly job in July 2026 and was killed half way
    (system_update._UPGRADE_CMDS). This one has run from inside the bot's
    own process tree, the one the nightly job runs in, on 28 and 29 Sep and
    1 and 4 Oct 2026, and through this check on 8 Oct; none hung. It still
    has a time limit, and a failed one is checked for an app left missing.
    """
    if platform.system() != "Darwin" or not shutil.which("brew"):
        return []
    installed = _bundle_version(RIVE_EDITOR_APP)
    cask = _brew_cask(RIVE_EDITOR_CASK)
    latest = _cask_version(cask)
    if not installed:
        if not cask.get("installed"):
            return []
        # brew still lists it, so this is an update that died half way, or an
        # app thrown away by hand. Either way the nightly would go quiet.
        return [AppStatus(RIVE_EDITOR_NAME, "rive", "", latest, "failed",
                          f"Homebrew lists Rive {cask.get('installed')} but {RIVE_EDITOR_APP} "
                          "is gone: `brew reinstall --cask rive` puts it back, "
                          "`brew uninstall --cask rive` forgets it")]
    if not cask.get("installed"):
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, "", "unknown",
                          "installed without Homebrew, so its own updater moves it "
                          "whenever it is open")]
    if not latest:
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, "", "unknown",
                          "could not read the newest version from Homebrew")]
    if not is_newer(latest, installed):
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, latest, "current")]
    if not auto_update:
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, latest, "outdated")]
    if version_tuple(latest)[0] != version_tuple(installed)[0]:
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, latest, "outdated",
                          "major version, worth a look before installing")]
    in_use = AppStatus(RIVE_EDITOR_NAME, "rive", installed, latest, "outdated",
                       "the editor was open, so the update waits for a night it is closed")
    if _rive_editor_open():
        return [in_use]

    works, why = _rive_editor_trial(latest)
    if not works:
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, latest, "failed",
                          f"{latest} failed its trial, so {installed} was kept: {why}")]
    if _rive_editor_open():
        return [in_use]

    rc, out = _run(["brew", "upgrade", "--cask", RIVE_EDITOR_CASK], timeout=900, env=_brew_env())
    if rc != 0:
        gone = "" if RIVE_EDITOR_APP.is_dir() else (
            f"; {RIVE_EDITOR_APP} is gone, `brew reinstall --cask rive` puts it back")
        return [AppStatus(RIVE_EDITOR_NAME, "rive", installed, latest, "failed",
                          (out[-200:] if out else f"rc={rc}") + gone)]
    now = _bundle_version(RIVE_EDITOR_APP)
    if now != latest:
        return [AppStatus(RIVE_EDITOR_NAME, "rive", now or installed, latest, "failed",
                          f"Homebrew finished, but Rive.app reports {now or 'nothing'}, not {latest}")]
    works, why = _rive_editor_good(RIVE_EDITOR_APP, latest)
    if not works:
        return [AppStatus(RIVE_EDITOR_NAME, "rive", latest, latest, "failed",
                          f"{latest} passed its trial but not where it was installed: {why}")]
    return [AppStatus(RIVE_EDITOR_NAME, "rive", latest, latest, "updated")]


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
    ("codex", check_codex_cli),
    # Two entries under one family, so a check that throws cannot take the
    # other Rive app down with it.
    ("rive", check_rive_cli),
    ("rive", check_rive_editor),
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
