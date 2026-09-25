"""Rive's web runtime, driven from Python: fetch it, serve it, run it headless.

The runtime (@rive-app/webgl2 by default, MIT) is pinned in web_runtime.json
and fetched straight from the npm registry into a user cache, checked
against its integrity hash, never installed into the repo. Chromium comes
from Playwright, which the browser, media and logo-animate skills already
use. WebGL2 runs on SwiftShader in headless Chromium, so no GPU is needed
and the same code runs on the Linux side.

Measured 25 Sep 2026 (webgl2 2.43.1, Chromium from Playwright, this Mac):
a 1280x720 scene rendered ~49 frames per second in one page; interiors
matched the CLI capture, edges differed by anti-aliasing only (mean 0.23
codes, 0.44% of pixels over 8 codes). The CLI stays the reference look.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import os
import platform
import shutil
import socketserver
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from rivelib import RiveError, SKILL_DIR

SCRIPTS = SKILL_DIR / "scripts"
PINS = json.loads((SCRIPTS / "web_runtime.json").read_text(encoding="utf-8"))
ENGINE_PAGE = SCRIPTS / "web" / "engine.html"

# SwiftShader: software WebGL2, identical on a GPU-less server.
CHROMIUM_ARGS = ["--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--use-angle=swiftshader"]


def cache_dir() -> Path:
    override = os.environ.get("RIVE_SKILL_CACHE")
    if override:
        return Path(override)
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "rive-skill"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "rive-skill"


def _verify_integrity(blob: bytes, integrity: str) -> bool:
    algo, _, expected = integrity.partition("-")
    if algo not in ("sha512", "sha384", "sha256"):
        return False
    actual = base64.b64encode(hashlib.new(algo, blob).digest()).decode()
    return actual == expected


def ensure_runtime(flavour: str | None = None, offline_ok: bool = True) -> Path:
    """Return a folder holding rive.js and rive.wasm for the pinned version."""
    flavour = flavour or PINS["default"]
    try:
        pin = PINS["packages"][flavour]
    except KeyError as exc:
        raise RiveError(f"unknown web runtime {flavour!r}; pinned: {', '.join(PINS['packages'])}") from exc
    dest = cache_dir() / "runtime" / f"{flavour}-{pin['version']}"
    marker = dest / ".verified"
    if marker.is_file() and all((dest / f).is_file() for f in ("rive.js", "rive.wasm")):
        return dest
    try:
        with urllib.request.urlopen(pin["tarball"], timeout=60) as resp:
            blob = resp.read()
    except OSError as exc:
        raise RiveError(f"could not download {pin['npm']}@{pin['version']}: {exc}",
                        "the web engine needs network once; after that it runs from the cache") from exc
    if not _verify_integrity(blob, pin["integrity"]):
        raise RiveError(f"{pin['npm']}@{pin['version']} failed its integrity check; refusing to use it")
    tmp = Path(tempfile.mkdtemp(prefix="rive_skill_runtime_"))
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            for name in pin["files"] + ["package.json"]:
                try:
                    member = tar.getmember(f"package/{name}")
                except KeyError:
                    continue
                data = tar.extractfile(member)
                if data is not None:
                    (tmp / name).write_bytes(data.read())
        if not (tmp / "rive.js").is_file() or not (tmp / "rive.wasm").is_file():
            raise RiveError(f"{pin['npm']} tarball has no rive.js/rive.wasm")
        (tmp / ".verified").write_text(pin["integrity"] + "\n", encoding="utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(tmp), str(dest))
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return dest


def runtime_version(flavour: str | None = None) -> str:
    flavour = flavour or PINS["default"]
    pin = PINS["packages"][flavour]
    return f"{pin['npm']}@{pin['version']}"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silence the access log
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WebSession:
    """A served folder plus a headless page running engine.html.

    Use as a context manager. The folder is a private mkdtemp copy that
    holds the runtime, the engine page and the .riv; it is removed on exit
    by that exact path.
    """

    def __init__(self, riv: str | Path, flavour: str | None = None, width: int = 16, height: int = 16):
        self.riv = Path(riv).resolve()
        if not self.riv.is_file():
            raise RiveError(f"{self.riv} does not exist")
        self.flavour = flavour or PINS["default"]
        self.viewport = (max(16, int(width)), max(16, int(height)))
        self.console: list[str] = []
        self._tmp = None
        self._server = None
        self._pw = None
        self._browser = None
        self.page = None
        self.renderer_string = None

    def __enter__(self) -> "WebSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RiveError("the web engine needs Playwright",
                            "pip install playwright && python -m playwright install chromium") from exc
        runtime = ensure_runtime(self.flavour)
        try:
            self._start(sync_playwright, runtime)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _start(self, sync_playwright, runtime: Path) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="rive_skill_web_"))
        for name in ("rive.js", "rive.wasm", "rive_fallback.wasm"):
            if (runtime / name).is_file():
                shutil.copy2(runtime / name, self._tmp / name)
        shutil.copy2(ENGINE_PAGE, self._tmp / "engine.html")
        shutil.copy2(self.riv, self._tmp / "file.riv")
        directory = str(self._tmp)

        class Handler(_QuietHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=directory, **kw)

        self._server = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        port = self._server.server_address[1]
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(args=CHROMIUM_ARGS)
        except Exception as exc:  # playwright raises its own Error type
            raise RiveError(f"could not start headless Chromium: {exc}",
                            "python -m playwright install chromium") from exc
        context = self._browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]},
                                            device_scale_factor=1)
        self.page = context.new_page()
        self.page.on("console", lambda m: self.console.append(m.text))
        self.page.goto(f"http://127.0.0.1:{port}/engine.html")
        self.renderer_string = self.page.evaluate(
            "(()=>{const g=document.createElement('canvas').getContext('webgl2');"
            "if(!g)return 'no webgl2';const d=g.getExtension('WEBGL_debug_renderer_info');"
            "return d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):'webgl2';})()")
        booted = self.page.evaluate("cfg => RS.boot(cfg)", {"riv": "file.riv", "wasm": "rive.wasm"})
        if not booted.get("ok"):
            error = booted.get("error", "unknown error")
            raise RiveError(f"the web runtime could not load {self.riv.name}: {error}")

    def __exit__(self, *exc):
        for closer in (lambda: self._browser and self._browser.close(),
                       lambda: self._pw and self._pw.stop(),
                       lambda: self._server and self._server.shutdown(),
                       lambda: self._server and self._server.server_close()):
            try:
                closer()
            except Exception:
                pass
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._browser = self._pw = self._server = self._tmp = None
        return False

    # ------------------------------------------------------------------ calls

    def info(self) -> dict:
        return self.page.evaluate("() => RS.info()")

    def setup(self, *, width: int, height: int, artboard: str | None = None,
              state_machine: str | None = None, fit: str = "contain", data: dict | None = None) -> dict:
        self.page.set_viewport_size({"width": max(16, width), "height": max(16, height)})
        result = self.page.evaluate("cfg => RS.setup(cfg)", {
            "width": int(width), "height": int(height), "artboard": artboard,
            "stateMachine": state_machine, "fit": fit, "data": data or {}})
        if not result.get("ok"):
            raise RiveError(f"web engine setup failed: {result.get('error')}")
        return result

    def frame_png(self, t: float, steps: list[dict] | None = None, data: dict | None = None) -> tuple[bytes, dict]:
        result = self.page.evaluate("a => RS.frameAt(a.t, a.steps, a.data)",
                                    {"t": float(t), "steps": steps or [], "data": data})
        if not result.get("ok"):
            raise RiveError(f"web engine frame at {t:.4f}s failed: {result.get('error')}")
        png = base64.b64decode(result.pop("png").split(",", 1)[1])
        return png, result
