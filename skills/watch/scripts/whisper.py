#!/usr/bin/env python3
"""Transcribe a video via Groq, OpenAI, or local Whisper CLI.

Strategy: extract audio (mono 16kHz mp3, tiny payload), then either upload to
whichever API has a key or shell out to the local `whisper` CLI. Returns
segments in the same shape as transcribe.parse_vtt so the rest of the pipeline
(filter_range, format_transcript) doesn't care where the transcript came from.

Pure stdlib for the API path — no `pip install groq` or `pip install openai`
needed. Local path requires the `whisper` binary on PATH.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

LOCAL_DEFAULT_MODEL = "base"
LOCAL_DEFAULT_DEVICE = "cpu"


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, credential). Prefers Groq → OpenAI → local CLI.

    For API backends the credential is the API key. For "local" it is "".
    If `preferred` is set, only that backend is considered.
    """
    if preferred == "local":
        if shutil.which("whisper"):
            return "local", ""
        return None, None

    def _from_env(name: str) -> str | None:
        value = os.environ.get(name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        Path.home() / ".config" / "watch" / ".env",
        Path.cwd() / ".env",
    ]

    candidates = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _from_env(key_name)
        if not value:
            for candidate in dotenv_paths:
                value = _from_dotenv(candidate, key_name)
                if value:
                    break
        if value:
            return backend, value

    if preferred is None and shutil.which("whisper"):
        return "local", ""

    return None, None


def extract_audio(
    video_path: str,
    out_path: Path,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit.

    Optional `start_seconds`/`duration_seconds` trim the audio before encoding.
    Trimming is critical for the local CPU backend (transcription time scales
    linearly with audio length) and free for the API path.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start_seconds is not None and start_seconds > 0:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    cmd += ["-i", video_path]
    if duration_seconds is not None and duration_seconds > 0:
        cmd += ["-t", f"{duration_seconds:.3f}"]
    cmd += [
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def _transcribe_local(audio_path: Path) -> dict:
    """Run the local `whisper` CLI on the audio file, return verbose-JSON-shape dict.

    Defaults: model `base`, device `cpu`. Override via WATCH_LOCAL_WHISPER_MODEL
    and WATCH_LOCAL_WHISPER_DEVICE environment variables. CPU is the default
    because most machines either lack CUDA or have a GPU too old for current
    PyTorch (e.g. Maxwell-era cards).
    """
    if shutil.which("whisper") is None:
        raise SystemExit(
            "local whisper CLI not found on PATH. Install: `pip install openai-whisper` "
            "or set GROQ_API_KEY / OPENAI_API_KEY in ~/.config/watch/.env"
        )

    model = os.environ.get("WATCH_LOCAL_WHISPER_MODEL", LOCAL_DEFAULT_MODEL)
    device = os.environ.get("WATCH_LOCAL_WHISPER_DEVICE", LOCAL_DEFAULT_DEVICE)

    out_dir = audio_path.parent
    cmd = [
        "whisper",
        str(audio_path),
        "--model", model,
        "--device", device,
        "--output_dir", str(out_dir),
        "--output_format", "json",
        "--verbose", "False",
        "--fp16", "True" if device != "cpu" else "False",
    ]
    print(
        f"[watch] local whisper: model={model} device={device} (this can take several minutes on CPU)…",
        file=sys.stderr,
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()[-5:]
        raise SystemExit(
            "local whisper failed (exit "
            f"{result.returncode}): {' | '.join(stderr_tail) or 'no stderr output'}"
        )

    json_path = out_dir / f"{audio_path.stem}.json"
    if not json_path.exists():
        raise SystemExit(f"local whisper produced no JSON at {json_path}")

    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"local whisper JSON unreadable: {exc}")


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _segments_from_response(data: dict) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → transcribe → parse segments.

    Returns (segments, backend_used). Segment timestamps are always absolute
    (relative to the original video), even when audio was trimmed. Raises
    SystemExit on any failure.
    """
    if backend is None:
        detected_backend, detected_key = load_api_key()
        backend = detected_backend
        if api_key is None:
            api_key = detected_key
    elif backend != "local" and api_key is None:
        _, detected_key = load_api_key(backend)
        api_key = detected_key

    if not backend:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper backend available. Set GROQ_API_KEY or OPENAI_API_KEY "
            "in ~/.config/watch/.env, or install the `whisper` CLI for local "
            f"transcription. Run `python3 {setup_py}` to configure."
        )

    if backend in ("groq", "openai") and not api_key:
        raise SystemExit(f"Whisper backend '{backend}' has no API key configured.")

    label = "local CLI" if backend == "local" else f"{backend} API"
    print(f"[watch] extracting audio for Whisper ({label})…", file=sys.stderr)
    audio_path = extract_audio(
        video_path,
        audio_out,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
    )
    size_kb = audio_path.stat().st_size / 1024

    if backend == "local":
        print(f"[watch] audio: {size_kb:.0f} kB — running local whisper…", file=sys.stderr)
        response = _transcribe_local(audio_path)
    elif backend == "groq":
        print(f"[watch] audio: {size_kb:.0f} kB — uploading to Groq Whisper…", file=sys.stderr)
        response = _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path)
    elif backend == "openai":
        print(f"[watch] audio: {size_kb:.0f} kB — uploading to OpenAI Whisper…", file=sys.stderr)
        response = _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path)
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")

    segments = _segments_from_response(response)
    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    offset = start_seconds or 0.0
    if offset > 0:
        for seg in segments:
            seg["start"] = round(seg["start"] + offset, 2)
            seg["end"] = round(seg["end"] + offset, 2)

    print(f"[watch] transcribed {len(segments)} segments via {label}", file=sys.stderr)
    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "usage: whisper.py <video-path> [<audio-out.mp3>] [--backend groq|openai|local]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
