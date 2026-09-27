#!/usr/bin/env python3
"""Download a torrent (magnet or .torrent URL) via aria2c.

VPN-gated by default: refuses to start unless ProtonVPN is connected. On Linux
it also binds every aria2c socket (peers, DHT, listen port) to the VPN's own
interface, so if the tunnel drops mid-download the transfer stops instead of
carrying on over the home line (macOS: check only, see vpn_gate). Override
with --no-vpn for trusted content (Linux ISOs, public domain, own backups).

No seeding after download (--seed-time=0) to minimize exposure window.
Lands in ~/Downloads/torrents/.
"""

import argparse
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

DOWNLOAD_DIR = Path.home() / "Downloads" / "torrents"


def _vpn_device_linux():
    """The network device of the active ProtonVPN connection, or None.

    Read from nmcli's NAME:TYPE:DEVICE line for the connection, the same
    listing the vpn skill reads, rather than assuming a device name.
    """
    if shutil.which("nmcli") is None:
        return None
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().splitlines():
        if "ProtonVPN" in line:
            device = line.rsplit(":", 1)[-1].strip()
            return device or None
    return None


def _vpn_connected_mac() -> bool:
    """Detect an active VPN tunnel via `scutil --nwi`.

    ProtonVPN on macOS ships only a GUI app (no CLI), so we read the system
    network state instead. When any IKEv2/IPSec tunnel is up, `scutil --nwi`
    lists the tunnel interface with a literal "VPN server :" line. That string
    is absent whenever no VPN is connected.
    """
    if shutil.which("scutil") is None:
        return False
    try:
        result = subprocess.run(
            ["scutil", "--nwi"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    return "VPN server" in result.stdout


def vpn_gate():
    """(connected, device): is a VPN up, and which device to bind aria2c to.

    On Linux the ProtonVPN connection's own device is returned, so every
    aria2c socket can be bound to it. On macOS the ProtonVPN app has no CLI
    that names its tunnel, so only the check is made and nothing is bound.
    """
    if platform.system() == "Darwin":
        return _vpn_connected_mac(), None
    device = _vpn_device_linux()
    return device is not None, device


def vpn_connected() -> bool:
    """Cross-platform ProtonVPN reachability check."""
    return vpn_gate()[0]


def main():
    parser = argparse.ArgumentParser(
        description="Download a torrent via aria2c with optional VPN gate."
    )
    parser.add_argument(
        "--magnet",
        required=True,
        help="Magnet link or .torrent URL",
    )
    parser.add_argument(
        "--no-vpn",
        action="store_true",
        help="Skip VPN check (only for trusted content)",
    )
    parser.add_argument(
        "--dir",
        default=str(DOWNLOAD_DIR),
        help=f"Download directory (default: {DOWNLOAD_DIR})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress aria2c progress (still shows summary on completion)",
    )
    args = parser.parse_args()

    if shutil.which("aria2c") is None:
        sys.stderr.write(
            "aria2c not found in PATH. Install: "
            "Linux -> sudo apt install aria2  macOS -> brew install aria2\n"
        )
        sys.exit(2)

    magnet = args.magnet.strip()
    if not magnet.startswith(("magnet:", "http://", "https://")):
        sys.stderr.write(f"--magnet must be a magnet: URI or http(s):// URL, got: {magnet[:60]}\n")
        sys.exit(2)

    device = None
    if not args.no_vpn:
        connected, device = vpn_gate()
        if not connected:
            sys.stderr.write(
                "ProtonVPN is not connected. Refusing to start download.\n"
                "Connect first via the vpn skill (vpn.py connect --country NL).\n"
                "Or pass --no-vpn if this is trusted content (Linux ISO, public domain).\n"
            )
            sys.exit(3)

    download_dir = Path(args.dir).expanduser()
    download_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "aria2c",
        "--dir", str(download_dir),
        "--seed-time=0",
        "--summary-interval=10",
        "--max-overall-upload-limit=1K",
        "--file-allocation=falloc",
        magnet,
    ]
    if device:
        # aria2c binds every socket it opens, DHT and the listen port
        # included, to this interface's address (measured with --interface=lo
        # on aria2 1.37.0: UDP DHT and TCP listen both on 127.0.0.1). When the
        # tunnel drops, that address is gone and nothing can go out another way.
        cmd.insert(1, f"--interface={device}")
    if args.quiet:
        cmd.insert(1, "--quiet=true")

    sys.stderr.write(f"Starting download into {download_dir}\n")
    started_at = time.time()
    try:
        result = subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        sys.stderr.write("\nDownload interrupted by user.\n")
        sys.exit(130)

    if result.returncode != 0:
        sys.stderr.write(f"aria2c exited with code {result.returncode}\n")
        sys.exit(result.returncode)

    new_files = [
        p for p in download_dir.rglob("*")
        if p.is_file()
        and not p.name.endswith(".aria2")
        and p.stat().st_mtime >= started_at - 5
    ]
    new_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    total_bytes = sum(p.stat().st_size for p in new_files)
    print("Download complete.")
    print(f"Directory: {download_dir}")
    print(f"Files: {len(new_files)}, total {total_bytes / (1024 * 1024):.1f} MB")
    for p in new_files[:10]:
        size_mb = p.stat().st_size / (1024 * 1024)
        print(f"  {p.relative_to(download_dir)}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
