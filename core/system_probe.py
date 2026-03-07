"""
System Capability Probe — runs once on first boot.

Checks what binaries, libraries, and features are available on this machine.
Saves results to data/system_caps.json so the bot knows what works without
checking every time.

Re-run on /update to detect newly installed tools.
"""

import json
import logging
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _check_binary(name: str) -> dict:
    """Check if a binary exists and get its version."""
    path = shutil.which(name)
    if not path:
        return {"available": False}

    # Try to get version
    version = ""
    for flag in ["--version", "-version", "version"]:
        try:
            result = subprocess.run(
                [path, flag],
                capture_output=True, text=True, timeout=5
            )
            output = (result.stdout or result.stderr or "").strip()
            if output:
                # Take first line only
                version = output.split("\n")[0][:100]
                break
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    return {"available": True, "path": path, "version": version}


def _check_python_module(module: str) -> bool:
    """Check if a Python module is importable in the current venv."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _detect_package_manager() -> str:
    """Detect which package manager is available."""
    for mgr, binary in [
        ("apt", "apt-get"), ("dnf", "dnf"), ("yum", "yum"),
        ("pacman", "pacman"), ("zypper", "zypper"), ("apk", "apk"),
        ("brew", "brew"),
    ]:
        if shutil.which(binary):
            return mgr
    return "none"


def _get_ram_gb() -> float:
    """Get total RAM in GB."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return round(int(result.stdout.strip()) / (1024**3), 1)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
    except Exception:
        pass
    return 0


def probe_system(data_dir: Path) -> dict:
    """Run a full system capability probe. Returns and saves the results."""
    caps = {
        "probed_at": datetime.now().isoformat(),
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "ram_gb": _get_ram_gb(),
        "package_manager": _detect_package_manager(),
    }

    # Core binaries
    binaries = [
        "git", "curl", "wget", "ffmpeg", "ffprobe", "sox",
        "node", "npm", "jq", "htop", "tmux",
        "tesseract", "espeak-ng",
        "sqlite3", "aria2c", "zip", "unzip", "tar",
    ]
    caps["binaries"] = {}
    for b in binaries:
        caps["binaries"][b] = _check_binary(b)

    # Python modules (for pip-only skills)
    modules = [
        "PIL", "pydub", "moviepy", "librosa", "playwright",
        "qrcode", "feedparser", "colorthief", "markitdown",
        "fontTools", "deep_translator", "httpx", "bs4",
    ]
    caps["python_modules"] = {}
    for m in modules:
        caps["python_modules"][m] = _check_python_module(m)

    # Skill readiness — based on what's available
    skill_status = {}
    skill_deps = {
        "weather": ["httpx"],
        "translate": ["deep_translator"],
        "ocr": ["tesseract", "PIL"],
        "compress": ["zip", "tar"],
        "downloads": ["aria2c"],
        "summarize": ["httpx", "bs4"],
        "pdf": ["tesseract"],
        "image-editing": ["PIL"],
        "audio-editing": ["ffmpeg", "pydub"],
        "video-editing": ["ffmpeg", "moviepy"],
        "audio-analysis": ["ffmpeg", "librosa"],
        "color-palette": ["colorthief"],
        "text-to-speech": ["espeak-ng"],
        "font-tools": ["fontTools"],
        "git": ["git"],
        "database": ["sqlite3"],
        "api-test": ["curl"],
        "docs": ["markitdown"],
        "qrcode": ["qrcode"],
        "rss": ["feedparser"],
        "regex": [],
        "browser": ["playwright"],
    }

    for skill, deps in skill_deps.items():
        ready = True
        missing = []
        for dep in deps:
            # Check binaries first, then Python modules
            if dep in caps["binaries"]:
                if not caps["binaries"][dep]["available"]:
                    ready = False
                    missing.append(dep)
            elif dep in caps["python_modules"]:
                if not caps["python_modules"][dep]:
                    ready = False
                    missing.append(dep)
            else:
                # Unknown dep — assume available
                pass
        skill_status[skill] = {
            "ready": ready,
            "missing": missing if missing else None,
            "auto_install": True,  # self_install.py can try to install missing deps
        }

    caps["skills"] = skill_status

    # Summary counts
    ready_count = sum(1 for s in skill_status.values() if s["ready"])
    caps["summary"] = {
        "skills_ready": ready_count,
        "skills_total": len(skill_status),
        "skills_need_install": len(skill_status) - ready_count,
    }

    # Save to disk
    caps_file = data_dir / "system_caps.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    caps_file.write_text(json.dumps(caps, indent=2) + "\n")
    logger.info(f"System probe complete: {ready_count}/{len(skill_status)} skills ready")

    return caps


def load_caps(data_dir: Path) -> dict:
    """Load cached system capabilities. Returns empty dict if not probed yet."""
    caps_file = data_dir / "system_caps.json"
    if caps_file.exists():
        try:
            return json.loads(caps_file.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def get_caps_summary(data_dir: Path) -> str:
    """Return a human-readable summary of system capabilities for the LLM context."""
    caps = load_caps(data_dir)
    if not caps:
        return "System capabilities not yet probed."

    lines = [
        f"OS: {caps.get('os', '?')} {caps.get('os_version', '')}",
        f"Arch: {caps.get('arch', '?')} / RAM: {caps.get('ram_gb', '?')} GB",
        f"Package manager: {caps.get('package_manager', '?')}",
    ]

    # Available binaries
    available = [b for b, info in caps.get("binaries", {}).items()
                 if info.get("available")]
    if available:
        lines.append(f"Available tools: {', '.join(available)}")

    missing_bins = [b for b, info in caps.get("binaries", {}).items()
                    if not info.get("available")]
    if missing_bins:
        lines.append(f"Missing tools: {', '.join(missing_bins)}")

    # Skill summary
    summary = caps.get("summary", {})
    if summary:
        lines.append(
            f"Skills: {summary.get('skills_ready', 0)}/{summary.get('skills_total', 0)} ready, "
            f"{summary.get('skills_need_install', 0)} need dependency install"
        )

    return "\n".join(lines)
