"""
System Capability Probe — runs on first boot and nightly.

Checks what binaries, libraries, and features are available on this machine.
Saves results to data/system_caps.json so the bot knows what works without
checking every time.

Re-run on /update to detect newly installed tools.
Runs nightly to catch drift from system updates.
"""

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EXTRA_PATH_DIRS = [
    Path.home() / ".local" / "bin",
    Path.home() / ".cargo" / "bin",
    Path.home() / ".npm-global" / "bin",
    Path("/snap/bin"),
    Path("/usr/local/bin"),
]


def _augment_path():
    """Ensure PATH includes common tool directories.

    Scheduler subprocesses may not load .bashrc, so we explicitly
    add common directories where tools get installed.
    """
    current = os.environ.get("PATH", "")
    dirs = current.split(os.pathsep)
    for d in EXTRA_PATH_DIRS:
        ds = str(d)
        try:
            exists = ds not in dirs and d.is_dir()
        except OSError:
            exists = False
        if exists:
            dirs.insert(0, ds)
    os.environ["PATH"] = os.pathsep.join(dirs)


def _is_usage_text(line: str) -> bool:
    """Return True if a line looks like usage/help output, not a version."""
    lower = line.lower()
    if lower.startswith(("usage:", "use:", "options:")):
        return True
    if "[-" in line and "]" in line:
        return True
    if lower.startswith(("a modern", "the ", "an ")):
        return True
    return False


def _extract_version(output: str) -> str:
    """Extract a clean version string from command output."""
    for line in output.split("\n")[:5]:
        line = line.strip()
        if not line:
            continue
        if _is_usage_text(line):
            continue
        match = re.search(r"[vV]?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)", line)
        if match:
            return match.group(1)
    return ""


def _check_binary(name: str, aliases: list = None) -> dict:
    """Check if a binary exists and get its version.

    Supports alias lookup and improved multi-line version extraction.
    """
    path = shutil.which(name)
    if not path and aliases:
        for alias in aliases:
            path = shutil.which(alias)
            if path:
                break
    if not path:
        return {"available": False}

    version = ""
    for flag in ["--version", "-version", "version"]:
        try:
            result = subprocess.run(
                [path, flag],
                capture_output=True, text=True, timeout=5,
            )
            output = (result.stdout or result.stderr or "").strip()
            if not output:
                continue
            extracted = _extract_version(output)
            if extracted:
                version = extracted
                break
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError,
                PermissionError):
            continue

    return {"available": True, "path": path, "version": version}


def _check_python_module(module: str) -> bool:
    """Check if a Python module is importable in the current venv."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=5,
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


def _check_flatpak_apps() -> dict:
    """Check for Flatpak-installed applications. Returns {app_id: True}."""
    if not shutil.which("flatpak"):
        return {}
    try:
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            apps = {}
            for line in result.stdout.strip().splitlines():
                app_id = line.strip()
                if app_id:
                    apps[app_id] = True
            return apps
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return {}


def _get_snap_versions() -> dict:
    """Get installed snap package names and versions. Returns {name: version}."""
    if not shutil.which("snap"):
        return {}
    try:
        result = subprocess.run(
            ["snap", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
        snaps = {}
        for line in result.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                snaps[parts[0]] = parts[1]
        return snaps
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


def _get_ram_gb() -> float:
    """Get total RAM in GB, snapped to common sizes."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                raw_gb = int(result.stdout.strip()) / (1024 ** 3)
                common = [1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128]
                return min(common, key=lambda x: abs(x - raw_gb))
        else:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if "MemTotal" in line:
                        kb = int(line.split()[1])
                        raw_gb = kb / (1024 * 1024)
                        common = [1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128]
                        return min(common, key=lambda x: abs(x - raw_gb))
    except (OSError, ValueError):
        pass
    return 0


def _get_cpu_info() -> dict:
    """Get CPU model name and core count."""
    info = {"cores": os.cpu_count() or 0, "model": ""}

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                info["model"] = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if "model name" in line:
                        cpu = line.split(":", 1)[1].strip()
                        cpu = cpu.replace("(R)", "").replace("(TM)", "")
                        cpu = cpu.replace(" CPU", "")
                        cpu = re.sub(r"\s+", " ", cpu).strip()
                        info["model"] = cpu
                        break
        except OSError:
            info["model"] = platform.processor() or ""

    return info


def _get_disk_free_gb() -> float:
    """Get free disk space on the home partition in GB."""
    try:
        usage = shutil.disk_usage(Path.home())
        return round(usage.free / (1024 ** 3), 1)
    except OSError:
        return 0


def _detect_storage_type() -> str:
    """Detect whether the home filesystem is on NVMe, SSD, or HDD."""
    if platform.system() != "Linux":
        return "Unknown"

    home_device = _get_home_device()

    try:
        result = subprocess.run(
            ["lsblk", "-d", "-o", "NAME,ROTA,TRAN", "--noheadings"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return "Unknown"

        devices = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            if name.startswith(("loop", "ram", "dm-", "sr")):
                continue
            rotational = parts[1]
            transport = parts[2] if len(parts) > 2 else ""
            devices[name] = (rotational, transport)

        if home_device:
            for dev_name, (rota, tran) in devices.items():
                if home_device.startswith(dev_name):
                    if "nvme" in dev_name or "nvme" in tran:
                        return "NVMe"
                    return "SSD" if rota == "0" else "HDD"

        for dev_name, (rota, tran) in devices.items():
            if "nvme" in dev_name or "nvme" in tran:
                return "NVMe"
        for dev_name, (rota, tran) in devices.items():
            if rota == "0":
                return "SSD"
        for dev_name, (rota, tran) in devices.items():
            if rota == "1":
                return "HDD"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "Unknown"


def _get_home_device() -> str:
    """Find the block device name backing the home directory."""
    try:
        result = subprocess.run(
            ["df", "--output=source", str(Path.home())],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 2:
                source = lines[1].strip()
                base = source.replace("/dev/", "")
                if "nvme" in base:
                    base = re.sub(r"p\d+$", "", base)
                else:
                    base = re.sub(r"\d+$", "", base)
                return base
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _detect_gpu() -> dict:
    """Detect GPU model and VRAM. Returns dict with name and vram_gb."""
    info = {"available": False, "name": "", "vram_gb": 0}

    # NVIDIA via nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(", ")
            if len(parts) == 2:
                info["available"] = True
                info["name"] = parts[0].strip()
                try:
                    info["vram_gb"] = round(int(parts[1].strip()) / 1024)
                except ValueError:
                    pass
                return info
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # macOS: system_profiler
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "Chipset Model" in line:
                        info["available"] = True
                        info["name"] = line.split(":", 1)[1].strip()
                    elif "VRAM" in line:
                        match = re.search(r"(\d+)\s*(MB|GB)", line)
                        if match:
                            val = int(match.group(1))
                            if match.group(2) == "MB":
                                info["vram_gb"] = round(val / 1024)
                            else:
                                info["vram_gb"] = val
                if info["available"]:
                    return info
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fallback: lspci (Linux)
    if platform.system() == "Linux":
        try:
            result = subprocess.run(
                ["lspci"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "VGA" in line or "3D" in line:
                        parts = line.split(": ", 1)
                        if len(parts) > 1:
                            info["available"] = True
                            info["name"] = parts[1].strip()
                            return info
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return info


def probe_system(data_dir: Path) -> dict:
    """Run a full system capability probe. Returns and saves the results."""
    _augment_path()

    cpu_info = _get_cpu_info()
    caps = {
        "probed_at": datetime.now().isoformat(),
        "os": platform.system(),
        "os_version": platform.version(),
        "os_release": _get_os_release(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "cpu_model": cpu_info["model"],
        "cpu_cores": cpu_info["cores"],
        "ram_gb": _get_ram_gb(),
        "disk_free_gb": _get_disk_free_gb(),
        "storage_type": _detect_storage_type(),
        "package_manager": _detect_package_manager(),
    }

    # GPU
    gpu = _detect_gpu()
    caps["gpu"] = gpu

    # Core binaries (with alias support)
    binaries_to_check = [
        ("git", None),
        ("curl", None),
        ("wget", None),
        ("ffmpeg", None),
        ("ffprobe", None),
        ("sox", None),
        ("node", None),
        ("npm", None),
        ("jq", None),
        ("htop", None),
        ("tmux", None),
        ("tesseract", None),
        ("espeak-ng", None),
        ("sqlite3", None),
        ("aria2c", None),
        ("zip", None),
        ("unzip", None),
        ("tar", None),
        ("blender", None),
        ("gimp", None),
        ("inkscape", None),
        ("soffice", None),
        ("convert", None),
        ("rclone", None),
        ("chromium", ["chromium-browser"]),
        ("nb", None),
        ("fluidsynth", None),
        ("lilypond", None),
        ("docker", None),
        ("lighthouse", None),
        ("compare", None),
        ("rg", None),
        ("fdfind", ["fd"]),
        ("batcat", ["bat"]),
        ("eza", None),
        ("pandoc", None),
        ("hugo", None),
        ("cargo", None),
        ("rustc", None),
    ]

    caps["binaries"] = {}
    for item in binaries_to_check:
        if isinstance(item, tuple):
            name, aliases = item
        else:
            name, aliases = item, None
        caps["binaries"][name] = _check_binary(name, aliases)

    # Python modules (for pip-only skills)
    modules = [
        "PIL", "pydub", "moviepy", "librosa", "playwright",
        "qrcode", "feedparser", "colorthief", "markitdown",
        "fontTools", "deep_translator", "httpx", "bs4",
        "openpyxl", "termgraph", "buku", "realesrgan",
        "mingus", "pretty_midi", "mido", "basic_pitch",
        "music21", "scipy", "demucs", "whisper", "yaml",
    ]
    caps["python_modules"] = {}
    for m in modules:
        caps["python_modules"][m] = _check_python_module(m)

    # Snap packages (Linux)
    snap_versions = _get_snap_versions()
    caps["snap_available"] = shutil.which("snap") is not None
    caps["snap_packages"] = snap_versions

    # Map snap packages to binaries (if binary not found in PATH but is a snap)
    _snap_to_binary = {
        "blender": "blender",
        "godot-4": "godot-4",
        "musescore": "musescore",
    }
    for snap_name, binary_name in _snap_to_binary.items():
        if snap_name in snap_versions and binary_name in caps["binaries"]:
            if not caps["binaries"][binary_name]["available"]:
                caps["binaries"][binary_name] = {
                    "available": True,
                    "path": f"snap run {snap_name}",
                    "version": snap_versions[snap_name],
                }

    # Flatpak apps (Linux)
    flatpak_apps = _check_flatpak_apps()
    caps["flatpak_available"] = shutil.which("flatpak") is not None
    caps["flatpak_apps"] = flatpak_apps

    _flatpak_to_binary = {
        "org.blender.Blender": "blender",
        "org.gimp.GIMP": "gimp",
        "org.inkscape.Inkscape": "inkscape",
        "org.libreoffice.LibreOffice": "soffice",
        "org.chromium.Chromium": "chromium",
    }
    for app_id, binary_name in _flatpak_to_binary.items():
        if app_id in flatpak_apps and binary_name in caps["binaries"]:
            if not caps["binaries"][binary_name]["available"]:
                caps["binaries"][binary_name] = {
                    "available": True,
                    "path": f"flatpak run {app_id}",
                    "version": "(flatpak)",
                }

    # Skill readiness
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
        "blender": ["blender"],
        "gimp": ["gimp"],
        "inkscape": ["inkscape"],
        "spreadsheet": ["soffice", "openpyxl"],
        "scraper": ["playwright"],
        "media": ["playwright"],
        "icon-gen": ["PIL", "convert"],
        "sprite-gen": ["PIL"],
        "code-scaffold": [],
        "charts": ["termgraph"],
        "notes": ["nb"],
        "bookmarks": ["buku"],
        "cloud-sync": ["rclone"],
        "web-build": ["node"],
        "upscale": ["realesrgan"],
        "midi": ["mido"],
        "midi-to-audio": ["fluidsynth", "ffmpeg"],
        "sheet-music": ["lilypond"],
        "music-theory": ["music21"],
        "audio-to-midi": ["basic_pitch"],
        "algorithmic-composition": ["mingus", "pretty_midi"],
        "sound-design": ["scipy"],
        "stems": ["ffmpeg", "demucs"],
        "voice": ["ffmpeg", "whisper"],
        "docker-services": ["docker"],
        "lighthouse": ["lighthouse"],
        "screenshot-diff": ["compare", "PIL"],
        "workflow": ["yaml"],
        "research": ["httpx", "bs4"],
        "price-monitor": ["httpx", "bs4"],
        "background-removal": ["PIL"],
        "impeccable": [],
        "image-gen": ["httpx"],
    }

    for skill, deps in skill_deps.items():
        ready = True
        missing = []
        for dep in deps:
            if dep in caps["binaries"]:
                if not caps["binaries"][dep]["available"]:
                    ready = False
                    missing.append(dep)
            elif dep in caps["python_modules"]:
                if not caps["python_modules"][dep]:
                    ready = False
                    missing.append(dep)
        skill_status[skill] = {
            "ready": ready,
            "missing": missing if missing else None,
            "auto_install": True,
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
    caps_file.write_text(json.dumps(caps, indent=2) + "\n", encoding="utf-8")
    logger.info(f"System probe complete: {ready_count}/{len(skill_status)} skills ready")

    return caps


def _get_os_release() -> str:
    """Get a human-friendly OS release string."""
    if platform.system() == "Darwin":
        ver = platform.mac_ver()[0]
        return f"macOS {ver}" if ver else "macOS"

    try:
        result = subprocess.run(
            ["lsb_release", "-d", "-s"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: /etc/os-release
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass

    return f"{platform.system()} {platform.release()}"


def load_caps(data_dir: Path) -> dict:
    """Load cached system capabilities. Returns empty dict if not probed yet."""
    caps_file = data_dir / "system_caps.json"
    if caps_file.exists():
        try:
            return json.loads(caps_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError, UnicodeDecodeError):
            return {}
    return {}


def get_caps_summary(data_dir: Path) -> str:
    """Return a human-readable summary of system capabilities for the LLM context."""
    caps = load_caps(data_dir)
    if not caps:
        return "System capabilities not yet probed."

    lines = []

    # Machine info
    os_release = caps.get("os_release", "")
    if not os_release:
        os_release = f"{caps.get('os', '?')} {caps.get('os_version', '')}"
    lines.append(f"OS: {os_release}")

    cpu_model = caps.get("cpu_model", "")
    cpu_cores = caps.get("cpu_cores", "?")
    if cpu_model:
        lines.append(f"CPU: {cpu_model} ({cpu_cores} cores)")
    else:
        lines.append(f"CPU cores: {cpu_cores}")

    ram = caps.get("ram_gb", "?")
    disk_free = caps.get("disk_free_gb", "?")
    storage_type = caps.get("storage_type", "")
    storage_str = f" {storage_type}" if storage_type and storage_type != "Unknown" else ""
    lines.append(f"RAM: {ram} GB / Disk free: {disk_free} GB{storage_str}")

    # GPU
    gpu = caps.get("gpu", {})
    if isinstance(gpu, dict) and gpu.get("available"):
        gpu_str = gpu.get("name", "Unknown")
        vram = gpu.get("vram_gb", 0)
        if vram:
            gpu_str += f" ({vram}GB VRAM)"
        lines.append(f"GPU: {gpu_str}")

    lines.append(f"Arch: {caps.get('arch', '?')} / Package manager: {caps.get('package_manager', '?')}")

    # Available binaries
    available = [b for b, info in caps.get("binaries", {}).items()
                 if info.get("available")]
    if available:
        lines.append(f"Available tools: {', '.join(sorted(available))}")

    missing_bins = [b for b, info in caps.get("binaries", {}).items()
                    if not info.get("available")]
    if missing_bins:
        lines.append(f"Missing tools: {', '.join(sorted(missing_bins))}")

    # Snap packages
    snap_pkgs = caps.get("snap_packages", {})
    if snap_pkgs:
        snap_list = [f"{name} ({ver})" for name, ver in snap_pkgs.items()]
        lines.append(f"Snap packages: {', '.join(snap_list)}")

    # Flatpak apps
    flatpak_apps = caps.get("flatpak_apps", {})
    if flatpak_apps:
        lines.append(f"Flatpak apps: {', '.join(flatpak_apps.keys())}")
    elif caps.get("flatpak_available"):
        lines.append("Flatpak: installed (no apps)")

    # Skill summary
    summary = caps.get("summary", {})
    if summary:
        lines.append(
            f"Skills: {summary.get('skills_ready', 0)}/{summary.get('skills_total', 0)} ready, "
            f"{summary.get('skills_need_install', 0)} need dependency install"
        )

    return "\n".join(lines)
