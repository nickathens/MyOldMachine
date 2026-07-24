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
from datetime import timedelta
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
            with open("/proc/uptime", encoding="utf-8") as f:
                seconds = float(f.read().split()[0])
                delta = timedelta(seconds=int(seconds))
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                return f"{days}d {hours}h {minutes}m"
    except Exception:
        pass
    return "unknown"


def get_system_uptime_seconds() -> Optional[float]:
    """System uptime in seconds since boot, or None if unreadable.

    Measured from the SYSTEM's boot (kern.boottime on macOS, /proc/uptime on
    Linux), NOT the bot's start time. Used to suppress CPU/RAM/swap alerts while
    the machine is still in its post-boot settling window.
    """
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True, text=True, timeout=5
            )
            match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
            if match:
                return max(0.0, time.time() - int(match.group(1)))
        else:
            with open("/proc/uptime", encoding="utf-8") as f:
                return float(f.read().split()[0])
    except Exception:
        pass
    return None


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
            page_size = 4096  # Intel default; Apple Silicon uses 16384
            pages_active = 0
            pages_wired = 0
            for line in result.stdout.splitlines():
                if "page size" in line.lower():
                    m = re.search(r"(\d+)", line)
                    if m:
                        page_size = int(m.group(1))
                if "Pages active" in line:
                    m = re.search(r"(\d+)", line.split(":")[1])
                    if m:
                        pages_active = int(m.group(1))
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
            with open("/proc/meminfo", encoding="utf-8") as f:
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


def _read_proc_stat_cpu() -> Optional[tuple[float, float]]:
    """Return (busy_ticks, total_ticks) cumulative since boot from /proc/stat.

    busy = total - (idle + iowait). Linux only. Returns None on any read/parse
    failure so the caller can skip the CPU check rather than report a bad number.
    """
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("cpu "):
                    fields = [float(x) for x in line.split()[1:]]
                    if len(fields) < 4:
                        return None
                    idle = fields[3] + (fields[4] if len(fields) > 4 else 0.0)
                    total = sum(fields)
                    return total - idle, total
    except (OSError, ValueError):
        return None
    return None


def get_cpu_usage() -> Optional[float]:
    """Measure ACTUAL CPU busy percentage over a short interval.

    This reports real processor effort (user+system time), NOT load average.
    Load average counts processes runnable OR waiting on I/O, so during a boot
    storm it reads high while the CPU sits mostly idle — the source of the old
    post-restart "CPU at 100%" false alarm. Here we sample true CPU time over a
    ~1s window and report busy% = 100 - idle%.

    Blocking (~1s): callers must run it off the event loop. run_health_check
    offloads the whole check to a thread executor for exactly this reason.
    Returns 0..100, or None if the platform reading is unavailable (the caller
    then skips the CPU check).
    """
    system = platform.system()
    try:
        if system == "Darwin":
            # `top -l 2` takes two real samples ~1s apart. The FIRST sample is
            # cumulative-since-boot (meaningless as an instant); the SECOND is
            # the true interval reading, so we keep the LAST "CPU usage" line.
            result = subprocess.run(
                ["top", "-l", "2", "-n", "0"],
                capture_output=True, text=True, timeout=15
            )
            idle: Optional[float] = None
            for line in result.stdout.splitlines():
                if "CPU usage" in line:
                    # "CPU usage: 3.44% user, 6.89% sys, 89.65% idle"
                    m = re.search(r"([\d.]+)%\s*idle", line)
                    if m:
                        idle = float(m.group(1))
            if idle is not None:
                return round(max(0.0, min(100.0 - idle, 100.0)), 1)
        else:
            first = _read_proc_stat_cpu()
            if first is None:
                return None
            time.sleep(0.5)
            second = _read_proc_stat_cpu()
            if second is None:
                return None
            busy_delta = second[0] - first[0]
            total_delta = second[1] - first[1]
            if total_delta <= 0:
                return None
            return round(max(0.0, min(busy_delta / total_delta * 100.0, 100.0)), 1)
    except Exception:
        return None
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
            with open("/proc/meminfo", encoding="utf-8") as f:
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

    Disk and network are judged from second one. CPU, RAM and swap are only
    judged once the machine is past its post-boot settling window, because a
    booting machine's processor and memory readings are transient noise — that
    is what produced the daily post-restart "CPU at 100%" false alarm.
    """
    global _consecutive_net_failures, _consecutive_cpu_breaches
    alerts = []

    # --- Always live: disk is not distorted by boot ---
    disk = get_disk_usage("/")
    if disk["free_gb"] < 2:
        alerts.append(f"CRITICAL: Disk almost full — {disk['free_gb']} GB free")
    elif disk["free_gb"] < 5:
        alerts.append(f"WARNING: Low disk space — {disk['free_gb']} GB free")

    # --- Boot-sensitive: CPU / RAM / swap. Suppress until the machine settles ---
    uptime = get_system_uptime_seconds()
    settled = uptime is None or uptime >= _SETTLING_WINDOW_SECONDS
    if settled:
        mem = get_memory_usage()
        if mem["percent"] > 95:
            alerts.append(f"CRITICAL: RAM at {mem['percent']}% — {mem['free_gb']} GB free")
        elif mem["percent"] > 90:
            alerts.append(f"WARNING: RAM at {mem['percent']}% — {mem['free_gb']} GB free")

        swap = get_swap_usage()
        if swap["total_gb"] > 0 and swap["percent"] > 80:
            alerts.append(f"WARNING: Swap at {swap['percent']}% — {swap['used_gb']:.1f}/{swap['total_gb']:.1f} GB")

        # Real CPU busy%, and only after N consecutive breaches (mirrors the
        # network guard) so a single transient spike never speaks.
        cpu = get_cpu_usage()
        if cpu is not None and cpu > _CPU_BUSY_THRESHOLD:
            _consecutive_cpu_breaches += 1
            if _consecutive_cpu_breaches >= _CPU_BREACH_THRESHOLD:
                load = get_load_average() or "unknown"
                alerts.append(
                    f"WARNING: CPU busy at {cpu}% across "
                    f"{_consecutive_cpu_breaches} consecutive checks (load avg: {load})"
                )
        else:
            _consecutive_cpu_breaches = 0
    else:
        # Still waking up: do not judge CPU/RAM/swap, and clear any pre-reboot
        # breach streak so it cannot carry across the boot.
        _consecutive_cpu_breaches = 0

    # --- Always live: network has its own 2-strike guard ---
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

# CPU alerting is deliberately conservative — the old check cried wolf after
# every restart. Three guards now gate it: (1) get_cpu_usage measures REAL
# busy%, not load average; (2) we require N consecutive breaching checks, like
# the network guard above, so one transient spike stays silent; (3) check_critical
# skips CPU/RAM/swap entirely until the machine is past its post-boot settling
# window (below).
_CPU_BUSY_THRESHOLD = 95.0    # Percent BUSY (not load) that counts as a breach
_CPU_BREACH_THRESHOLD = 3     # Consecutive breaching checks before we alert
_consecutive_cpu_breaches: int = 0

# A freshly booted machine runs every login item, launch agent, Spotlight and
# the bot itself at once, so CPU/RAM/swap read as transient noise for a few
# minutes. Suppress those alerts until system uptime clears this window. Disk
# and network alerts stay live from second one — they are not distorted by boot.
_SETTLING_WINDOW_SECONDS = 5 * 60


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
    # check_critical() shells out (top samples ~1s, curl probes the network), so
    # run it in a thread executor rather than blocking the event loop.
    loop = asyncio.get_running_loop()
    alerts = await loop.run_in_executor(None, check_critical, bot_dir)
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
# The watchdog keys on poll-cycle liveness (record_poll_cycle), not inbound-update
# silence. PTB long-polls, so a healthy loop completes a getUpdates cycle every few
# seconds even when nobody is texting; no cycle for this long means it is wedged.
POLLING_POLL_STALE_THRESHOLD = 180   # No getUpdates cycle for this long → poll loop stuck
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
    - Every completed getUpdates poll cycle refreshes last_poll_time via
      record_poll_cycle(). This is the authoritative liveness signal: PTB
      long-polls, so a healthy loop ticks every few seconds even when no
      updates arrive. A stale heartbeat means the poll loop itself is wedged,
      which is the only condition worth recovering from. (record_update() still
      tracks inbound updates but is informational only; a quiet chat is not a
      fault, so silence no longer triggers a restart.)
    - Every 60 seconds the monitor checks elapsed time since the last poll cycle.
    - If POLLING_POLL_STALE_THRESHOLD passes with no completed poll cycle:
      1. Check internet connectivity (async HTTP HEAD requests).
      2. If internet is down → log it, keep waiting (nothing we can do).
      3. If internet is up but still no poll cycle on the *next* check
         (two consecutive stale checks) → the polling loop is stuck.
         Restart the local Bot API if one is configured; otherwise exit the
         process cleanly so systemd/launchd restarts it.
    - Until the first heartbeat lands, the watchdog stays in safe mode (never
      restarts), so it cannot act on a missing signal during warm-up.
    - All recovery is silent — no messages sent to the user.
    """

    def __init__(
        self,
        local_api_base: Optional[str] = None,
    ):
        self.local_api_base = local_api_base

        # State
        # last_poll_time is the authoritative liveness signal: refreshed every
        # time a getUpdates poll cycle completes (record_poll_cycle). None until
        # the first cycle lands, so we never act before the poll loop has proven
        # it is alive.
        self.last_poll_time: Optional[float] = None
        # last_update_time is informational only (most recent inbound update).
        # It no longer drives restarts: legitimate silence is not a fault.
        self.last_update_time: float = time.monotonic()
        # -inf (not 0.0) so the first recovery is never gated by the cooldown:
        # time.monotonic()'s zero point is ~boot, so 0.0 would suppress recovery
        # for the first POLLING_RECOVERY_COOLDOWN seconds of uptime, exactly the
        # startup window where a wedged poll loop is most likely.
        self.last_recovery_time: float = float("-inf")
        self.internet_was_down: bool = False
        self._was_stale: bool = False
        self._warned_no_heartbeat: bool = False  # One-shot warning if heartbeat never arrives
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None

    def record_update(self):
        """Call on every incoming Telegram update.

        Informational only: tracks the most recent inbound message so the
        diagnostics read sensibly. It does NOT drive restart decisions; a quiet
        chat is not a fault. The poll-cycle heartbeat (record_poll_cycle) is the
        signal that matters.
        """
        self.last_update_time = time.monotonic()

    def record_poll_cycle(self):
        """Call every time a getUpdates poll cycle completes successfully.

        This is the watchdog's heartbeat. PTB long-polls Telegram, so a healthy
        loop completes a cycle every few seconds even when no updates arrive
        (Telegram returns an empty batch when the long-poll window elapses).
        A stale last_poll_time therefore means the poll loop itself is wedged,
        the one condition that actually warrants recovery.
        """
        self.last_poll_time = time.monotonic()

    def start(self):
        """Start the background monitor."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Polling health monitor started (check every %ds, poll-stale threshold %ds)",
            POLLING_CHECK_INTERVAL, POLLING_POLL_STALE_THRESHOLD,
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
        """Recover only when the poll loop is genuinely wedged.

        The authoritative signal is the poll-cycle heartbeat (last_poll_time),
        refreshed by record_poll_cycle() on every completed getUpdates round-trip.
        Because PTB long-polls, a healthy loop ticks every few seconds regardless
        of whether anyone is texting, so a stale heartbeat means the poll loop
        itself has stopped. This replaces the old "no inbound updates for 30
        minutes" trigger, which fired during normal quiet periods and churned
        recovery for no reason.
        """
        # Failsafe: if the heartbeat has never landed, the hook may be missing or
        # the loop is still warming up. Degrade safely to "never recover on
        # silence" rather than risk a restart loop on a bad signal. Warn once.
        if self.last_poll_time is None:
            if not self._warned_no_heartbeat:
                logger.warning(
                    "No poll-cycle heartbeat recorded yet. Watchdog is in safe "
                    "mode (no silence-triggered recovery) until the poll loop "
                    "reports a completed getUpdates cycle."
                )
                self._warned_no_heartbeat = True
            return

        elapsed = time.monotonic() - self.last_poll_time

        if elapsed < POLLING_POLL_STALE_THRESHOLD:
            # Poll loop is ticking normally.
            self._was_stale = False
            return

        logger.warning(
            "No getUpdates poll cycle completed for %.0f seconds (threshold: %d). "
            "Diagnosing...",
            elapsed, POLLING_POLL_STALE_THRESHOLD,
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
                "Internet OK but no poll cycle completing, will confirm on "
                "next check before recovering."
            )
            self._was_stale = True
            return

        # Step 3: Second consecutive stale check. The poll loop is wedged.
        logger.warning(
            "No poll cycle for %.0f seconds despite healthy internet "
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
                "Stale polling: internet OK but no getUpdates cycles completing"
            )
            if restarted:
                # Give the poll loop a fresh grace window to reconnect through
                # the restarted API rather than immediately re-triggering.
                self.last_poll_time = time.monotonic()
                self._was_stale = False
                return

        # No local API (or restart failed). The polling library itself is stuck.
        # Exit cleanly so systemd/launchd restarts the entire bot.
        logger.critical(
            "Polling loop appears stuck. Exiting for automatic restart. "
            "Reason: %d seconds with no completed poll cycle, internet OK.",
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

        system = platform.system()

        # Detect the service manager and try to restart
        if system == "Linux":
            # Try user-level systemd first, then system-level
            for cmd in [
                ["systemctl", "--user", "restart", "telegram-bot-api"],
                ["systemctl", "restart", "telegram-bot-api"],
            ]:
                proc = None
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
                except asyncio.TimeoutError:
                    logger.warning("Restart via %s timed out after 30s", " ".join(cmd))
                    if proc is not None:
                        try:
                            proc.kill()
                            await proc.wait()
                        except Exception:
                            pass
                    continue
                except Exception as e:
                    logger.debug("Restart via %s failed: %s", " ".join(cmd), e)
                    continue

        elif system == "Darwin":
            # On macOS the local Bot API runs as a LaunchDaemon labeled
            # com.telegram-bot-api. `launchctl kickstart -k` is the most
            # reliable way to bounce it without reloading the plist.
            #
            # We try the no-sudo path first (works if the daemon is in the
            # current user's gui domain — rare). Then fall back to the
            # system-domain path with sudo, reading the password from
            # ~/.sudo_pass that the install wizard stored mode 0600.
            kickstart_no_sudo = [
                "launchctl", "kickstart", "-k", "system/com.telegram-bot-api",
            ]
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *kickstart_no_sudo,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode == 0:
                    logger.info("Restarted telegram-bot-api via %s",
                                " ".join(kickstart_no_sudo))
                    await asyncio.sleep(5)
                    self.last_update_time = time.monotonic()
                    return True
            except asyncio.TimeoutError:
                logger.warning("kickstart (no-sudo) timed out after 15s")
                if proc is not None:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("kickstart (no-sudo) failed: %s", e)

            sudo_pass_file = Path.home() / ".sudo_pass"
            password: Optional[str] = None
            try:
                if sudo_pass_file.exists():
                    password = sudo_pass_file.read_text(
                        encoding="utf-8"
                    ).strip() or None
            except OSError:
                password = None

            sudo_cmd = ["sudo", "-S", "launchctl", "kickstart", "-k",
                        "system/com.telegram-bot-api"] if password else \
                       ["sudo", "-n", "launchctl", "kickstart", "-k",
                        "system/com.telegram-bot-api"]
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *sudo_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdin_data = (password + "\n").encode() if password else None
                _, _ = await asyncio.wait_for(
                    proc.communicate(input=stdin_data), timeout=30,
                )
                if proc.returncode == 0:
                    logger.info("Restarted telegram-bot-api via %s",
                                " ".join(sudo_cmd[:4] + ["..."]))
                    await asyncio.sleep(5)
                    self.last_update_time = time.monotonic()
                    return True
            except asyncio.TimeoutError:
                logger.warning("kickstart (sudo) timed out after 30s")
                if proc is not None:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("kickstart (sudo) failed: %s", e)

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
