"""swisstransfer.py, big files as a SwissTransfer download link (2026-10-17).

Nothing here reaches SwissTransfer: a socket guard fails any test that tries
to leave this machine, and the script talks to a local fake of the site that
holds it to the same rules the real one enforces. The fake checks the CSRF
header on every API call, verifies the ALTCHA proof of work for real, refuses
a transfer from an unconfirmed sender, and assembles parts only from ETags
that match what it stored. What is pinned: nothing is sent before a sender is
confirmed, every byte arrives in order and is proved by download, a part that
fails is retried with a fresh address and a dead one marks the transfer failed,
the session never reaches the storage hosts, and the 2 GB line is one number
in both the script and utils/send_to_telegram.py.
"""

import base64
import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
import random
import re
import secrets
import socket
import stat
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "swisstransfer" / "scripts" / "swisstransfer.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load("swisstransfer_under_test", SCRIPT)

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


XSRF_COOKIE_VALUE = "tok%3Dabc"     # the cookie arrives url-encoded
XSRF_HEADER = "tok=abc"             # and the API wants it decoded
CODE = "Q1W2E3"
SIGNING_KEY = b"fake-altcha-key"


def _error(code, attribute=None, top="validation_failed"):
    entry = {"code": code, "description": f"validation.{code}"}
    if attribute:
        entry["context"] = {"attribute": attribute}
    return {"result": "error", "error": {"code": top, "description": "refused", "errors": [entry]}}


class FakeSite:
    """The parts of swisstransfer.com the script uses, with its rules."""

    def __init__(self):
        self.lock = threading.Lock()
        self.port = 0
        self.reset()

    def reset(self):
        self.calls = []                # (method, path)
        self.storage_headers = []      # headers each storage PUT carried
        self.validations = {}
        self.transfers = {}
        self.links = {}
        self.challenges = {}
        self.part_failures = {}        # (file path, chunk) -> failures left
        self.answer_419_once = set()
        self.lapsed = False
        self.page_states = []
        self.tamper = False

    # ── helpers ──
    def page_html(self, component, props):
        state = json.dumps({"component": component, "version": "app:test", "url": "/", "props": props})
        return (f'<html><body><script data-page="app" type="application/json">{state}</script>'
                "</body></html>").encode()

    def altcha_ok(self, payload):
        try:
            got = json.loads(base64.b64decode(payload))
        except (ValueError, TypeError):
            return False
        known = self.challenges.get(got.get("challenge"))
        if not known:
            return False
        salt, number, signature = known
        digest = hashlib.sha256(f"{salt}{got.get('number')}".encode()).hexdigest()
        return (digest == got["challenge"] and got.get("number") == number
                and got.get("signature") == signature and got.get("salt") == salt)

    def handle(self, h, method):
        split = urlsplit(h.path)
        path, h.query = split.path, dict(parse_qsl(split.query))
        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else b""
        with self.lock:
            self.calls.append((method, path))
        if path.startswith("/storage/"):
            return self.storage(h, method, path, body)
        if path.startswith("/blob/"):
            return self.blob(h, path)
        if method == "GET" and path == "/en":
            return h.reply(200, raw=self.page_html("transfer/store", {}), ctype="text/html", headers=[
                ("Set-Cookie", f"SWISSTRANSFER-API-XSRF-TOKEN={XSRF_COOKIE_VALUE}; path=/"),
                ("Set-Cookie", "ST_SESSION=s1; path=/; httponly")])
        if method == "GET" and path.startswith("/dl/"):
            return self.link_page(h, path.rsplit("/", 1)[1])
        if not path.startswith("/api/1/"):
            return h.reply(404, {"message": "not found"})
        if h.headers.get("X-XSRF-TOKEN") != XSRF_HEADER:
            return h.reply(419, {"message": "CSRF token mismatch."})
        if path in self.answer_419_once:
            self.answer_419_once.discard(path)
            return h.reply(419, {"message": "CSRF token mismatch."})
        doc = json.loads(body) if body else {}
        return self.api(h, method, path, doc)

    def api(self, h, method, path, doc):
        parts = path.split("/")[3:]
        if method == "GET" and parts == ["altcha-challenge"]:
            number = random.randint(0, 3000)
            salt = secrets.token_hex(12) + "?expires=9999999999&"
            challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
            signature = hmac.new(SIGNING_KEY, challenge.encode(), hashlib.sha256).hexdigest()
            self.challenges[challenge] = (salt, number, signature)
            return h.reply(200, {"algorithm": "SHA-256", "challenge": challenge, "maxNumber": 5000,
                                 "salt": salt, "signature": signature})
        if method == "POST" and parts == ["email-validations"]:
            vid = str(uuid.uuid4())
            self.validations[vid] = {"email": doc.get("email"), "confirmed": False}
            return h.reply(201, {"result": "success", "data": {"id": vid}})
        if method == "GET" and len(parts) == 2 and parts[0] == "email-validations":
            v = self.validations.get(parts[1])
            if not h.query.get("email"):
                return h.reply(422, _error("validation_rule_required", "email"))
            if not v or v["email"] != h.query["email"]:
                return h.reply(404, _error("not_found", top="not_found"))
            return h.reply(200, {"result": "success",
                                 "data": {"is_validated": v["confirmed"] and not self.lapsed}})
        if method == "POST" and len(parts) == 3 and parts[0] == "email-validations":
            v = self.validations.get(parts[1])
            if not v:
                return h.reply(404, _error("not_found", top="not_found"))
            if parts[2] == "resend":
                return h.reply(200, {"result": "success", "data": {"id": parts[1]}})
            if doc.get("token") != CODE:
                return h.reply(422, {"result": "error", "error": {"code": "invalid_validation_token",
                                                                  "description": "Invalid token"}})
            v["confirmed"] = True
            return h.reply(200, {"result": "success", "data": {"id": parts[1]}})
        if method == "POST" and parts == ["transfers"]:
            return self.create(h, doc)
        if method == "POST" and len(parts) == 3 and parts[0] == "transfers" and parts[2] == "files":
            return self.presign(h, parts[1], doc)
        if method == "PATCH" and len(parts) == 4 and parts[0] == "transfers" and parts[2] == "files":
            return self.complete_file(h, parts[1], parts[3], doc)
        if method == "PATCH" and len(parts) == 2 and parts[0] == "transfers":
            return self.finish(h, parts[1], doc)
        if method == "GET" and len(parts) == 4 and parts[0] == "links" and parts[2] == "files":
            t = self.links.get(parts[1])
            if not t:
                return h.reply(404, _error("not_found", top="not_found"))
            url = f"http://127.0.0.1:{self.port}/blob/{t}/{parts[3]}"
            return h.reply(200, {"result": "success", "data": {"url": url}})
        return h.reply(404, {"message": "no such route"})

    def create(self, h, doc):
        if not self.altcha_ok(doc.get("altcha", "")):
            return h.reply(422, _error("altcha", "altcha"))
        v = self.validations.get(doc.get("email_validation_id"))
        if self.lapsed or not v or not v["confirmed"] or v["email"] != doc.get("email"):
            return h.reply(422, _error("verified_email_check_failed", "email"))
        if doc.get("method") != "link" or doc.get("language") != "en":
            return h.reply(422, _error("invalid", "method"))
        tid = str(uuid.uuid4())
        files = {}
        for f in doc["files"]:
            files[str(uuid.uuid4())] = {"path": f["path"], "size": f["size"], "parts": {}, "data": None}
        self.transfers[tid] = {"files": files, "status": "pending", "doc": doc}
        return h.reply(201, {"result": "success", "data": {
            "id": tid, "files": [{"id": fid, "path": f["path"], "size": f["size"]} for fid, f in files.items()]}})

    def presign(self, h, tid, doc):
        t = self.transfers.get(tid)
        if not t:
            return h.reply(404, _error("transfer_not_found", top="transfer_not_found"))
        if t["status"] != "pending":
            return h.reply(409, _error("transfer_not_uploadable", top="transfer_not_uploadable"))
        out = []
        for ask in doc.get("upload_url_requests") or []:
            chunk = ask.get("chunk")
            url = (f"http://127.0.0.1:{self.port}/storage/{tid}/{ask['file']}/{chunk or 0}"
                   f"?X-Amz-Signature={secrets.token_hex(4)}")
            out.append({"file": ask["file"], "chunk": chunk, "url": url})
        return h.reply(200, {"result": "success", "data": out})

    def storage(self, h, method, path, body):
        self.storage_headers.append({k.lower(): v for k, v in h.headers.items()})
        _, _, tid, fid, chunk = path.split("/")
        f = self.transfers[tid]["files"][fid]
        key = (f["path"], int(chunk))
        with self.lock:
            left = self.part_failures.get(key, 0)
            if left:
                self.part_failures[key] = left - 1
        if left:
            return h.reply(500, raw=b"<Error>InternalError</Error>", ctype="application/xml")
        f["parts"][int(chunk)] = body
        return h.reply(200, raw=b"", ctype="text/plain",
                       headers=[("ETag", f'"{hashlib.md5(body).hexdigest()}"')])

    def complete_file(self, h, tid, fid, doc):
        f = self.transfers[tid]["files"][fid]
        if 0 in f["parts"]:
            if doc != {}:
                return h.reply(422, _error("invalid", "etags"))
            f["data"] = f["parts"][0]
        else:
            etags = doc.get("etags") or []
            want = sorted(f["parts"])
            if [e.get("chunk_index") for e in etags] != want or want != list(range(1, len(want) + 1)):
                return h.reply(422, _error("invalid", "etags"))
            for e in etags:
                if e["etag"] != f'"{hashlib.md5(f["parts"][e["chunk_index"]]).hexdigest()}"':
                    return h.reply(422, _error("format", "etags.0.etag"))
            f["data"] = b"".join(f["parts"][i] for i in want)
        if len(f["data"]) != f["size"]:
            return h.reply(422, _error("size_mismatch", "size"))
        return h.reply(200, {"result": "success", "data": {"id": fid}})

    def finish(self, h, tid, doc):
        t = self.transfers.get(tid)
        if not t:
            return h.reply(404, _error("transfer_not_found", top="transfer_not_found"))
        if doc.get("status") == "failed":
            t["status"] = "failed"
            return h.reply(200, {"result": "success", "data": {"id": tid}})
        if any(f["data"] is None for f in t["files"].values()):
            return h.reply(422, _error("incomplete", "status"))
        t["status"] = "completed"
        link = str(uuid.uuid4())
        self.links[link] = tid
        return h.reply(200, {"result": "success", "data": {
            "id": tid, "link": {"id": link, "download_url": f"https://www.swisstransfer.com/dl/{link}"}}})

    def link_page(self, h, link):
        tid = self.links.get(link)
        if not tid:
            return h.reply(200, raw=self.page_html("errors/transfer/not-found", {}), ctype="text/html")
        component = self.page_states.pop(0) if self.page_states else "link/show"
        t = self.transfers[tid]
        props = {"transfer": {"id": tid, "expires_at": 1792321200, "extension_mail_due_at": None,
                              "files": [{"id": fid, "path": f["path"], "size": f["size"]}
                                        for fid, f in t["files"].items()]}}
        return h.reply(200, raw=self.page_html(component, props), ctype="text/html")

    def blob(self, h, path):
        _, _, tid, fid = path.split("/")
        data = self.transfers[tid]["files"][fid]["data"]
        if self.tamper:
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        return h.reply(200, raw=data, ctype="application/octet-stream")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, payload=None, raw=None, ctype="application/json", headers=()):
        data = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.fake.handle(self, "GET")

    def do_POST(self):
        self.server.fake.handle(self, "POST")

    def do_PATCH(self):
        self.server.fake.handle(self, "PATCH")

    def do_PUT(self):
        self.server.fake.handle(self, "PUT")


class SiteCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeSite()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.fake = cls.fake
        cls.fake.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.fake.reset()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.user_dir = self.tmp / "user"
        self.user_dir.mkdir()
        env = mock.patch.dict(os.environ, {"JARVIS_USER_DIR": str(self.user_dir)})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (("BASE", f"http://127.0.0.1:{self.fake.port}"),
                            ("SECURE", ("https://", "http://127.0.0.1:")),
                            ("PART", 64 * 1024), ("BACKOFF", (0.0, 0.0)), ("SCAN_POLL", 0.01)):
            patch = mock.patch.object(mod, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                mod.main(list(args))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def confirm_sender(self, email="sender@example.com"):
        self.assertEqual(self.cli("sender", email)[0], 0)
        self.assertEqual(self.cli("confirm", CODE)[0], 0)

    def make(self, name, size):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(size))
        return path

    def puts(self):
        return [c for c in self.fake.calls if c[0] == "PUT"]


class SenderTests(SiteCase):
    def test_a_code_is_asked_for_then_confirmed_and_kept_private(self):
        code, out, _ = self.cli("sender", "sender@example.com")
        self.assertEqual(code, 0)
        self.assertIn("six character code", out)
        state = self.user_dir / "swisstransfer"
        sender = json.loads((state / "sender.json").read_text())
        self.assertIsNone(sender["confirmed"])
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((state / "sender.json").stat().st_mode), 0o600)
        code, out, _ = self.cli("confirm", CODE)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads((state / "sender.json").read_text())["confirmed"])
        self.assertIn("confirmed", self.cli("status")[1])

    def test_a_wrong_code_is_refused_and_the_wait_continues(self):
        self.cli("sender", "sender@example.com")
        code, _, err = self.cli("confirm", "ZZZZZZ")
        self.assertEqual(code, 2)
        self.assertIn("did not accept that code", err)
        self.assertIn("waiting for its code", self.cli("status")[1])

    def test_a_malformed_code_or_address_never_leaves(self):
        self.assertEqual(self.cli("sender", "not-an-address")[0], 2)
        self.assertEqual(self.fake.calls, [])
        self.cli("sender", "sender@example.com")
        before = len(self.fake.calls)
        self.assertEqual(self.cli("confirm", "12345")[0], 2)
        self.assertEqual(len(self.fake.calls), before)

    def test_status_asks_whether_the_confirmation_still_holds(self):
        self.confirm_sender()
        self.assertIn("still holds", self.cli("status")[1])
        self.fake.lapsed = True
        out = self.cli("status")[1]
        self.assertIn("no longer accepts", out)
        self.assertIn("waiting for its code", self.cli("status")[1])

    def test_each_person_has_their_own_sender(self):
        self.confirm_sender()
        other = self.tmp / "other"
        other.mkdir()
        with mock.patch.dict(os.environ, {"JARVIS_USER_DIR": str(other)}):
            self.assertIn("none yet", self.cli("status")[1])
            code, _, err = self.cli("upload", str(self.make("x.bin", 10)))
        self.assertEqual(code, 2)
        self.assertIn("no confirmed sender", err)


class UploadTests(SiteCase):
    def test_nothing_is_sent_before_a_sender_is_confirmed(self):
        path = self.make("a.bin", 1000)
        code, _, err = self.cli("upload", str(path))
        self.assertEqual(code, 2)
        self.assertIn("no confirmed sender", err)
        self.assertEqual(self.fake.calls, [])
        self.cli("sender", "sender@example.com")
        calls = len(self.fake.calls)
        code, _, err = self.cli("upload", str(path))
        self.assertEqual(code, 2)
        self.assertIn("waiting for its code", err)
        self.assertEqual(len(self.fake.calls), calls)

    def test_one_part_file_arrives_whole_and_is_recorded(self):
        self.confirm_sender()
        path = self.make("small.bin", 5000)
        code, out, err = self.cli("upload", str(path), "--days", "7", "--downloads", "20",
                                  "--message", "hello")
        self.assertEqual(code, 0, err)
        link = re.search(r"Link: (\S+)", out).group(1)
        self.assertRegex(link, r"^https://www\.swisstransfer\.com/dl/[0-9a-f-]{36}$")
        self.assertIn("The link page says: ready.", out)
        t = next(iter(self.fake.transfers.values()))
        self.assertEqual(t["status"], "completed")
        self.assertEqual(t["doc"]["expires_in_days"], 7)
        self.assertEqual(t["doc"]["max_download"], 20)
        self.assertEqual(t["doc"]["recipients"], [])
        stored = next(iter(t["files"].values()))
        self.assertEqual(stored["data"], path.read_bytes())
        record = json.loads((self.user_dir / "swisstransfer" / "transfers.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["link"], link)
        self.assertEqual(record["files"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(record["expires"], "2026-10-18T11:00:00+00:00")   # the page's own expiry

    def test_a_many_part_file_arrives_in_order_from_quoted_etags(self):
        self.confirm_sender()
        size = mod.PART * 5 + 1234
        path = self.make("big.bin", size)
        code, out, err = self.cli("upload", str(path))
        self.assertEqual(code, 0, err)
        stored = next(iter(next(iter(self.fake.transfers.values()))["files"].values()))
        self.assertEqual(sorted(stored["parts"]), [1, 2, 3, 4, 5, 6])
        self.assertEqual(stored["data"], path.read_bytes())
        self.assertIn("% sent", out)    # a transfer bigger than a part reports progress

    def test_a_folder_keeps_its_structure_and_skips_hidden_files(self):
        self.confirm_sender()
        self.make("deliver/a.bin", 300)
        self.make("deliver/sub/b.bin", 70_000)
        self.make("deliver/.DS_Store", 10)
        code, _, err = self.cli("upload", str(self.tmp / "deliver"))
        self.assertEqual(code, 0, err)
        paths = sorted(f["path"] for f in next(iter(self.fake.transfers.values()))["files"].values())
        self.assertEqual(paths, ["deliver/a.bin", "deliver/sub/b.bin"])

    def test_two_files_under_one_name_are_refused(self):
        self.confirm_sender()
        a = self.make("one/x.bin", 10)
        b = self.make("two/x.bin", 10)
        code, _, err = self.cli("upload", str(a), str(b))
        self.assertEqual(code, 2)
        self.assertIn("two files would arrive as x.bin", err)
        self.assertEqual(self.fake.transfers, {})

    def test_limits_are_refused_before_anything_leaves(self):
        self.confirm_sender()
        path = self.make("a.bin", 100)
        with mock.patch.object(mod, "MAX_TRANSFER", 99):
            self.assertEqual(self.cli("upload", str(path))[0], 2)
        with mock.patch.object(mod, "MAX_FILES", 1):
            self.assertEqual(self.cli("upload", str(path), str(self.make("b.bin", 1)))[0], 2)
        self.assertEqual(self.cli("upload", str(self.tmp / "missing.bin"))[0], 2)
        self.assertEqual(self.fake.transfers, {})

    def test_a_failing_part_is_retried_with_a_fresh_address(self):
        self.confirm_sender()
        path = self.make("retry.bin", mod.PART * 3)
        self.fake.part_failures[("retry.bin", 2)] = 2
        code, _, err = self.cli("upload", str(path))
        self.assertEqual(code, 0, err)
        stored = next(iter(next(iter(self.fake.transfers.values()))["files"].values()))
        self.assertEqual(stored["data"], path.read_bytes())
        self.assertEqual(len(self.puts()), 5)   # three parts plus two failed tries

    def test_a_part_that_never_goes_marks_the_transfer_failed(self):
        self.confirm_sender()
        path = self.make("dead.bin", mod.PART * 2)
        self.fake.part_failures[("dead.bin", 1)] = 99
        code, _, err = self.cli("upload", str(path))
        self.assertEqual(code, 1)
        self.assertIn("failed 4 times", err)
        self.assertEqual(next(iter(self.fake.transfers.values()))["status"], "failed")
        self.assertFalse((self.user_dir / "swisstransfer" / "transfers.jsonl").exists())

    def test_a_lapsed_confirmation_is_refused_before_any_part(self):
        self.confirm_sender()
        self.fake.lapsed = True
        code, _, err = self.cli("upload", str(self.make("a.bin", 100)))
        self.assertEqual(code, 2)
        self.assertIn("no longer accepts the confirmation", err)
        self.assertEqual(self.puts(), [])
        sender = json.loads((self.user_dir / "swisstransfer" / "sender.json").read_text())
        self.assertIsNone(sender["confirmed"])

    def test_a_stale_csrf_token_is_renewed_once(self):
        self.confirm_sender()
        self.fake.answer_419_once.add("/api/1/transfers")
        code, _, err = self.cli("upload", str(self.make("a.bin", 100)))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.calls.count(("POST", "/api/1/transfers")), 2)

    def test_the_session_never_reaches_storage(self):
        self.confirm_sender()
        self.cli("upload", str(self.make("a.bin", mod.PART * 2 + 1)))
        self.assertEqual(len(self.fake.storage_headers), 3)
        for headers in self.fake.storage_headers:
            self.assertNotIn("cookie", headers)
            self.assertNotIn("x-xsrf-token", headers)
            self.assertEqual(headers.get("content-type"), "application/octet-stream")

    def test_a_storage_address_that_is_not_secure_is_refused(self):
        self.confirm_sender()
        with mock.patch.object(mod, "SECURE", ("https://",)):
            code, _, err = self.cli("upload", str(self.make("a.bin", 100)))
        self.assertEqual(code, 1)
        self.assertIn("not https", err)
        self.assertEqual(self.puts(), [])
        self.assertEqual(next(iter(self.fake.transfers.values()))["status"], "failed")


class AltchaTests(unittest.TestCase):
    def test_the_answer_is_what_the_widget_sends(self):
        salt, number = "abc?expires=1&", 4242
        challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
        payload = json.loads(base64.b64decode(mod.solve_altcha(
            {"algorithm": "SHA-256", "challenge": challenge, "maxNumber": 10000, "salt": salt,
             "signature": "sig"})))
        self.assertEqual(payload["number"], number)
        self.assertEqual({k: payload[k] for k in ("algorithm", "challenge", "salt", "signature")},
                         {"algorithm": "SHA-256", "challenge": challenge, "salt": salt, "signature": "sig"})

    def test_a_human_check_is_never_answered(self):
        with self.assertRaisesRegex(mod.Refused, "human check"):
            mod.solve_altcha({"algorithm": "PBKDF2", "codeChallenge": {"image": "data:..."}})
        with self.assertRaisesRegex(mod.Refused, "changed shape"):
            mod.solve_altcha({"algorithm": "SHA-512", "challenge": "00"})

    def test_no_answer_in_range_is_refused(self):
        with self.assertRaisesRegex(mod.Refused, "no answer"):
            mod.solve_altcha({"algorithm": "SHA-256", "challenge": "00" * 32, "maxNumber": 50,
                              "salt": "s", "signature": "x"})


class FenceTests(unittest.TestCase):
    def test_only_the_known_calls_can_leave(self):
        site = mod.Site()
        for method, path in (("DELETE", "/api/1/transfers/abcdefgh"), ("GET", "/api/1/auth"),
                             ("POST", "/api/1/transfers/../x/files"), ("GET", "/dl/not-a-uuid")):
            with self.subTest(method=method, path=path), self.assertRaises(mod.Refused):
                site.api(method, path)


class ProgressTests(unittest.TestCase):
    def test_a_failed_try_takes_its_bytes_back(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"x" * 1000)
            f.flush()
            progress = mod._Progress(1000)
            part = mod._Part(f.name, 100, 500, progress)
            while part.read(64):
                pass
            self.assertEqual(progress.sent, 500)
            part.undo()
            part.close()
            self.assertEqual(progress.sent, 0)


class CheckTests(SiteCase):
    def upload_one(self, size=70_000):
        self.confirm_sender()
        path = self.make("deliver.bin", size)
        code, out, err = self.cli("upload", str(path))
        self.assertEqual(code, 0, err)
        return re.search(r"Link: (\S+)", out).group(1)

    def test_the_scan_is_waited_out_and_names_and_sizes_compared(self):
        link = self.upload_one()
        self.fake.page_states = ["errors/transfer/waiting-for-antivirus"] * 2
        code, out, _ = self.cli("check", link, "--wait", "5")
        self.assertEqual(code, 0)
        self.assertIn(": ready", out)
        self.assertIn("Names and sizes match", out)

    def test_a_download_proves_the_bytes(self):
        link = self.upload_one()
        back = self.tmp / "back"
        code, out, err = self.cli("check", link, "--download", str(back))
        self.assertEqual(code, 0, err)
        self.assertIn("identical to what was sent", out)
        self.assertEqual((back / "deliver.bin").read_bytes(), (self.tmp / "deliver.bin").read_bytes())

    def test_a_folder_comes_back_with_its_structure(self):
        self.confirm_sender()
        self.make("deliver/a.bin", 300)
        self.make("deliver/sub/a.bin", mod.PART + 5)
        code, out, err = self.cli("upload", str(self.tmp / "deliver"))
        self.assertEqual(code, 0, err)
        link = re.search(r"Link: (\S+)", out).group(1)
        back = self.tmp / "back"
        code, out, err = self.cli("check", link, "--download", str(back))
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("identical to what was sent"), 2)
        self.assertEqual((back / "deliver" / "sub" / "a.bin").read_bytes(),
                         (self.tmp / "deliver" / "sub" / "a.bin").read_bytes())

    def test_a_name_cannot_climb_out_of_the_download_folder(self):
        link = self.upload_one()
        stored = next(iter(next(iter(self.fake.transfers.values()))["files"].values()))
        stored["path"] = "../../escape.bin"
        back = self.tmp / "nest" / "back"
        self.cli("check", link, "--download", str(back))
        self.assertTrue((back / "escape.bin").exists())
        self.assertFalse((self.tmp / "escape.bin").exists())

    def test_a_changed_file_is_caught(self):
        link = self.upload_one()
        self.fake.tamper = True
        code, out, err = self.cli("check", link, "--download", str(self.tmp / "back"))
        self.assertEqual(code, 1)
        self.assertIn("DIFFERENT", out)

    def test_an_unknown_link_reads_as_gone(self):
        code, out, _ = self.cli("check", f"https://www.swisstransfer.com/dl/{uuid.uuid4()}")
        self.assertEqual(code, 0)
        self.assertIn("not found", out)

    def test_only_a_swisstransfer_link_is_read(self):
        for link in ("https://evil.example/dl/" + str(uuid.uuid4()), "http://www.swisstransfer.com/dl/x"):
            with self.subTest(link=link):
                self.assertEqual(self.cli("check", link)[0], 2)
        self.assertEqual(self.fake.calls, [])


class RuleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def sparse(self, name, size):
        path = self.tmp / name
        with open(path, "wb") as f:
            f.truncate(size)        # no disk is used
        return path

    def run_rule(self, *paths):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            mod.main(["rule", *map(str, paths)])
        return caught.exception.code, out.getvalue()

    def test_over_two_gb_goes_as_a_link(self):
        code, out = self.run_rule(self.sparse("big.mov", mod.LINK_RULE_BYTES + 1),
                                  self.sparse("small.mov", 10))
        self.assertEqual(code, 0)
        self.assertIn("big.mov: 2.00 GB, over 2 GB: send it as a link", out)

    def test_exactly_two_gb_stays_on_telegram(self):
        code, out = self.run_rule(self.sparse("edge.mov", mod.LINK_RULE_BYTES))
        self.assertEqual(code, 1)
        self.assertIn("Telegram", out)


class TelegramSenderRuleTests(unittest.TestCase):
    """utils/send_to_telegram.py refuses a file over the line and points here."""

    @classmethod
    def setUpClass(cls):
        cls.send = _load("send_for_swisstransfer_rule", ROOT / "utils" / "send_to_telegram.py")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def sparse(self, size):
        path = self.tmp / "master.mov"
        with open(path, "wb") as f:
            f.truncate(size)
        return path

    def test_the_line_is_one_number(self):
        self.assertEqual(self.send.LINK_RULE_BYTES, mod.LINK_RULE_BYTES)

    def test_a_file_over_the_line_never_reaches_telegram(self):
        path = self.sparse(self.send.LINK_RULE_BYTES + 1)
        env = {k: v for k, v in os.environ.items() if k != "JARVIS_USER_ID"}
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.send, "get_token", return_value="t"), \
                mock.patch.object(self.send.httpx, "post", side_effect=AssertionError("sent")), \
                mock.patch("sys.argv", ["send", "--user", "1", "--document", str(path)]), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as caught:
            self.send.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("skills/swisstransfer", err.getvalue())

    def test_a_file_at_the_line_still_goes(self):
        self.assertFalse(self.send.over_link_rule(self.sparse(self.send.LINK_RULE_BYTES)))
        self.assertFalse(self.send.over_link_rule(self.tmp / "missing.mov"))


if __name__ == "__main__":
    unittest.main()
