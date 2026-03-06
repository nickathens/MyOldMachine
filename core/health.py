"""
System Health Monitor.

Tracks disk, CPU, RAM, uptime, and network status.
Provides /health command output and critical alerts.
"""

import logging
import os
import platform
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
            import re
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
            import re
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
    """Check if we have internet connectivity."""
    try:
        # Use curl instead of ping — works consistently across Linux and macOS
        # without platform-specific flag differences
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "3", "-o", "/dev/null", "https://www.google.com"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
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

    if not get_network_status():
        alerts.append("WARNING: No internet connectivity")

    return alerts
