#!/usr/bin/env python3
"""
Record a presentation HTML as video using ffmpeg x11grab.

Opens the presentation in headed Chromium on display :0,
triggers auto-scroll via ?autoplay=1, and captures with
ffmpeg x11grab (CPU-based H.264 encoding, no GPU involvement).

Concurrency safety:
    Shares the advisory file lock at /tmp/claude_video_recording.lock
    with record_video.py. Only one recording at a time.

Usage:
    python record_presentation.py --html presentation.html --output video.mp4
    python record_presentation.py --html presentation.html --output video.mp4 --audio bg_music.mp3
    python record_presentation.py --html presentation.html --output video.mp4 --width 1920 --height 1080
"""

import argparse
import atexit
import errno
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DISPLAY_NUM = ":0"
XAUTHORITY = "/run/user/1000/gdm/Xauthority"
MAX_WAIT = 600
POLL_INTERVAL = 2
LOCK_PATH = "/tmp/claude_video_recording.lock"

_lock_fd: int | None = None
_active_procs: list[subprocess.Popen] = []
_extra_pids: list[int] = []
_temp_files: list[str] = []
_cleanup_done = False


def _acquire_lock() -> None:
    global _lock_fd
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            print("Error: another recording is in progress.", file=sys.stderr)
            sys.exit(75)
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _lock_fd = fd


def _release_lock() -> None:
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(_lock_fd)
    except OSError:
        pass
    _lock_fd = None
    try:
        os.unlink(LOCK_PATH)
    except OSError:
        pass


def _register_proc(proc: subprocess.Popen) -> subprocess.Popen:
    _active_procs.append(proc)
    return proc


def _register_pids(pids: list[int]) -> None:
    for p in pids:
        if p and p not in _extra_pids:
            _extra_pids.append(p)


def _register_tmp_file(path: str) -> str:
    _temp_files.append(path)
    return path


def _snapshot_chromium_pids() -> set[int]:
    result = subprocess.run(["pgrep", "-x", "chrome"], capture_output=True, text=True)
    pids: set[int] = set()
    for line in result.stdout.strip().splitlines():
        try:
            pids.add(int(line.strip()))
        except ValueError:
            pass
    return pids


def _kill_pid(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _cleanup_all() -> None:
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    for p in list(_active_procs):
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    for pid in list(_extra_pids):
        _kill_pid(pid, signal.SIGTERM)

    deadline = time.monotonic() + 5.0
    for p in list(_active_procs):
        try:
            remaining = deadline - time.monotonic()
            p.wait(timeout=max(0.1, remaining))
        except Exception:
            pass

    for p in list(_active_procs):
        try:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=2)
        except Exception:
            pass
    for pid in list(_extra_pids):
        try:
            os.kill(pid, 0)
            _kill_pid(pid, signal.SIGKILL)
        except OSError:
            pass

    _active_procs.clear()
    _extra_pids.clear()

    for f in _temp_files:
        try:
            os.unlink(f)
        except OSError:
            pass
    _temp_files.clear()

    _release_lock()


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        _cleanup_all()
        sys.exit(128 + signum)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass
    atexit.register(_cleanup_all)


def _find_chromium() -> str:
    candidates = sorted(Path.home().glob(
        ".cache/ms-playwright/chromium-*/chrome-linux*/chrome"
    ))
    if candidates:
        return str(candidates[-1])
    print("Error: Playwright Chromium not found", file=sys.stderr)
    sys.exit(1)


def _find_window(title_hint: str) -> str | None:
    for search in (
        ["xdotool", "search", "--name", title_hint],
        ["xdotool", "search", "--class", "chromium-browser"],
    ):
        result = subprocess.run(search, capture_output=True, text=True, env=os.environ)
        for wid in result.stdout.strip().splitlines():
            if wid.strip():
                return wid.strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Record presentation HTML as video")
    parser.add_argument("--html", required=True, help="Path to presentation HTML")
    parser.add_argument("--output", default="/tmp/presentation.mp4", help="Output video path")
    parser.add_argument("--width", type=int, default=1920, help="Video width")
    parser.add_argument("--height", type=int, default=1080, help="Video height")
    parser.add_argument("--fps", type=int, default=30, help="Frame rate")
    parser.add_argument("--audio", default=None, help="Background audio to mux in")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Seconds to hold on cover before scrolling starts")
    args = parser.parse_args()

    html_path = Path(args.html).resolve()
    if not html_path.exists():
        print(f"Error: HTML file not found: {html_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("ffmpeg") is None:
        print("Error: ffmpeg not found", file=sys.stderr)
        sys.exit(1)

    _install_signal_handlers()
    _acquire_lock()

    raw_output = str(output_path)
    if args.audio:
        fd, raw_output = tempfile.mkstemp(suffix=".mp4", prefix="pres_raw_")
        os.close(fd)
        _register_tmp_file(raw_output)

    os.environ["DISPLAY"] = DISPLAY_NUM
    os.environ["XAUTHORITY"] = XAUTHORITY

    chromium = _find_chromium()

    success = False
    browser = None
    pw = None
    ffmpeg_proc = None
    try:
        print(f"[1/5] Launching Chromium on {DISPLAY_NUM} ({args.width}x{args.height})...")
        from playwright.sync_api import sync_playwright

        baseline_pids = _snapshot_chromium_pids()

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            executable_path=chromium,
            headless=False,
            args=[
                "--no-sandbox",
                "--no-first-run",
                "--disable-extensions",
                "--disable-infobars",
                f"--window-size={args.width},{args.height}",
            ],
        )
        time.sleep(0.8)
        after_pids = _snapshot_chromium_pids()
        _register_pids(list(after_pids - baseline_pids))

        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        url = f"file://{html_path}?autoplay=1&delay={args.delay}"
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            try:
                page.goto(url, wait_until="load", timeout=30000)
            except Exception as e:
                print(f"Error: failed to load {url}: {e}", file=sys.stderr)
                return

        print("[2/5] Positioning window...")
        time.sleep(2)

        wid = _find_window(html_path.stem)
        if not wid:
            wid = _find_window("chromium")
        if not wid:
            print("Error: Could not find Chromium window", file=sys.stderr)
            return

        subprocess.run(
            [
                "xdotool",
                "windowsize", wid, str(args.width), str(args.height),
                "windowmove", wid, "0", "0",
                "windowactivate", wid,
                "windowfocus", wid,
            ],
            capture_output=True, env=os.environ,
        )
        time.sleep(1)

        print(f"[3/5] Starting ffmpeg x11grab ({args.fps}fps, H.264)...")
        ffmpeg_proc = _register_proc(subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-framerate", str(args.fps),
                "-video_size", f"{args.width}x{args.height}",
                "-i", f"{DISPLAY_NUM}+0,0",
                "-c:v", "libx264",
                "-crf", "20",
                "-preset", "fast",
                "-pix_fmt", "yuv420p",
                raw_output,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ,
        ))
        time.sleep(1)

        if ffmpeg_proc.poll() is not None:
            err = ffmpeg_proc.stderr.read().decode("utf-8", errors="replace") if ffmpeg_proc.stderr else ""
            print(f"Error: ffmpeg failed to start: {err[-500:]}", file=sys.stderr)
            return

        print("[4/5] Waiting for autoplay to complete...")
        elapsed = 0
        while elapsed < MAX_WAIT:
            try:
                if not browser.is_connected():
                    print("     Warning: Chromium disconnected")
                    break
            except Exception:
                break
            try:
                done = page.evaluate("() => window.__autoplayDone === true")
                if done:
                    print(f"     Autoplay finished after {elapsed}s")
                    break
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
        else:
            print(f"     Warning: autoplay did not finish within {MAX_WAIT}s")

        time.sleep(3)

        print("[5/5] Stopping recording...")
        # Send 'q' to ffmpeg stdin for clean shutdown
        try:
            ffmpeg_proc.stdin.write(b"q")
            ffmpeg_proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            ffmpeg_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
        try:
            _active_procs.remove(ffmpeg_proc)
        except ValueError:
            pass

        # Close Playwright
        try:
            browser.close()
        except Exception:
            pass
        browser = None
        try:
            pw.stop()
        except Exception:
            pass
        pw = None

        for pid in list(_extra_pids):
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
            else:
                _kill_pid(pid, signal.SIGTERM)
        _extra_pids.clear()

        if not Path(raw_output).exists() or Path(raw_output).stat().st_size == 0:
            print("Error: Raw video not created!", file=sys.stderr)
            return

        # Mux audio if provided
        if args.audio:
            audio_path = Path(args.audio).resolve()
            if audio_path.exists():
                print("     Muxing audio...")
                cmd = [
                    "ffmpeg", "-y",
                    "-i", raw_output,
                    "-i", str(audio_path),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-shortest",
                    str(output_path),
                ]
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=300)
                except subprocess.TimeoutExpired:
                    print("Error: ffmpeg audio mux timed out", file=sys.stderr)
                    return
                if result.returncode != 0:
                    print(f"ffmpeg error: {result.stderr.decode(errors='replace')[-500:]}",
                          file=sys.stderr)
                    return
            else:
                print(f"Warning: Audio not found: {audio_path}", file=sys.stderr)
                shutil.move(raw_output, str(output_path))

        success = True
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        _cleanup_all()

    if not success:
        sys.exit(1)

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate,duration,width,height",
                "-of", "default=noprint_wrappers=1",
                str(output_path),
            ],
            capture_output=True, text=True,
        )
        print(f"\nDone: {output_path} ({size_mb:.1f} MB)")
        print(probe.stdout.strip())
    else:
        print("Error: Final video not created!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
