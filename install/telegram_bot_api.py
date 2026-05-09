#!/usr/bin/env python3
"""
Local Telegram Bot API server installer.

Builds the official `telegram-bot-api` (https://github.com/tdlib/telegram-bot-api)
from source and registers it as a system service. Used to lift Telegram's
default 50MB upload / 20MB download caps so the bot can send and receive
files up to ~2 GB.

Opt-in. Skipped unless `telegram_local_api_enabled` is set on the wizard
config. Idempotent: re-runs reuse an existing build if the binary is
already present.

Layout (relative to repo_dir):
    data/telegram-bot-api/repo/                 git clone
    data/telegram-bot-api/repo/build/           cmake build dir
    data/telegram-bot-api/repo/build/telegram-bot-api  built binary
    data/telegram-bot-api/state/                runtime data dir (--dir=)
    data/telegram-bot-api/state/api.log         server log

Credentials live in the main `.env` as TELEGRAM_API_ID and
TELEGRAM_API_HASH; the systemd unit / launchd plist sources that file.

Service runs as the orchestrator user in multi-user mode, otherwise as the
current user. A single server instance is shared across all slots — slot
accounts only need to talk to localhost:8081, not read the data dir.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from install.os_detect import OSInfo
from install.sudo import sudo_run as _shared_sudo_run

BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

REPO_URL = "https://github.com/tdlib/telegram-bot-api.git"
DEFAULT_PORT = 8081

# Build dependencies per package manager. Names verified against actual
# package indexes for each distro.
LINUX_BUILD_DEPS = {
    "apt": ["cmake", "gperf", "libssl-dev", "zlib1g-dev", "g++", "git", "make"],
    "dnf": ["cmake", "gperf", "openssl-devel", "zlib-devel", "gcc-c++", "git", "make"],
    "yum": ["cmake", "gperf", "openssl-devel", "zlib-devel", "gcc-c++", "git", "make"],
    "pacman": ["cmake", "gperf", "openssl", "zlib", "gcc", "make", "git"],
    "zypper": ["cmake", "gperf", "libopenssl-devel", "zlib-devel", "gcc-c++", "git", "make"],
    "apk": ["cmake", "gperf", "openssl-dev", "zlib-dev", "g++", "git", "make"],
}
LINUX_INSTALL_TEMPLATES = {
    "apt": "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkgs}",
    "dnf": "dnf install -y {pkgs}",
    "yum": "yum install -y {pkgs}",
    "pacman": "pacman -S --noconfirm --needed {pkgs}",
    "zypper": "zypper install -y {pkgs}",
    "apk": "apk add {pkgs}",
}
MACOS_BUILD_DEPS = ["cmake", "gperf", "openssl@3", "zlib"]


def info(msg: str) -> None:
    print(f"{BLUE}[TBA]{NC} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")


def validate_credentials(api_id: str, api_hash: str) -> tuple[bool, str]:
    """Validate api_id / api_hash format (does not check Telegram-side validity)."""
    if not api_id or not api_id.strip().isdigit():
        return False, "api_id must be a non-empty positive integer"
    api_hash = (api_hash or "").strip()
    if len(api_hash) != 32:
        return False, "api_hash must be exactly 32 characters (32-char hex string)"
    if not all(c in "0123456789abcdefABCDEF" for c in api_hash):
        return False, "api_hash must contain only hex digits (0-9, a-f, A-F)"
    return True, ""


def paths_for(repo_dir: Path) -> dict:
    """Return canonical paths used by this module. Pure function for testing."""
    work_dir = Path(repo_dir) / "data" / "telegram-bot-api"
    return {
        "work_dir": work_dir,
        "repo_path": work_dir / "repo",
        "build_dir": work_dir / "repo" / "build",
        "binary_path": work_dir / "repo" / "build" / "telegram-bot-api",
        "state_dir": work_dir / "state",
        "log_path": work_dir / "state" / "api.log",
    }


def _which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def _install_linux_build_deps(os_info: OSInfo, password: Optional[str]) -> bool:
    """Install C++ build deps via the detected package manager."""
    mgr = os_info.package_manager
    if not mgr or mgr not in LINUX_BUILD_DEPS:
        warn(
            f"No supported package manager (detected: {mgr or 'none'}). "
            "Cannot install telegram-bot-api build deps automatically."
        )
        return False
    pkgs = " ".join(LINUX_BUILD_DEPS[mgr])
    cmd_tpl = LINUX_INSTALL_TEMPLATES[mgr]
    info(f"Installing telegram-bot-api build deps via {mgr}: {pkgs}")
    result = _shared_sudo_run(cmd_tpl.format(pkgs=pkgs), password=password)
    if result.returncode != 0:
        warn(f"Build dep install reported non-zero exit: {result.stderr[:200]}")
        warn("Will continue and rely on PATH probes — some packages may already be present.")
    return True


def _install_macos_build_deps(os_info: OSInfo) -> bool:
    """Install C++ build deps via Homebrew. Skips packages already present."""
    brew = os_info.brew_path or _which("brew") or "brew"
    if not Path(brew).exists() and not _which("brew"):
        error("Homebrew not found. Run the main provisioner first.")
        return False
    for pkg in MACOS_BUILD_DEPS:
        check = subprocess.run(
            [brew, "list", pkg],
            capture_output=True, text=True, timeout=30,
        )
        if check.returncode == 0:
            ok(f"{pkg} already installed")
            continue
        info(f"brew install {pkg}")
        result = subprocess.run(
            [brew, "install", pkg],
            timeout=1800,
        )
        if result.returncode != 0:
            warn(f"brew install {pkg} returned non-zero. Will continue.")
    return True


def _build_deps_present(os_info: OSInfo) -> tuple[bool, list[str]]:
    """Probe whether the necessary toolchain binaries are on PATH.

    Returns (ok, missing). This is a binary-level check that catches the
    case where pkg install reported success but PATH was not refreshed.
    """
    required = ["git", "cmake", "make", "gperf"]
    if os_info.os_type == "linux":
        required.append("g++")
    else:
        required.extend(["clang++", "clang"])
    missing = [b for b in required if not _which(b) and not _which(b.replace("++", ""))]
    return (not missing, missing)


def _clone_repo(repo_path: Path) -> bool:
    """Clone tdlib/telegram-bot-api with submodules. Idempotent."""
    if repo_path.exists() and (repo_path / ".git").exists():
        ok(f"Repo already cloned at {repo_path}")
        return True
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    info(f"Cloning {REPO_URL} (with submodules) — this can take a few minutes")
    result = subprocess.run(
        ["git", "clone", "--recursive", "--depth", "1", REPO_URL, str(repo_path)],
        timeout=1200,
    )
    if result.returncode != 0:
        error("git clone failed")
        return False
    return True


def _cmake_build(repo_path: Path, build_dir: Path,
                 binary_path: Path, os_info: OSInfo) -> bool:
    """Run cmake configure + build. Streams output (long-running)."""
    if binary_path.exists():
        ok(f"Binary already built at {binary_path}")
        return True
    build_dir.mkdir(parents=True, exist_ok=True)

    extra_cmake_args: list[str] = []
    # macOS: tell cmake where Homebrew put openssl. brew --prefix output is
    # the canonical resolver (works on both /usr/local and /opt/homebrew).
    if os_info.os_type == "macos":
        brew = os_info.brew_path or _which("brew") or "brew"
        try:
            r = subprocess.run(
                [brew, "--prefix", "openssl@3"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                extra_cmake_args.append(
                    f"-DOPENSSL_ROOT_DIR={r.stdout.strip()}"
                )
        except Exception:
            pass

    info("Running cmake configure...")
    cfg = subprocess.run(
        ["cmake", "-DCMAKE_BUILD_TYPE=Release", *extra_cmake_args, ".."],
        cwd=str(build_dir),
        timeout=600,
    )
    if cfg.returncode != 0:
        error("cmake configure failed")
        return False

    cores = max(1, (os.cpu_count() or 1) - 1)
    info(
        f"Running cmake build (-j{cores}) — "
        "this can take 30-90 min on slower machines"
    )
    bld = subprocess.run(
        ["cmake", "--build", ".", "--target", "telegram-bot-api",
         "-j", str(cores)],
        cwd=str(build_dir),
        timeout=10800,  # 3h hard cap
    )
    if bld.returncode != 0:
        error("cmake build failed")
        return False

    if not binary_path.exists():
        error(f"Build reported success but binary missing at {binary_path}")
        return False
    ok(f"Built {binary_path} ({binary_path.stat().st_size // (1024*1024)} MB)")
    return True


def render_systemd_unit(template: str, *, user: str, env_file: Path,
                        binary_path: Path, state_dir: Path,
                        port: int = DEFAULT_PORT) -> str:
    """Substitute placeholders in the systemd unit template."""
    return (template
            .replace("{{USER}}", user)
            .replace("{{ENV_FILE}}", str(env_file))
            .replace("{{BINARY}}", str(binary_path))
            .replace("{{STATE_DIR}}", str(state_dir))
            .replace("{{PORT}}", str(port)))


def render_launchd_plist(template: str, *, user: str, env_file: Path,
                         binary_path: Path, state_dir: Path,
                         port: int = DEFAULT_PORT) -> str:
    """Substitute placeholders in the launchd plist template."""
    return (template
            .replace("{{USER}}", user)
            .replace("{{ENV_FILE}}", str(env_file))
            .replace("{{BINARY}}", str(binary_path))
            .replace("{{STATE_DIR}}", str(state_dir))
            .replace("{{PORT}}", str(port)))


def _ensure_state_dir_owned(state_dir: Path, user: str,
                            multiuser_enabled: bool,
                            password: Optional[str]) -> bool:
    """Create state_dir and ensure the runtime user owns it with mode 0700.

    The daemon runs as ``user`` and writes ``api.log`` plus (on macOS) the
    launchd-redirected ``launchd-stdout.log`` / ``launchd-stderr.log`` here.
    If the directory was created by the install user but the daemon runs as
    a different user (the multi-user case where the daemon runs as
    ``mom_orchestrator``), launchd fails with EX_CONFIG (78) before the
    binary even starts, and systemd fails the first write to api.log.

    Single-user mode: install user already owns the dir from mkdir, so we
    only tighten the mode to 0o700. No sudo needed.

    Multi-user mode: ``user`` is the orchestrator account. Use sudo via
    ``install.multiuser.set_owner`` / ``set_perms`` to chown + chmod.
    """
    state_dir.mkdir(parents=True, exist_ok=True)

    if multiuser_enabled:
        try:
            from install.multiuser import set_owner, set_perms
        except ImportError as exc:
            error(
                "install.multiuser unavailable; cannot chown state_dir "
                f"to {user}: {exc}"
            )
            return False
        if not set_owner(state_dir, user, password=password, recursive=True):
            error(
                f"Failed to chown {state_dir} to {user}. The daemon will "
                "fail to start with EX_CONFIG (78) until this is fixed."
            )
            return False
        if not set_perms(state_dir, 0o700, password=password):
            error(f"Failed to chmod 0700 {state_dir}")
            return False
        return True

    try:
        os.chmod(state_dir, 0o700)
    except OSError as exc:
        warn(f"chmod 0700 {state_dir} failed: {exc}")
    return True


def _register_systemd_service(repo_dir: Path, *,
                              binary_path: Path, state_dir: Path,
                              env_file: Path,
                              user: str,
                              password: Optional[str]) -> bool:
    template_path = (Path(__file__).parent / "templates"
                     / "telegram-bot-api.service")
    if not template_path.exists():
        error(f"Service template not found: {template_path}")
        return False

    content = render_systemd_unit(
        template_path.read_text(encoding="utf-8"),
        user=user, env_file=env_file,
        binary_path=binary_path, state_dir=state_dir,
    )
    service_path = "/etc/systemd/system/telegram-bot-api.service"
    fd, tmp_name = tempfile.mkstemp(
        suffix=".service", prefix="telegram_bot_api_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        cp = _shared_sudo_run(
            f"cp {shlex.quote(tmp_name)} {service_path}", password=password
        )
        if cp.returncode != 0:
            error(f"Failed to install service file: {cp.stderr[:200]}")
            return False
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    _shared_sudo_run("systemctl daemon-reload", password=password)
    _shared_sudo_run(
        "systemctl enable telegram-bot-api", password=password
    )
    start = _shared_sudo_run(
        "systemctl start telegram-bot-api", password=password
    )
    if start.returncode != 0:
        warn(
            "Service did not start cleanly. "
            "Check: sudo systemctl status telegram-bot-api"
        )
    else:
        ok(f"Systemd service installed at {service_path}")
    return True


def _register_launchd_service(repo_dir: Path, *,
                              binary_path: Path, state_dir: Path,
                              env_file: Path,
                              user: str,
                              password: Optional[str]) -> bool:
    template_path = (Path(__file__).parent / "templates"
                     / "com.telegram-bot-api.plist")
    if not template_path.exists():
        error(f"Plist template not found: {template_path}")
        return False

    content = render_launchd_plist(
        template_path.read_text(encoding="utf-8"),
        user=user, env_file=env_file,
        binary_path=binary_path, state_dir=state_dir,
    )
    daemon_path = "/Library/LaunchDaemons/com.telegram-bot-api.plist"
    fd, tmp_name = tempfile.mkstemp(
        suffix=".plist", prefix="telegram_bot_api_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        cp = _shared_sudo_run(
            f"cp {shlex.quote(tmp_name)} {daemon_path}", password=password
        )
        if cp.returncode != 0:
            error(f"Failed to install plist: {cp.stderr[:200]}")
            return False
        _shared_sudo_run(f"chmod 644 {daemon_path}", password=password)
        _shared_sudo_run(
            f"chown root:wheel {daemon_path}", password=password
        )
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    # Unload old, then load fresh (works on both legacy and bootstrap launchctl)
    _shared_sudo_run(
        f"launchctl unload {daemon_path}", password=password, timeout=10
    )
    load = _shared_sudo_run(
        f"launchctl load -w {daemon_path}", password=password, timeout=10
    )
    if load.returncode != 0:
        warn(f"launchctl load returned non-zero: {load.stderr[:200]}")
        warn(
            f"Try: sudo launchctl bootstrap system {daemon_path}"
        )
    else:
        ok(f"LaunchDaemon installed at {daemon_path}")
    return True


def setup_telegram_bot_api(repo_dir: Path, config: dict, os_info: OSInfo,
                           password: Optional[str]) -> bool:
    """Top-level entry point. Build + register service. Idempotent.

    Returns True on success, False on any unrecoverable failure. Caller
    should treat False as a non-fatal "feature unavailable" signal — the
    bot still works without the local API, just with Telegram's default
    50 MB upload cap.
    """
    if not config.get("telegram_local_api_enabled"):
        return True  # opt-out, nothing to do

    api_id = str(config.get("telegram_api_id", "")).strip()
    api_hash = str(config.get("telegram_api_hash", "")).strip()
    valid, reason = validate_credentials(api_id, api_hash)
    if not valid:
        error(f"Invalid Telegram API credentials: {reason}")
        return False

    paths = paths_for(repo_dir)

    # Ensure deps. We only install if the binary doesn't already exist;
    # rebuild paths skip this entirely for speed.
    if not paths["binary_path"].exists():
        if os_info.os_type == "linux":
            _install_linux_build_deps(os_info, password)
        elif os_info.os_type == "macos":
            _install_macos_build_deps(os_info)
        else:
            error(f"Unsupported OS for telegram-bot-api: {os_info.os_type}")
            return False

        deps_ok, missing = _build_deps_present(os_info)
        if not deps_ok:
            error(
                "Missing build tools after dep install: "
                f"{', '.join(missing)}"
            )
            error("Skipping telegram-bot-api — bot will work without it.")
            return False

        if not _clone_repo(paths["repo_path"]):
            return False

        if not _cmake_build(paths["repo_path"], paths["build_dir"],
                            paths["binary_path"], os_info):
            return False
    else:
        ok(f"Reusing existing build at {paths['binary_path']}")

    # Service registration
    env_file = repo_dir / ".env"
    user = (config.get("multiuser_orchestrator_user")
            if config.get("multiuser_enabled")
            else os.environ.get("USER") or "")
    if not user:
        # Fallback for environments without USER set
        try:
            import getpass
            user = getpass.getuser()
        except Exception:
            user = "root"

    multiuser_enabled = bool(config.get("multiuser_enabled"))
    if not _ensure_state_dir_owned(
        paths["state_dir"], user, multiuser_enabled, password,
    ):
        return False

    if os_info.os_type == "linux":
        ok2 = _register_systemd_service(
            repo_dir,
            binary_path=paths["binary_path"],
            state_dir=paths["state_dir"],
            env_file=env_file,
            user=user,
            password=password,
        )
    else:
        ok2 = _register_launchd_service(
            repo_dir,
            binary_path=paths["binary_path"],
            state_dir=paths["state_dir"],
            env_file=env_file,
            user=user,
            password=password,
        )

    if ok2:
        ok(
            "Local Bot API server is running on "
            f"http://localhost:{DEFAULT_PORT}"
        )
        ok(f"Data dir: {paths['state_dir']}")
    return ok2
