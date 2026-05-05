#!/usr/bin/env python3
"""macOS package URL resolver.

Picks the latest version of a workstation app that is compatible with the
host macOS, by querying upstream listings at install time. Replaces the
previous hardcoded URL table that bit-rotted whenever upstream rotated a
patch version off the mirror (e.g. LibreOffice 25.8.5 -> 25.8.6).

Each app gets its own resolver because each upstream has a different shape:

  - LibreOffice publishes a directory listing of versions at
    download.documentfoundation.org/libreoffice/stable/ and a deterministic
    DMG path under each version dir.
  - Blender publishes per-LTS-branch directories at
    download.blender.org/release/ with DMGs inside each branch.
  - Inkscape exposes DMGs at media.inkscape.org with predictable URL
    patterns but no clean directory index, so we probe known patch
    numbers with HEAD.
  - rclone has a `-current-` URL that always points at latest.
  - ImageMagick has no macOS binary distribution; the resolver returns
    None and the caller skips with a clear note.
  - Chromium has no stable old-macOS binary; same treatment.

Compatibility ceilings for each app are encoded in BRANCH_MIN_MACOS tables
that are short and rarely change. The version itself is always picked
dynamically from upstream. If the listing fetch fails (no network, mirror
down), each resolver falls back to a baked-in last-known-good URL so the
install never hard-fails on a transient outage.

The resolver returns ResolvedDownload describing the URL plus the metadata
the caller needs to install it (DMG vs ZIP, app bundle name, binary name).
"""

from __future__ import annotations

import logging
import platform
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

USER_AGENT = "MyOldMachine/1.0 (+https://github.com/nickathens/MyOldMachine)"
HTTP_TIMEOUT = 15

# Type alias: (major, minor) e.g. (10, 15) for Catalina, (11, 0) for Big Sur.
MacosVersion = tuple[int, int]


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ResolvedDownload:
    """A download chosen dynamically based on host macOS version."""
    url: str
    type: str               # "dmg" or "zip"
    version: str            # The chosen version string, for logging
    compat_note: str        # One-line description for the user/log
    app_name: str = ""      # For type="dmg": the .app bundle inside
    binary: str = ""        # For type="zip": the binary inside
    arch: str = "x86_64"    # "x86_64" or "arm64"


def get_host_macos_version() -> MacosVersion:
    """Detect host macOS major.minor. Returns (0, 0) on non-Darwin."""
    if platform.system() != "Darwin":
        return (0, 0)
    try:
        ver = platform.mac_ver()[0]
    except Exception:
        return (0, 0)
    if not ver:
        return (0, 0)
    parts = ver.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return (0, 0)


def get_host_arch() -> str:
    """Return 'arm64' on Apple Silicon, 'x86_64' otherwise."""
    return "arm64" if platform.machine() == "arm64" else "x86_64"


def resolve(name: str, host_macos: MacosVersion,
            arch: Optional[str] = None) -> Optional[ResolvedDownload]:
    """Return a download URL compatible with `host_macos`, or None.

    `name` is the package key from install/compat.py PACKAGES (e.g. "blender").
    None means: no compatible download is available — the caller should skip
    with a clear note instead of trying to install.
    """
    if arch is None:
        arch = get_host_arch()
    fn = _RESOLVERS.get(name)
    if not fn:
        logger.debug(f"No macOS resolver for package {name!r}")
        return None
    try:
        return fn(host_macos, arch)
    except Exception as e:
        logger.warning(f"Resolver for {name} crashed: {e}")
        return None


# Notes shown to the user when a known-skip package returns None on macOS.
# These describe the absence of a binary distribution, not a transient error.
MACOS_UNSUPPORTED_NOTES: dict[str, str] = {
    "imagemagick": (
        "ImageMagick has no standalone macOS binary distribution; only Homebrew "
        "ships it. Pillow-based skills work without it."
    ),
    "chromium": (
        "No vetted standalone Chromium binary for old macOS. Playwright's bundled "
        "Chromium under ~/Library/Caches/ms-playwright/ covers the browser skill."
    ),
}


def macos_skip_note(name: str) -> str:
    """Return a human-readable explanation for why `name` has no macOS download,
    or '' if no specific note is registered."""
    return MACOS_UNSUPPORTED_NOTES.get(name, "")


# ─────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────


def _fetch(url: str, timeout: int = HTTP_TIMEOUT) -> Optional[str]:
    """GET a URL, return body as text. Returns None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return data.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        logger.debug(f"GET {url} failed: {e}")
        return None
    except Exception as e:
        logger.debug(f"GET {url} unexpected error: {e}")
        return None


def _head_ok(url: str, timeout: int = HTTP_TIMEOUT) -> bool:
    """Return True if the URL responds 2xx or 3xx for a HEAD request.

    Some servers don't implement HEAD properly and return 405 — for those
    we still consider it "alive" because the URL exists; the GET will work.
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}, method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as e:
        if e.code == 405:
            return True  # HEAD not supported — treat as alive
        return False
    except (urllib.error.URLError, TimeoutError):
        return False
    except Exception:
        return False


def _parse_version(s: str) -> tuple[int, ...]:
    """Parse 'X.Y.Z' to (X, Y, Z). Non-numeric segments become 0."""
    out: list[int] = []
    for part in s.split("."):
        m = re.match(r"^(\d+)", part)
        out.append(int(m.group(1)) if m else 0)
    return tuple(out)


def _macos_ge(host: MacosVersion, target: MacosVersion) -> bool:
    """True if host macOS >= target. Handles 10.x and 11+ numbering."""
    return host >= target


# ─────────────────────────────────────────────────────────────────────
# LibreOffice
# ─────────────────────────────────────────────────────────────────────

# Each X.Y branch and the minimum macOS version it supports.
# Updated when TDF announces drops in support — see
# https://wiki.documentfoundation.org/ReleasePlan and the per-release notes.
# 26.x dropped Catalina; 25.8 is the last branch supporting macOS 10.15.
LIBREOFFICE_BRANCH_MIN_MACOS: dict[str, MacosVersion] = {
    "26.2": (11, 0),
    "26.0": (11, 0),
    "25.8": (10, 15),
    "25.2": (10, 15),
    "24.8": (10, 15),
    "24.2": (10, 15),
}

LIBREOFFICE_LISTING = "https://download.documentfoundation.org/libreoffice/stable/"

# Last-known-good fallback per macOS branch, used when the listing is
# unreachable. Picked because they were on the mirror at the time this
# module was written; if upstream rotates them, the dynamic path will
# still find the current version on next attempt.
LIBREOFFICE_FALLBACK: dict[str, str] = {
    "10.15": "25.8.6",
    "11.0": "26.2.3",
}


def _libreoffice_dmg_url(version: str, arch: str) -> str:
    # TDF publishes both x86_64 and aarch64 DMGs. Path naming differs
    # slightly between branches but the modern convention is consistent.
    arch_dir = "x86_64" if arch == "x86_64" else "aarch64"
    arch_suffix = "x86-64" if arch == "x86_64" else "aarch64"
    return (
        f"{LIBREOFFICE_LISTING}{version}/mac/{arch_dir}/"
        f"LibreOffice_{version}_MacOS_{arch_suffix}.dmg"
    )


def _resolve_libreoffice(host: MacosVersion, arch: str) -> Optional[ResolvedDownload]:
    listing = _fetch(LIBREOFFICE_LISTING)
    versions: list[str] = []
    if listing:
        versions = sorted(
            set(re.findall(r'href="(\d+\.\d+\.\d+)/"', listing)),
            key=_parse_version,
        )

    # Walk newest-first, return the first version on a branch the host can run.
    for ver in reversed(versions):
        parts = ver.split(".")
        if len(parts) < 2:
            continue
        branch = f"{parts[0]}.{parts[1]}"
        min_macos = LIBREOFFICE_BRANCH_MIN_MACOS.get(branch)
        if min_macos is None or not _macos_ge(host, min_macos):
            continue
        return ResolvedDownload(
            url=_libreoffice_dmg_url(ver, arch),
            type="dmg",
            app_name="LibreOffice.app",
            version=ver,
            arch=arch,
            compat_note=(
                f"LibreOffice {ver} (branch {branch}, requires macOS "
                f"{min_macos[0]}.{min_macos[1]}+)"
            ),
        )

    # Listing failed (no versions parsed) — fall back to baked-in known-good.
    # LIBREOFFICE_FALLBACK keys are macOS-version floors (the minimum macOS
    # the LibreOffice version supports); values are LibreOffice versions.
    # Walk newest-LibreOffice-first; pick the first whose macOS floor the host meets.
    if not versions:
        fallback_entries: list[tuple[MacosVersion, str]] = []
        for macos_str, lo_ver in LIBREOFFICE_FALLBACK.items():
            try:
                parts = macos_str.split(".")
                macos_floor: MacosVersion = (
                    int(parts[0]), int(parts[1]) if len(parts) > 1 else 0,
                )
            except (ValueError, IndexError):
                continue
            fallback_entries.append((macos_floor, lo_ver))
        fallback_entries.sort(key=lambda x: _parse_version(x[1]), reverse=True)
        for macos_floor, lo_ver in fallback_entries:
            if _macos_ge(host, macos_floor):
                return ResolvedDownload(
                    url=_libreoffice_dmg_url(lo_ver, arch),
                    type="dmg",
                    app_name="LibreOffice.app",
                    version=lo_ver,
                    arch=arch,
                    compat_note=(
                        f"LibreOffice {lo_ver} "
                        f"(offline fallback, upstream listing unreachable)"
                    ),
                )

    logger.info(
        f"No LibreOffice version compatible with macOS {host[0]}.{host[1]}"
    )
    return None


# ─────────────────────────────────────────────────────────────────────
# Blender
# ─────────────────────────────────────────────────────────────────────

# Blender LTS branches and minimum macOS version. The Foundation publishes
# system requirements per release at docs.blender.org. Pre-3.0 LTS used a
# 2.83/2.93 cadence; from 3.0 onward, every X.0 minor release that's not
# .1/.4 is LTS for two years.
# 4.x dropped 10.15 Catalina; 3.6 is the last LTS to support it.
BLENDER_LTS_MIN_MACOS: dict[str, MacosVersion] = {
    "5.0": (11, 0),
    "4.5": (11, 0),
    "4.2": (11, 0),
    "3.6": (10, 15),
    "3.3": (10, 13),
    "2.93": (10, 13),
    "2.83": (10, 13),
}

BLENDER_LISTING = "https://download.blender.org/release/"
BLENDER_FALLBACK: dict[str, tuple[str, str]] = {
    # branch -> (version, dmg_filename_arch_marker)
    "3.6": ("3.6.23", "x64"),
    "4.5": ("4.5.0", "x64"),
}


def _resolve_blender(host: MacosVersion, arch: str) -> Optional[ResolvedDownload]:
    # Pick the highest LTS branch the host can run.
    candidate_branches = sorted(
        (b for b, m in BLENDER_LTS_MIN_MACOS.items() if _macos_ge(host, m)),
        key=_parse_version,
        reverse=True,
    )
    if not candidate_branches:
        logger.info(f"No Blender LTS branch compatible with macOS {host[0]}.{host[1]}")
        return None

    arch_marker = "arm64" if arch == "arm64" else "x64"

    for branch in candidate_branches:
        branch_url = f"{BLENDER_LISTING}Blender{branch}/"
        listing = _fetch(branch_url)
        if listing:
            # Match e.g. blender-3.6.23-macos-x64.dmg
            pattern = (
                rf'href="(blender-(\d+\.\d+\.\d+)-macos-{re.escape(arch_marker)}\.dmg)"'
            )
            matches = re.findall(pattern, listing)
            if matches:
                # Sort by version descending and take first
                matches.sort(key=lambda m: _parse_version(m[1]), reverse=True)
                fname, ver = matches[0]
                min_macos = BLENDER_LTS_MIN_MACOS[branch]
                return ResolvedDownload(
                    url=f"{branch_url}{fname}",
                    type="dmg",
                    app_name="Blender.app",
                    version=ver,
                    arch=arch,
                    compat_note=(
                        f"Blender {ver} LTS (branch {branch}, requires macOS "
                        f"{min_macos[0]}.{min_macos[1]}+)"
                    ),
                )

        # Listing fetch failed — fall back to baked-in known-good for this branch
        fallback = BLENDER_FALLBACK.get(branch)
        if fallback:
            ver, _fb_arch_marker = fallback
            # Always use the requested arch in the URL. Blender publishes
            # both x64 and arm64 DMGs for every release, so the marker on
            # the fallback record is just a hint about what was tested.
            url = f"{branch_url}blender-{ver}-macos-{arch_marker}.dmg"
            min_macos = BLENDER_LTS_MIN_MACOS[branch]
            return ResolvedDownload(
                url=url,
                type="dmg",
                app_name="Blender.app",
                version=ver,
                arch=arch,
                compat_note=(
                    f"Blender {ver} LTS (offline fallback, "
                    f"requires macOS {min_macos[0]}.{min_macos[1]}+)"
                ),
            )

    return None


# ─────────────────────────────────────────────────────────────────────
# Inkscape
# ─────────────────────────────────────────────────────────────────────

# Inkscape 1.x supports macOS 10.13+. 1.x is the only modern series; 0.92
# is too old to bother with. We probe known patch numbers in descending
# order and return the highest URL that responds 2xx/3xx.
INKSCAPE_MIN_MACOS: MacosVersion = (10, 13)
INKSCAPE_BRANCHES: list[tuple[int, int]] = [(1, 4), (1, 3)]
INKSCAPE_MAX_PATCH = 9   # probe up to .9 in each branch
INKSCAPE_FALLBACK_VERSION = "1.4.3"


def _inkscape_dmg_url(version: str, arch: str) -> str:
    suffix = "arm64" if arch == "arm64" else "x86_64"
    return f"https://media.inkscape.org/dl/resources/file/Inkscape-{version}_{suffix}.dmg"


def _resolve_inkscape(host: MacosVersion, arch: str) -> Optional[ResolvedDownload]:
    if not _macos_ge(host, INKSCAPE_MIN_MACOS):
        logger.info(
            f"Inkscape requires macOS {INKSCAPE_MIN_MACOS[0]}.{INKSCAPE_MIN_MACOS[1]}+, "
            f"host has {host[0]}.{host[1]}"
        )
        return None

    # Probe newest-first across known major.minor branches
    for major, minor in INKSCAPE_BRANCHES:
        for patch in range(INKSCAPE_MAX_PATCH, -1, -1):
            ver = f"{major}.{minor}.{patch}" if patch > 0 else f"{major}.{minor}"
            url = _inkscape_dmg_url(ver, arch)
            if _head_ok(url):
                return ResolvedDownload(
                    url=url,
                    type="dmg",
                    app_name="Inkscape.app",
                    version=ver,
                    arch=arch,
                    compat_note=(
                        f"Inkscape {ver} (requires macOS "
                        f"{INKSCAPE_MIN_MACOS[0]}.{INKSCAPE_MIN_MACOS[1]}+)"
                    ),
                )

    # Network down — return baked-in last-known-good
    fallback_url = _inkscape_dmg_url(INKSCAPE_FALLBACK_VERSION, arch)
    return ResolvedDownload(
        url=fallback_url,
        type="dmg",
        app_name="Inkscape.app",
        version=INKSCAPE_FALLBACK_VERSION,
        arch=arch,
        compat_note=(
            f"Inkscape {INKSCAPE_FALLBACK_VERSION} (offline fallback, "
            f"all probes failed)"
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# rclone
# ─────────────────────────────────────────────────────────────────────


def _resolve_rclone(host: MacosVersion, arch: str) -> Optional[ResolvedDownload]:
    # rclone publishes a `current` URL that always points at the latest
    # release. Works on every macOS version that can run modern Go binaries
    # (10.13+). No version probing needed.
    if not _macos_ge(host, (10, 13)):
        return None
    arch_suffix = "arm64" if arch == "arm64" else "amd64"
    return ResolvedDownload(
        url=f"https://downloads.rclone.org/rclone-current-osx-{arch_suffix}.zip",
        type="zip",
        binary="rclone",
        version="current",
        arch=arch,
        compat_note="rclone (official static binary, all macOS versions)",
    )


# ─────────────────────────────────────────────────────────────────────
# ImageMagick / Chromium — no macOS binary distribution
# ─────────────────────────────────────────────────────────────────────


def _resolve_imagemagick(host: MacosVersion, arch: str) -> Optional[ResolvedDownload]:
    # ImageMagick has no official macOS binary tarball. The recommended
    # path is Homebrew. On old macOS where brew is broken, the caller
    # should skip ImageMagick — Pillow-based skills still work via pip.
    return None


def _resolve_chromium(host: MacosVersion, arch: str) -> Optional[ResolvedDownload]:
    # No vetted standalone Chromium binary for old macOS. The browser skill
    # uses Playwright, which downloads its own bundled Chromium under
    # ~/Library/Caches/ms-playwright/ — that path is independent of any
    # global chromium binary, so the skill works even when this returns None.
    return None


# ─────────────────────────────────────────────────────────────────────
# Resolver registry
# ─────────────────────────────────────────────────────────────────────


_RESOLVERS: dict[str, Callable[[MacosVersion, str], Optional[ResolvedDownload]]] = {
    "libreoffice": _resolve_libreoffice,
    "blender": _resolve_blender,
    "inkscape": _resolve_inkscape,
    "rclone": _resolve_rclone,
    "imagemagick": _resolve_imagemagick,
    "chromium": _resolve_chromium,
}


# ─────────────────────────────────────────────────────────────────────
# CLI for manual probing
# ─────────────────────────────────────────────────────────────────────


def _main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Probe macOS upstream URLs for compatible versions."
    )
    parser.add_argument("name", nargs="?", help="package name (libreoffice, blender, ...)")
    parser.add_argument(
        "--macos", default="",
        help="host macOS version (e.g. 10.15 or 14.4). Defaults to detected.",
    )
    parser.add_argument(
        "--arch", default="",
        choices=["", "x86_64", "arm64"],
        help="host arch. Defaults to detected.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="resolve all known packages",
    )
    args = parser.parse_args()

    if args.macos:
        try:
            parts = args.macos.split(".")
            host: MacosVersion = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (ValueError, IndexError):
            print(f"Invalid --macos: {args.macos}")
            return 2
    else:
        host = get_host_macos_version()
    arch = args.arch or get_host_arch()

    targets = list(_RESOLVERS.keys()) if args.all else (
        [args.name] if args.name else []
    )
    if not targets:
        parser.print_help()
        return 2

    results: dict[str, Optional[dict]] = {}
    for name in targets:
        r = resolve(name, host, arch)
        results[name] = (
            None if r is None else {
                "url": r.url, "type": r.type, "version": r.version,
                "compat_note": r.compat_note, "app_name": r.app_name,
                "binary": r.binary, "arch": r.arch,
            }
        )

    print(json.dumps({
        "host_macos": f"{host[0]}.{host[1]}",
        "arch": arch,
        "resolved": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
