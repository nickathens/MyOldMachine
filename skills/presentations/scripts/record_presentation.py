#!/usr/bin/env python3
"""
Record a presentation HTML as video using ffmpeg x11grab.

Opens the presentation in headed Chromium on display :0, makes the window
fullscreen at a device scale of 1, triggers auto-scroll via ?autoplay=1, and
captures the top-left WIDTHxHEIGHT of the screen with ffmpeg x11grab
(CPU-based H.264 encoding, no GPU involvement).

Why fullscreen at scale 1 (2026-09-27): on a HiDPI desktop (measured on a
4096x2160 GNOME screen at 200%) a normal window is placed after the dock and
below the top bar, carries tabs and a URL bar, and renders the page at 2x, so
a 0,0 capture showed the desktop chrome and a zoomed corner of the page.
Fullscreen covers the dock and bar, and scale 1 makes one page pixel one
screen pixel, so the emulated viewport sits exactly at 0,0.

Before recording, a neutral grey page is shown and one frame is checked: a
notification or dialog over the capture area stops the run with the frame
saved beside the output, instead of a silently ruined video.

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
# Seconds between the page's load event and the start of the capture: the
# cover's entrance animation (about 3 s) plays out first, as it did before.
SETTLE_AFTER_LOAD = 2.5
GATE_GREY = 127

_lock_fd: int | None = None
_active_procs: list[subprocess.Popen] = []
_temp_files: list[str] = []
_banners_to_restore: str | None = None
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


def _register_tmp_file(path: str) -> str:
    _temp_files.append(path)
    return path


def _gsettings(*args: str) -> str | None:
    """Run gsettings on the desktop session; None when it cannot."""
    env = dict(os.environ)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    try:
        result = subprocess.run(["gsettings", *args], capture_output=True, text=True,
                                timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


# Written while banners are muted, removed when they are restored. A run
# killed outright (SIGKILL, the OOM killer) never restores them and would
# leave the desktop's notifications off with no visible cause; the next
# recording finds the marker and turns them back on (audit, 2026-09-27).
BANNERS_MARKER = Path.home() / ".cache" / "myoldmachine" / "banners-muted"


def _quiet_banners() -> None:
    """Hide new notification banners for the length of the recording."""
    global _banners_to_restore
    key = ("org.gnome.desktop.notifications", "show-banners")
    if _gsettings("get", *key) == "true" and _gsettings("set", *key, "false") is not None:
        _banners_to_restore = "true"
        try:
            BANNERS_MARKER.parent.mkdir(parents=True, exist_ok=True)
            BANNERS_MARKER.write_text("true", encoding="utf-8")
        except OSError:
            pass


def _restore_banners() -> None:
    global _banners_to_restore
    if _banners_to_restore:
        _gsettings("set", "org.gnome.desktop.notifications", "show-banners",
                   _banners_to_restore)
        _banners_to_restore = None
        BANNERS_MARKER.unlink(missing_ok=True)


def _restore_after_a_killed_run() -> None:
    """Banners a previous, killed recording muted and never restored."""
    if BANNERS_MARKER.exists():
        _gsettings("set", "org.gnome.desktop.notifications", "show-banners", "true")
        BANNERS_MARKER.unlink(missing_ok=True)
        print("Note: a killed recording had left notification banners off; turned them back on",
              file=sys.stderr)


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
    _active_procs.clear()

    for f in _temp_files:
        try:
            os.unlink(f)
        except OSError:
            pass
    _temp_files.clear()

    _restore_banners()
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


def _go_fullscreen(page) -> dict:
    """Fullscreen the page's window through CDP; return the window bounds."""
    cdp = page.context.new_cdp_session(page)
    window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
    cdp.send("Browser.setWindowBounds",
             {"windowId": window_id, "bounds": {"windowState": "fullscreen"}})
    time.sleep(1.0)
    return cdp.send("Browser.getWindowBounds", {"windowId": window_id})["bounds"]


def _grab_frame(width: int, height: int) -> bytes:
    """One RGB frame of the capture area, as raw bytes."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "x11grab", "-draw_mouse", "0",
         "-video_size", f"{width}x{height}", "-i", f"{DISPLAY_NUM}+0,0",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=30, env=os.environ,
    )
    return result.stdout if result.returncode == 0 else b""


def foreign_fraction(frame: bytes, grey: int = GATE_GREY, tolerance: int = 24) -> float:
    """Share of pixels that are not the neutral grey the gate page paints.

    1.0 when the frame is empty or truncated (nothing was captured)."""
    import numpy as np
    if not frame or len(frame) % 3:
        return 1.0
    px = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
    off = np.abs(px - grey).max(axis=1) > tolerance
    return float(off.mean())


def _capture_area_is_clear(page, width: int, height: int, out_png: Path) -> bool:
    """Show a grey page and prove nothing covers the capture area.

    A leftover notification, a crash dialog or a keyring prompt on the
    desktop would otherwise sit in every frame of the video."""
    page.set_content(f"<html><body style='margin:0;background:rgb({GATE_GREY},{GATE_GREY},"
                     f"{GATE_GREY})'></body></html>")
    frame = b""
    for _ in range(4):  # the fullscreen hint bubble fades within a second
        time.sleep(1.0)
        frame = _grab_frame(width, height)
        if foreign_fraction(frame) < 0.001:
            return True
    if frame and len(frame) == width * height * 3:
        from PIL import Image
        Image.frombytes("RGB", (width, height), frame).save(out_png)
    return False


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
    _restore_after_a_killed_run()

    html_path = Path(args.html).resolve()
    if not html_path.exists():
        print(f"Error: HTML file not found: {html_path}", file=sys.stderr)
        sys.exit(1)
    if args.width <= 0 or args.height <= 0 or args.width % 2 or args.height % 2:
        parser.error("--width and --height must be positive even numbers (H.264 4:2:0)")
    if args.fps <= 0:
        parser.error("--fps must be > 0")

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
    fd, ffmpeg_log = tempfile.mkstemp(suffix=".log", prefix="pres_ffmpeg_")
    os.close(fd)
    _register_tmp_file(ffmpeg_log)

    os.environ["DISPLAY"] = DISPLAY_NUM
    os.environ["XAUTHORITY"] = XAUTHORITY

    success = False
    browser = None
    pw = None
    ffmpeg_proc = None
    try:
        print(f"[1/5] Launching Chromium fullscreen on {DISPLAY_NUM} ({args.width}x{args.height})...")
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--no-first-run",
                "--disable-extensions",
                # One page pixel per screen pixel, whatever the desktop scale.
                "--force-device-scale-factor=1",
            ],
        )
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        bounds = _go_fullscreen(page)
        if bounds.get("width", 0) < args.width or bounds.get("height", 0) < args.height:
            print(f"Error: the screen ({bounds.get('width')}x{bounds.get('height')}) is smaller "
                  f"than {args.width}x{args.height}", file=sys.stderr)
            sys.exit(1)

        print("[2/5] Checking the capture area is clear...")
        _quiet_banners()
        blocked_png = output_path.with_suffix(".blocked.png")
        if not _capture_area_is_clear(page, args.width, args.height, blocked_png):
            print("Error: something on the desktop covers the capture area (a notification "
                  f"or a dialog). Frame saved to {blocked_png}. Dismiss it on display "
                  f"{DISPLAY_NUM} and run again.", file=sys.stderr)
            sys.exit(1)

        # as_uri() percent-encodes the path, so '#', '?' or '%' in a folder
        # name cannot cut the URL short.
        url = f"{html_path.as_uri()}?autoplay=1&delay={args.delay}"
        try:
            page.goto(url, wait_until="load", timeout=120000)
        except Exception as e:
            print(f"Error: failed to load {url}: {e}", file=sys.stderr)
            sys.exit(1)
        try:
            page.evaluate("() => document.fonts.ready.then(() => true)")
        except Exception:
            pass
        time.sleep(SETTLE_AFTER_LOAD)

        print(f"[3/5] Starting ffmpeg x11grab ({args.fps}fps, H.264)...")
        with open(ffmpeg_log, "wb") as log:
            # stdout and stderr never go to an unread pipe: ffmpeg's progress
            # lines filled a 64 KB pipe in a few minutes and stalled capture.
            ffmpeg_proc = _register_proc(subprocess.Popen(
                [
                    "ffmpeg", "-y", "-nostats", "-loglevel", "error",
                    "-f", "x11grab",
                    "-draw_mouse", "0",
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
                stdout=subprocess.DEVNULL,
                stderr=log,
                env=os.environ,
            ))
        time.sleep(1)

        if ffmpeg_proc.poll() is not None:
            err = Path(ffmpeg_log).read_text(errors="replace")
            print(f"Error: ffmpeg failed to start: {err[-500:]}", file=sys.stderr)
            sys.exit(1)

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

        if not Path(raw_output).exists() or Path(raw_output).stat().st_size == 0:
            err = Path(ffmpeg_log).read_text(errors="replace")
            print(f"Error: Raw video not created! {err[-500:]}", file=sys.stderr)
            sys.exit(1)

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
                    sys.exit(1)
                if result.returncode != 0:
                    print(f"ffmpeg error: {result.stderr.decode(errors='replace')[-500:]}",
                          file=sys.stderr)
                    sys.exit(1)
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
