#!/usr/bin/env python3
"""ProtonVPN control script for MyOldMachine.

Cross-platform wrapper around the ProtonVPN CLI. Supports Linux and macOS.

The official ProtonVPN GUI app and CLI cannot run simultaneously on either
platform, so this script stops the GUI before issuing CLI commands. The GUI
is NOT restarted automatically -- the user can launch it manually if they
want it back.

Credentials must already be stored on the machine (sign in via the GUI or
CLI once before using this skill).
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _is_mac() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _proton_binary() -> str | None:
    """Locate the ProtonVPN CLI binary, preferring `protonvpn` then `protonvpn-cli`."""
    for candidate in ("protonvpn", "protonvpn-cli"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _run_vpn_cmd(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a ProtonVPN CLI command. Sets DBus on Linux when available."""
    binary = _proton_binary()
    if not binary:
        raise FileNotFoundError(
            "ProtonVPN CLI not found. Install it first: "
            "Linux -> https://protonvpn.com/support/linux-vpn-tool/  "
            "macOS -> brew install --cask protonvpn"
        )
    env = os.environ.copy()
    if _is_linux():
        # Prefer caller-provided session bus; otherwise try the conventional
        # path for the running user. Avoids hardcoding uid 1000.
        if "DBUS_SESSION_BUS_ADDRESS" not in env:
            uid = os.getuid()
            bus = Path(f"/run/user/{uid}/bus")
            if bus.exists():
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _stop_gui_linux() -> bool:
    """Kill the ProtonVPN GUI on Linux. Returns True if it was running."""
    if shutil.which("pgrep") is None:
        return False
    check = subprocess.run(["pgrep", "-f", "protonvpn-app"], capture_output=True, text=True)
    if check.returncode != 0:
        return False
    subprocess.run(["pkill", "-f", "protonvpn-app"], capture_output=True)
    for _ in range(10):
        again = subprocess.run(["pgrep", "-f", "protonvpn-app"], capture_output=True)
        if again.returncode != 0:
            return True
        time.sleep(0.5)
    subprocess.run(["pkill", "-9", "-f", "protonvpn-app"], capture_output=True)
    time.sleep(1)
    return True


def _stop_gui_mac() -> bool:
    """Quit the ProtonVPN GUI on macOS. Returns True if it was running."""
    osascript = shutil.which("osascript")
    if osascript is None:
        return False
    check = subprocess.run(
        [osascript, "-e", 'tell application "System Events" to (name of processes) contains "ProtonVPN"'],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0 or "true" not in check.stdout.lower():
        return False
    subprocess.run(
        [osascript, "-e", 'tell application "ProtonVPN" to quit'],
        capture_output=True,
    )
    for _ in range(10):
        again = subprocess.run(
            [osascript, "-e", 'tell application "System Events" to (name of processes) contains "ProtonVPN"'],
            capture_output=True,
            text=True,
        )
        if again.returncode == 0 and "false" in again.stdout.lower():
            return True
        time.sleep(0.5)
    return True


def _ensure_cli_ready() -> None:
    """Stop the GUI if it's running so the CLI can take over."""
    stopped = _stop_gui_mac() if _is_mac() else _stop_gui_linux()
    if stopped:
        time.sleep(2)


def _public_ip() -> str:
    """Best-effort public IP lookup. Returns 'unknown' on failure."""
    if shutil.which("curl") is None:
        return "unknown"
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5", "https://api.ipify.org"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = result.stdout.strip()
        return out if (result.returncode == 0 and out) else "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _status_linux() -> tuple[bool, str, str]:
    """Linux status via NetworkManager. Returns (connected, server, internal_ip)."""
    if shutil.which("nmcli") is None:
        return False, "", ""
    nm = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
        capture_output=True, text=True,
    )
    vpn_line = next(
        (line for line in nm.stdout.strip().splitlines() if "ProtonVPN" in line),
        None,
    )
    if not vpn_line:
        return False, "", ""
    parts = vpn_line.split(":")
    server = parts[0].replace("ProtonVPN ", "") if parts else ""
    internal_ip = ""
    ip_result = subprocess.run(
        ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "proton0"],
        capture_output=True, text=True,
    )
    for line in ip_result.stdout.strip().splitlines():
        if "IP4.ADDRESS" in line and ":" in line:
            internal_ip = line.split(":", 1)[1].split("/")[0]
            break
    return True, server, internal_ip


def _status_mac() -> tuple[bool, str, str]:
    """macOS status via the protonvpn CLI itself. Returns (connected, server, internal_ip)."""
    try:
        result = _run_vpn_cmd(["status"], timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False, "", ""
    out = result.stdout.lower()
    if "no active" in out or "disconnected" in out or "not connected" in out:
        return False, "", ""
    server = ""
    internal_ip = ""
    for raw in result.stdout.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("server:") or low.startswith("connected to:"):
            server = line.split(":", 1)[1].strip()
        elif low.startswith("ip:") or low.startswith("ip address:"):
            internal_ip = line.split(":", 1)[1].strip()
    return True, server, internal_ip


def cmd_status() -> None:
    """Show VPN status using the platform's most authoritative source."""
    connected, server, internal_ip = _status_mac() if _is_mac() else _status_linux()
    if not connected:
        print("VPN: Disconnected")
        return
    print("VPN: Connected")
    if server:
        print(f"Server: {server}")
    if internal_ip:
        print(f"Internal IP: {internal_ip}")
    print(f"Public IP: {_public_ip()}")


def cmd_connect(country: str | None = None) -> None:
    """Connect to ProtonVPN (fastest server unless country specified)."""
    _ensure_cli_ready()
    args = ["connect"]
    if country:
        args.extend(["--country", country.upper()])
    label = f" ({country.upper()})" if country else " (fastest)"
    print(f"Connecting to ProtonVPN{label}...")
    try:
        result = _run_vpn_cmd(args, timeout=60)
    except FileNotFoundError as e:
        print(str(e))
        sys.exit(1)
    if result.returncode == 0:
        print(result.stdout.strip() or "Connected successfully.")
    else:
        msg = result.stderr.strip() or result.stdout.strip()
        print(f"Connection failed: {msg}")
        sys.exit(1)


def cmd_disconnect() -> None:
    """Disconnect from ProtonVPN."""
    _ensure_cli_ready()
    try:
        result = _run_vpn_cmd(["disconnect"])
    except FileNotFoundError as e:
        print(str(e))
        sys.exit(1)
    if result.returncode == 0:
        print(result.stdout.strip() or "Disconnected.")
        return
    msg = (result.stderr.strip() or result.stdout.strip()).lower()
    if "not connected" in msg or "no active" in msg:
        print("VPN is already disconnected.")
        return
    print(f"Disconnect failed: {result.stderr.strip() or result.stdout.strip()}")
    sys.exit(1)


def cmd_countries() -> None:
    """List the country codes ProtonVPN exposes for this account."""
    _ensure_cli_ready()
    try:
        result = _run_vpn_cmd(["countries", "list"])
    except FileNotFoundError as e:
        print(str(e))
        sys.exit(1)
    if result.returncode == 0:
        print(result.stdout.strip())
    else:
        print(f"Failed to list countries: {result.stderr.strip() or result.stdout.strip()}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="ProtonVPN control (Linux + macOS)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show VPN status")
    connect = sub.add_parser("connect", help="Connect to VPN")
    connect.add_argument("--country", "-c", help="Country code (e.g. NL, US, JP)")
    sub.add_parser("disconnect", help="Disconnect from VPN")
    sub.add_parser("countries", help="List available countries")
    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "connect":
        cmd_connect(args.country)
    elif args.command == "disconnect":
        cmd_disconnect()
    elif args.command == "countries":
        cmd_countries()


if __name__ == "__main__":
    main()
