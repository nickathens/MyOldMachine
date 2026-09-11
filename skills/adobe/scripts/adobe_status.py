#!/usr/bin/env python3
"""What Adobe software is actually on this machine, and what can be driven headlessly.

Never assume the suite is installed, signed in, or scriptable. Run this.
Standard library only. macOS only.
"""

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

APPS_DIR = Path("/Applications")
CC_APP = Path("/Applications/Utilities/Adobe Creative Cloud/ACC/Creative Cloud.app")
OOBE = Path.home() / "Library/Application Support/Adobe/OOBE"

# Background services the desktop app starts at login. CCXProcess is the one
# with a public history of idling hot.
SERVICES = [
    ("CCXProcess", "Creative Cloud content sync"),
    ("AdobeIPCBroker", "inter-app messaging"),
    ("Adobe Desktop Service", "desktop app backend"),
    ("Core Sync", "Creative Cloud file sync"),
    ("AdobeGCClient", "Adobe Genuine Service"),
]


def app_version(app: Path) -> str | None:
    """Short version string out of an .app bundle, or None if unreadable."""
    plist = app / "Contents/Info.plist"
    try:
        with plist.open("rb") as fh:
            info = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return None
    return info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")


def find_adobe_apps() -> list[dict]:
    """Every Adobe application bundle in /Applications, with its version.

    Adobe versions its folders (``Adobe After Effects 2026/``) so the app lives
    one level down, not at the top. Look in both places.
    """
    found = []
    if not APPS_DIR.is_dir():
        return found
    for entry in sorted(APPS_DIR.iterdir()):
        if not entry.name.startswith("Adobe"):
            continue
        if entry.suffix == ".app":
            candidates = [entry]
        else:
            candidates = sorted(entry.glob("*.app"))
        for app in candidates:
            found.append(
                {
                    "name": app.stem,
                    "path": str(app),
                    "version": app_version(app),
                }
            )
    return found


def find_aerender() -> str | None:
    """The aerender binary, which ships beside After Effects."""
    on_path = shutil.which("aerender")
    if on_path:
        return on_path
    hits = sorted(APPS_DIR.glob("Adobe After Effects */aerender"))
    return str(hits[-1]) if hits else None


def find_mocha() -> str | None:
    """The bundled Mocha AE, if After Effects is installed."""
    hits = sorted(APPS_DIR.glob("Adobe After Effects */*/mocha*.app"))
    hits += sorted(APPS_DIR.glob("Adobe After Effects */mocha*.app"))
    return str(hits[-1]) if hits else None


def signed_in() -> bool | None:
    """Whether an Adobe ID is signed in on this machine.

    Read the markers Adobe actually writes, in priority order, and return None
    when neither is present. Do NOT infer 'signed in' from the OOBE directory
    merely being non-empty: the installer drops scratch files there before
    anyone has typed a password, so that test reads yes on a machine where
    nothing can be installed, which is the most expensive wrong answer here.
    """
    if not OOBE.is_dir():
        return False
    # Positive markers: the account profile the desktop app writes after a
    # successful sign-in.
    for marker in ("opm.db", "User Profile", "AdobeID"):
        if (OOBE / marker).exists():
            return True
    # Negative marker: written on sign-out and present on a never-signed-in
    # machine. Authoritative when no positive marker is there.
    if (OOBE / "logged_out_guid").exists():
        return False
    return None


def running_services() -> list[tuple[str, str, bool]]:
    try:
        ps = subprocess.run(
            ["ps", "axco", "command"], capture_output=True, text=True, timeout=20
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return [(n, d, False) for n, d in SERVICES]
    lines = {ln.strip() for ln in ps.splitlines()}
    return [(n, d, n in lines) for n, d in SERVICES]


def machine_ram_gb() -> float | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return int(out) / (1024**3)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def cpu_cores() -> int | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return int(out)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def collect() -> dict:
    apps = find_adobe_apps()
    aerender = find_aerender()
    cores = cpu_cores()
    ram = machine_ram_gb()
    return {
        "creative_cloud_installed": CC_APP.exists(),
        "creative_cloud_version": app_version(CC_APP) if CC_APP.exists() else None,
        "signed_in": signed_in(),
        "apps": apps,
        "aerender": aerender,
        "mocha_ae": find_mocha(),
        "services": [
            {"name": n, "role": d, "running": r} for n, d, r in running_services()
        ],
        "cpu_cores": cores,
        "ram_gb": round(ram, 1) if ram else None,
        "ae_recommended_ram_gb": (cores * 4 + 20) if cores else None,
    }


def report(data: dict) -> int:
    print("ADOBE ON THIS MACHINE")
    print("=" * 58)

    if not data["creative_cloud_installed"]:
        print("Creative Cloud desktop app: NOT INSTALLED")
        print("  brew install --cask adobe-creative-cloud")
    else:
        print(f"Creative Cloud desktop app: {data['creative_cloud_version']}")

    si = data["signed_in"]
    print(
        "Signed in:                  "
        + {True: "yes", False: "NO - apps cannot be installed until someone signs in",
           None: "cannot tell"}[si]
    )

    print()
    if data["apps"]:
        print("Applications:")
        for a in data["apps"]:
            print(f"  {a['name']:<38} {a['version'] or '?'}")
    else:
        print("Applications:               none installed yet")
        if data["creative_cloud_installed"] and not si:
            print("  Launch the desktop app and sign in; installing is a click each.")
            print('  open -a "/Applications/Utilities/Adobe Creative Cloud/ACC/Creative Cloud.app"')

    print()
    print("HEADLESS CAPABILITY")
    print("-" * 58)
    if data["aerender"]:
        print(f"  Render an AE project:  YES  ({data['aerender']})")
    else:
        print("  Render an AE project:  no   (After Effects not installed)")
    print("  Author/edit a comp:    no   (ExtendScript needs a live GUI session)")
    print("  Photoshop batch:       no   (same; AppleScript bridge into a GUI only)")

    if data["mocha_ae"]:
        print()
        print("  Mocha AE is present. It is the STRIPPED build: planar track and")
        print("  roto only. No mesh warp, remove, mega plate, insert, stabilise,")
        print("  lens, and no export outside After Effects. Those need Mocha Pro")
        print("  from Boris FX, which no Adobe tier includes.")

    print()
    print("MACHINE")
    print("-" * 58)
    ram, want = data["ram_gb"], data["ae_recommended_ram_gb"]
    if ram and want:
        verdict = "OK" if ram >= want else f"under Adobe's guidance by {want - ram:.0f} GB"
        print(f"  RAM {ram:.0f} GB, {data['cpu_cores']} cores. AE guidance wants {want} GB: {verdict}.")
    running = [s["name"] for s in data["services"] if s["running"]]
    if running:
        print(f"  Adobe background services running: {', '.join(running)}")
    else:
        print("  No Adobe background services running.")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("Adobe Creative Cloud is macOS only; nothing to report here.", file=sys.stderr)
        return 2

    data = collect()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    return report(data)


if __name__ == "__main__":
    sys.exit(main())
