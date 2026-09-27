#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import json
import shutil
import time
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}

# Caption tracks worth fetching: English, Greek, and the ORIGINAL speech
# recognition track in any language (YouTube names it "<lang>-orig"). Asking
# for English alone meant a Greek video's "captions" were YouTube's machine
# translation into English, which mangles names (notes 2026-09-05).
SUB_LANGS = "en,en-US,en-GB,el,.*-orig"


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https")


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[watch] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    return {
        "video_path": str(p),
        "subtitle_path": None,
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
    }


def caption_plan(raw: dict) -> list[tuple[str, str]]:
    """Caption tracks in order of preference for this video: (code, kind).

    The video's own language first (a manual track, then the original speech
    recognition), then English, and an automatic English track on a video in
    another language last, named for what it is: a machine translation.
    """
    lang = (raw.get("language") or "").lower() or None
    manual = [k for k in (raw.get("subtitles") or {}) if k != "live_chat"]
    auto = list(raw.get("automatic_captions") or {})
    plan: list[tuple[str, str]] = []

    def add(code, kind):
        if code not in [c for c, _ in plan]:
            plan.append((code, kind))

    if lang:
        for code in manual:
            if code == lang or code.startswith(lang + "-"):
                add(code, "manual")
        if f"{lang}-orig" in auto:
            add(f"{lang}-orig", "automatic, original language")
        if lang in auto:
            add(lang, "automatic")
    for code in ("en", "en-US", "en-GB"):
        if code in manual:
            add(code, "manual")
    for code in auto:
        if code.endswith("-orig"):
            add(code, "automatic, original language")
    if "en" in auto:
        add("en", "automatic English" if lang in (None, "en")
            else f"automatic English, machine translated from {lang}")
    return plan


def _pick_subtitle(out_dir: Path, raw: dict | None = None) -> tuple[Path | None, str | None]:
    """The best caption file on disk for this video, and what kind it is."""
    for code, kind in caption_plan(raw or {}):
        path = out_dir / f"video.{code}.vtt"
        if path.exists():
            return path, f"{kind} ({code})"
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None, None
    return candidates[0], "unknown kind"


def _retry_captions(url: str, output_template: str, code: str) -> None:
    """Fetch one caption track again through YouTube's android_vr client.

    The default client often answers caption requests with HTTP 429 while the
    video itself downloads fine; android_vr gets the track (notes 2026-09-05,
    re-measured 2026-09-27 on a Greek talk). It can no longer fetch video
    formats without a token, so it is used for the captions only.
    """
    cmd = [
        "yt-dlp", "--skip-download", "--ignore-errors", "--no-playlist",
        "--write-subs", "--write-auto-subs",
        "--extractor-args", "youtube:player_client=android_vr",
        "--sub-langs", code, "--sub-format", "vtt", "--convert-subs", "vtt",
        "-o", output_template, url,
    ]
    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def download_url(url: str, out_dir: Path) -> dict:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")

    # The folder is the caller's and may be reused. A video.* left by an
    # earlier request was returned as this one's after a failed download
    # (audit F41, 2026-09-06). A manifest names the URL each file set came
    # from; anything not from THIS url is cleared before yt-dlp runs.
    manifest = out_dir / "video.source.json"
    previous = None
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text()).get("url")
        except (OSError, ValueError):
            previous = None
    if previous != url:
        for stale in out_dir.glob("video*"):
            if stale.is_file():
                stale.unlink()
    started = time.time()

    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", SUB_LANGS,
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", output_template,
        url,
    ]

    # yt-dlp may exit non-zero if a subtitle variant fails (e.g. 429) even when
    # the video itself downloaded fine. Treat "video file present" as success.
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video = _pick_video(out_dir)
    if video is None or (previous != url and video.stat().st_mtime < started - 1):
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode})"
        )
    manifest.write_text(json.dumps({"url": url, "downloaded_at": started}))

    info_path = out_dir / "video.info.json"
    info: dict = {}
    raw: dict = {}
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text())
            info = {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "language": raw.get("language"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception:
            info = {"url": url}

    subtitle, kind = _pick_subtitle(out_dir, raw)
    plan = caption_plan(raw)
    best = plan[0][0] if plan else None
    # The track the video should be read in did not arrive (a 429 on the
    # caption request is the usual reason): ask again for that one track.
    if best and (subtitle is None or subtitle.name != f"video.{best}.vtt"):
        _retry_captions(url, output_template, best)
        subtitle, kind = _pick_subtitle(out_dir, raw)

    return {
        "video_path": str(video),
        "subtitle_path": str(subtitle) if subtitle else None,
        "subtitle_kind": kind,
        "info": info or {"url": url},
        "downloaded": True,
    }


def download(source: str, out_dir: Path) -> dict:
    if is_url(source):
        return download_url(source, out_dir)
    return resolve_local(source)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    result = download(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result, indent=2))
