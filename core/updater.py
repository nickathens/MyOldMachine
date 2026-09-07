"""
Self-Update Mechanism.

Pulls latest code from git, reinstalls pip dependencies, and restarts the service.
Triggered via /update command in Telegram.
"""

import logging
import os
import platform
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_sudo_password():
    """Read sudo password. Delegates to install.sudo."""
    from install.sudo import get_sudo_password as _shared_get_sudo_password
    return _shared_get_sudo_password()


def _run(cmd: str, cwd: str = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=cwd, timeout=timeout
    )


def _is_dubious_ownership(stderr: str) -> bool:
    """Detect git's safe.directory rejection."""
    return "dubious ownership" in (stderr or "").lower()


def _add_safe_directory(path: str) -> bool:
    """Register path with git's global safe.directory list. Idempotent."""
    quoted = shlex.quote(path)
    check = _run(
        "git config --global --get-all safe.directory",
        timeout=10,
    )
    if check.returncode == 0 and path in check.stdout.splitlines():
        return True
    add = _run(
        f"git config --global --add safe.directory {quoted}",
        timeout=10,
    )
    return add.returncode == 0


def _run_git(cmd: str, cwd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """
    Run a git command, auto-recovering from 'dubious ownership' once by adding
    cwd to safe.directory. Avoids forcing the user into a Terminal session
    when we can fix the trust boundary ourselves.
    """
    result = _run(cmd, cwd=cwd, timeout=timeout)
    if result.returncode != 0 and _is_dubious_ownership(result.stderr):
        logger.warning(
            "git rejected %s as dubious — adding to safe.directory and retrying",
            cwd,
        )
        if _add_safe_directory(cwd):
            result = _run(cmd, cwd=cwd, timeout=timeout)
    return result


def get_current_version(bot_dir: Path) -> str:
    """Get the current git commit hash (short)."""
    result = _run_git("git rev-parse --short HEAD", cwd=str(bot_dir))
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def get_current_branch(bot_dir: Path) -> str:
    """Get the current git branch."""
    result = _run_git("git rev-parse --abbrev-ref HEAD", cwd=str(bot_dir))
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def check_for_updates(bot_dir: Path) -> tuple[bool, str]:
    """
    Check if there are updates available.
    Returns (has_updates, description).
    """
    result = _run_git("git fetch origin", cwd=str(bot_dir))
    if result.returncode != 0:
        return False, f"Failed to check: {result.stderr[:100]}"

    branch = get_current_branch(bot_dir)
    result = _run_git(f"git log HEAD..origin/{branch} --oneline", cwd=str(bot_dir))
    if result.returncode != 0:
        return False, "Could not compare with remote"

    commits = result.stdout.strip()
    if not commits:
        return False, "Already up to date"

    count = len(commits.splitlines())
    return True, f"{count} new commit(s) available:\n{commits}"


def pull_updates(bot_dir: Path) -> tuple[bool, str]:
    """
    Pull latest code from git.
    Returns (success, message).
    """
    current = get_current_version(bot_dir)
    result = _run_git("git pull --ff-only", cwd=str(bot_dir))

    if result.returncode != 0:
        return False, (
            f"Git pull --ff-only failed (local changes?): {result.stderr[:200]}\n"
            f"Fix manually: cd {bot_dir} && git stash && git pull"
        )

    new = get_current_version(bot_dir)

    # Reinstall pip deps in case requirements changed.
    # Use list-form subprocess to avoid shell-quoting issues on paths with spaces.
    venv_pip = bot_dir / ".venv" / "bin" / "pip"
    if venv_pip.exists():
        try:
            pip_result = subprocess.run(
                [str(venv_pip), "install", "--quiet", "-r",
                 str(bot_dir / "requirements.txt")],
                capture_output=True, text=True,
                cwd=str(bot_dir), timeout=300,
            )
            if pip_result.returncode != 0:
                logger.warning(f"pip install after update had issues: {pip_result.stderr[:200]}")
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"pip install after update failed to run: {e}")

    return True, f"Updated: {current} → {new}"


# ─── Is the Mini App actually back? ──────────────────────────────────
#
# restart_service() returns True the moment it has SPAWNED the detached
# restart script. That is the honest answer to "was the restart scheduled",
# and it is all the caller could ever learn about its own service, since the
# bot is about to be killed by the very restart it asked for. It is not an
# answer to "did the service come back", and /restart presented it as one:
# the `if not mini_ok` branch was unreachable on Linux and on macOS, so a
# Mini App that failed to start after an update told the user nothing.
#
# The Mini App is the one target the bot CAN verify, because it outlives it
# by a few seconds. It is an HTTP server, so its own socket answers the
# question on both platforms without asking systemd or launchd anything.
#
# Three states, never two. "Cannot tell" is not "down": a probe that fails
# for its own reasons (no port configured, the bot's own event loop wedged)
# must not put a red line on screen about a service that is running fine.

MINIAPP_HEALTH_UP = "up"
MINIAPP_HEALTH_DOWN = "down"
MINIAPP_HEALTH_UNKNOWN = "unknown"


def miniapp_health(timeout: float = 2.0) -> tuple[str, object]:
    """(state, detail) for the locally bound Mini App.

    state is "up" (it answered /health), "down" (the port refused the
    connection or the answer was not the Mini App's), or "unknown" (the
    probe could not be made at all).

    detail is the answering process's opaque instance id when it is known,
    so a caller can tell a restarted Mini App from the one that never went
    away. An older build whose /health predates that field answers None,
    which is a "cannot prove it bounced", not a failure.
    """
    import json as _json
    import urllib.error
    import urllib.request

    try:
        from install.miniapp_setup import miniapp_port
        port = miniapp_port()
    except Exception as exc:
        return MINIAPP_HEALTH_UNKNOWN, f"cannot resolve the port: {exc}"

    url = f"http://127.0.0.1:{port}/health"
    # Never through a proxy. urllib reads http_proxy from the environment,
    # and a machine with one set would send a loopback probe out to it and
    # get a connection error back — a red line about a Mini App that is
    # running perfectly well.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = resp.read(4096)
    except urllib.error.HTTPError as exc:
        # It answered, just not with 200. Something is listening and it is
        # not serving /health, which is a real problem, but a reachable one.
        return MINIAPP_HEALTH_DOWN, f"HTTP {exc.code} from {url}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return MINIAPP_HEALTH_DOWN, f"{url} did not answer: {reason}"

    try:
        payload = _json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return MINIAPP_HEALTH_DOWN, f"{url} answered, but not with JSON"
    if not isinstance(payload, dict) or not payload.get("ok"):
        return MINIAPP_HEALTH_DOWN, f"{url} answered {payload!r}"
    instance = payload.get("instance")
    return MINIAPP_HEALTH_UP, instance if isinstance(instance, str) else None


def wait_for_miniapp(before_instance: object = None, timeout: float = 25.0,
                     interval: float = 0.5,
                     _sleep=None, _now=None) -> tuple[str, str]:
    """Wait out a scheduled Mini App restart and report what happened.

    Returns (verdict, detail) where verdict is one of:

      "restarted"  it is answering again and demonstrably a new process
      "up"         it is answering, but nothing proved it ever went away
      "down"       the deadline passed with the port not answering
      "unknown"    the probe itself could not run

    The trap this exists to avoid: the restart script sleeps a few seconds
    before it touches the unit, so the OLD Mini App is still answering when
    the wait begins. A naive "is it healthy" poll therefore passes
    immediately, for the wrong process. Proof of a bounce is either a pid
    that changed or a probe that failed and then recovered; short of one of
    those this says "up", not "restarted", and the caller stays quiet
    rather than claiming something it did not see.

    `before_instance` is whatever `miniapp_health` returned before the
    restart was asked for. None means there was nothing to compare against,
    which is the case on an install whose Mini App predates the id field.
    """
    import time as _time

    # _sleep and _now are injection points for the tests: the deadline is
    # the whole subject here, and a test that had to spend real seconds
    # proving it would be one more slow test nobody runs.
    sleep = _sleep or _time.sleep
    now = _now or _time.monotonic
    deadline = now() + timeout
    saw_down = False
    last_detail = ""
    while True:
        state, detail = miniapp_health(timeout=min(2.0, max(0.5, interval * 2)))
        if state == MINIAPP_HEALTH_UNKNOWN:
            return MINIAPP_HEALTH_UNKNOWN, str(detail)
        if state == MINIAPP_HEALTH_DOWN:
            saw_down = True
            last_detail = str(detail)
        elif saw_down:
            return "restarted", "it went away and came back"
        elif (isinstance(detail, str) and isinstance(before_instance, str)
                and detail != before_instance):
            return "restarted", "a different process is answering now"
        # Anything else is "still up, and nothing has proved it bounced":
        # either there is no id to compare on one side, or the same process
        # is still answering. Keep polling for the down edge. If the
        # deadline arrives with it still up, that is "up", not a restart
        # we witnessed and not a failure to report.
        if now() >= deadline:
            if saw_down:
                return MINIAPP_HEALTH_DOWN, last_detail
            return MINIAPP_HEALTH_UP, "still answering; no restart observed"
        sleep(interval)


_SERVICE_TARGETS = {
    "bot": {
        "linux_service": "myoldmachine",
        "macos_plist_name": "com.myoldmachine.bot.plist",
    },
    "miniapp": {
        "linux_service": "myoldmachine-miniapp",
        "macos_plist_name": "com.myoldmachine.miniapp.plist",
    },
}


def restart_service(target: str = "bot") -> tuple[bool, str]:
    """
    Restart a MyOldMachine service.

    target: "bot" (default, the Telegram bot) or "miniapp" (the dashboard server).
    Returns (success, message). Success means the restart was *scheduled*; the
    caller's process may continue running for a few seconds before being killed
    when the detached launchctl reload / systemctl restart step runs.
    """
    spec = _SERVICE_TARGETS.get(target)
    if spec is None:
        return False, f"Unknown restart target: {target!r}"

    system = platform.system()

    if system == "Linux":
        import re as _re
        import tempfile
        # Allow override via env (used by tests + custom installs). The bot
        # target additionally honors the legacy SERVICE_NAME env var.
        env_key = "MOM_MINIAPP_SERVICE" if target == "miniapp" else "SERVICE_NAME"
        service_name = os.environ.get(env_key, spec["linux_service"])
        # Validate synchronously, before spawning anything: service_name is
        # interpolated into the shell script below, so reject anything with
        # shell-meta characters or whitespace.
        if not _re.fullmatch(r"[a-zA-Z0-9_@.-]+", service_name):
            return False, f"Invalid service name: {service_name!r}"

        # Detach the restart into a background script (mirrors the macOS path).
        # `systemctl restart` of our own unit tears down this process's cgroup,
        # so a blocking subprocess.run() here gets SIGTERM'd mid-call and never
        # returns — dropping the HTTP response the Mini App still owes the
        # client. A detached script that sleeps a beat first lets the response
        # land, then restarts the unit out from under us. The password is read
        # from ~/.sudo_pass inside the script (never on argv or in the script
        # body), matching how the macOS branch handles credentials.
        sudo_pass_file = Path.home() / ".sudo_pass"
        if sudo_pass_file.exists():
            sudo_cmd = f'cat "{sudo_pass_file}" | sudo -S'
        else:
            sudo_cmd = 'sudo -n'
        restart_script = tempfile.NamedTemporaryFile(
            mode='w', suffix='.sh', delete=False, prefix='mom_restart_'
        )
        restart_script.write(
            f'#!/bin/bash\n'
            f'sleep 3\n'
            f'{sudo_cmd} systemctl restart {service_name}\n'
            f'rm -f "{restart_script.name}"\n'
        )
        restart_script.close()
        os.chmod(restart_script.name, 0o700)
        subprocess.Popen(
            [restart_script.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, "Service restarting..."

    elif system == "Darwin":
        plist_name = spec["macos_plist_name"]
        daemon_plist = Path("/Library/LaunchDaemons") / plist_name
        agent_plist = Path.home() / "Library" / "LaunchAgents" / plist_name

        if daemon_plist.exists():
            import tempfile
            sudo_pass_file = Path.home() / ".sudo_pass"
            restart_script = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', delete=False, prefix='mom_restart_'
            )
            if sudo_pass_file.exists():
                sudo_cmd = f'cat "{sudo_pass_file}" | sudo -S'
            else:
                sudo_cmd = 'sudo -n'
            restart_script.write(
                f'#!/bin/bash\n'
                f'sleep 3\n'
                f'{sudo_cmd} launchctl unload "{daemon_plist}" 2>/dev/null\n'
                f'sleep 1\n'
                f'{sudo_cmd} launchctl load -w "{daemon_plist}"\n'
                f'rm -f "{restart_script.name}"\n'
            )
            restart_script.close()
            os.chmod(restart_script.name, 0o700)
            subprocess.Popen(
                [restart_script.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "Service restarting (LaunchDaemon)..."

        if agent_plist.exists():
            import tempfile
            restart_script = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', delete=False, prefix='mom_restart_'
            )
            restart_script.write(
                f'#!/bin/bash\n'
                f'sleep 3\n'
                f'launchctl unload "{agent_plist}" 2>/dev/null\n'
                f'sleep 1\n'
                f'launchctl load -w "{agent_plist}"\n'
                f'rm -f "{restart_script.name}"\n'
            )
            restart_script.close()
            os.chmod(restart_script.name, 0o700)
            subprocess.Popen(
                [restart_script.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, "Service restarting (LaunchAgent)..."

        return False, f"No LaunchDaemon or LaunchAgent plist found for {target}"

    return False, f"Unsupported OS: {system}"


def full_update(bot_dir: Path) -> str:
    """
    Update cycle: pull code + install deps. Does NOT restart automatically.
    The user must send /restart to apply — this prevents killing the bot mid-response.
    """
    lines = []

    # Check
    has_updates, check_msg = check_for_updates(bot_dir)
    if not has_updates:
        return check_msg

    lines.append(check_msg)

    # Pull
    success, pull_msg = pull_updates(bot_dir)
    lines.append(pull_msg)
    if not success:
        return "\n".join(lines)

    lines.append("")
    lines.append("Code updated. Send /restart to apply the changes.")

    return "\n".join(lines)
