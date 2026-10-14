#!/usr/bin/env python3
"""Magnific upscaling through its API, behind a free quote.

Every Magnific API job takes credits from the account's one balance (the web
plan's "unlimited" never covers the API), so nothing here spends on its own:

  check          free: is the key good, and which upscale jobs exist
  quote FILE     free and offline: measures the file, prints the price from
                 Magnific's API price list and a six character code
  run CODE       the only command that spends: that quote's job, once, on the
                 unchanged file, at the same price, within 24 hours
  status CODE    free: where a job stands
  fetch CODE     free: wait for a job that is already running and download it
  ledger         every quote and job on this machine, and what was sent

Requests are fenced to the upscaler endpoints plus the upload endpoint a local
video needs (the video upscalers only take an address); any other Magnific
path is refused before a request leaves. Results are downloaded as Magnific
sends them, never re-encoded: their links expire 30 minutes after the job ends.

Fields, prices and limits were read on 2026-10-14 from docs.magnific.com, its
OpenAPI file and magnific.com/api/pricing (that page needs a real browser).

Exit codes: 0 done, 1 a job failed or its outcome is unknown, 2 refused before
anything was sent, 3 still running when the wait ran out (fetch it later).
"""

import argparse
import base64
import functools
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.magnific.com"
KEY_NAME = "MAGNIFIC_API_KEY"
# The install's own .env, at the repo root above skills/upscale/scripts. A
# bot that strips keys from the environment it hands to commands still
# leaves the key readable here. .parent, not .parents[3]: a copy kept fewer
# than four folders deep must still import.
ENV_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".env"
USER_AGENT = "upscale-skill-magnific/1"
QUOTE_TTL = 24 * 3600
PRICES_READ = "2026-10-14"
MAX_API_BODY = 16 << 20
POLL = {"image": 5, "video": 20}           # seconds between status checks
WAIT = {"image": 900, "video": 7200}       # default wait for a result

# Euros per image by output size, the same for Creative, Precision V1 and V2:
# "1k to 2k" 0.10, "2k to 4k" 0.20, "4k to 8k" and "5k to 10k" 1.20, from
# magnific.com/api/pricing. The docs say the price follows the result's pixel
# area but publish no pixel size for a tier, and their six worked examples
# (1280x720 at 4x for €0.40, 640x480 at 8x for €0.50) fit neither these tiers
# nor any one area rule. So the quote counts the LONG SIDE, which is at or
# above every published figure (a test holds it to the six); the real charge
# can be lower. Past 10k there is no published price.
IMAGE_TIERS = ((2048, 0.10, "1k to 2k"), (4096, 0.20, "2k to 4k"), (8192, 1.20, "4k to 8k"),
               (10240, 1.20, "5k to 10k"))
CREATIVE_MAX_PIXELS = 25_300_000           # the Creative endpoint refuses a bigger result

# Euros per frame by output resolution. The API also takes 720p, but the price
# list has no 720p line, so it is not offered; nor are Topaz (no published
# price), FPS boost and frame interpolation (they add frames at no published
# rate) or Precision V1 (no scale setting, so its result size and price are
# unknown before it runs).
VIDEO_PER_FRAME = {
    "precision": {"1k": 0.013, "2k": 0.019, "4k": 0.024},
    "standard": {"1k": 0.013, "2k": 0.019, "4k": 0.024},
    "turbo": {"1k": 0.010, "2k": 0.012, "4k": 0.013},
}
# The web app names these sizes 1080p, 1440p (2K) and 2160p (4K). Read the
# same way here only to refuse a job that would not enlarge the video.
VIDEO_SHORT_SIDE = {"1k": 1080, "2k": 1440, "4k": 2160}

IMAGE_ENDPOINTS = {"precision": "/v1/ai/image-upscaler-precision-v2", "creative": "/v1/ai/image-upscaler"}
VIDEO_ENDPOINTS = {"precision": "/v1/ai/video-upscaler-precision", "standard": "/v1/ai/video-upscaler",
                   "turbo": "/v1/ai/video-upscaler/turbo"}
# Turbo jobs are read back through the standard video upscaler's task path.
STATUS_PATHS = {"/v1/ai/video-upscaler/turbo": "/v1/ai/video-upscaler"}
UPLOAD_PATH = "/v1/ai/uploads/request-url"
UPLOAD_MAX = 1 << 30                       # the upload endpoint's limit per file
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
MEDIA_SUFFIXES = (".png", ".jpg", ".webp", ".mp4", ".mov", ".webm")
MODE_NAMES = {("image", "precision"): "Precision V2 (stays close to the source)",
              ("image", "creative"): "Creative (invents detail)",
              ("video", "precision"): "Video Precision (faithful)", ("video", "standard"): "Video Upscaler",
              ("video", "turbo"): "Video Upscaler Turbo"}

# The only requests this key may make. A task id is a uuid in the OpenAPI
# file; the pattern is looser so a paid job can never be stranded by a format
# change, but it still cannot hold a slash or a dot.
_TASK = r"[A-Za-z0-9_-]{8,80}"
ALLOWED = {
    "POST": re.compile(r"/v1/ai/(image-upscaler|image-upscaler-precision-v2|video-upscaler|video-upscaler/turbo"
                       r"|video-upscaler-precision|uploads/request-url)"),
    "GET": re.compile(rf"/v1/ai/(image-upscaler|image-upscaler-precision-v2|video-upscaler"
                      rf"|video-upscaler-precision)(/{_TASK})?"),
}
TASK_RE = re.compile(_TASK)
CODE_RE = re.compile(r"[0-9a-f]{6}")
SENT_STATES = ("submitting", "submitted", "done", "failed", "unknown")


class Refused(Exception):
    """A request or job this script will not make. Nothing paid has been sent."""


class JobError(Exception):
    """A job that failed, or whose outcome is unknown."""


def _int_range(lo, hi):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise Refused(f"must be a whole number from {lo} to {hi}, not {value!r}")
        return value
    return check


def _one_of(*choices):
    def check(value):
        if value not in choices:
            raise Refused(f"must be one of {', '.join(choices)}, not {value!r}")
        return value
    return check


def _text(value):
    if not str(value).strip():
        raise Refused("must not be empty")
    return str(value)


PCT, TEN = _int_range(0, 100), _int_range(-10, 10)
# Which settings each upscaler takes, with the API's own ranges and choices.
FIELDS = {
    ("image", "precision"): {"sharpen": PCT, "smart_grain": PCT, "ultra_detail": PCT,
                             "flavor": _one_of("sublime", "photo", "photo_denoiser")},
    ("image", "creative"): {"prompt": _text, "creativity": TEN, "hdr": TEN, "resemblance": TEN, "fractality": TEN,
                            "engine": _one_of("automatic", "magnific_illusio", "magnific_sharpy", "magnific_sparkle"),
                            "optimized_for": _one_of("standard", "soft_portraits", "hard_portraits",
                                                     "art_n_illustration", "videogame_assets", "nature_n_landscapes",
                                                     "films_n_photography", "3d_renders", "science_fiction_n_horror")},
    ("video", "precision"): {"sharpen": PCT, "smart_grain": PCT, "strength": PCT},
    ("video", "standard"): {"creativity": PCT, "sharpen": PCT, "smart_grain": PCT, "flavor": _one_of("vivid", "natural")},
}
FIELDS[("video", "turbo")] = FIELDS[("video", "standard")]
# Precision V2's own defaults (OpenAPI file). By its docs, ultra detail is "how
# much fine detail the AI adds", smart grain adds texture and sharpen sharpens,
# so a quote that leaves them unset says so.
PRECISION_DEFAULTS = {"ultra_detail": 30, "smart_grain": 7, "sharpen": 7}
SETTINGS = ("sharpen", "smart_grain", "ultra_detail", "strength", "creativity", "hdr", "resemblance", "fractality",
            "flavor", "engine", "optimized_for", "prompt")


# ---------------------------------------------------------------- the key

def api_key():
    """The key from the environment, else from the install's .env. Never printed."""
    key = os.environ.get(KEY_NAME, "").strip()
    if not key:
        try:
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            name, sep, value = line.strip().partition("=")
            if sep and name.strip().removeprefix("export ").strip() == KEY_NAME:
                key = value.strip().strip('"').strip("'")
    if not key:
        raise Refused(f"{KEY_NAME} is not set (environment or {ENV_FILE})")
    return key


# ---------------------------------------------------------------- transport

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """API calls never follow a redirect: urllib would carry every header,
    the key included, on to wherever it points. It comes back as its 3xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _network(fn):
    """http.client reports a cut off or garbled answer (IncompleteRead,
    BadStatusLine) as HTTPException, which is not an OSError. Every caller here
    handles a network failure as OSError, the paid start's "outcome unknown"
    included, so a broken answer is raised as one."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except http.client.HTTPException as exc:
            raise ConnectionError(f"a broken answer from the server: {exc!r:.100}") from None
    return wrapper


@_network
def _fetch(method, url, headers, body=None, timeout=60):
    """One API exchange: (HTTP status, body bytes). Network failures raise OSError."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_API_BODY)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_API_BODY)


def _https(url, what):
    if urllib.parse.urlsplit(str(url)).scheme != "https":
        raise JobError(f"{what} is not an https address: {str(url)[:80]}")


@_network
def _stream(url, dest, timeout=60):
    """Stream one result to dest. No key: results live on another host. A
    download that ends short of the length the server stated raises: a sized
    read returns what came and says nothing, so a cut file would have been
    saved as the paid result."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        stated = (resp.headers.get("Content-Length") or "").strip()
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            got += len(chunk)
    if stated.isdigit() and got != int(stated):
        raise ConnectionError(f"the download stopped at {got} of {stated} bytes")


def _download(url, dest):
    _https(url, "the result")
    _stream(url, dest)


@_network
def _put_stream(url, headers, path, timeout=300):
    """PUT a file to a signed storage address with the headers it was signed
    with. Returns the HTTP status. The key never goes to storage."""
    hdrs = {k: v for k, v in headers.items() if k.lower() not in ("x-magnific-api-key", "content-length")}
    hdrs.update({"Content-Length": str(path.stat().st_size), "User-Agent": USER_AGENT})
    with open(path, "rb") as fh:
        req = urllib.request.Request(url, data=fh, method="PUT", headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code


def _put_file(url, headers, path):
    _https(url, "the upload address")
    return _put_stream(url, headers, path)


def api(method, path, payload=None, timeout=60):
    """A Magnific API call, fenced to the upscaler paths: (status, JSON dict)."""
    rx = ALLOWED.get(method)
    if not (rx and rx.fullmatch(path)):
        raise Refused(f"{method} {path} is outside the upscaler endpoints this key is kept to")
    headers = {"x-magnific-api-key": api_key(), "Accept": "application/json", "User-Agent": USER_AGENT}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    status, raw = _fetch(method, API + path, headers, body, timeout)
    try:
        data = json.loads(raw or b"null")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {"message": (raw or b"")[:300].decode("utf-8", "replace").strip()}
    return status, data


def api_error(status, data):
    problem = data.get("problem") if isinstance(data.get("problem"), dict) else {}
    message = data.get("message") or problem.get("message") or ""
    bad = "; ".join(f"{p.get('name')}: {p.get('reason')}" for p in problem.get("invalid_params") or []
                    if isinstance(p, dict))
    head = f"HTTP {status} " if status is not None else ""      # None: no answer came at all
    return f"{head}{message}".strip() + (f" ({bad})" if bad else "")


# ---------------------------------------------------------------- files

def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def sniff(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def image_info(path):
    """Format, size, alpha and bit depth of a PNG, JPEG or WebP; None for anything else."""
    with open(path, "rb") as fh:
        head = fh.read(32)
    fmt = sniff(head)
    if fmt is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        raise Refused("Pillow reads an image's size before it can be priced and is not installed: "
                      "pip install Pillow") from None
    try:
        with Image.open(path) as im:
            width, height = im.size
            alpha = im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in im.info
    except Exception as exc:
        raise Refused(f"cannot read {Path(path).name} as an image: {exc}") from None
    # A PNG states its depth in IHDR; Pillow reports a 16-bit RGB PNG as plain "RGB".
    bits = head[24] if fmt == "PNG" else 8
    return {"format": fmt, "width": width, "height": height, "alpha": bool(alpha), "bits": int(bits)}


def _rate(text):
    try:
        num, _, den = str(text or "").partition("/")
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def frame_count(stream, duration=None):
    """The frames a job will be billed for. The container's count and a packet
    count can disagree; the larger is quoted, so a quote never runs short."""
    counts = [int(v) for v in (stream.get("nb_frames"), stream.get("nb_read_packets")) if str(v or "").isdigit()]
    fps = _rate(stream.get("avg_frame_rate"))
    if not counts and fps and duration:
        counts = [math.ceil(fps * duration)]
    return max(counts) if counts else 0


def _ffprobe(args, timeout=300):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Refused("ffprobe measures a video before it can be priced and is not installed; it comes with "
                      "ffmpeg (sudo apt install ffmpeg, or brew install ffmpeg on a Mac)") from None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"ffprobe failed: {exc}") from None
    try:
        return out.returncode, json.loads(out.stdout or "{}")
    except ValueError:
        return out.returncode, {}


def video_info(path):
    """Size, frames, rate and sound of an MP4, MOV or WebM; None for any other kind of file."""
    if Path(path).suffix.lower() not in VIDEO_TYPES:
        return None
    code, info = _ffprobe(["-select_streams", "v:0", "-count_packets", "-show_entries",
                           "stream=codec_name,width,height,nb_frames,nb_read_packets,avg_frame_rate"
                           ":format=duration", "-of", "json", str(path)])
    streams = info.get("streams") or []
    if code != 0 or not streams:
        raise Refused(f"no video stream in {Path(path).name}")
    stream = streams[0]
    try:
        duration = float((info.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    frames = frame_count(stream, duration)
    if frames <= 0:
        raise Refused(f"cannot count the frames of {Path(path).name}, so it cannot be priced")
    _, sound = _ffprobe(["-select_streams", "a", "-show_entries", "stream=index", "-of", "json", str(path)])
    return {"format": Path(path).suffix.lower().lstrip(".").upper(), "codec": stream.get("codec_name"),
            "width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0), "frames": frames,
            "fps": _rate(stream.get("avg_frame_rate")), "duration": duration, "audio": bool(sound.get("streams"))}


# ---------------------------------------------------------------- pricing

def image_price(width, height, mode, scale):
    """(euros, tier name, result size)."""
    out_w, out_h = width * scale, height * scale
    if mode == "creative" and out_w * out_h > CREATIVE_MAX_PIXELS:
        raise Refused(f"Creative caps a result at 25.3 MP; {out_w}x{out_h} is {out_w * out_h / 1e6:.1f} MP")
    edge = max(out_w, out_h)
    tier = next((t for t in IMAGE_TIERS if edge <= t[0]), None)
    if tier is None:
        raise Refused(f"a {out_w}x{out_h} result is past the 10k tier, which has no published price")
    return tier[1], tier[2], (out_w, out_h)


def video_price(mode, resolution, frames):
    rate = VIDEO_PER_FRAME[mode][resolution]
    return round(frames * rate, 6), rate


def price_of(quote):
    """Price a quote from its stored measurements with the tables as they are now."""
    if quote["kind"] == "image":
        source = quote["input"]
        return image_price(source["width"], source["height"], quote["mode"], quote["scale"])[0]
    return video_price(quote["mode"], quote["params"]["resolution"], quote["input"]["frames"])[0]


def euros(value):
    """Rounded UP to the cent: a quote may read a little high, never low."""
    return f"€{math.ceil(round(value * 100, 6)) / 100:.2f}"


# ---------------------------------------------------------------- quotes on disk

def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    folder = Path(base) / "magnific"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def quote_path(code):
    if not CODE_RE.fullmatch(code or ""):
        raise Refused(f"{code!r} is not a quote code (six characters, 0 to 9 and a to f)")
    return state_dir() / f"{code}.json"


def save(quote):
    path = quote_path(quote["code"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(quote, indent=1))
    os.replace(tmp, path)


def load(code):
    code = str(code).lower()
    path = quote_path(code)
    try:
        quote = json.loads(path.read_text())
    except FileNotFoundError:
        raise Refused(f"there is no quote {code} on this machine") from None
    except ValueError:
        quote = None
    if not isinstance(quote, dict) or quote.get("code") != code:
        raise Refused(f"the quote file {path} cannot be read")
    return quote


def claim(code):
    """One quote, one job. The claim is made atomically before anything is
    sent, and stays once a paid request may have gone out."""
    try:
        fd = os.open(state_dir() / f"{code}.claim", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Refused(f"quote {code} has already been run, or is running now; quote again for a new job") from None
    os.close(fd)


def release(code):
    (state_dir() / f"{code}.claim").unlink(missing_ok=True)


# ---------------------------------------------------------------- quote

def build_quote(args):
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        raise Refused(f"there is no file at {path}")
    info, kind = image_info(path), "image"
    if info is None:
        info, kind = video_info(path), "video"
    if info is None:
        raise Refused(f"{path.name} is not a PNG, JPEG or WebP image, nor an MP4, MOV or WebM video")
    mode = args.mode or "precision"
    if (kind, mode) not in FIELDS:
        allowed = " or ".join(m for k, m in FIELDS if k == kind)
        raise Refused(f"{kind}s take --mode {allowed}, not {mode}")
    params = {}
    for name in SETTINGS:
        value = getattr(args, name, None)
        if value is None:
            continue
        check = FIELDS[(kind, mode)].get(name)
        if check is None:
            raise Refused(f"--{name.replace('_', '-')} does not apply to a {kind} with --mode {mode}")
        try:
            params[name] = check(value)
        except Refused as exc:
            raise Refused(f"--{name.replace('_', '-')} {exc}") from None
    quote = {"code": None, "created": time.time(), "kind": kind, "mode": mode, "state": "quoted",
             "input": {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_of(path), **info}}
    notes = []
    if kind == "image":
        if args.resolution or args.prores:
            raise Refused("--resolution and --prores are for videos; an image takes --scale")
        scale = 2 if args.scale is None else args.scale
        if mode == "creative" and scale not in (2, 4, 8, 16):
            raise Refused("Creative scales by 2, 4, 8 or 16")
        if not 2 <= scale <= 16:
            raise Refused("Precision V2 scales by 2 to 16")
        price, tier, (out_w, out_h) = image_price(info["width"], info["height"], mode, scale)
        params["scale_factor"] = scale if mode == "precision" else f"{scale}x"
        quote.update(scale=scale, result={"width": out_w, "height": out_h})
        price_line = (f"{euros(price)}, the \"{tier}\" tier of Magnific's API price list (read {PRICES_READ}), "
                      f"by the long side")
        notes.append("Magnific charges by the result's pixel area and publishes no pixel size for a tier, so the "
                     "dearest reading is quoted; the real charge can be lower")
        if mode == "precision":
            left = [f"{name.replace('_', ' ')} {value}" for name, value in PRECISION_DEFAULTS.items()
                    if name not in params]
            if left:
                notes.append(f"left at Magnific's defaults, which its docs say add AI detail, grain and "
                             f"sharpening: {', '.join(left)}; 0 for each stays closest to the source")
        if info["bits"] == 16:
            notes.append("16 bit source: Magnific documents no 16 bit result, so expect 8 bit back")
        if info["alpha"]:
            notes.append("the source has transparency, and Magnific documents no transparent result")
        if mode == "creative":
            notes.append("Creative invents detail (new texture, changed small lettering); Precision is the faithful one")
    else:
        if args.scale is not None:
            raise Refused("--scale is for images; a video takes --resolution 1k, 2k or 4k")
        if path.stat().st_size > UPLOAD_MAX:
            raise Refused("the upload limit is 1 GiB per file")
        resolution = args.resolution or "2k"
        short = min(info["width"], info["height"])
        if short >= VIDEO_SHORT_SIDE[resolution]:
            raise Refused(f"{info['width']}x{info['height']} is already {resolution} "
                          f"(about {VIDEO_SHORT_SIDE[resolution]}p) or larger, so this would not enlarge it")
        params["resolution"] = resolution
        if mode in ("standard", "turbo"):
            params.setdefault("flavor", "natural")   # Magnific's default, vivid, changes the colours
        if args.prores:
            params["output_format"] = "prores_422_hq"
        price, rate = video_price(mode, resolution, info["frames"])
        quote["result"] = {"resolution": resolution}
        price_line = (f"{euros(price)} = {info['frames']} frames at €{rate:g} (Magnific's API price list, "
                      f"read {PRICES_READ})")
        notes.append("the file is first uploaded to Magnific's storage (deleted there after about 7 days)")
        if args.prores:
            notes.append("ProRes has no price line of its own; the per frame price is the same list's")
        if info["audio"]:
            notes.append("the source has sound; whether Magnific keeps it is checked when the result arrives")
    quote.update(endpoint=(IMAGE_ENDPOINTS if kind == "image" else VIDEO_ENDPOINTS)[mode], params=params,
                 price_eur=price, price_line=price_line, notes=notes)
    quote["status_path"] = STATUS_PATHS.get(quote["endpoint"], quote["endpoint"])
    while True:
        code = secrets.token_hex(3)
        if not quote_path(code).exists() and not (state_dir() / f"{code}.claim").exists():
            break
    quote["code"] = code
    save(quote)
    return quote


def describe_source(quote):
    src = quote["input"]
    if quote["kind"] == "image":
        return f"{Path(src['path']).name}, {src['format']} {src['width']}x{src['height']}"
    fps = f" at {src['fps']:g} fps" if src.get("fps") else ""
    sound = ", with sound" if src.get("audio") else ""
    return (f"{Path(src['path']).name}, {src.get('codec') or src['format']} {src['width']}x{src['height']}, "
            f"{src['frames']} frames{fps} ({src['duration']:.1f} s){sound}")


def describe_settings(quote):
    shown = {k: v for k, v in quote["params"].items() if k not in ("scale_factor", "resolution", "output_format")}
    text = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in shown.items())
    return (text + "; the rest at Magnific's defaults") if text else "Magnific's defaults"


def cmd_quote(args):
    quote = build_quote(args)
    until = time.strftime("%a %d %b %H:%M", time.localtime(quote["created"] + QUOTE_TTL))
    if quote["kind"] == "image":
        result = quote["result"]
        what = f"{MODE_NAMES[('image', quote['mode'])]}, {quote['scale']}x"
        size = f"{result['width']}x{result['height']} ({result['width'] * result['height'] / 1e6:.1f} MP)"
    else:
        res = quote["result"]["resolution"]
        what = f"{MODE_NAMES[('video', quote['mode'])]}, to {res} (about {VIDEO_SHORT_SIDE[res]}p)"
        size = f"{res}" + (", ProRes 422 HQ master plus an H.264 preview"
                           if quote["params"].get("output_format") else ", H.264 MP4")
    print(f"Magnific quote {quote['code']}: nothing spent. Valid until {until}.")
    print(f"  file      {describe_source(quote)}")
    print(f"            {quote['input']['path']}")
    print(f"  upscaler  {what}")
    print(f"  settings  {describe_settings(quote)}")
    print(f"  result    {size}")
    print(f"  price     {quote['price_line']}")
    for note in quote["notes"]:
        print(f"  note      {note}")
    print(f"Only after a yes:  python {Path(__file__).resolve()} run {quote['code']}")


# ---------------------------------------------------------------- run

def submit_payload(quote, data):
    """The request body. A video goes to Magnific's storage first: the video
    upscalers only take an https address."""
    params = dict(quote["params"])
    if quote["kind"] == "image":
        return {"image": base64.b64encode(data).decode("ascii"), **params}
    src = Path(quote["input"]["path"])
    status, answer = api("POST", UPLOAD_PATH, {"files": [{"content_type": VIDEO_TYPES[src.suffix.lower()]}]})
    files = answer.get("files") if status == 200 else None
    slot = files[0] if isinstance(files, list) and files and isinstance(files[0], dict) else {}
    if not slot.get("upload_url") or not slot.get("asset_url"):
        raise JobError(f"Magnific gave no upload address: {api_error(status, answer)}")
    signed = {"Content-Type": VIDEO_TYPES[src.suffix.lower()], **(slot.get("headers") or {})}
    put = _put_file(slot["upload_url"], signed, src)
    if not 200 <= put < 300:
        raise JobError(f"the upload to Magnific's storage failed: HTTP {put}")
    return {"video": slot["asset_url"], **params}


def cmd_run(args):
    quote = load(args.code)
    code = quote["code"]
    if quote.get("state") != "quoted":
        raise Refused(f"quote {code} is {quote.get('state')}, not waiting to run; nothing was sent. "
                      f"`status {code}` says more")
    if time.time() - quote["created"] > QUOTE_TTL:
        raise Refused(f"quote {code} is more than 24 hours old; nothing was sent. Quote again")
    src = Path(quote["input"]["path"])
    data = src.read_bytes() if quote["kind"] == "image" and src.is_file() else None
    if not src.is_file() or src.stat().st_size != quote["input"]["bytes"] or \
            (hashlib.sha256(data).hexdigest() if data is not None else sha256_of(src)) != quote["input"]["sha256"]:
        raise Refused(f"{src} is gone or changed since the quote; nothing was sent. Quote again")
    if price_of(quote) != quote["price_eur"]:
        raise Refused("this script's price list changed since the quote; nothing was sent. Quote again")
    claim(code)
    try:
        payload = submit_payload(quote, data)
    except BaseException as exc:
        release(code)                            # nothing paid has gone out
        if isinstance(exc, OSError):
            raise JobError(f"the upload did not go through ({exc}); nothing paid was sent, so "
                           f"`run {code}` can be tried again") from None
        raise
    output = str(Path(args.output).expanduser().resolve()) if args.output else None
    quote.update(state="submitting", submitted=time.time(), output=output)
    save(quote)
    print(f"Spending: quote {code}, {euros(quote['price_eur'])} by the price list, "
          f"{MODE_NAMES[(quote['kind'], quote['mode'])]} on {src.name}", flush=True)
    try:
        status, answer = api("POST", quote["endpoint"], payload, timeout=300)
    except OSError as exc:
        quote.update(state="unknown", error=f"no answer: {exc}")
        save(quote)
        raise JobError(unknown_text(code, f"no answer ({exc})")) from None
    task = answer.get("data") if isinstance(answer.get("data"), dict) else {}
    task_id = str(task.get("task_id") or "")
    if 200 <= status < 300 and TASK_RE.fullmatch(task_id):
        print(f"Magnific job {task_id} started.", flush=True)
        quote.update(state="submitted", task_id=task_id)
        save(quote)
        return finish(quote, output, args.wait)
    if 400 <= status < 500:
        quote.update(state="rejected", error=api_error(status, answer))
        save(quote)
        raise JobError(f"Magnific refused the job, so no job was made: {api_error(status, answer)}. "
                       f"Quote again once the cause is fixed")
    quote.update(state="unknown", error=api_error(status, answer))
    save(quote)
    raise JobError(unknown_text(code, api_error(status, answer)))


def unknown_text(code, why):
    return (f"Magnific's answer to quote {code} was {why}, so the job may or may not have started, and may "
            f"have been charged. Do NOT quote and run it again before looking: `check` lists Magnific's jobs; "
            f"if one is there, `fetch {code} --task TASK_ID` downloads it")


def wait_for(quote, wait):
    """The result addresses, None for a failed job, or "running" when the wait ran out."""
    path = f"{quote['status_path']}/{quote['task_id']}"
    deadline = time.monotonic() + wait
    last, misses = None, 0
    while True:
        try:
            status, answer = api("GET", path)
        except OSError as exc:
            status, answer = None, {"message": str(exc)}
        task = answer.get("data") if isinstance(answer.get("data"), dict) else None
        if status == 200 and task is not None:
            state = task.get("status")
            if state != last:
                print(f"{time.strftime('%H:%M:%S')} {state}", flush=True)
                last = state
            if state == "COMPLETED":
                return [u for u in task.get("generated") or [] if isinstance(u, str)]
            if state == "FAILED":
                return None
        elif status in (401, 403):
            raise JobError(f"the key was refused while waiting: {api_error(status, answer)}; the job "
                           f"may still finish, `fetch {quote['code']}` once the key works")
        else:
            misses += 1
            if misses in (1, 10, 100):
                print(f"status check failed ({api_error(status, answer)}); still trying", flush=True)
        if time.monotonic() >= deadline:
            return "running"
        time.sleep(POLL[quote["kind"]])


def finish(quote, output, wait):
    code = quote["code"]
    result = wait_for(quote, wait if wait is not None else WAIT[quote["kind"]])
    if result == "running":
        print(f"Still running when the wait ran out. `fetch {code}` picks it up; the result's link "
              f"works for 30 minutes after it ends.")
        return 3
    if result is None or not result:
        quote.update(state="failed", error="reported FAILED" if result is None else "ended without a result")
        save(quote)
        again = "" if result is None else f" (`fetch {code}` looks again, for free)"
        raise JobError(f"Magnific's job {quote['task_id']} {quote['error']}{again}. Whether a failed job is "
                       f"charged is not documented; the web app's balance shows it")
    paths = save_results(quote, result, output or quote.get("output"))
    quote.update(state="done", outputs=[str(p) for p in paths], finished=time.time())
    save(quote)
    for path in paths:
        print(check_result(quote, path))
    print(f"Done: quote {code}, {euros(quote['price_eur'])} by the price list.")
    return 0


# ---------------------------------------------------------------- results

def default_output(quote):
    src = Path(quote["input"]["path"])
    size = f"{quote['scale']}x" if quote["kind"] == "image" else quote["params"]["resolution"]
    return src.with_name(f"{src.stem}_magnific_{quote['mode']}_{size}")


def unique_path(path):
    """Never write over a file: a paid result gets a fresh name instead."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def real_extension(path, hint):
    with open(path, "rb") as fh:
        head = fh.read(16)
    fmt = sniff(head)
    if fmt:
        return {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
    if head[4:8] == b"ftyp":
        return ".mov" if head[8:12] == b"qt  " else ".mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    return hint or ".bin"


def save_results(quote, urls, output):
    base = Path(output).expanduser().resolve() if output else default_output(quote)
    if base.is_dir():
        base = base / default_output(quote).name
    base.parent.mkdir(parents=True, exist_ok=True)
    stem = base.stem if base.suffix.lower() in MEDIA_SUFFIXES + (".jpeg",) else base.name
    # With ProRes, the .mov is the master and takes the name; the MP4 is its preview.
    has_master = any(urllib.parse.urlsplit(u).path.lower().endswith(".mov") for u in urls)
    ordered = sorted(urls, key=lambda u: not urllib.parse.urlsplit(u).path.lower().endswith(".mov"))
    saved = []
    for i, url in enumerate(ordered):
        hint = Path(urllib.parse.urlsplit(url).path).suffix.lower()
        name = stem if i == 0 else (f"{stem}_preview" if has_master and hint == ".mp4" else f"{stem}_{i + 1}")
        part = base.parent / f".{name}.part"
        for attempt in range(3):
            try:
                _download(url, part)
                break
            except OSError as exc:
                if attempt == 2:
                    part.unlink(missing_ok=True)
                    raise JobError(f"the result could not be downloaded ({exc}); `fetch {quote['code']}` "
                                   f"retries within 30 minutes of the job's end") from None
                time.sleep(5 * (attempt + 1))
        if part.stat().st_size == 0:
            part.unlink()
            raise JobError(f"Magnific sent an empty file for {url[:80]}")
        final = unique_path(base.parent / (name + real_extension(part, hint)))
        os.replace(part, final)
        asked = {".jpeg": ".jpg"}.get(base.suffix.lower(), base.suffix.lower())
        if i == 0 and asked in MEDIA_SUFFIXES and final.suffix != asked:
            print(f"Saved as {final.suffix} because Magnific sent that format, not {base.suffix}")
        saved.append(final)
    return saved


def check_result(quote, path):
    """What actually arrived, beside what was asked for."""
    if quote["kind"] == "image":
        try:
            info = image_info(path)
        except Refused as exc:                   # the job is paid and done; exit 2 would say nothing was sent
            return f"Saved {path}, but it does not read as an image ({exc}), so look at it before using it"
        if info is None:
            return f"Saved {path}: not a PNG, JPEG or WebP, so look at it before using it"
        line = f"Saved {path}: {info['format']} {info['width']}x{info['height']}, {info['bits']} bit"
        want = quote["result"]
        if {info["width"], info["height"]} != {want["width"], want["height"]}:
            line += f"; asked for {want['width']}x{want['height']}"
        if quote["input"]["bits"] == 16 and info["bits"] != 16:
            line += "; the 16 bit source came back 8 bit"
        if quote["input"]["alpha"] and not info["alpha"]:
            line += "; the transparency was lost"
        return line
    try:
        info = video_info(path)
    except Refused as exc:
        return f"Saved {path}; ffprobe could not read it ({exc})"
    if info is None:
        return f"Saved {path}"
    line = (f"Saved {path}: {info['codec']} {info['width']}x{info['height']}, {info['frames']} frames"
            + (f" at {info['fps']:g} fps" if info.get("fps") else ""))
    if info["frames"] != quote["input"]["frames"]:
        line += f"; the source had {quote['input']['frames']}"
    if quote["input"]["audio"] and not info["audio"]:
        line += "; the sound was dropped (the video-editing skill can put the source's sound back)"
    return line


# ---------------------------------------------------------------- free commands

def cmd_check(args):
    paths = ("/v1/ai/image-upscaler-precision-v2", "/v1/ai/image-upscaler", "/v1/ai/video-upscaler-precision",
             "/v1/ai/video-upscaler")
    for path in paths:
        status, answer = api("GET", path)
        if status in (401, 403):
            print(f"The key was refused: {api_error(status, answer)}")
            return 1
        if status == 404 and "task not found" in str(answer.get("message", "")).lower():
            print(f"{path}: key works, no jobs yet")
        elif status == 200 and isinstance(answer.get("data"), list):
            tasks = [t for t in answer["data"] if isinstance(t, dict)]
            counts = {}
            for t in tasks:
                counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
            summary = ", ".join(f"{n} {s}" for s, n in counts.items())
            print(f"{path}: key works, {len(tasks)} jobs ({summary})")
            for t in tasks[:5]:
                print(f"    {t.get('task_id')}  {t.get('status')}")
        else:
            print(f"{path}: {api_error(status, answer)}")
    return 0


def cmd_status(args):
    quote = load(args.code)
    print(f"Quote {quote['code']}: {quote['state']}, {describe_source(quote)}, "
          f"{MODE_NAMES[(quote['kind'], quote['mode'])]}, {euros(quote['price_eur'])}")
    if quote.get("error"):
        print(f"  {quote['error']}")
    for out in quote.get("outputs") or []:
        print(f"  saved {out}")
    if quote.get("task_id"):
        status, answer = api("GET", f"{quote['status_path']}/{quote['task_id']}")
        task = answer.get("data") if isinstance(answer.get("data"), dict) else None
        print(f"  Magnific job {quote['task_id']}: "
              + (str(task.get("status")) if status == 200 and task else api_error(status, answer)))
    return 0


def cmd_fetch(args):
    quote = load(args.code)
    if args.task:
        if quote["state"] not in ("unknown", "submitting"):
            raise Refused(f"--task is only for a job whose start went unanswered or was cut off; quote "
                          f"{quote['code']} is {quote['state']}")
        if not TASK_RE.fullmatch(args.task):
            raise Refused(f"{args.task!r} is not a Magnific task id")
        quote.update(state="submitted", task_id=args.task)
        save(quote)
    # A failed job is looked at again too: the look is free, and a job that
    # reported COMPLETED with no result yet is not lost to the tool.
    if quote["state"] not in ("submitted", "failed"):
        done = "; saved " + ", ".join(quote.get("outputs") or []) if quote["state"] == "done" else ""
        raise Refused(f"quote {quote['code']} is {quote['state']}, nothing to fetch{done}")
    return finish(quote, args.output, args.wait)


def cmd_ledger(args):
    quotes = []
    for path in sorted(state_dir().glob("*.json")):
        try:
            quotes.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            print(f"unreadable: {path}")
    if not quotes:
        print("No quotes yet.")
        return 0
    for q in sorted(quotes, key=lambda q: q.get("created", 0)):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(q.get("created", 0)))
        task = f" job {q['task_id']}" if q.get("task_id") else ""
        print(f"{q['code']}  {when}  {q['kind']} {q['mode']:9}  {euros(q['price_eur']):>7}  {q['state']:10}"
              f"{task}  {Path(q['input']['path']).name}")
    sent = [q for q in quotes if q.get("state") in SENT_STATES]
    total = sum(q["price_eur"] for q in sent)
    print(f"Sent to Magnific: {len(sent)} jobs, {euros(total)} by the price list "
          f"(unknown ones counted; the web app's balance is the real figure).")
    return 0


# ---------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="free: is the key good, which upscale jobs exist")
    q = sub.add_parser("quote", help="free, offline: price a job and get its code")
    q.add_argument("file")
    q.add_argument("--mode", choices=("precision", "creative", "standard", "turbo"),
                   help="images: precision (default) or creative; videos: precision (default), standard, turbo")
    q.add_argument("--scale", type=int, help="images: 2 to 16 (creative: 2, 4, 8, 16); default 2")
    q.add_argument("--resolution", choices=("1k", "2k", "4k"), help="videos: default 2k")
    q.add_argument("--prores", action="store_true", help="videos: ProRes 422 HQ master plus an H.264 preview")
    for name in ("sharpen", "smart_grain", "ultra_detail", "strength", "creativity", "hdr", "resemblance",
                 "fractality"):
        q.add_argument("--" + name.replace("_", "-"), dest=name, type=int)
    for name in ("flavor", "engine", "optimized_for", "prompt"):
        q.add_argument("--" + name.replace("_", "-"), dest=name)
    r = sub.add_parser("run", help="SPENDS CREDITS: run a quoted job, once")
    r.add_argument("code")
    r.add_argument("-o", "--output", help="where to save; the extension follows what Magnific sends")
    r.add_argument("--wait", type=int, help="seconds to wait for the result (image 900, video 7200)")
    s = sub.add_parser("status", help="free: where a quoted job stands")
    s.add_argument("code")
    f = sub.add_parser("fetch", help="free: wait for a running job and download it")
    f.add_argument("code")
    f.add_argument("-o", "--output")
    f.add_argument("--wait", type=int)
    f.add_argument("--task", help="adopt this Magnific task for a quote whose start went unanswered")
    sub.add_parser("ledger", help="every quote and job, and what was sent")
    args = ap.parse_args(argv)
    commands = {"check": cmd_check, "quote": cmd_quote, "run": cmd_run, "status": cmd_status, "fetch": cmd_fetch,
                "ledger": cmd_ledger}
    try:
        return commands[args.cmd](args) or 0
    except Refused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    except JobError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
