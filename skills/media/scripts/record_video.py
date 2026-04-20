#!/usr/bin/env python3
"""
Record a video of a webpage (or the whole display) to H.264 MP4.

Uses the documented fallback method: Xvfb virtual display + ffmpeg x11grab
constant-framerate capture. This is portable across any Linux laptop that
MyOldMachine targets: no NVIDIA GPU or gpu-screen-recorder required.

Replaces the prior Playwright `record_video_dir` (CDP screencast) approach,
which produced unwatchable variable-framerate WebM regardless of hardware.

Concurrency safety:
    An advisory file lock at /tmp/claude_video_recording.lock ensures only one
    recording runs at a time. Attempting to start a second recording while one
    is running exits immediately with code 75 (EX_TEMPFAIL).

    Every spawned subprocess (Xvfb, Chromium, ffmpeg) is registered so that
    SIGINT/SIGTERM/SIGHUP or any uncaught exception tears them down cleanly.
    This prevents Xvfb/Chromium pile-ups that destabilize the system under
    repeated runs.

Usage:
    record_video.py <url> <output.mp4>
    record_video.py <url> <output.mp4> --duration 10 --width 1920 --height 1080
    record_video.py <url> <output.mp4> --duration 8 --audio music.mp3
    record_video.py --screen <output.mp4> --duration 10
"""
import argparse
import atexit
import errno
import fcntl
import os
import platform
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FFMPEG_SETTLE = 2  # seconds to let ffmpeg stabilize before counting recording duration
LOCK_PATH = "/tmp/claude_video_recording.lock"

# --- cleanup harness (module-level state, accessed by signal handlers) -----

_lock_fd: int | None = None
_active_procs: list[subprocess.Popen] = []
_temp_dirs: list[str] = []
_temp_files: list[str] = []
_cleanup_done = False


def _acquire_lock() -> None:
    """Grab a non-blocking exclusive advisory lock. Exit 75 if held.

    Opens without O_TRUNC so that a losing contender does not wipe the
    winner's PID before we get a chance to print it for diagnostics.
    """
    global _lock_fd
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            holder = ""
            try:
                holder = Path(LOCK_PATH).read_text().strip()
            except OSError:
                pass
            msg = f"another recording is in progress (lock at {LOCK_PATH})"
            if holder:
                msg += f"; held by PID {holder}"
            print(f"Error: {msg}. Refusing to run concurrently.", file=sys.stderr)
            sys.exit(75)
        raise
    # Only the winner gets here. Truncate and write our PID for diagnostics.
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


def _register_tmp_dir(path: str) -> str:
    _temp_dirs.append(path)
    return path


def _register_tmp_file(path: str) -> str:
    _temp_files.append(path)
    return path


def _cleanup_all() -> None:
    """Kill all tracked subprocesses and remove tracked temp paths. Idempotent."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    # Round 1: polite SIGINT (ffmpeg flushes output on SIGINT/q)
    for p in list(_active_procs):
        try:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        except Exception:
            pass

    # Wait up to 5s total for all procs to exit
    deadline = time.monotonic() + 5.0
    for p in list(_active_procs):
        try:
            remaining = deadline - time.monotonic()
            p.wait(timeout=max(0.1, remaining))
        except Exception:
            pass

    # Round 2: SIGKILL survivors
    for p in list(_active_procs):
        try:
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(timeout=2)
                except Exception:
                    pass
        except Exception:
            pass

    _active_procs.clear()

    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)
    _temp_dirs.clear()

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


# --- recording logic --------------------------------------------------------

def _find_chromium() -> str:
    """Return path to a usable Chromium binary. Prefers Playwright's copy."""
    pw_candidates = sorted(
        Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome")
    )
    if pw_candidates:
        return str(pw_candidates[-1])
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    print("Error: no Chromium binary found (tried Playwright cache and PATH)",
          file=sys.stderr)
    sys.exit(1)


def _pick_xvfb_display() -> str:
    """Pick an unused display number in [50, 100)."""
    for _ in range(30):
        n = random.randint(50, 99)
        lock_file = f"/tmp/.X{n}-lock"
        socket_file = f"/tmp/.X11-unix/X{n}"
        if not os.path.exists(lock_file) and not os.path.exists(socket_file):
            return f":{n}"
    print("Error: could not find a free Xvfb display number after 30 attempts",
          file=sys.stderr)
    sys.exit(1)


def _wait_for_xvfb(display: str, timeout: float = 10.0) -> bool:
    """Wait until the Xvfb X server is ready to accept connections.

    Prefers xdpyinfo (actual connection test); falls back to the socket file
    if xdpyinfo is not installed. Safe either way.
    """
    deadline = time.monotonic() + timeout
    env = dict(os.environ)
    env["DISPLAY"] = display
    socket_path = f"/tmp/.X11-unix/X{display.lstrip(':')}"
    have_xdpyinfo = shutil.which("xdpyinfo") is not None
    while time.monotonic() < deadline:
        if have_xdpyinfo:
            try:
                result = subprocess.run(
                    ["xdpyinfo"], capture_output=True, env=env,
                )
                if result.returncode == 0:
                    return True
            except FileNotFoundError:
                have_xdpyinfo = False
        elif os.path.exists(socket_path):
            # Give the server a brief moment to start accepting connections
            time.sleep(0.2)
            return True
        time.sleep(0.2)
    return False


def _record(display: str, width: int, height: int, output_path: Path,
            duration: int, fps: int, audio: str | None) -> bool:
    """Record `display` to output_path. Runs ffmpeg x11grab directly."""
    raw_fd, raw_video = tempfile.mkstemp(suffix=".mp4", prefix="record_raw_")
    os.close(raw_fd)
    _register_tmp_file(raw_video)

    grab_cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab",
        "-framerate", str(fps),
        "-video_size", f"{width}x{height}",
        "-i", f"{display}+0,0",
        "-t", str(duration + FFMPEG_SETTLE),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        raw_video,
    ]
    ffmpeg_proc = _register_proc(subprocess.Popen(
        grab_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=os.environ,
    ))

    try:
        ffmpeg_proc.wait(timeout=duration + FFMPEG_SETTLE + 30)
    except subprocess.TimeoutExpired:
        print("Error: ffmpeg capture timed out; killing", file=sys.stderr)
        ffmpeg_proc.kill()
        try:
            ffmpeg_proc.wait(timeout=5)
        except Exception:
            pass
        return False
    try:
        _active_procs.remove(ffmpeg_proc)
    except ValueError:
        pass

    if ffmpeg_proc.returncode != 0:
        err = ""
        if ffmpeg_proc.stderr:
            err = ffmpeg_proc.stderr.read().decode("utf-8", errors="replace")
        print(f"Error: ffmpeg x11grab failed (rc={ffmpeg_proc.returncode}): "
              f"{err[-500:]}", file=sys.stderr)
        return False

    raw = Path(raw_video)
    if not raw.exists() or raw.stat().st_size == 0:
        print("Error: raw video not created or empty", file=sys.stderr)
        return False

    # Post-process: trim settle prefix, optional audio mux, re-encode at CRF 20
    post_cmd = ["ffmpeg", "-y", "-ss", str(FFMPEG_SETTLE), "-i", raw_video]
    has_audio = False
    if audio:
        ap = Path(audio).resolve()
        if ap.exists():
            post_cmd.extend(["-i", str(ap)])
            has_audio = True
        else:
            print(f"Warning: audio file not found: {ap}", file=sys.stderr)

    post_cmd.extend([
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
    ])
    if has_audio:
        post_cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
        ])
    post_cmd.append(str(output_path))

    try:
        result = subprocess.run(post_cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("Error: ffmpeg post-process timed out after 300s", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"ffmpeg post-process error: "
              f"{result.stderr.decode(errors='replace')[-500:]}", file=sys.stderr)
        return False
    return True


def _stop_proc(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """SIGTERM then SIGKILL, and drop from tracking."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        _active_procs.remove(proc)
    except ValueError:
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Record a webpage or display as H.264 MP4 (Xvfb + ffmpeg x11grab)"
    )
    parser.add_argument("url", nargs="?",
                        help="URL to record (omit when using --screen)")
    parser.add_argument("output", help="Output video file path (.mp4)")
    parser.add_argument("--width", type=int, default=1920,
                        help="Window width when launching Chromium (default: 1920)")
    parser.add_argument("--height", type=int, default=1080,
                        help="Window height when launching Chromium (default: 1080)")
    parser.add_argument("--duration", type=int, default=5,
                        help="Recording duration in seconds (default: 5)")
    parser.add_argument("--fps", type=int, default=30,
                        help="Frame rate (default: 30)")
    parser.add_argument("--audio", default=None,
                        help="Optional audio file to mux into the video")
    parser.add_argument("--screen", action="store_true",
                        help="Record the current DISPLAY instead of launching a "
                             "browser on a new Xvfb display")
    args = parser.parse_args()

    if platform.system() != "Linux":
        print("Error: record_video.py currently supports Linux only. "
              "On macOS, use an alternative such as `screencapture -V` or QuickTime.",
              file=sys.stderr)
        sys.exit(2)

    if not args.screen and not args.url:
        parser.error("URL is required unless --screen is set")
    if args.duration <= 0:
        parser.error("--duration must be > 0")
    if args.fps <= 0:
        parser.error("--fps must be > 0")
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be > 0")

    for tool in ("ffmpeg",):
        if shutil.which(tool) is None:
            print(f"Error: {tool} is required. Install with `apt install {tool}` "
                  f"or `brew install {tool}`.", file=sys.stderr)
            sys.exit(1)
    if not args.screen:
        for tool in ("Xvfb", "xdotool"):
            if shutil.which(tool) is None:
                print(f"Error: {tool} is required for URL recording. "
                      f"Install with `apt install xvfb xdotool`.", file=sys.stderr)
                sys.exit(1)

    output_path = Path(args.output).resolve()
    if output_path.suffix.lower() != ".mp4":
        new_path = output_path.with_suffix(".mp4")
        print(f"Notice: output will be H.264 MP4. Changing extension "
              f"{output_path.suffix or '(none)'} -> .mp4 ({new_path.name})",
              file=sys.stderr)
        output_path = new_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _install_signal_handlers()
    _acquire_lock()

    success = False
    try:
        if args.screen:
            display = os.environ.get("DISPLAY")
            if not display:
                print("Error: --screen requires DISPLAY to be set in the environment",
                      file=sys.stderr)
                return
            # Read real geometry if xdpyinfo is available, else use args.
            width, height = args.width, args.height
            xdpy = shutil.which("xdpyinfo")
            if xdpy:
                result = subprocess.run(
                    [xdpy], capture_output=True, text=True, env=os.environ,
                )
                for line in result.stdout.splitlines():
                    s = line.strip()
                    if s.startswith("dimensions:"):
                        parts = s.split()
                        if len(parts) >= 2 and "x" in parts[1]:
                            w, h = parts[1].split("x", 1)
                            try:
                                width, height = int(w), int(h)
                            except ValueError:
                                pass
                        break
            print(f"Recording display {display} ({width}x{height}) for "
                  f"{args.duration}s at {args.fps}fps...")
            success = _record(display, width, height, output_path,
                              args.duration, args.fps, args.audio)
        else:
            display = _pick_xvfb_display()
            xvfb_proc = _register_proc(subprocess.Popen(
                [
                    "Xvfb", display,
                    "-screen", "0", f"{args.width}x{args.height}x24",
                    "-nolisten", "tcp",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ))
            if not _wait_for_xvfb(display, timeout=10.0):
                print(f"Error: Xvfb on {display} did not become ready within 10s",
                      file=sys.stderr)
                return

            chromium = _find_chromium()
            user_data_dir = _register_tmp_dir(
                tempfile.mkdtemp(prefix="record_video_chromium_")
            )
            chromium_env = dict(os.environ)
            chromium_env["DISPLAY"] = display
            chromium_proc = _register_proc(subprocess.Popen(
                [
                    chromium,
                    f"--user-data-dir={user_data_dir}",
                    "--no-sandbox",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                    "--disable-infobars",
                    "--disable-translate",
                    "--disable-features=TranslateUI,InfiniteSessionRestore,Translate",
                    "--disable-restore-session-state",
                    "--use-gl=swiftshader",
                    f"--app={args.url}",
                    f"--window-size={args.width},{args.height}",
                    "--window-position=0,0",
                ],
                env=chromium_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ))
            print(f"Loading {args.url}...")
            # Let Chromium paint the page before capture starts
            time.sleep(3.5)

            # Verify Chromium is still running
            if chromium_proc.poll() is not None:
                print(f"Error: Chromium exited early (rc={chromium_proc.returncode})",
                      file=sys.stderr)
                return

            print(f"Recording {display} for {args.duration}s at {args.fps}fps...")
            success = _record(display, args.width, args.height, output_path,
                              args.duration, args.fps, args.audio)
            _stop_proc(chromium_proc, grace=5.0)
            _stop_proc(xvfb_proc, grace=3.0)
    finally:
        _cleanup_all()

    if not success:
        sys.exit(1)

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate,duration,width,height,codec_name",
                "-of", "default=noprint_wrappers=1",
                str(output_path),
            ],
            capture_output=True, text=True,
        )
        print(f"\nDone: {output_path} ({size_mb:.1f} MB)")
        print(probe.stdout.strip())
    else:
        print("Error: output file was not created", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
