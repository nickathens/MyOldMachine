#!/usr/bin/env python3
"""
Bot startup cleanup — kill stale processes and clean temp files.

Called early in bot.py main() before the event loop starts.
Closes the gap where Stop hooks never fire (bot crash, reboot, kill -9).

Cross-platform: works on Linux and macOS.
"""

import glob
import logging
import os
import platform
import shutil
import signal
import subprocess
import time

logger = logging.getLogger("startup_cleanup")

IS_MACOS = platform.system() == "Darwin"

# Process patterns that indicate orphaned skill processes
BROWSER_PATTERNS = [
    "chrome-headless-shell",
    "chromium --headless",
    "chromium-browser --headless",
]

SKILL_PROCESS_PATTERNS = [
    # browser/scraper/media (Playwright)
    "playwright",
    "browser.py",
    # blender
    "blender --background",
    "blender -b ",
    # stems
    "demucs",
    # gimp
    "gimp -i",
    "gimp --no-interface",
    # inkscape
    "inkscape --pipe",
    "inkscape --export",
    # godot
    "godot --headless",
    # aria2c (downloads)
    "aria2c ",
    # lighthouse
    "lighthouse ",
]

# Browser daemon state files
BROWSER_STATE_FILES = [
    "/tmp/browser_daemon.sock",
    "/tmp/browser_daemon.pid",
    "/tmp/browser_daemon_state.json",
    "/tmp/browser_storage.json",
    "/tmp/browser_refs.json",
]

# Temp file patterns to clean (glob)
TEMP_PATTERNS = [
    "/tmp/playwright_videos_*",
    "/tmp/screenshot_*.png",
    "/tmp/scrape_*.html",
    "/tmp/moviepy_*",
    "/tmp/tmp*.mp4",
    "/tmp/tmp*.avi",
    "/tmp/tmp*.mkv",
    "/tmp/tmp*.wav",
    "/tmp/tmp*.mp3",
    "/tmp/tmp*.ogg",
    "/tmp/tmp*.png",
    "/tmp/tmp*.jpg",
    "/tmp/tmp*.gif",
    "/tmp/tmp*.svg",
    "/tmp/tmp*.pdf",
    "/tmp/separated/*",
    "/tmp/skill_hooks/",
]

# Min age for temp files before cleanup (seconds)
TEMP_MIN_AGE = 3600  # 1 hour


def _get_all_pids() -> list[tuple[int, str]]:
    """Get all running PIDs and their command lines. Cross-platform."""
    pids = []
    exclude = {os.getpid(), os.getppid()}
    try:
        if IS_MACOS:
            cmd = ["ps", "-ax", "-o", "pid,args"]
        else:
            cmd = ["ps", "-eo", "pid,args", "--no-headers"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        start = 1 if IS_MACOS and lines else 0

        for line in lines[start:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid in exclude:
                continue
            cmd_str = parts[1]
            if "startup_cleanup" in cmd_str:
                continue
            pids.append((pid, cmd_str))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return pids


def _kill_pids(pids: list[int], grace_sec: float = 0.8):
    """SIGTERM all, wait, SIGKILL survivors."""
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(grace_sec)
    for pid in pids:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _clean_temp_files() -> int:
    """Remove old temp files matching known patterns."""
    cutoff = time.time() - TEMP_MIN_AGE
    cleaned = 0
    for pattern in TEMP_PATTERNS:
        for path in glob.glob(pattern):
            try:
                mtime = os.path.getmtime(path)
                if mtime >= cutoff:
                    continue
                if os.path.isfile(path):
                    os.unlink(path)
                    cleaned += 1
                elif os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                    cleaned += 1
            except OSError:
                pass
    return cleaned


def run_startup_cleanup() -> dict:
    """
    Kill stale skill processes and clean temp files.
    Returns a summary dict with counts of what was cleaned.

    Called synchronously at bot startup, before the event loop.
    """
    summary = {
        "browser_killed": 0,
        "skill_processes_killed": 0,
        "state_files_cleaned": 0,
        "temp_files_cleaned": 0,
    }

    all_pids = _get_all_pids()

    # 1. Kill orphaned browser/Chromium processes
    browser_pids = []
    for pid, cmd in all_pids:
        if any(pat in cmd for pat in BROWSER_PATTERNS):
            browser_pids.append(pid)

    if browser_pids:
        _kill_pids(browser_pids)
        summary["browser_killed"] = len(browser_pids)
        logger.info(f"Startup: killed {len(browser_pids)} orphaned browser processes")

    # 2. Kill other stale skill processes
    killed_set = set(browser_pids)
    skill_pids = []
    for pid, cmd in all_pids:
        if pid in killed_set:
            continue
        if any(pat in cmd for pat in SKILL_PROCESS_PATTERNS):
            skill_pids.append(pid)

    if skill_pids:
        _kill_pids(skill_pids)
        summary["skill_processes_killed"] = len(skill_pids)
        logger.info(f"Startup: killed {len(skill_pids)} stale skill processes")

    # 3. Clean browser state files (daemon is dead now)
    for f in BROWSER_STATE_FILES:
        try:
            os.unlink(f)
            summary["state_files_cleaned"] += 1
        except OSError:
            pass

    # 4. Clean old temp files
    cleaned = _clean_temp_files()
    summary["temp_files_cleaned"] = cleaned
    if cleaned:
        logger.info(f"Startup: cleaned {cleaned} temp files")

    return summary
