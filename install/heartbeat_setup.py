"""MyOldMachine down-alert installer (external heartbeat).

`utils/heartbeat.py` has always been able to ping an uptime monitor, but
nothing scheduled it: the user had to hand-write a systemd unit from
docs/heartbeat.md or park a job in the bot's own scheduler. This module is the
missing half. It asks for a ping URL, installs the schedule for the platform
(a systemd service + timer on Linux, a launchd agent on macOS), and sends one
real ping so the alert path is proven rather than assumed.

The schedule is gated with `--require-service`, so the pings stop when the bot
stops and the monitor reports a dead bot, not only a dead machine.
"""

from __future__ import annotations

import getpass
import os
import platform
import shlex
import tempfile
from pathlib import Path
from urllib.parse import urlparse

REPO_DIR = Path(__file__).parent.parent

# Names the bot registers itself under (install/service.py).
BOT_SYSTEMD_SERVICE = "myoldmachine"
BOT_LAUNCHD_LABEL = "com.myoldmachine.bot"


def bot_service_name() -> str:
    """The systemd unit the bot actually runs under.

    Custom installs rename it and point SERVICE_NAME at the new name; that is
    already how core.updater.restart_service finds it. Gating on the wrong
    name would make the ping skip forever on such a host.
    """
    return (os.environ.get("SERVICE_NAME") or "").strip() or BOT_SYSTEMD_SERVICE

# Our own identities.
SYSTEMD_UNIT_NAME = "myoldmachine-heartbeat"
SYSTEMD_UNIT_PATH = f"/etc/systemd/system/{SYSTEMD_UNIT_NAME}.service"
SYSTEMD_TIMER_PATH = f"/etc/systemd/system/{SYSTEMD_UNIT_NAME}.timer"
LAUNCH_AGENT_LABEL = "com.myoldmachine.heartbeat"

DEFAULT_INTERVAL_MIN = 2

# Output formatting — mirrors install/miniapp_setup.py / service.py palette.
BOLD = "\033[1m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"


def info(msg: str) -> None:
    print(f"{BLUE}[HB]{NC} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")


# ─── State helpers ───────────────────────────────────────────────────


def _launch_agent_plist_path() -> Path:
    """Path to the macOS LaunchAgent plist for the heartbeat."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def is_heartbeat_configured(repo_dir: Path = REPO_DIR) -> bool:
    """True if the heartbeat schedule is installed for this platform.

    Keyed on the schedule, not on HEARTBEAT_URL: a URL sitting in .env with
    nothing firing it is precisely the half-configured state this module
    exists to finish, so it must still count as "not configured".
    """
    system = platform.system()
    if system == "Linux":
        return Path(SYSTEMD_TIMER_PATH).exists()
    if system == "Darwin":
        return _launch_agent_plist_path().exists()
    return False


def normalize_ping_url(raw: str) -> str | None:
    """Return a usable ping URL, or None when it is not one.

    Validated at the trust boundary rather than discovered at 3am: a typo here
    means the monitor never goes green and the operator believes they are
    covered. Requires an http(s) scheme and a host.
    """
    candidate = (raw or "").strip().strip('"').strip("'")
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return candidate


def normalize_interval(raw, default: int = DEFAULT_INTERVAL_MIN) -> int:
    """Interval in minutes: an integer >= 1, falling back to `default`."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


# ─── Linux: systemd service + timer ──────────────────────────────────


def render_systemd_units(service_text: str, timer_text: str, repo_dir: Path,
                         interval_min: int, username: str) -> tuple[str, str]:
    """Fill both unit templates. Pure (no I/O), so it is unit-testable."""
    service, timer = service_text, timer_text
    venv_python = repo_dir / ".venv" / "bin" / "python"
    service = service.replace("{{USER}}", username)
    service = service.replace("{{PYTHON}}", str(venv_python))
    service = service.replace("{{BOT_SERVICE}}", bot_service_name())
    # WORKING_DIR last: it appears inside the ExecStart path too.
    service = service.replace("{{WORKING_DIR}}", str(repo_dir))

    timer = timer.replace("{{INTERVAL_MIN}}", str(interval_min))
    return service, timer


def _install_systemd_timer(repo_dir: Path, interval_min: int,
                           sudo_password: str | None) -> bool:
    """Write both units under /etc/systemd/system and enable the timer."""
    from install.service import sudo_run

    tpl_dir = repo_dir / "install" / "templates"
    for name in (f"{SYSTEMD_UNIT_NAME}.service", f"{SYSTEMD_UNIT_NAME}.timer"):
        if not (tpl_dir / name).exists():
            error(f"Template not found: {tpl_dir / name}")
            return False

    venv_python = repo_dir / ".venv" / "bin" / "python"
    if not venv_python.exists():
        error(f"Virtual environment not found at {venv_python}")
        warn("Run the installer again to create it.")
        return False

    service, timer = render_systemd_units(
        (tpl_dir / f"{SYSTEMD_UNIT_NAME}.service").read_text(encoding="utf-8"),
        (tpl_dir / f"{SYSTEMD_UNIT_NAME}.timer").read_text(encoding="utf-8"),
        repo_dir, interval_min, getpass.getuser(),
    )

    for content, dest, suffix in (
        (service, SYSTEMD_UNIT_PATH, ".service"),
        (timer, SYSTEMD_TIMER_PATH, ".timer"),
    ):
        fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix="mom_heartbeat_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            result = sudo_run(f"cp {shlex.quote(tmp_name)} {dest}", sudo_password)
            if result.returncode != 0:
                error(f"Failed to install {dest}: {result.stderr.strip()}")
                return False
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    info("Enabling systemd timer...")
    sudo_run("systemctl daemon-reload", sudo_password)
    # Only the timer is enabled. The .service is oneshot and must stay
    # unenabled, or it would also fire once at boot outside the timer.
    sudo_run(f"systemctl enable {SYSTEMD_UNIT_NAME}.timer", sudo_password)
    result = sudo_run(f"systemctl start {SYSTEMD_UNIT_NAME}.timer", sudo_password)
    if result.returncode != 0:
        warn(f"Timer may not have started: {result.stderr.strip()[:200]}")
        warn(f"  sudo systemctl status {SYSTEMD_UNIT_NAME}.timer")
    else:
        check = sudo_run(f"systemctl is-active {SYSTEMD_UNIT_NAME}.timer", sudo_password)
        if "active" in check.stdout:
            ok(f"Heartbeat timer running (every {interval_min} min)")
        else:
            warn("Timer registered but may not be active. Check:")
            warn(f"  sudo systemctl status {SYSTEMD_UNIT_NAME}.timer")
    return True


# ─── macOS: launchd agent ────────────────────────────────────────────


def render_heartbeat_plist(template_text: str, repo_dir: Path,
                           interval_min: int, home: Path) -> str:
    """Fill the LaunchAgent plist template. Pure (no I/O), so it is testable."""
    venv_python = repo_dir / ".venv" / "bin" / "python"
    content = template_text
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{BOT_LABEL}}", BOT_LAUNCHD_LABEL)
    content = content.replace("{{INTERVAL_SEC}}", str(interval_min * 60))
    content = content.replace("{{LOG_DIR}}", str(repo_dir / "data" / "logs"))
    content = content.replace("{{VENV_BIN}}", str(repo_dir / ".venv" / "bin"))
    content = content.replace("{{HOME}}", str(home))
    # WORKING_DIR last: it appears inside the script path too.
    content = content.replace("{{WORKING_DIR}}", str(repo_dir))
    return content


def _install_launchd_agent(repo_dir: Path, interval_min: int) -> bool:
    """Render the heartbeat LaunchAgent and load it. No sudo: it lives in
    the user's own ~/Library/LaunchAgents, like the bot and Mini App agents."""
    from install.os_detect import detect as detect_os
    from install.service import _launchctl_load

    template = repo_dir / "install" / "templates" / f"{LAUNCH_AGENT_LABEL}.plist"
    if not template.exists():
        error(f"Plist template not found: {template}")
        return False

    venv_python = repo_dir / ".venv" / "bin" / "python"
    if not venv_python.exists():
        error(f"Virtual environment not found at {venv_python}")
        warn("Run the installer again to create it.")
        return False

    content = render_heartbeat_plist(
        template.read_text(encoding="utf-8"), repo_dir, interval_min, Path.home()
    )
    (repo_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)

    plist_path = _launch_agent_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        plist_path.write_text(content, encoding="utf-8")
    except OSError as e:
        error(f"Failed to write LaunchAgent plist: {e}")
        return False

    _launchctl_load(plist_path, detect_os(), system_wide=False)
    ok(f"Heartbeat LaunchAgent installed at {plist_path}")
    info(f"It pings every {interval_min} min while the bot is running.")
    return True


# ─── Orchestration ───────────────────────────────────────────────────


def run_heartbeat_setup_step(config: dict, ask=None) -> None:
    """Interactive down-alert install. Plugs into wizard.OPTIONAL_FEATURES."""
    system = platform.system()
    if system not in ("Linux", "Darwin"):
        warn("The heartbeat schedule needs Linux (systemd) or macOS (launchd).")
        warn("On other hosts, schedule utils/heartbeat.py with cron yourself.")
        return

    if ask is None:
        from install.wizard import ask as _ask
        ask = _ask

    print()
    print(f"  {BOLD}Down alert (external heartbeat){NC}")
    print("  An always-on assistant that goes dark should not do so silently.")
    print("  A small ping goes out on a timer while the bot is running. When the")
    print("  bot dies, the machine freezes, or the network drops, the pings stop")
    print("  and your monitor emails you, usually within minutes.")
    print()
    print(f"  {YELLOW}You need a ping URL first.{NC} Any dead-man's-switch monitor works;")
    print("  healthchecks.io has a free tier. Create a check, set its period to")
    print("  match the interval below and its grace to a few times that, then")
    print("  copy the ping URL it gives you (https://hc-ping.com/<uuid>).")
    print()

    raw_url = ask("Ping URL (blank to skip)", default="", required=False)
    url = normalize_ping_url(raw_url)
    if not url:
        if (raw_url or "").strip():
            warn("That does not look like an http(s) URL. Skipping.")
        ok("Skipping the down alert. The bot runs without it.")
        return

    interval = normalize_interval(
        ask("Ping every how many minutes?", default=str(DEFAULT_INTERVAL_MIN))
    )

    if system == "Linux":
        # Sudo password is cached by install.sudo; this re-uses it.
        from install.service import get_sudo_password
        if not _install_systemd_timer(REPO_DIR, interval, get_sudo_password()):
            warn("Schedule install failed. Nothing else was changed. The manual")
            warn("units are in install/templates/ and docs/heartbeat.md.")
            return
    else:  # Darwin
        if not _install_launchd_agent(REPO_DIR, interval):
            warn("Schedule install failed. Nothing else was changed. The manual")
            warn("plist is in install/templates/ and docs/heartbeat.md.")
            return

    # Only now record it, so a failed install never leaves a URL in .env
    # advertising an alert that does not exist.
    config["heartbeat_url"] = url
    config["heartbeat_interval_min"] = interval

    _prove_the_ping(url)


def _prove_the_ping(url: str) -> None:
    """Send one real ping. The monitor flipping to 'up' is the only evidence
    the alert path works end to end, so do it here rather than leave it as
    homework the operator never does."""
    import sys as _sys
    _sys.path.insert(0, str(REPO_DIR))
    from utils.heartbeat import send_ping

    info("Sending one ping to prove the path...")
    if send_ping(url):
        ok("Ping accepted. Your monitor should now show the check as up.")
    else:
        warn("The ping did not get a success response. The schedule is still")
        warn("installed; check the URL, then run it by hand:")
        warn(f"  {REPO_DIR / '.venv' / 'bin' / 'python'} {REPO_DIR / 'utils' / 'heartbeat.py'}")
    print()
    print(f"  {BOLD}One thing only you can finish:{NC} stop the bot for longer than")
    print("  your monitor's grace period once, and confirm the email actually")
    print("  arrives. An alert nobody has ever seen fire is not an alert.")
