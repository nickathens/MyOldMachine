"""
System Health Monitor.

Tracks disk, CPU, RAM, uptime, network status, and polling liveness.
Provides /health command output, critical alerts, and automatic recovery
when the Telegram polling loop stops receiving updates.
"""

import asyncio
import logging
import os
import platform
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bot start time for uptime tracking
_start_time = time.time()


def get_uptime() -> str:
    """Get bot uptime as human-readable string."""
    elapsed = time.time() - _start_time
    delta = timedelta(seconds=int(elapsed))
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def get_system_uptime() -> str:
    """Get system uptime."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True, text=True, timeout=5
            )
            # Parse: { sec = 1709123456, usec = 0 }
            match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
            if match:
                boot_time = int(match.group(1))
                elapsed = time.time() - boot_time
                delta = timedelta(seconds=int(elapsed))
                return str(delta)
        else:
            with open("/proc/uptime") as f:
                seconds = float(f.read().split()[0])
                delta = timedelta(seconds=int(seconds))
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                return f"{days}d {hours}h {minutes}m"
    except Exception:
        pass
    return "unknown"


def get_disk_usage(path: str = "/") -> dict:
    """Get disk usage stats."""
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        return {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "percent": round(used / total * 100, 1) if total > 0 else 0,
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


def get_memory_usage() -> dict:
    """Get RAM usage stats."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            total = int(result.stdout.strip())
            # Get used via vm_stat
            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5
            )
            page_size = 4096  # Intel default; Apple Silicon uses 16384 — parsed below
            pages_free = 0
            pages_active = 0
            pages_inactive = 0
            pages_wired = 0
            for line in result.stdout.splitlines():
                if "page size" in line.lower():
                    m = re.search(r"(\d+)", line)
                    if m:
                        page_size = int(m.group(1))
                if "Pages free" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        pages_free = int(m.group(1))
                if "Pages active" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        pages_active = int(m.group(1))
                if "Pages inactive" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        pages_inactive = int(m.group(1))
                if "Pages wired" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        pages_wired = int(m.group(1))
            used = (pages_active + pages_wired) * page_size
            return {
                "total_gb": round(total / (1024**3), 1),
                "used_gb": round(used / (1024**3), 1),
                "free_gb": round((total - used) / (1024**3), 1),
                "percent": round(used / total * 100, 1) if total > 0 else 0,
            }
        else:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0])  # in kB
                        info[key] = val
            total = info.get("MemTotal", 0) * 1024
            available = info.get("MemAvailable", 0) * 1024
            used = total - available
            return {
                "total_gb": round(total / (1024**3), 1),
                "used_gb": round(used / (1024**3), 1),
                "free_gb": round(available / (1024**3), 1),
                "percent": round(used / total * 100, 1) if total > 0 else 0,
            }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


def get_cpu_usage() -> Optional[float]:
    """Get CPU usage estimate (non-blocking — uses load average, not sleep)."""
    try:
        load = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        # 1-minute load average normalized to percentage
        return round(min(load[0] / cpu_count * 100, 100.0), 1)
    except (OSError, AttributeError):
        pass
    return None


def get_load_average() -> Optional[str]:
    """Get system load average."""
    try:
        load = os.getloadavg()
        return f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
    except (OSError, AttributeError):
        return None


def get_network_status() -> bool:
    """Check if we have internet connectivity (tries multiple hosts)."""
    for url in ("https://api.telegram.org", "https://www.google.com", "https://1.1.1.1"):
        try:
            result = subprocess.run(
                ["curl", "-sf", "--max-time", "5", "-o", "/dev/null", url],
                capture_output=True, timeout=8
            )
            if result.returncode == 0:
                return True
        except Exception:
            continue
    return False


def build_health_report(bot_dir: Optional[Path] = None) -> str:
    """Build a full health report string."""
    lines = ["System Health Report", ""]

    # Bot uptime
    lines.append(f"Bot uptime: {get_uptime()}")
    lines.append(f"System uptime: {get_system_uptime()}")
    lines.append("")

    # CPU
    load = get_load_average()
    if load:
        lines.append(f"Load average: {load}")

    # Memory
    mem = get_memory_usage()
    lines.append(f"RAM: {mem['used_gb']}/{mem['total_gb']} GB ({mem['percent']}%)")

    # Swap
    swap = get_swap_usage()
    if swap["total_gb"] > 0:
        lines.append(f"Swap: {swap['used_gb']:.1f}/{swap['total_gb']:.1f} GB ({swap['percent']}%)")

    # Disk
    disk = get_disk_usage("/")
    lines.append(f"Disk: {disk['used_gb']}/{disk['total_gb']} GB ({disk['percent']}%)")
    if disk["free_gb"] < 5:
        lines.append(f"  WARNING: Low disk space ({disk['free_gb']} GB free)")

    # Bot data directory
    if bot_dir:
        data_disk = get_disk_usage(str(bot_dir))
        data_dir = bot_dir / "data"
        if data_dir.exists():
            data_size = sum(
                f.stat().st_size for f in data_dir.rglob("*") if f.is_file()
            )
            lines.append(f"Bot data: {round(data_size / (1024**2), 1)} MB")

    lines.append("")

    # Network
    online = get_network_status()
    lines.append(f"Network: {'Online' if online else 'OFFLINE'}")

    # OS info
    lines.append(f"OS: {platform.system()} {platform.release()}")
    lines.append(f"Python: {platform.python_version()}")

    return "\n".join(lines)


def get_swap_usage() -> dict:
    """Get swap usage stats."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "vm.swapusage"],
                capture_output=True, text=True, timeout=5
            )
            # Parse: "total = 2048.00M  used = 1024.00M  free = 1024.00M"
            total = used = free = 0.0
            m = re.search(r"total\s*=\s*([\d.]+)M", result.stdout)
            if m:
                total = float(m.group(1)) / 1024  # Convert to GB
            m = re.search(r"used\s*=\s*([\d.]+)M", result.stdout)
            if m:
                used = float(m.group(1)) / 1024
            m = re.search(r"free\s*=\s*([\d.]+)M", result.stdout)
            if m:
                free = float(m.group(1)) / 1024
            return {
                "total_gb": round(total, 2),
                "used_gb": round(used, 2),
                "free_gb": round(free, 2),
                "percent": round(used / total * 100, 1) if total > 0 else 0,
            }
        else:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0])  # in kB
                        info[key] = val
            total = info.get("SwapTotal", 0) * 1024
            free = info.get("SwapFree", 0) * 1024
            used = total - free
            return {
                "total_gb": round(total / (1024**3), 2),
                "used_gb": round(used / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
                "percent": round(used / total * 100, 1) if total > 0 else 0,
            }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


def check_critical(bot_dir: Optional[Path] = None) -> list[str]:
    """
    Check for critical conditions that should trigger alerts.
    Returns list of alert messages (empty if all OK).
    """
    alerts = []

    disk = get_disk_usage("/")
    if disk["free_gb"] < 2:
        alerts.append(f"CRITICAL: Disk almost full — {disk['free_gb']} GB free")
    elif disk["free_gb"] < 5:
        alerts.append(f"WARNING: Low disk space — {disk['free_gb']} GB free")

    mem = get_memory_usage()
    if mem["percent"] > 95:
        alerts.append(f"CRITICAL: RAM at {mem['percent']}% — {mem['free_gb']} GB free")
    elif mem["percent"] > 90:
        alerts.append(f"WARNING: RAM at {mem['percent']}% — {mem['free_gb']} GB free")

    swap = get_swap_usage()
    if swap["total_gb"] > 0 and swap["percent"] > 80:
        alerts.append(f"WARNING: Swap at {swap['percent']}% — {swap['used_gb']:.1f}/{swap['total_gb']:.1f} GB")

    cpu = get_cpu_usage()
    if cpu is not None and cpu > 95:
        load = get_load_average() or "unknown"
        alerts.append(f"WARNING: CPU load sustained at {cpu}% (load: {load})")

    global _consecutive_net_failures
    if not get_network_status():
        _consecutive_net_failures += 1
        if _consecutive_net_failures >= _NET_FAILURE_THRESHOLD:
            alerts.append("WARNING: No internet connectivity")
    else:
        _consecutive_net_failures = 0

    return alerts


# ---------------------------------------------------------------------------
# Proactive health alerting
# ---------------------------------------------------------------------------

# Track which alerts have been sent to avoid repeated notifications.
# Key: alert message prefix (e.g. "CRITICAL: Disk"), Value: timestamp last sent.
_alert_cooldowns: dict[str, float] = {}
_ALERT_COOLDOWN_SECONDS = 4 * 3600  # Don't repeat the same alert for 4 hours

# Track consecutive network failures — only alert after 2+ in a row
_consecutive_net_failures: int = 0
_NET_FAILURE_THRESHOLD = 2  # Require this many consecutive failures before alerting


def _alert_key(alert_msg: str) -> str:
    """Extract a stable key from an alert message for cooldown tracking."""
    # Use the part before the dash for grouping: "CRITICAL: Disk almost full"
    parts = alert_msg.split("—")
    return parts[0].strip() if parts else alert_msg


async def run_health_check(send_fn, admin_user_ids: list[int],
                           bot_dir: Optional[Path] = None):
    """
    Run a health check and send alerts to admin users if any issues found.

    send_fn: async function(user_id: int, text: str) -> bool
    admin_user_ids: list of Telegram user IDs to alert
    """
    alerts = check_critical(bot_dir)
    if not alerts:
        return

    now = time.time()
    new_alerts = []
    for alert in alerts:
        key = _alert_key(alert)
        last_sent = _alert_cooldowns.get(key, 0)
        if now - last_sent >= _ALERT_COOLDOWN_SECONDS:
            new_alerts.append(alert)
            _alert_cooldowns[key] = now

    if not new_alerts:
        return

    message = "Health Alert\n\n" + "\n".join(new_alerts)
    for uid in admin_user_ids:
        try:
            await send_fn(uid, message)
        except Exception as e:
            logger.error(f"Failed to send health alert to {uid}: {e}")


# ---------------------------------------------------------------------------
# Polling liveness monitor
# ---------------------------------------------------------------------------
# Detects when the Telegram polling loop stops receiving updates (due to
# network issues, stale connections, or library-level failures) and recovers
# by cleanly exiting the process so systemd/launchd restarts it.

POLLING_CHECK_INTERVAL = 60      # How often to check (seconds)
POLLING_STALE_THRESHOLD = 1800   # 30 minutes with zero updates → investigate
POLLING_RECOVERY_COOLDOWN = 300  # 5 min cooldown between recovery attempts

# Internet check targets — lightweight HEAD requests
_CONNECTIVITY_HOSTS = [
    "https://api.telegram.org",
    "https://www.google.com",
    "https://1.1.1.1",
]
_CONNECTIVITY_TIMEOUT = 10


class PollingHealthMonitor:
    """
    Monitors Telegram polling liveness and recovers from silent failures.

    How it works:
    - Every incoming Telegram update resets the last_update_time via record_update().
    - Every 60 seconds the monitor checks elapsed time since last update.
    - If 30+ minutes pass with no updates:
      1. Check internet connectivity (async HTTP HEAD requests).
      2. If internet is down → log it, keep waiting (nothing we can do).
      3. If internet is up but we still see no updates on the *next* check
         (two consecutive stale checks = ~32 minutes minimum) → the polling
         loop is likely stuck. Exit the process cleanly so systemd restarts it.
    - If a local Bot API is configured, attempt to restart it first before
      exiting the whole process.
    - All recovery is silent — no messages sent to the user.
    """

    def __init__(
        self,
        local_api_base: Optional[str] = None,
    ):
        self.local_api_base = local_api_base

        # State
        self.last_update_time: float = time.monotonic()
        self.last_recovery_time: float = 0.0
        self.internet_was_down: bool = False
        self._was_stale: bool = False
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

    def record_update(self):
        """Call on every incoming Telegram update."""
        self.last_update_time = time.monotonic()

    def start(self):
        """Start the background monitor."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Polling health monitor started (check every %ds, stale threshold %ds)",
            POLLING_CHECK_INTERVAL, POLLING_STALE_THRESHOLD,
        )

    async def stop(self):
        """Stop the monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Polling health monitor stopped")

    async def _loop(self):
        """Main loop — runs until stopped or process exits."""
        # Give the bot time to start up and receive initial updates
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Polling health check error: %s", e, exc_info=True)

            await asyncio.sleep(POLLING_CHECK_INTERVAL)

    async def _check(self):
        """Single health check cycle."""
        elapsed = time.monotonic() - self.last_update_time

        if elapsed < POLLING_STALE_THRESHOLD:
            # Updates arriving normally
            self._was_stale = False
            return

        logger.warning(
            "No Telegram updates for %.0f seconds (threshold: %d). Diagnosing...",
            elapsed, POLLING_STALE_THRESHOLD,
        )

        # Step 1: Internet connectivity
        internet_ok = await self._check_internet()

        if not internet_ok:
            if not self.internet_was_down:
                self.internet_was_down = True
                logger.warning("Internet connectivity lost. Waiting for recovery...")
            self._was_stale = False
            return

        # Internet restored after outage
        if self.internet_was_down:
            self.internet_was_down = False
            logger.info("Internet connectivity restored")
            # If using a local Bot API, restart it — its upstream connection is
            # likely stale. Otherwise the polling library should reconnect on its own.
            if self.local_api_base:
                await self._restart_local_api(
                    "Internet restored after outage — restarting local Bot API"
                )
            self._was_stale = False
            return

        # Step 2: Internet is up. Two-check confirmation to prevent false positives.
        if not self._was_stale:
            logger.info(
                "Internet OK but no updates. Could be a quiet period — "
                "will confirm on next check."
            )
            self._was_stale = True
            return

        # Step 3: Second consecutive stale check. Something is wrong.
        logger.warning(
            "No updates for %.0f seconds despite healthy internet "
            "(confirmed over 2 checks). Taking recovery action.",
            elapsed,
        )

        # Enforce cooldown
        now = time.monotonic()
        if now - self.last_recovery_time < POLLING_RECOVERY_COOLDOWN:
            remaining = POLLING_RECOVERY_COOLDOWN - (now - self.last_recovery_time)
            logger.info("Recovery cooldown active (%.0fs remaining). Skipping.", remaining)
            return
        self.last_recovery_time = now

        # If using a local Bot API, restart that service first
        if self.local_api_base:
            restarted = await self._restart_local_api(
                "Stale polling — internet OK but no updates arriving"
            )
            if restarted:
                self._was_stale = False
                return

        # No local API (or restart failed). The polling library itself is stuck.
        # Exit cleanly so systemd/launchd restarts the entire bot.
        logger.critical(
            "Polling loop appears stuck. Exiting for automatic restart. "
            "Reason: %d seconds with no updates, internet OK.",
            int(elapsed),
        )
        # os._exit avoids cleanup complications — systemd will restart us
        os._exit(1)

    async def _check_internet(self) -> bool:
        """Check internet connectivity via async HTTP HEAD requests."""
        try:
            import httpx
        except ImportError:
            # httpx not available — fall back to synchronous curl check in executor
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, get_network_status)

        async with httpx.AsyncClient(timeout=_CONNECTIVITY_TIMEOUT) as client:
            for url in _CONNECTIVITY_HOSTS:
                try:
                    response = await client.head(url)
                    if response.status_code < 500:
                        return True
                except Exception:
                    continue
        return False

    async def _restart_local_api(self, reason: str) -> bool:
        """
        Attempt to restart the local Bot API service.
        Returns True if restart succeeded, False otherwise.
        """
        logger.info("Attempting local Bot API restart. Reason: %s", reason)

        # Detect the service manager and try to restart
        if platform.system() == "Linux":
            # Try user-level systemd first, then system-level
            for cmd in [
                ["systemctl", "--user", "restart", "telegram-bot-api"],
                ["systemctl", "restart", "telegram-bot-api"],
            ]:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                    if proc.returncode == 0:
                        logger.info("Restarted telegram-bot-api via %s", " ".join(cmd))
                        await asyncio.sleep(5)
                        self.last_update_time = time.monotonic()
                        return True
                except (asyncio.TimeoutError, Exception) as e:
                    logger.debug("Restart via %s failed: %s", " ".join(cmd), e)
                    continue

        logger.warning("Could not restart local Bot API")
        return False


# Module-level singleton for easy access from bot.py
_polling_monitor: Optional[PollingHealthMonitor] = None


def init_polling_monitor(
    local_api_base: Optional[str] = None,
) -> PollingHealthMonitor:
    """Create and return the polling health monitor singleton."""
    global _polling_monitor
    _polling_monitor = PollingHealthMonitor(
        local_api_base=local_api_base,
    )
    return _polling_monitor


def get_polling_monitor() -> Optional[PollingHealthMonitor]:
    """Get the polling monitor instance (or None if not initialized)."""
    return _polling_monitor


def record_polling_update():
    """Convenience: record an update on the singleton monitor."""
    if _polling_monitor:
        _polling_monitor.record_update()
