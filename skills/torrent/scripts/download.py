#!/usr/bin/env python3
"""Download a torrent (magnet or .torrent URL) via aria2c.

VPN-gated by default: refuses to start unless ProtonVPN is connected.
Override with --no-vpn for trusted content (Linux ISOs, public domain, own backups).

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


def _vpn_connected_linux() -> bool:
    """nmcli check for an active ProtonVPN connection. Mirrors the vpn skill."""
    if shutil.which("nmcli") is None:
        return False
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.strip().splitlines():
        if "ProtonVPN" in line:
            return True
    return False


def _vpn_connected_mac() -> bool:
    """Shell out to the protonvpn CLI and parse `status` output."""
    binary = shutil.which("protonvpn") or shutil.which("protonvpn-cli")
    if binary is None:
        return False
    try:
        result = subprocess.run(
            [binary, "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    out = result.stdout.lower()
    if "no active" in out or "disconnected" in out or "not connected" in out:
        return False
    # If status command succeeds and doesn't contain a disconnected marker,
    # treat as connected.
    return bool(out.strip())


def vpn_connected() -> bool:
    """Cross-platform ProtonVPN reachability check."""
    if platform.system() == "Darwin":
        return _vpn_connected_mac()
    return _vpn_connected_linux()


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

    if not args.no_vpn:
        if not vpn_connected():
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
