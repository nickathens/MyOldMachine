#!/usr/bin/env python3
"""Put a .riv on the web: build a page, prove it plays, describe a file.

  page    a folder (index.html, rive.js, rive.wasm, the .riv) or one
          self-contained .html file, using Rive's web runtime
          (@rive-app/webgl2 by default: the Rive Renderer, vector feathering
          included). --controls adds a panel that edits the file's view
          model live, which is the quickest way to show a client what the
          data does.
  verify  open a page (or a .riv) in headless Chromium the way a visitor
          would, with the real clock: console errors, whether it loaded,
          whether it drew, and optionally whether a click did anything.
  info    what is inside any .riv: artboards and sizes, timelines, state
          machines (and legacy inputs), view models, enums, assets.

Web runtimes reject unsigned scripts. A file whose scripts were built with
`rive <dir> --once` loads but its scripted parts do nothing on a page; build
it with `rive <dir> --publish` (needs `rive login`). Files without scripts
are fine unsigned.

  rive_web.py page build/intro.riv -o site/ --title "Studio" --state-machine Main
  rive_web.py page build/card.riv -o card.html --single-file --controls
  rive_web.py verify site/index.html --click 560,560
  rive_web.py info build/intro.riv
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rivelib as L  # noqa: E402
import riveweb as W  # noqa: E402

FITS = {"contain": "Contain", "cover": "Cover", "fill": "Fill", "fit-width": "FitWidth",
        "fit-height": "FitHeight", "none": "None", "scale-down": "ScaleDown", "layout": "Layout"}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  html, body {{ margin: 0; height: 100%; background: {background}; }}
  body {{ display: flex; align-items: center; justify-content: center; }}
  #stage {{ {canvas_css} display: block; }}
  #controls {{ position: fixed; top: 12px; right: 12px; max-width: 320px; font: 13px/1.4 system-ui, sans-serif;
              color: #eee; background: rgba(20,20,20,.86); border-radius: 10px; padding: 10px 12px; }}
  #controls label {{ display: block; margin: 6px 0 2px; opacity: .8; }}
  #controls input[type=text], #controls select {{ width: 100%; box-sizing: border-box; }}
  #controls button {{ margin-top: 6px; }}
</style>
</head>
<body>
<canvas id="stage" aria-label="{title}" role="img"></canvas>
{controls_div}
{runtime_tag}
<script>
"use strict";
const CONFIG = {config};
// Read the file before the player starts. Named no state machine, rive.js
// 2.43.1 plays the first timeline, so listeners and binds are dead; it cannot
// see the artboard's default one, so the first is used, with a note when
// there are several. Bind only a file that has a view model: autoBind on one
// without logs a console error.
async function boot(source) {{
  try {{
    const file = new rive.RiveFile(source);
    await file.init();
    const f = file.getInstance();
    let sm = CONFIG.stateMachine;
    const board = CONFIG.artboard ? f.artboardByName(CONFIG.artboard) : f.defaultArtboard();
    if (board) {{
      const names = [];
      for (let i = 0; i < board.stateMachineCount(); i++) names.push(board.stateMachineByIndex(i).name);
      if (!sm && names.length) {{
        sm = names[0];
        if (names.length > 1) console.warn("rive: " + names.length + " state machines, playing " + sm +
                                           "; choose one with --state-machine");
      }}
      if (board.delete) board.delete();
    }}
    start({{ riveFile: file, stateMachine: sm || undefined, autoBind: f.viewModelCount() > 0 }});
  }} catch (e) {{
    window.__rive = {{ loaded: false, error: String(e && e.message || e) }};
  }}
}}
{loader}
function start(options) {{
  const canvas = document.getElementById("stage");
  const layout = new rive.Layout({{ fit: rive.Fit[CONFIG.fit], alignment: rive.Alignment.Center }});
  const r = new rive.Rive(Object.assign({{
    canvas, layout, autoplay: true,
    artboard: CONFIG.artboard || undefined,
    onLoad: () => {{
      r.resizeDrawingSurfaceToCanvas();
      const vmi = r.viewModelInstance;
      for (const [path, value] of Object.entries(CONFIG.data)) setValue(vmi, path, value);
      const b = r.bounds;
      window.__rive = {{ loaded: true, contents: r.contents, stateMachines: r.stateMachineNames,
                         artboard: {{ width: b.maxX - b.minX, height: b.maxY - b.minY }} }};
      if (CONFIG.controls) buildControls(r);
    }},
    onLoadError: (e) => {{ window.__rive = {{ loaded: false, error: String(e && e.data || e) }}; }},
  }}, options));
  window.addEventListener("resize", () => r.resizeDrawingSurfaceToCanvas());
  window.__r = r;
}}
function setValue(vmi, path, value) {{
  if (!vmi) return false;
  const tries = [["number", p => p.value = Number(value)], ["string", p => p.value = String(value)],
    ["boolean", p => p.value = (value === true || value === "true" || value === "1")],
    ["color", p => {{ let h = String(value).replace(/^#/, ""); if (h.length === 6) h = "FF" + h; p.value = parseInt(h, 16) | 0; }}],
    ["enum", p => p.value = String(value)], ["trigger", p => p.trigger()]];
  for (const [kind, apply] of tries) {{
    let prop = null; try {{ prop = vmi[kind](path); }} catch (e) {{ prop = null; }}
    if (prop) {{ apply(prop); return true; }}
  }}
  console.warn("no view model property " + path);
  return false;
}}
function buildControls(r) {{
  const box = document.getElementById("controls"), vmi = r.viewModelInstance;
  if (!box || !vmi) return;
  for (const p of vmi.properties) {{
    const row = document.createElement("div"), label = document.createElement("label");
    label.textContent = p.name; row.appendChild(label);
    let input = null;
    if (p.type === "string") {{ input = document.createElement("input"); input.type = "text"; input.value = vmi.string(p.name).value;
      input.oninput = () => vmi.string(p.name).value = input.value;
      vmi.string(p.name).on(() => {{ if (document.activeElement !== input) input.value = vmi.string(p.name).value; }}); }}
    else if (p.type === "number") {{ input = document.createElement("input"); input.type = "range"; input.min = 0; input.max = 100; input.step = 0.1;
      input.value = vmi.number(p.name).value; input.oninput = () => vmi.number(p.name).value = Number(input.value);
      vmi.number(p.name).on(() => input.value = vmi.number(p.name).value); }}
    else if (p.type === "boolean") {{ input = document.createElement("input"); input.type = "checkbox"; input.checked = vmi.boolean(p.name).value;
      input.onchange = () => vmi.boolean(p.name).value = input.checked;
      vmi.boolean(p.name).on(() => input.checked = vmi.boolean(p.name).value); }}
    else if (p.type === "color") {{ input = document.createElement("input"); input.type = "color";
      input.oninput = () => vmi.color(p.name).value = parseInt("FF" + input.value.slice(1), 16) | 0; }}
    else if (p.type === "trigger") {{ input = document.createElement("button"); input.textContent = "fire";
      input.onclick = () => vmi.trigger(p.name).trigger(); }}
    else if (p.type === "enumType") {{ input = document.createElement("select"); const e = vmi.enum(p.name);
      for (const v of e.values) {{ const o = document.createElement("option"); o.textContent = v; input.appendChild(o); }}
      input.value = e.value; input.onchange = () => e.value = input.value; }}
    if (input) {{ row.appendChild(input); box.appendChild(row); }}
  }}
}}
</script>
</body>
</html>
"""

FOLDER_LOADER = """rive.RuntimeLoader.setWasmUrl("rive.wasm");
if (rive.RuntimeLoader.setWasmFallbackUrl) rive.RuntimeLoader.setWasmFallbackUrl(null);
boot({ src: CONFIG.src });"""

INLINE_LOADER = """function b64(s) { const b = atob(s), u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u.buffer; }
rive.RuntimeLoader.setWasmBinary(b64(document.getElementById("wasm").textContent.trim()));
if (rive.RuntimeLoader.setWasmFallbackUrl) rive.RuntimeLoader.setWasmFallbackUrl(null);
boot({ buffer: b64(document.getElementById("riv").textContent.trim()) });"""


def build_page(riv: Path, out: Path, *, title: str, artboard=None, state_machine=None, fit="contain",
               background="#000000", size=None, data=None, controls=False, single=False,
               flavour=None) -> Path:
    runtime = W.ensure_runtime(flavour)
    if fit not in FITS:
        raise L.RiveError(f"fit {fit!r}: one of {', '.join(FITS)}")
    if size:
        w, h = size
        canvas_css = f"width: min(100vw, {w}px); aspect-ratio: {w} / {h};"
    else:
        canvas_css = "width: 100vw; height: 100vh;"
    if background != "transparent" and not re.fullmatch(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})",
                                                         background):
        raise L.RiveError(f"background {background!r}: #RRGGBB, #RRGGBBAA or transparent")
    config = {"src": riv.name, "artboard": artboard, "stateMachine": state_machine, "fit": FITS[fit],
              "data": data or {}, "controls": bool(controls)}
    fields = dict(title=html.escape(title), background=background,
                  canvas_css=canvas_css, controls_div='<div id="controls"></div>' if controls else "",
                  config=json.dumps(config).replace("<", "\\u003c"))
    if single:
        js = (runtime / "rive.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
        wasm = base64.b64encode((runtime / "rive.wasm").read_bytes()).decode()
        riv_b64 = base64.b64encode(riv.read_bytes()).decode()
        tag = (f"<script>{js}</script>\n<script id=\"wasm\" type=\"application/octet-stream\">{wasm}</script>\n"
               f"<script id=\"riv\" type=\"application/octet-stream\">{riv_b64}</script>")
        page = PAGE.format(runtime_tag=tag, loader=INLINE_LOADER, **fields)
        target = out if out.suffix == ".html" else out / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        return target
    out.mkdir(parents=True, exist_ok=True)
    for name in ("rive.js", "rive.wasm"):
        shutil.copy2(runtime / name, out / name)
    shutil.copy2(riv, out / riv.name)
    page = PAGE.format(runtime_tag='<script src="rive.js"></script>', loader=FOLDER_LOADER, **fields)
    (out / "index.html").write_text(page, encoding="utf-8")
    return out / "index.html"


def verify_page(page: Path, *, click=None, wait: float = 1.5, width: int = 1280, height: int = 720,
                shots: Path | None = None) -> dict:
    """Load a built page with the real clock, like a visitor, and report."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise L.RiveError("verify needs Playwright", "pip install playwright && python -m playwright install chromium") from exc
    import http.server
    import threading
    folder = page.parent
    report = {"page": str(page), "console_errors": [], "console": []}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(folder), **kw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    shots = shots or Path(tempfile.mkdtemp(prefix="rive_skill_verify_"))
    shots.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=W.CHROMIUM_ARGS)
            ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
            pg = ctx.new_page()
            pg.on("console", lambda m: (report["console_errors"] if m.type == "error" else report["console"])
                  .append(m.text))
            pg.on("pageerror", lambda e: report["console_errors"].append(str(e)))
            pg.goto(f"http://127.0.0.1:{server.server_address[1]}/{page.name}")
            deadline = time.time() + 30
            state = None
            while time.time() < deadline:
                state = pg.evaluate("window.__rive || null")
                if state:
                    break
                time.sleep(0.1)
            report["loaded"] = bool(state and state.get("loaded"))
            report["load_error"] = (state or {}).get("error")
            report["state_machines"] = (state or {}).get("stateMachines")
            time.sleep(wait)
            before = shots / "page.png"
            pg.screenshot(path=str(before))
            report["screenshot"] = str(before)
            report["blank"] = L.blank_reason(before)
            if click:
                # The real clock runs between screenshots, so a page that moves
                # by itself would credit any click (measured: Rive's spinning
                # rml_triangle, clicked on an empty corner). Look twice first.
                time.sleep(wait)
                idle = shots / "page_idle.png"
                pg.screenshot(path=str(idle))
                moving = L.sha256_file(before) != L.sha256_file(idle)
                box = pg.evaluate("(()=>{const r=document.getElementById('stage').getBoundingClientRect();"
                                  "return [r.left,r.top,r.width,r.height];})()")
                # map artboard coordinates through the page's centred contain fit
                board = (state or {}).get("artboard") or {}
                aw, ah = board.get("width"), board.get("height")
                x, y = click
                if aw and ah:
                    s = min(box[2] / aw, box[3] / ah)
                    ox, oy = box[0] + (box[2] - aw * s) / 2, box[1] + (box[3] - ah * s) / 2
                    px, py = ox + x * s, oy + y * s
                else:
                    px, py = box[0] + x, box[1] + y
                pg.mouse.click(px, py)
                time.sleep(wait)
                after = shots / "page_clicked.png"
                pg.screenshot(path=str(after))
                report["clicked_screenshot"] = str(after)
                if moving:
                    report["click_changed_picture"] = None
                    report["click_note"] = ("the page changes on its own between two screenshots, so before and "
                                            "after cannot show what the click did; check the control on the "
                                            "project with rive_check.py --interaction")
                else:
                    report["click_changed_picture"] = L.sha256_file(idle) != L.sha256_file(after)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    unsigned = [m for m in report["console_errors"] + report["console"] if "sign" in m.lower()]
    if unsigned:
        report["unsigned_scripts"] = unsigned
    report["ok"] = report["loaded"] and not report["blank"] and not report["console_errors"]
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("page", help="build a web page for a .riv")
    p.add_argument("riv")
    p.add_argument("-o", "--output", required=True, help="a folder, or a .html file with --single-file")
    p.add_argument("--title", default="Rive")
    p.add_argument("--artboard")
    p.add_argument("--state-machine")
    p.add_argument("--fit", default="contain", choices=sorted(FITS))
    p.add_argument("--background", default="#000000", help="page background: #RRGGBB or transparent")
    p.add_argument("--size", help="WIDTHxHEIGHT of the canvas (default: fill the window)")
    p.add_argument("--data", action="append", help="PATH=VALUE set after load")
    p.add_argument("--controls", action="store_true", help="add a live view model panel")
    p.add_argument("--single-file", action="store_true", help="inline the runtime and the .riv")
    p.add_argument("--runtime", choices=["webgl2", "canvas"])
    p.add_argument("--verify", action="store_true", help="verify the page after building it")
    v = sub.add_parser("verify", help="load a page or a .riv headless and report")
    v.add_argument("target")
    v.add_argument("--click", help="X,Y in artboard coordinates")
    v.add_argument("--wait", type=float, default=1.5)
    v.add_argument("--state-machine")
    v.add_argument("--shots", help="folder for the screenshots")
    i = sub.add_parser("info", help="list what a .riv contains")
    i.add_argument("riv")
    i.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "page":
            data = dict(d.split("=", 1) for d in (args.data or []))
            size = tuple(int(x) for x in args.size.lower().split("x")) if args.size else None
            page = build_page(Path(args.riv).resolve(), Path(args.output), title=args.title,
                              artboard=args.artboard, state_machine=args.state_machine, fit=args.fit,
                              background=args.background, size=size, data=data, controls=args.controls,
                              single=args.single_file, flavour=args.runtime)
            print(f"wrote {page} ({W.runtime_version(args.runtime)})")
            if args.verify:
                rep = verify_page(page)
                print(json.dumps(rep, indent=2))
                return 0 if rep["ok"] else 1
            return 0
        if args.cmd == "verify":
            target = Path(args.target).resolve()
            tmp = None
            if target.suffix == ".riv":
                tmp = Path(tempfile.mkdtemp(prefix="rive_skill_page_"))
                target = build_page(target, tmp, title=target.stem, state_machine=args.state_machine)
            click = tuple(float(c) for c in args.click.split(",")) if args.click else None
            try:
                rep = verify_page(target, click=click, wait=args.wait,
                                  shots=Path(args.shots) if args.shots else None)
            finally:
                L.remove_tree(tmp)
            print(json.dumps(rep, indent=2))
            return 0 if rep["ok"] else 1
        with W.WebSession(Path(args.riv)) as session:
            info = session.info()
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"default artboard: {info.get('defaultArtboard')}")
            for b in info["artboards"]:
                print(f"artboard {b['name']} {b['width']:g}x{b['height']:g}")
                for a in b["animations"]:
                    print(f"  timeline {a['name']} ({a['duration']} frames at {a['fps']} fps)")
                for sm in b["stateMachines"]:
                    extra = f", legacy inputs {sm['legacyInputs']}" if sm["legacyInputs"] else ""
                    print(f"  state machine {sm['name']}{extra}")
            for vm in info["viewModels"]:
                props = ", ".join(f"{p['name']}:{p['type']}" for p in vm["properties"])
                print(f"view model {vm['name']}: {props}")
            for e in info["enums"]:
                print(f"enum {e['name']}: {', '.join(e['values'])}")
            for a in info["assets"]:
                where = "embedded" if a["embedded"] else ("CDN" if a["cdn"] else "referenced (host supplies it)")
                print(f"asset {a['name']}.{a['ext']} {where}")
        return 0
    except L.RiveError as exc:
        print(f"rive_web: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
