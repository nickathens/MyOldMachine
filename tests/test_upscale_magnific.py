"""magnific.py, the upscale skill's paid route (2026-10-14).

The key spends the account's credits, so nothing here reaches Magnific: the
three transport functions are replaced by a recording fake, a socket guard
fails any test that tries to leave this machine, and the transport itself is
tested against local HTTP servers. What is pinned: a quote is free and
offline, a job runs only from its own quote (once, unchanged file, same price,
inside 24 hours), every request stays inside the upscaler endpoints, the key
goes to Magnific and nowhere else, and a paid result is never lost or written
over.

The video flow tests use fake videos whose numbers a fake ffprobe reads
back, so they also run where ffmpeg is not installed; RealVideo holds that
fake to the real ffprobe on real files wherever ffmpeg is.
"""

# Pillow is the one library this needs: a CI without it skips the module
# instead of failing on an import.
import importlib.util as _ilu
import unittest as _unittest

if _ilu.find_spec("PIL") is None:
    raise _unittest.SkipTest("needs Pillow")

import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from PIL import Image

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "upscale" / "scripts" / "magnific.py"
_spec = importlib.util.spec_from_file_location("magnific_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

FAKE_KEY = "MSfake00magnific00key00for00tests"
TASK = "046b6c7f-0b8a-43b9-b35d-6489e6daee91"
QUOTE_RE = re.compile(r"Magnific quote ([0-9a-f]{6})")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
REAL_FFPROBE, REAL_FETCH = mod._ffprobe, mod._fetch

_real_connect = socket.socket.connect
_net_guard = None


def _loopback_only(sock, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise AssertionError(f"a test tried to reach {address}")
    return _real_connect(sock, address)


def setUpModule():
    global _net_guard
    _net_guard = mock.patch.object(socket.socket, "connect", _loopback_only)
    _net_guard.start()


def tearDownModule():
    _net_guard.stop()


def cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = mod.main([str(a) for a in args])
        except SystemExit as exc:
            code = exc.code
        except Exception as exc:                 # an escaped exception is a result to assert on
            code = f"raised {exc!r}"
    return code, out.getvalue(), err.getvalue()


def png_bytes(w, h, fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 150)).save(buf, fmt)
    return buf.getvalue()


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True, timeout=120)


def fake_video(path, width=320, height=240, frames=12, fps="24/1", audio=False):
    """A fake video file: fake_ffprobe reads these numbers back."""
    Path(path).write_text(json.dumps({"fake_video": [width, height, frames, fps, audio]}))
    return Path(path)


def fake_ffprobe(args, timeout=300):
    """ffprobe's answer for a fake video, in the real one's shape
    (RealVideo.test_stand_in_reads_like_the_real_thing holds it there); any
    other file goes to the real ffprobe."""
    try:
        width, height, frames, fps, audio = json.loads(Path(args[-1]).read_text())["fake_video"]
    except (OSError, ValueError, KeyError, TypeError):
        return REAL_FFPROBE(args, timeout)
    if "a" in args:                                   # the sound probe: -select_streams a
        return 0, {"streams": [{"index": 1}] if audio else []}
    num, _, den = fps.partition("/")
    return 0, {"streams": [{"codec_name": "h264", "width": width, "height": height, "avg_frame_rate": fps,
                            "nb_frames": str(frames), "nb_read_packets": str(frames)}],
               "format": {"duration": f"{frames * int(den or 1) / int(num):.6f}"}}


def raw_server(test, reply):
    """A server on 127.0.0.1 that reads each whole request, answers with these
    exact bytes and hangs up: a garbled or cut off answer, on purpose."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    test.addCleanup(sock.close)

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    data = b""
                    while b"\r\n\r\n" not in data and (chunk := conn.recv(65536)):
                        data += chunk
                    head, _, body = data.partition(b"\r\n\r\n")
                    length = re.search(rb"(?i)content-length:\s*(\d+)", head)
                    while length and len(body) < int(length.group(1)) and (chunk := conn.recv(65536)):
                        body += chunk
                    conn.sendall(reply)
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return f"http://127.0.0.1:{sock.getsockname()[1]}"


def outcome(call):
    """The exception a call raised, or None: a wrong kind of exception then
    fails an assertion instead of erroring the test."""
    try:
        call()
    except Exception as exc:
        return exc
    return None


class Tripwire:
    """Any use of the network fails the test."""

    def __call__(self, *args, **kwargs):
        raise AssertionError(f"network used: {args[:2]}")


class FakeMagnific:
    """Stands in for _fetch, _stream and _put_stream and records every call."""

    def __init__(self):
        self.calls = []
        self.post_status, self.post_error = 200, None
        self.statuses = ["IN_PROGRESS", "COMPLETED"]
        self.generated = ["https://cdn.example/result.png"]
        self.results = {}
        self.put_status = 200
        self.list_answer = (404, {"message": "Task not found"})

    def fetch(self, method, url, headers, body=None, timeout=60):
        path = urlsplit(url).path
        payload = json.loads(body) if body else None
        self.calls.append(("api", method, path, dict(headers), payload))
        if method == "POST" and path == "/v1/ai/uploads/request-url":
            slot = {"file_id": "upl_vid_1", "upload_url": "https://storage.example/put?sig=1",
                    "headers": {"Content-Type": "video/mp4", "x-goog-content-length-range": "0,1073741824"},
                    "expires_in": 120, "asset_url": "https://cdn.example/asset.mp4?token=t",
                    "asset_url_expires_in": 86400}
            return 200, json.dumps({"files": [slot]}).encode()
        if method == "POST":
            if self.post_error:
                raise self.post_error
            if self.post_status != 200:
                return self.post_status, b'{"message": "no"}'
            return 200, json.dumps({"data": {"task_id": TASK, "status": "CREATED", "generated": []}}).encode()
        if method == "GET" and path.endswith("/" + TASK):
            state = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            generated = self.generated if state == "COMPLETED" else []
            return 200, json.dumps({"data": {"task_id": TASK, "status": state, "generated": generated}}).encode()
        if method == "GET":
            return self.list_answer[0], json.dumps(self.list_answer[1]).encode()
        raise AssertionError(f"unexpected {method} {url}")

    def stream(self, url, dest, timeout=60):
        self.calls.append(("download", "GET", url, {}, None))
        Path(dest).write_bytes(self.results[url])

    def put(self, url, headers, path, timeout=300):
        self.calls.append(("put", "PUT", url, dict(headers), Path(path).read_bytes()))
        return self.put_status

    def paid(self):
        return [c for c in self.calls if c[0] == "api" and c[1] == "POST" and "uploads" not in c[2]]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = Path(tempfile.mkdtemp(prefix="magnific-fixtures-"))
        f = cls.fixtures
        Image.new("RGB", (600, 400), (40, 80, 120)).save(f / "photo.png")
        Image.new("RGB", (1280, 720), (40, 80, 120)).save(f / "wide.png")
        Image.new("RGBA", (64, 64), (40, 80, 120, 128)).save(f / "alpha.png")
        Image.new("I;16", (64, 64), 40000).save(f / "deep.png")
        (f / "notes.txt").write_text("not media")
        fake_video(f / "clip.mp4")
        fake_video(f / "tall.mp4", 1080, 1920, 2)
        fake_video(f / "master.mov", 640, 480)          # not clip.mp4's bytes, so a swap shows

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixtures, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="magnific-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "state"), mod.KEY_NAME: FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (("ENV_FILE", self.tmp / "no.env"), ("POLL", {"image": 0, "video": 0}),
                            ("_ffprobe", fake_ffprobe)):
            patcher = mock.patch.object(mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fake = FakeMagnific()
        self.fake.results["https://cdn.example/result.png"] = png_bytes(1200, 800)
        self.use(self.fake.fetch, self.fake.stream, self.fake.put)

    def use(self, fetch, stream, put):
        for name, value in (("_fetch", fetch), ("_stream", stream), ("_put_stream", put)):
            patcher = mock.patch.object(mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def offline(self):
        trip = Tripwire()
        self.use(trip, trip, trip)

    def copy(self, name, as_name=None):
        dest = self.tmp / (as_name or name)
        shutil.copy(self.fixtures / name, dest)
        return dest

    def quote(self, *args):
        code, out, err = cli("quote", *args)
        self.assertEqual(code, 0, err)
        return QUOTE_RE.search(out).group(1), out

    def record(self, code):
        return json.loads((self.tmp / "state" / "magnific" / f"{code}.json").read_text())

    def rewrite(self, code, **changes):
        path = self.tmp / "state" / "magnific" / f"{code}.json"
        quote = json.loads(path.read_text())
        quote.update(changes)
        path.write_text(json.dumps(quote))


class Quote(Base):
    def test_quote_makes_no_request(self):
        self.offline()
        code, out = self.quote(self.copy("photo.png"))
        self.assertIn("€0.10", out)
        self.assertIn("1200x800", out)
        self.assertIn("nothing spent", out)
        self.quote(self.copy("clip.mp4"))
        self.assertEqual(len(list((self.tmp / "state" / "magnific").glob("*.json"))), 2)
        self.assertEqual(self.record(code)["state"], "quoted")

    def test_image_tiers_by_long_side(self):
        cases = [((1024, 1024, 2), 0.10), ((1025, 500, 2), 0.20), ((2048, 2048, 2), 0.20),
                 ((2049, 10, 2), 1.20), ((5120, 5120, 2), 1.20), ((1000, 700, 4), 0.20)]
        for (w, h, scale), price in cases:
            self.assertEqual(mod.image_price(w, h, "precision", scale)[0], price, f"{w}x{h} at {scale}x")
        with self.assertRaises(mod.Refused):
            mod.image_price(5121, 100, "precision", 2)

    # Magnific's docs, "How much does the Magnific Upscaler cost?" on the
    # Creative and Precision pages (read 2026-10-14): input, factor, euros.
    # They price by pixel area on a finer ladder than the price list, so they
    # are a second source the quote must never fall below.
    MAGNIFIC_EXAMPLES = [((640, 480), 2, 0.10), ((640, 480), 4, 0.20), ((640, 480), 8, 0.50),
                         ((1280, 720), 2, 0.10), ((1280, 720), 4, 0.40), ((1920, 1080), 2, 0.20)]

    def test_quote_never_below_magnific_examples(self):
        for (w, h), scale, published in self.MAGNIFIC_EXAMPLES:
            for mode in ("precision", "creative"):
                self.assertGreaterEqual(mod.image_price(w, h, mode, scale)[0], published,
                                        f"{mode} {w}x{h} at {scale}x")

    def test_quote_says_the_charge_can_be_lower(self):
        _, out = self.quote(self.copy("wide.png"))
        self.assertIn("2560x1440", out)
        self.assertIn("€0.20", out)
        self.assertIn("the real charge can be lower", out)
        self.assertNotIn("pixel area it is", out)       # the old figure, which could read below the docs

    def test_precision_defaults_are_named(self):
        photo = self.copy("photo.png")
        code, out = self.quote(photo)
        self.assertIn("Precision V2 (stays close to the source)", out)
        self.assertIn("ultra detail 30, smart grain 7, sharpen 7; 0 for each stays closest", out)
        self.assertNotIn("ultra_detail", self.record(code)["params"])   # named, never sent
        _, out = self.quote(photo, "--ultra-detail", 0)
        self.assertIn("sharpening: smart grain 7, sharpen 7;", out)
        _, out = self.quote(photo, "--ultra-detail", 0, "--smart-grain", 0, "--sharpen", 0)
        self.assertNotIn("left at Magnific's defaults", out)
        _, out = self.quote(photo, "--mode", "creative")
        self.assertNotIn("left at Magnific's defaults", out)

    def test_missing_pillow_is_explained(self):
        photo = self.copy("photo.png")
        with mock.patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            code, _, err = cli("quote", photo)
        self.assertEqual(code, 2, err)
        self.assertIn("pip install Pillow", err)

    def test_missing_ffprobe_is_explained(self):
        clip = self.copy("clip.mp4")
        with mock.patch.object(mod, "_ffprobe", REAL_FFPROBE), mock.patch.dict(os.environ, {"PATH": str(self.tmp)}):
            code, _, err = cli("quote", clip)
        self.assertEqual(code, 2, err)
        self.assertIn("sudo apt install ffmpeg", err)

    def test_creative_cap(self):
        with self.assertRaises(mod.Refused):
            mod.image_price(2600, 2600, "creative", 2)
        self.assertEqual(mod.image_price(2500, 2500, "creative", 2)[0], 1.20)
        self.assertEqual(mod.image_price(2600, 2600, "precision", 2)[0], 1.20)

    def test_creative_scale_choices(self):
        photo = self.copy("photo.png")
        for args, want in (((), 0), (("--scale", 3), 0), (("--scale", 17), 2), (("--scale", 1), 2),
                           (("--scale", 0), 2), (("--mode", "creative", "--scale", 3), 2),
                           (("--mode", "creative", "--scale", 8), 0)):
            self.assertEqual(cli("quote", photo, *args)[0], want, args)

    def test_option_for_other_mode_refused(self):
        photo, clip = self.copy("photo.png"), self.copy("clip.mp4")
        refused = [(photo, "--prompt", "a face"), (photo, "--resolution", "4k"), (photo, "--prores"),
                   (clip, "--scale", 2), (photo, "--mode", "creative", "--creativity", 11),
                   (photo, "--flavor", "natural"), (clip, "--mode", "standard", "--strength", 50),
                   (clip, "--mode", "creative"), (photo, "--mode", "turbo")]
        for args in refused:
            code, _, err = cli("quote", *args)
            self.assertEqual(code, 2, args)
            self.assertIn("Refused", err)
        self.assertEqual(cli("quote", clip, "--mode", "standard", "--creativity", 50)[0], 0)
        self.assertEqual(cli("quote", photo, "--mode", "creative", "--creativity", -10)[0], 0)

    def test_video_price_counts_frames(self):
        clip = self.copy("clip.mp4")
        _, out = self.quote(clip)
        self.assertIn("12 frames at €0.019", out)
        self.assertIn("€0.23", out)
        code, out = self.quote(clip, "--mode", "turbo", "--resolution", "4k")
        self.assertIn("€0.16", out)
        self.assertAlmostEqual(self.record(code)["price_eur"], 0.156)

    def test_frame_count_takes_larger(self):
        self.assertEqual(mod.frame_count({"nb_frames": "10", "nb_read_packets": "12"}), 12)
        self.assertEqual(mod.frame_count({"nb_frames": "12", "nb_read_packets": "10"}), 12)
        self.assertEqual(mod.frame_count({"avg_frame_rate": "24/1"}, 0.5), 12)
        self.assertEqual(mod.frame_count({}), 0)

    def test_video_must_enlarge(self):
        tall = self.copy("tall.mp4")
        code, _, err = cli("quote", tall, "--resolution", "1k")
        self.assertEqual(code, 2)
        self.assertIn("would not enlarge", err)
        self.assertEqual(cli("quote", tall, "--resolution", "2k")[0], 0)

    def test_720p_is_not_offered(self):
        self.assertEqual(cli("quote", self.copy("clip.mp4"), "--resolution", "720p")[0], 2)

    def test_depth_and_alpha_notes(self):
        _, out = self.quote(self.copy("deep.png"))
        self.assertIn("16 bit source", out)
        _, out = self.quote(self.copy("alpha.png"))
        self.assertIn("transparency", out)

    def test_other_files_refused(self):
        self.assertEqual(cli("quote", self.copy("notes.txt"))[0], 2)
        self.assertEqual(cli("quote", self.tmp / "missing.png")[0], 2)


class Run(Base):
    def test_run_requires_known_code(self):
        self.offline()
        for code in ("zzzzzz", "abc123", "abc"):
            self.assertEqual(cli("run", code)[0], 2, code)

    def test_run_refuses_changed_file(self):
        photo = self.copy("photo.png")
        code, _ = self.quote(photo)
        self.offline()
        with open(photo, "ab") as fh:
            fh.write(b"x")
        self.assertEqual(cli("run", code)[0], 2)
        self.assertFalse((self.tmp / "state" / "magnific" / f"{code}.claim").exists())

    def test_run_refuses_same_size_change(self):
        photo = self.copy("photo.png")
        code, _ = self.quote(photo)
        self.offline()
        data = bytearray(photo.read_bytes())
        data[-20] ^= 0xFF
        photo.write_bytes(bytes(data))
        code_, _, err = cli("run", code)
        self.assertEqual(code_, 2)
        self.assertIn("changed since the quote", err)

    def test_run_refuses_expired(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.rewrite(code, created=time.time() - 25 * 3600)
        self.offline()
        code_, _, err = cli("run", code)
        self.assertEqual(code_, 2)
        self.assertIn("24 hours", err)

    def test_run_refuses_price_change(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.offline()
        tiers = ((2048, 0.15, "1k to 2k"),) + mod.IMAGE_TIERS[1:]
        with mock.patch.object(mod, "IMAGE_TIERS", tiers):
            code_, _, err = cli("run", code)
        self.assertEqual(code_, 2)
        self.assertIn("price list changed", err)

    def test_concurrent_claim(self):
        code, _ = self.quote(self.copy("photo.png"))
        (self.tmp / "state" / "magnific" / f"{code}.claim").touch()
        self.offline()
        self.assertEqual(cli("run", code)[0], 2)

    def test_state_blocks_rerun_without_claim(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.rewrite(code, state="done")
        self.offline()
        self.assertEqual(cli("run", code)[0], 2)

    def test_run_once_only(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.assertEqual(cli("run", code, "-o", self.tmp / "out.png")[0], 0)
        self.assertEqual(cli("run", code, "-o", self.tmp / "again.png")[0], 2)
        self.assertEqual(len(self.fake.paid()), 1)

    def test_image_job_end_to_end(self):
        photo = self.copy("photo.png")
        code, _ = self.quote(photo)
        rc, out, err = cli("run", code, "-o", self.tmp / "out.png")
        self.assertEqual(rc, 0, err)
        paid = self.fake.paid()
        self.assertEqual(len(paid), 1)
        _, _, path, headers, body = paid[0]
        self.assertEqual(path, "/v1/ai/image-upscaler-precision-v2")
        self.assertEqual(base64.b64decode(body["image"]), photo.read_bytes())
        self.assertEqual(body["scale_factor"], 2)
        for call in self.fake.calls:
            if call[0] == "api":
                self.assertEqual(call[3]["x-magnific-api-key"], FAKE_KEY)
        self.assertEqual([c[2] for c in self.fake.calls if c[0] == "download"], ["https://cdn.example/result.png"])
        with Image.open(self.tmp / "out.png") as im:
            self.assertEqual(im.size, (1200, 800))
        quote = self.record(code)
        self.assertEqual(quote["state"], "done")
        self.assertEqual(quote["outputs"], [str(self.tmp / "out.png")])
        self.assertIn("PNG 1200x800, 8 bit", out)

    def test_creative_payload(self):
        code, _ = self.quote(self.copy("photo.png"), "--mode", "creative", "--scale", 4, "--prompt", "a quiet street",
                             "--engine", "magnific_sharpy")
        self.fake.results["https://cdn.example/result.png"] = png_bytes(2400, 1600)
        self.assertEqual(cli("run", code, "-o", self.tmp / "c.png")[0], 0)
        _, _, path, _, body = self.fake.paid()[0]
        self.assertEqual(path, "/v1/ai/image-upscaler")
        self.assertEqual((body["scale_factor"], body["prompt"], body["engine"]), ("4x", "a quiet street",
                                                                                   "magnific_sharpy"))

    def test_video_job_end_to_end(self):
        clip = self.copy("clip.mp4")
        self.fake.generated = ["https://cdn.example/up.mp4"]
        self.fake.results["https://cdn.example/up.mp4"] = clip.read_bytes()
        code, _ = self.quote(clip, "--strength", 80)
        rc, out, err = cli("run", code, "-o", self.tmp / "clip_up.mp4")
        self.assertEqual(rc, 0, err)
        upload = [c for c in self.fake.calls if c[2] == "/v1/ai/uploads/request-url"]
        self.assertEqual(upload[0][4], {"files": [{"content_type": "video/mp4"}]})
        put = [c for c in self.fake.calls if c[0] == "put"]
        self.assertEqual(put[0][2], "https://storage.example/put?sig=1")
        self.assertEqual(put[0][3]["x-goog-content-length-range"], "0,1073741824")
        self.assertEqual(put[0][4], clip.read_bytes())
        _, _, path, _, body = self.fake.paid()[0]
        self.assertEqual(path, "/v1/ai/video-upscaler-precision")
        self.assertEqual(body, {"video": "https://cdn.example/asset.mp4?token=t", "resolution": "2k", "strength": 80})
        self.assertTrue((self.tmp / "clip_up.mp4").is_file())
        self.assertIn("12 frames", out)

    def test_turbo_polls_standard_path(self):
        clip = self.copy("clip.mp4")
        self.fake.generated = ["https://cdn.example/up.mp4"]
        self.fake.results["https://cdn.example/up.mp4"] = clip.read_bytes()
        code, _ = self.quote(clip, "--mode", "turbo")
        self.assertEqual(cli("run", code, "-o", self.tmp / "t.mp4")[0], 0)
        _, _, path, _, body = self.fake.paid()[0]
        self.assertEqual(path, "/v1/ai/video-upscaler/turbo")
        self.assertEqual(body.get("flavor"), "natural")
        polls = [c[2] for c in self.fake.calls if c[1] == "GET" and c[0] == "api"]
        self.assertTrue(polls and all(p == f"/v1/ai/video-upscaler/{TASK}" for p in polls), polls)

    def test_prores_names_master_and_preview(self):
        clip = self.copy("clip.mp4")
        self.fake.generated = ["https://cdn.example/p.mp4", "https://cdn.example/m.mov"]
        self.fake.results["https://cdn.example/p.mp4"] = clip.read_bytes()
        self.fake.results["https://cdn.example/m.mov"] = (self.fixtures / "master.mov").read_bytes()
        code, _ = self.quote(clip, "--prores")
        self.assertEqual(cli("run", code, "-o", self.tmp / "graded.mov")[0], 0)
        self.assertEqual(self.fake.paid()[0][4]["output_format"], "prores_422_hq")
        self.assertEqual((self.tmp / "graded.mov").read_bytes(), (self.fixtures / "master.mov").read_bytes())
        self.assertEqual((self.tmp / "graded_preview.mp4").read_bytes(), clip.read_bytes())

    def test_post_rejected_4xx(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.post_status = 400
        self.assertEqual(cli("run", code)[0], 1)
        self.assertEqual(self.record(code)["state"], "rejected")
        self.assertFalse([c for c in self.fake.calls if c[1] == "GET"])
        self.assertEqual(cli("run", code)[0], 2)
        self.assertEqual(len(self.fake.paid()), 1)

    def test_post_network_error_is_unknown(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.post_error = TimeoutError("timed out")
        rc, _, err = cli("run", code)
        self.assertEqual(rc, 1)
        self.assertIn("Do NOT", err)
        self.assertEqual(self.record(code)["state"], "unknown")
        self.assertEqual(cli("run", code)[0], 2)
        self.assertEqual(len(self.fake.paid()), 1)

    def test_5xx_is_unknown(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.post_status = 503
        self.assertEqual(cli("run", code)[0], 1)
        self.assertEqual(self.record(code)["state"], "unknown")

    def test_upload_failure_releases_claim(self):
        clip = self.copy("clip.mp4")
        self.fake.generated = ["https://cdn.example/up.mp4"]
        self.fake.results["https://cdn.example/up.mp4"] = clip.read_bytes()
        code, _ = self.quote(clip)
        self.fake.put_status = 403
        self.assertEqual(cli("run", code)[0], 1)
        self.assertEqual(self.fake.paid(), [])
        self.assertEqual(self.record(code)["state"], "quoted")
        self.assertFalse((self.tmp / "state" / "magnific" / f"{code}.claim").exists())
        self.fake.put_status = 200
        self.assertEqual(cli("run", code, "-o", self.tmp / "v.mp4")[0], 0)
        self.assertEqual(len(self.fake.paid()), 1)

    def test_upload_network_error_releases_claim(self):
        code, _ = self.quote(self.copy("clip.mp4"))
        fetch = self.fake.fetch

        def no_storage(method, url, *rest, **kw):
            if url.endswith("/uploads/request-url"):
                raise ConnectionResetError("reset by peer")
            return fetch(method, url, *rest, **kw)

        self.use(no_storage, self.fake.stream, self.fake.put)
        rc, _, err = cli("run", code)
        self.assertEqual(rc, 1)
        self.assertIn("nothing paid was sent", err)
        self.assertEqual(self.record(code)["state"], "quoted")
        self.assertFalse((self.tmp / "state" / "magnific" / f"{code}.claim").exists())
        self.assertEqual(self.fake.paid(), [])

    def test_failed_task(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.statuses = ["FAILED"]
        self.assertEqual(cli("run", code)[0], 1)
        self.assertEqual(self.record(code)["state"], "failed")

    def test_wait_timeout_then_fetch(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.statuses = ["IN_PROGRESS"]
        rc, out, _ = cli("run", code, "-o", self.tmp / "late.png", "--wait", 0)
        self.assertEqual(rc, 3)
        self.assertIn(f"fetch {code}", out)
        self.assertEqual(self.record(code)["state"], "submitted")
        self.fake.statuses = ["COMPLETED"]
        self.assertEqual(cli("fetch", code)[0], 0)
        self.assertEqual(self.record(code)["state"], "done")
        self.assertTrue((self.tmp / "late.png").is_file())

    def test_fetch_adopts_task_for_unknown(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.post_error = TimeoutError("timed out")
        cli("run", code)
        self.assertEqual(cli("fetch", code, "--task", TASK, "-o", self.tmp / "found.png")[0], 0)
        self.assertEqual(self.record(code)["state"], "done")

    def test_fetch_adopts_task_after_cut_off_start(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.rewrite(code, state="submitting")
        (self.tmp / "state" / "magnific" / f"{code}.claim").touch()
        self.assertEqual(cli("fetch", code, "--task", TASK, "-o", self.tmp / "cut.png")[0], 0)
        self.assertEqual(self.record(code)["state"], "done")
        self.assertEqual(self.fake.paid(), [])

    def test_task_id_shown_even_if_state_cannot_be_saved(self):
        code, _ = self.quote(self.copy("photo.png"))
        real_save = mod.save

        def disk_full(quote):
            if quote.get("state") == "submitted":
                raise OSError("No space left on device")
            real_save(quote)

        with mock.patch.object(mod, "save", disk_full):
            rc, out, err = cli("run", code)
        self.assertEqual(rc, 1)
        self.assertIn(TASK, out)
        self.assertIn("No space left", err)
        self.assertEqual(self.record(code)["state"], "submitting")

    def test_fetch_task_only_for_unknown(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.offline()
        self.assertEqual(cli("fetch", code, "--task", TASK)[0], 2)
        self.assertEqual(self.record(code)["state"], "quoted")

    def test_existing_output_not_overwritten(self):
        (self.tmp / "out.png").write_bytes(b"keep")
        code, _ = self.quote(self.copy("photo.png"))
        self.assertEqual(cli("run", code, "-o", self.tmp / "out.png")[0], 0)
        self.assertEqual((self.tmp / "out.png").read_bytes(), b"keep")
        self.assertEqual(self.record(code)["outputs"], [str(self.tmp / "out_1.png")])

    def test_extension_follows_real_format(self):
        self.fake.results["https://cdn.example/result.png"] = png_bytes(1200, 800, "JPEG")
        code, _ = self.quote(self.copy("photo.png"))
        rc, out, _ = cli("run", code, "-o", self.tmp / "out.png")
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "out.jpg").is_file())
        self.assertFalse((self.tmp / "out.png").exists())
        self.assertIn("Saved as .jpg", out)

    def test_output_into_existing_folder(self):
        folder = self.tmp / "renders"
        folder.mkdir()
        code, _ = self.quote(self.copy("photo.png"))
        self.assertEqual(cli("run", code, "-o", folder)[0], 0)
        self.assertTrue((folder / "photo_magnific_precision_2x.png").is_file())

    def test_default_output_beside_source(self):
        photo = self.copy("photo.png")
        code, _ = self.quote(photo)
        self.assertEqual(cli("run", code)[0], 0)
        self.assertTrue((self.tmp / "photo_magnific_precision_2x.png").is_file())

    def test_key_never_printed(self):
        texts = []
        code, out = self.quote(self.copy("photo.png"))
        texts.append(out)
        for args in (("run", code, "-o", self.tmp / "o.png"), ("status", code), ("ledger",), ("check",),
                     ("run", code)):
            texts.extend(cli(*args)[1:])
        self.fake.list_answer = (401, {"message": "Unauthorized"})
        texts.extend(cli("check")[1:])
        joined = "\n".join(texts)
        self.assertNotIn(FAKE_KEY, joined)
        for path in (self.tmp / "state" / "magnific").iterdir():
            self.assertNotIn(FAKE_KEY, path.read_text())

    def test_ledger_totals(self):
        done, _ = self.quote(self.copy("photo.png"))
        cli("run", done, "-o", self.tmp / "o.png")
        refused, _ = self.quote(self.copy("photo.png", "second.png"))
        self.fake.post_status = 400
        cli("run", refused)
        rc, out, _ = cli("ledger")
        self.assertEqual(rc, 0)
        self.assertIn(done, out)
        self.assertIn(refused, out)
        self.assertIn("Sent to Magnific: 1 jobs, €0.10", out)

    def test_broken_answer_to_the_paid_start_is_unknown(self):
        code, _ = self.quote(self.copy("photo.png"))
        base = raw_server(self, b"HELLO THERE\r\n\r\n")
        with mock.patch.object(mod, "_fetch", REAL_FETCH), mock.patch.object(mod, "API", base):
            rc, _, err = cli("run", code)
        self.assertEqual(rc, 1, err)
        self.assertIn("broken answer", err)
        self.assertIn("Do NOT", err)
        self.assertEqual(self.record(code)["state"], "unknown")
        self.assertEqual(cli("run", code)[0], 2)

    def test_broken_status_answer_is_a_missed_check(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.statuses = ["IN_PROGRESS"]
        self.assertEqual(cli("run", code, "--wait", 0)[0], 3)
        base = raw_server(self, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n64\r\n" + b"x" * 20)
        with mock.patch.object(mod, "_fetch", REAL_FETCH), mock.patch.object(mod, "API", base):
            rc, out, err = cli("fetch", code, "--wait", 0)
        self.assertEqual(rc, 3, err)
        self.assertIn("broken answer", out)
        self.assertNotIn("HTTP None", out)              # no answer came, so there is no status to show
        self.assertEqual(self.record(code)["state"], "submitted")

    def test_cut_download_is_retried(self):
        code, _ = self.quote(self.copy("photo.png"))
        stream, cuts = self.fake.stream, []

        def cut_twice(url, dest, timeout=60):
            if len(cuts) < 2:
                cuts.append(url)
                Path(dest).write_bytes(b"\x89PNG cut")
                raise ConnectionError("the download stopped at 8 of 9000 bytes")
            return stream(url, dest, timeout)

        self.use(self.fake.fetch, cut_twice, self.fake.put)
        with mock.patch.object(mod.time, "sleep"):
            rc, _, err = cli("run", code, "-o", self.tmp / "out.png")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(cuts), 2)
        with Image.open(self.tmp / "out.png") as im:
            self.assertEqual(im.size, (1200, 800))

    def test_cut_three_times_stays_fetchable(self):
        code, _ = self.quote(self.copy("photo.png"))

        def always_cut(url, dest, timeout=60):
            Path(dest).write_bytes(b"\x89PNG cut")
            raise ConnectionError("the download stopped at 8 of 9000 bytes")

        self.use(self.fake.fetch, always_cut, self.fake.put)
        with mock.patch.object(mod.time, "sleep"):
            rc, _, err = cli("run", code, "-o", self.tmp / "out.png")
        self.assertEqual(rc, 1, err)
        self.assertIn("stopped at 8 of 9000 bytes", err)
        self.assertIn(f"fetch {code}", err)
        self.assertEqual(self.record(code)["state"], "submitted")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.is_file()), ["photo.png"])
        self.use(self.fake.fetch, self.fake.stream, self.fake.put)
        self.fake.statuses = ["COMPLETED"]
        self.assertEqual(cli("fetch", code)[0], 0)
        self.assertTrue((self.tmp / "out.png").is_file())
        self.assertEqual(len(self.fake.paid()), 1)

    def test_completed_without_result_can_be_fetched_again(self):
        code, _ = self.quote(self.copy("photo.png"))
        self.fake.generated = []
        rc, _, err = cli("run", code, "-o", self.tmp / "late.png")
        self.assertEqual(rc, 1, err)
        self.assertIn(f"`fetch {code}` looks again", err)
        self.assertEqual(self.record(code)["state"], "failed")
        self.fake.generated, self.fake.statuses = ["https://cdn.example/result.png"], ["COMPLETED"]
        self.assertEqual(cli("fetch", code)[0], 0)
        self.assertEqual(self.record(code)["state"], "done")
        self.assertTrue((self.tmp / "late.png").is_file())
        self.assertEqual(len(self.fake.paid()), 1)

    def test_unreadable_result_is_reported_not_refused(self):
        self.fake.results["https://cdn.example/result.png"] = b"\x89PNG\r\n\x1a\n" + bytes(40)
        code, _ = self.quote(self.copy("photo.png"))
        rc, out, err = cli("run", code, "-o", self.tmp / "out.png")
        self.assertEqual(rc, 0, err)
        self.assertIn("does not read as an image", out)
        self.assertEqual(self.record(code)["state"], "done")

    def test_unreadable_quote_is_refused(self):
        code, _ = self.quote(self.copy("photo.png"))
        path = self.tmp / "state" / "magnific" / f"{code}.json"
        self.offline()
        for text in ("{not json", "[]", json.dumps({"code": "ffffff", "state": "quoted"})):
            path.write_text(text)
            for args in (("run", code), ("status", code), ("fetch", code)):
                rc, _, err = cli(*args)
                self.assertEqual(rc, 2, (text, args, rc, err))
                self.assertIn("cannot be read", err)


class Fence(Base):
    def test_allowlist(self):
        self.offline()
        refused = [("POST", "/v1/ai/mystic"), ("GET", "/v1/analytics/usage"), ("POST", "/v1/ai/image-upscaler-precision"),
                   ("POST", "/v1/ai/video-upscaler-topaz"), ("GET", "/v1/ai/image-upscaler/../mystic"),
                   ("GET", "/v1/ai/image-upscaler?x=1"), ("DELETE", "/v1/ai/image-upscaler"), ("GET", "/v1/ai/uploads"),
                   ("POST", "/v1/ai/image-upscaler-precision-v2/x"), ("GET", "/v1/ai/video-upscaler/turbo")]
        for method, path in refused:
            with self.assertRaises(mod.Refused, msg=f"{method} {path}"):
                mod.api(method, path)

    def test_allowlist_lets_upscalers_through(self):
        for method, path in (("GET", "/v1/ai/image-upscaler-precision-v2"), ("GET", f"/v1/ai/video-upscaler/{TASK}"),
                             ("POST", "/v1/ai/video-upscaler/turbo"), ("POST", "/v1/ai/uploads/request-url")):
            mod.api(method, path, {} if method == "POST" else None)
        self.assertEqual(len(self.fake.calls), 4)

    def test_download_requires_https(self):
        self.offline()
        with self.assertRaises(mod.JobError):
            mod._download("http://cdn.example/result.png", self.tmp / "x.png")

    def test_put_requires_https(self):
        self.offline()
        with self.assertRaises(mod.JobError):
            mod._put_file("http://storage.example/put", {}, self.copy("photo.png"))

    def test_check_reads_404_as_no_jobs(self):
        rc, out, _ = cli("check")
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("key works, no jobs yet"), 4)
        self.fake.list_answer = (404, {"message": "Not found"})
        rc, out, _ = cli("check")
        self.assertNotIn("key works", out)
        self.fake.list_answer = (401, {"message": "Unauthorized"})
        rc, out, _ = cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("refused", out)

    def test_network_error_is_a_clean_failure(self):
        def down(*args, **kwargs):
            raise ConnectionRefusedError("connection refused")

        self.use(down, down, down)
        rc, _, err = cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("Failed: connection refused", err)

    def test_imports_from_a_shallow_folder(self):
        """parents[3] raised IndexError for a copy fewer than four folders deep."""
        namespace = {"__name__": "magnific_shallow", "__file__": "/magnific.py"}
        caught = outcome(lambda: exec(compile(SCRIPT.read_text(), "/magnific.py", "exec"), namespace))
        self.assertIsNone(caught, repr(caught))
        self.assertEqual(namespace["ENV_FILE"], Path("/.env"))

    def test_key_from_bot_env(self):
        env = self.tmp / "bot.env"
        with mock.patch.dict(os.environ, {mod.KEY_NAME: ""}), mock.patch.object(mod, "ENV_FILE", env):
            with self.assertRaises(mod.Refused):
                mod.api_key()
            env.write_text("OTHER=1\nexport MAGNIFIC_API_KEY='abc123'\n")
            self.assertEqual(mod.api_key(), "abc123")


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg and ffprobe")
class RealVideo(Base):
    """The real ffprobe on real files, and the fake video held to it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        f = cls.real = cls.fixtures / "real"
        f.mkdir()
        x264 = ("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=24", "-frames:v", 12, *x264, f / "clip.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=24", "-f", "lavfi", "-i", "sine=frequency=440",
               "-frames:v", 12, *x264, "-c:a", "aac", "-shortest", f / "sound.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24", "-frames:v", 2, *x264, f / "tall.mp4")
        ffmpeg("-i", f / "clip.mp4", "-c", "copy", "-f", "mov", f / "master.mov")

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(mod, "_ffprobe", REAL_FFPROBE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def real_copy(self, name):
        shutil.copy(self.real / name, self.tmp / name)
        return self.tmp / name

    def test_stand_in_reads_like_the_real_thing(self):
        for name, audio in (("clip.mp4", False), ("sound.mp4", True)):
            real = mod.video_info(self.real / name)
            twin = fake_video(self.tmp / f"twin_{name}", 320, 240, 12, "24/1", audio)
            with mock.patch.object(mod, "_ffprobe", fake_ffprobe):
                stand_in = mod.video_info(twin)
            self.assertAlmostEqual(stand_in.pop("duration"), real.pop("duration"), delta=0.1)
            self.assertEqual(stand_in, real, name)

    def test_real_clip_is_priced_by_its_frames(self):
        _, out = self.quote(self.real_copy("clip.mp4"))
        self.assertIn("12 frames at €0.019", out)
        self.assertIn("€0.23", out)

    def test_real_tall_clip_must_enlarge(self):
        tall = self.real_copy("tall.mp4")
        code, _, err = cli("quote", tall, "--resolution", "1k")
        self.assertEqual(code, 2)
        self.assertIn("would not enlarge", err)

    def test_dropped_sound_is_reported(self):
        sound = self.real_copy("sound.mp4")
        self.fake.generated = ["https://cdn.example/up.mp4"]
        self.fake.results["https://cdn.example/up.mp4"] = (self.real / "clip.mp4").read_bytes()
        code, out = self.quote(sound)
        self.assertIn("the source has sound", out)
        rc, out, err = cli("run", code, "-o", self.tmp / "up.mp4")
        self.assertEqual(rc, 0, err)
        self.assertIn("h264 320x240, 12 frames at 24 fps", out)
        self.assertIn("the sound was dropped", out)

    def test_result_named_by_its_bytes(self):
        clip = self.real_copy("clip.mp4")
        self.fake.generated = ["https://cdn.example/out", "https://cdn.example/out2"]
        self.fake.results["https://cdn.example/out"] = (self.real / "master.mov").read_bytes()
        self.fake.results["https://cdn.example/out2"] = clip.read_bytes()
        code, _ = self.quote(clip)
        self.assertEqual(cli("run", code, "-o", self.tmp / "named")[0], 0)
        self.assertEqual(sorted(self.record(code)["outputs"]),
                         [str(self.tmp / "named.mov"), str(self.tmp / "named_2.mp4")])


class _Recorder(BaseHTTPRequestHandler):
    """Local server: records each request, answers by path."""

    def log_message(self, *args):
        pass

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status = 400 if self.path == "/bad" else 200
        payload = self.server.payload if self.path == "/file" else b'{"message": "seen"}'
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = _answer


class Transport(unittest.TestCase):
    """The real transport functions against servers on 127.0.0.1."""

    def server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        srv.seen, srv.payload, srv.redirect_to = [], b"", ""
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    def test_fetch_sends_key_and_returns_errors(self):
        srv, base = self.server()
        status, body = mod._fetch("POST", base + "/bad", {"x-magnific-api-key": "k1", "Content-Type": "application/json"},
                                  b'{"a": 1}')
        self.assertEqual((status, json.loads(body)), (400, {"message": "seen"}))
        method, path, headers, sent = srv.seen[0]
        self.assertEqual((method, path, headers["x-magnific-api-key"], sent), ("POST", "/bad", "k1", b'{"a": 1}'))

    def test_api_redirect_not_followed(self):
        srv, base = self.server()
        other, other_base = self.server()
        srv.redirect_to = other_base + "/stolen"
        status, _ = mod._fetch("GET", base + "/redirect", {"x-magnific-api-key": "k1"})
        self.assertEqual(status, 302)
        self.assertEqual(other.seen, [])

    def test_stream_has_no_key(self):
        srv, base = self.server()
        srv.payload = os.urandom(3 << 20)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "r.bin"
            with mock.patch.dict(os.environ, {mod.KEY_NAME: FAKE_KEY}):
                mod._stream(base + "/file", dest)
            self.assertEqual(dest.read_bytes(), srv.payload)
        self.assertNotIn("x-magnific-api-key", srv.seen[0][2])

    def test_put_stream_sends_file_and_signed_headers(self):
        srv, base = self.server()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "v.mp4"
            path.write_bytes(os.urandom(200_000))
            status = mod._put_stream(base + "/put", {"Content-Type": "video/mp4", "x-goog-content-length-range":
                                                     "0,1073741824", "x-magnific-api-key": "leak"}, path)
            self.assertEqual(status, 200)
            method, _, headers, body = srv.seen[0]
            self.assertEqual((method, body), ("PUT", path.read_bytes()))
        self.assertEqual(headers["content-length"], "200000")
        self.assertEqual(headers["content-type"], "video/mp4")
        self.assertEqual(headers["x-goog-content-length-range"], "0,1073741824")
        self.assertNotIn("x-magnific-api-key", headers)
        self.assertNotIn("transfer-encoding", headers)

    def test_broken_answers_are_network_errors(self):
        """http.client raises these as HTTPException, which no OSError handler
        catches; each transport call must turn them into one."""
        replies = {"garbled": (b"HELLO THERE\r\n\r\n", "BadStatusLine"),
                   "cut chunks": (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n64\r\n" + b"x" * 20,
                                  "IncompleteRead")}
        with tempfile.TemporaryDirectory(prefix="magnific-test-") as d:
            video = Path(d) / "v.mp4"
            video.write_bytes(os.urandom(4096))
            for label, (reply, kind) in replies.items():
                base = raw_server(self, reply)
                calls = {"_fetch": lambda: mod._fetch("GET", base + "/x", {}),
                         "_stream": lambda: mod._stream(base + "/r", Path(d) / "r.bin"),
                         "_put_stream": lambda: mod._put_stream(base + "/p", {}, video)}
                if label == "cut chunks":
                    del calls["_put_stream"]              # an upload reads no answer body
                for name, call in calls.items():
                    caught = outcome(call)
                    self.assertIsInstance(caught, ConnectionError, f"{label} {name}: {caught!r}")
                    self.assertIn(kind, str(caught))

    def test_cut_download_raises(self):
        """A sized read returns what came and says nothing, so a cut result
        used to be saved as the whole file."""
        base = raw_server(self, b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"y" * 13)
        with tempfile.TemporaryDirectory(prefix="magnific-test-") as d:
            caught = outcome(lambda: mod._stream(base + "/r.png", Path(d) / "r.png"))
        self.assertIsInstance(caught, ConnectionError, repr(caught))
        self.assertIn("stopped at 13 of 100 bytes", str(caught))


if __name__ == "__main__":
    unittest.main()
