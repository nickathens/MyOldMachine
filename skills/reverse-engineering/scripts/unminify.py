#!/usr/bin/env python3
"""
Make shipped JavaScript readable again: recover the original sources from a
source map when there is one, otherwise decompile the minified code.

Usage:
  # the author's original files, from a source map (exact, the best result):
  python unminify.py --url https://site.com/app.min.js --out-dir ./recovered
  python unminify.py --file app.min.js --out-dir ./recovered

  # no map: decompile one file to readable code
  python unminify.py --file chunk.js --out-file chunk.readable.js
  # split a webpack/browserify/esbuild bundle into its modules, with a record
  # of the byte range each module came from in the original file
  python unminify.py --file bundle.js --unpack --out-dir ./modules

  # deliberately obfuscated code (hidden string tables): webcrack, sandboxed
  python unminify.py --file obf.js --deobfuscate --out-file obf.clean.js

Order of preference:
  1. A source map (inline data: URI, a //# sourceMappingURL comment, or a
     sibling .map) that carries `sourcesContent`. Each original file is written
     under --out-dir. This is the author's actual source. Without --out-dir the
     map is only reported and the code is decompiled as asked: recovered files
     never land in whatever folder the tool happens to run from.
  2. wakaru (pinned @wakaru/cli, a Rust decompiler): rewrites minifier
     shorthand back into plain code (1e4 becomes 10000, comma chains become
     statements, "u"<typeof x becomes typeof x === "undefined"), recovers names
     where the code still carries them, and with --unpack splits a bundle into
     modules plus provenance.json (module file to byte ranges in the input). It
     reads the code and never runs it, so it is safe on any input.
  3. js-beautify (pinned, through npx, install scripts off), formatting only,
     when wakaru is unavailable.
  4. If nothing is available, the raw content UNCHANGED with a note. A regex
     reformat would corrupt strings and regexes, so this never guesses.

--deobfuscate runs webcrack (pinned) instead. Undoing a hidden string table
(the javascript-obfuscator style) means running the code's own decoder, so
webcrack runs only inside sandbox.py's container: no network, read-only system,
no privileges, the input mounted read-only. wakaru leaves such strings hidden;
webcrack recovered them in testing (2026-10-12).

Source maps are untrusted input: a malicious map can name paths like
`../../etc/passwd`. Reconstructed paths are stripped of `..`/scheme/leading
slash and re-checked to stay inside --out-dir before any file is written. The
map's own name is untrusted too: for a local file it is read only as a regular
file inside that file's folder and under the size cap (an absolute name once
read any file of ours, and /dev/zero read until memory ran out), and a fetched
one follows redirects without reading their bodies (webfetch.py).

The pinned tools install once into ~/.local/share/re-tools/node (npm, install
scripts off). requests + stdlib + those tools.
"""
import argparse
import base64
import json
import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webfetch  # noqa: E402

# //# sourceMappingURL=...  or legacy //@ , and the CSS block-comment form.
SM_JS = re.compile(r"(?://[#@]\s*sourceMappingURL=([^\s'\"]+))")
SM_CSS = re.compile(r"/\*[#@]\s*sourceMappingURL=([^\s'\"*]+)\s*\*/")

TOOLS_DIR = os.path.expanduser("~/.local/share/re-tools/node")
PINNED = {"@wakaru/cli": "1.14.0", "webcrack": "2.16.0"}
WAKARU_BIN = {("Linux", "x86_64"): "@wakaru/cli-linux-x64/wakaru",
              ("Linux", "aarch64"): "@wakaru/cli-linux-arm64/wakaru",
              ("Darwin", "arm64"): "@wakaru/cli-darwin-arm64/wakaru"}
TOOL_TIMEOUT = 300
TOOL_MEMORY = 4 * 1024 ** 3  # address-space cap for the decompiler process
TOOL_OUTPUT = 2 * 1024 ** 3  # what the sandboxed webcrack may write before it is stopped
MAX_FETCH_BYTES = 100_000_000  # a script or map bigger than this is refused: a cut file would read wrong
BEAUTIFY = "js-beautify@2.0.3"


def fetch(url, timeout, insecure):
    """(text, content type) of a URL, read as a stream so a huge or endless
    response cannot fill memory, its redirects followed unread. Past
    MAX_FETCH_BYTES it raises ValueError."""
    _, _, headers, data, _ = webfetch.get(url, timeout, not insecure, MAX_FETCH_BYTES, check=True)
    if len(data) > MAX_FETCH_BYTES:
        raise ValueError(f"{url} is over the {MAX_FETCH_BYTES} byte cap; not loaded")
    return data.decode("utf-8", errors="replace"), headers.get("content-type", "")


def read_local_map(ref, base):
    """A map named by the local file `base`: read only if it is a regular file inside base's
    own folder and under the size cap. The name comes from the untrusted file, so an absolute
    one would read any file of ours, and /dev/zero would read until memory ran out."""
    folder = os.path.realpath(os.path.dirname(os.path.abspath(base)))
    path = os.path.realpath(os.path.join(folder, ref))
    if not path.startswith(folder + os.sep):
        raise ValueError(f"source map {ref!r} is outside the file's folder; not read")
    if not os.path.isfile(path):
        raise ValueError(f"source map {ref!r} is missing or not a regular file; not read")
    if os.path.getsize(path) > MAX_FETCH_BYTES:
        raise ValueError(f"source map {ref!r} is over the {MAX_FETCH_BYTES} byte cap; not read")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load(args):
    """Return (content, base, is_url, content_type). A local file is read only if it is a regular
    file under MAX_FETCH_BYTES, as a fetched one is: /dev/zero once read until memory ran out."""
    if args.url:
        text, content_type = fetch(args.url, args.timeout, args.insecure)
        return text, args.url, True, content_type
    if not os.path.isfile(args.file):
        raise ValueError(f"{args.file} is missing or not a regular file; not read")
    with open(args.file, "rb") as fh:
        data = fh.read(MAX_FETCH_BYTES + 1)
    if len(data) > MAX_FETCH_BYTES:
        raise ValueError(f"{args.file} is over the {MAX_FETCH_BYTES} byte cap; not loaded")
    return data.decode("utf-8", errors="replace"), args.file, False, ""


def find_map_url(content):
    """Return the last sourceMappingURL declared, or None."""
    hits = SM_JS.findall(content) + SM_CSS.findall(content)
    return hits[-1] if hits else None


def resolve_map(map_ref, base, is_url, timeout, insecure):
    """Turn a sourceMappingURL value into the map's JSON text."""
    if map_ref.startswith("data:"):
        header, _, data = map_ref.partition(",")
        if ";base64" in header:
            return base64.b64decode(data).decode("utf-8", errors="replace")
        return urllib.parse.unquote(data)
    if is_url:
        return fetch(urllib.parse.urljoin(base, map_ref), timeout, insecure)[0]
    return read_local_map(map_ref, base)


def try_sibling_map(base, is_url, timeout, insecure):
    """Many builds ship app.js.map next to app.js with no comment."""
    try:
        if is_url:
            text = fetch(base + ".map", timeout, insecure)[0]
            if text.strip().startswith("{"):
                return text
        elif os.path.exists(base + ".map"):
            return read_local_map(os.path.basename(base) + ".map", base)
    except Exception:  # noqa: BLE001 - a missing sibling map is the common case, not an error
        return None
    return None


def safe_rel(source):
    """Strip a source-map `sources` entry down to a relative path that cannot
    escape the output directory. Returns None if nothing usable remains."""
    s = source.replace("\\", "/")
    s = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", s)  # webpack:// ng:// file:// http(s)://
    s = s.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in s.split("/") if p not in ("", ".", "..")]
    if not parts:
        return None
    return os.path.join(*parts)


def reconstruct(map_json, out_dir):
    """Write every original source the map carries. Returns (written, skipped, total)."""
    m = json.loads(map_json)
    sources = m.get("sources") or []
    contents = m.get("sourcesContent") or []
    root = m.get("sourceRoot") or ""
    out_real = os.path.realpath(out_dir)
    os.makedirs(out_real, exist_ok=True)

    written, skipped, taken = [], [], set()
    for i, src in enumerate(sources):
        content = contents[i] if i < len(contents) else None
        if content is None:
            skipped.append((src, "no sourcesContent"))
            continue
        if not isinstance(content, str):
            skipped.append((src, "sourcesContent entry is not a string"))
            continue
        if not isinstance(src, str):
            skipped.append((str(src), "source path is not a string"))
            continue
        rel = safe_rel((root + src) if root and not src.startswith(("/", "http")) else src)
        if not rel:
            skipped.append((src, "empty after sanitising"))
            continue
        # Two sources can strip to one path: webpack names colliding modules "file?hash", and the
        # query goes. The later one gets "~2", "~3" before its extension instead of overwriting.
        stem, ext = os.path.splitext(rel)
        n = 1
        while rel in taken:
            n += 1
            rel = f"{stem}~{n}{ext}"
        dest = os.path.realpath(os.path.join(out_real, rel))
        if dest != out_real and not dest.startswith(out_real + os.sep):
            skipped.append((src, "path escaped out-dir"))
            continue
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:     # "src/a" and "src/a/b.js": a file and a folder cannot share a name
            skipped.append((src, f"could not be written ({e.strerror}): a file and a folder share its path"))
            continue
        taken.add(rel)
        written.append(rel)
    return written, skipped, len(sources)


def beautify(content, typ):
    """Return (text, note). Prefer npx js-beautify; fall back to raw unchanged."""
    try:
        proc = subprocess.run(
            ["npx", "--yes", "--ignore-scripts", BEAUTIFY, "--type", typ, "-f", "-"],
            input=content.encode("utf-8"), capture_output=True, timeout=180,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.decode("utf-8", errors="replace"), None
        note = (proc.stderr.decode("utf-8", errors="replace")[:200]
                or f"js-beautify exited {proc.returncode}")
    except FileNotFoundError:
        note = "npx not found"
    except subprocess.TimeoutExpired:
        note = "js-beautify timed out"
    except Exception as e:  # noqa: BLE001
        note = str(e)[:200]
    return content, f"js-beautify unavailable ({note}); emitting raw content unchanged"


def installed_version(pkg):
    pj = os.path.join(TOOLS_DIR, "node_modules", pkg, "package.json")
    try:
        with open(pj, "r", encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError):
        return None


def ensure_tools():
    """Install the pinned versions once. Returns None, or a reason it failed."""
    if all(installed_version(k) == v for k, v in PINNED.items()):
        return None
    if not shutil.which("npm"):
        return "npm not found"
    os.makedirs(TOOLS_DIR, exist_ok=True)
    pkgs = [f"{k}@{v}" for k, v in PINNED.items()]
    try:
        proc = subprocess.run(["npm", "install", "--prefix", TOOLS_DIR, "--no-audit", "--no-fund", "--ignore-scripts",
                               "--save-exact", *pkgs], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "npm install timed out"
    if proc.returncode != 0:
        return (proc.stderr.strip() or "npm install failed")[-300:]
    bad = [k for k, v in PINNED.items() if installed_version(k) != v]
    return f"version mismatch after install: {bad}" if bad else None


def wakaru_path():
    rel = WAKARU_BIN.get((platform.system(), platform.machine()))
    if not rel:
        return None
    path = os.path.join(TOOLS_DIR, "node_modules", rel)
    return path if os.access(path, os.X_OK) else None


def _cap_memory():
    # RLIMIT_AS is the OOM backstop on Linux. macOS does not enforce it the same
    # way and a low address-space cap makes the child fail to exec, so cap only
    # on Linux; the tool timeout is the backstop elsewhere.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (TOOL_MEMORY, TOOL_MEMORY))


def run_wakaru(src_path, out_file=None, unpack_dir=None, level="standard"):
    """Decompile with wakaru. Returns (text_or_None, note_or_None). With
    unpack_dir the modules and provenance.json are written there."""
    why = ensure_tools()
    exe = wakaru_path()
    if why or not exe:
        return None, f"wakaru unavailable ({why or 'no binary for this platform'})"
    cmd = [exe, src_path, "--level", level]
    if unpack_dir:
        cmd += ["--unpack", "-o", unpack_dir, "--provenance", "--force"]
    elif out_file:
        cmd += ["-o", out_file, "--force"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TOOL_TIMEOUT, preexec_fn=_cap_memory,
                              cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return None, f"wakaru timed out after {TOOL_TIMEOUT} s"
    if proc.returncode != 0:
        return None, "wakaru failed: " + proc.stderr.decode("utf-8", errors="replace")[-300:]
    if unpack_dir or out_file:
        return "", None
    return proc.stdout.decode("utf-8", errors="replace"), None


def run_webcrack_sandboxed(src_path, out_file=None, out_dir=None):
    """webcrack with deobfuscation, inside the offline sandbox. Returns
    (text_or_None, note_or_None)."""
    why = ensure_tools()
    if why:
        return None, f"webcrack unavailable ({why})"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sandbox
    work = tempfile.mkdtemp(prefix="re-webcrack-")
    try:
        in_dir = os.path.join(work, "in")
        res_dir = os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(res_dir)
        shutil.copy2(src_path, os.path.join(in_dir, "input.js"))
        cli = "/tool/node_modules/webcrack/src/cli-wrapper.js"
        # Always to a folder: the sandbox keeps only a 200 KB tail of stdout.
        cmd = ["node", cli, "/in/input.js", "-o", "/out/modules", "-f"]
        try:
            res = sandbox.run(cmd, ro=[f"{TOOLS_DIR}:/tool", f"{in_dir}:/in"], rw=[f"{res_dir}:/out"],
                              memory="2g", timeout=TOOL_TIMEOUT, max_write=TOOL_OUTPUT)
        except sandbox.SandboxError as e:
            return None, str(e)
        if res.get("over_cap"):
            return None, f"webcrack wrote more than {TOOL_OUTPUT >> 20} MB and was stopped"
        if res["timed_out"] or res["returncode"] != 0:
            return None, f"webcrack failed (exit {res['returncode']}): {res['stderr'][-300:]}"
        if not os.path.lexists(os.path.join(res_dir, "modules", "deobfuscated.js")):
            return None, "webcrack wrote no deobfuscated.js"
        # The container wrote these: read back only regular files, never through a link it left.
        try:
            with sandbox.open_output(res_dir, os.path.join("modules", "deobfuscated.js")) as fh:
                if out_dir:
                    skipped = sandbox.copy_output_tree(res_dir, "modules", out_dir)
                    if skipped:
                        print(f"Not copied (links or special files webcrack left): {skipped[:20]}", file=sys.stderr)
                    return "", None
                if out_file:
                    with open(out_file, "wb") as dst:
                        shutil.copyfileobj(fh, dst)
                    return "", None
                return fh.read().decode("utf-8", errors="replace"), None
        except sandbox.SandboxError as e:
            return None, f"webcrack's output was not used: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Recover or decompile shipped JavaScript (source map, wakaru, webcrack).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Fetch and process this URL.")
    src.add_argument("--file", help="Process this local file.")
    ap.add_argument("--out-dir", help="Where recovered sources (map) or unpacked modules (--unpack) go.")
    ap.add_argument("--out-file", help="Write single-file output here; default stdout.")
    ap.add_argument("--type", choices=["js", "css", "auto"], default="auto")
    ap.add_argument("--no-map", action="store_true", help="Ignore source maps.")
    ap.add_argument("--engine", choices=["auto", "wakaru", "beautify"], default="auto",
                    help="auto: wakaru, then js-beautify if wakaru is unavailable.")
    ap.add_argument("--level", choices=["minimal", "standard", "aggressive"], default="standard",
                    help="wakaru rewrite level.")
    ap.add_argument("--unpack", action="store_true", help="Split a bundle into modules (needs --out-dir).")
    ap.add_argument("--deobfuscate", action="store_true",
                    help="webcrack with string-table decoding, run inside the offline sandbox.")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification.")
    args = ap.parse_args()
    if args.unpack and not args.out_dir:
        ap.error("--unpack needs --out-dir")
    # The tools run from a neutral folder, so every path they see is absolute.
    for name in ("file", "out_dir", "out_file"):
        if getattr(args, name):
            setattr(args, name, os.path.abspath(getattr(args, name)))

    try:
        content, base, is_url, content_type = load(args)
    except Exception as e:  # noqa: BLE001
        print(f"Load failed: {e}", file=sys.stderr)
        sys.exit(1)

    typ = args.type
    if typ == "auto":
        typ = "css" if (base.endswith(".css") or "css" in content_type) else "js"

    # 1. Source map path.
    if not args.no_map and not args.deobfuscate:
        map_json = None
        ref = find_map_url(content)
        try:
            if ref:
                map_json = resolve_map(ref, base, is_url, args.timeout, args.insecure)
            if map_json is None:
                map_json = try_sibling_map(base, is_url, args.timeout, args.insecure)
        except Exception as e:  # noqa: BLE001 - fall through to decompiling if the map won't load
            print(f"Source map found but could not be loaded: {e}", file=sys.stderr)
            map_json = None

        if map_json and not args.out_dir:
            print("This file has a source map, which may hold the original files: rerun with --out-dir DIR to "
                  "recover them. Decompiling as asked instead.", file=sys.stderr)
        elif map_json:
            try:
                written, skipped, total = reconstruct(map_json, args.out_dir)
            except Exception as e:  # noqa: BLE001
                print(f"Source map present but malformed: {e}", file=sys.stderr)
                written, skipped, total = [], [], 0
            if written:
                out_real = os.path.realpath(args.out_dir)
                print(f"Recovered {len(written)}/{total} original source(s) into {out_real}:")
                for w in written[:200]:
                    print(f"  {w}")
                if skipped:
                    print(f"\nSkipped {len(skipped)}:", file=sys.stderr)
                    for s, why in skipped[:50]:
                        print(f"  {s} -- {why}", file=sys.stderr)
                return
            print("A source map was found but carried no usable sourcesContent; decompiling instead.",
                  file=sys.stderr)

    # The tools read a file; a fetched URL goes into its own fresh folder first.
    work = None
    src_path = args.file
    if is_url:
        work = tempfile.mkdtemp(prefix="re-unminify-")
        src_path = os.path.join(work, "input." + typ)
        with open(src_path, "w", encoding="utf-8") as fh:
            fh.write(content)
    try:
        text, note = None, None
        if args.deobfuscate:
            if typ != "js":
                print("--deobfuscate is for JavaScript only", file=sys.stderr)
                sys.exit(2)
            text, note = run_webcrack_sandboxed(src_path, args.out_file, args.out_dir)
            if text is None:
                print(note, file=sys.stderr)
                sys.exit(1)
        elif typ == "js" and args.engine in ("auto", "wakaru"):
            text, note = run_wakaru(src_path, args.out_file, args.out_dir if args.unpack else None, args.level)
            if text is None and args.engine == "wakaru":
                print(note, file=sys.stderr)
                sys.exit(1)
        if text is None:
            if note:
                print(note + "; falling back to js-beautify", file=sys.stderr)
            if args.unpack:
                print("--unpack needs wakaru; nothing written", file=sys.stderr)
                sys.exit(1)
            text, note = beautify(content, typ)
            if note:
                print(note, file=sys.stderr)
            if args.out_file:
                with open(args.out_file, "w", encoding="utf-8") as fh:
                    fh.write(text)
                text = ""
        if text == "":
            dest = args.out_dir if (args.unpack or (args.deobfuscate and args.out_dir)) else args.out_file
            if args.unpack:
                files = [f for f in os.listdir(dest) if f.endswith(".js")]
                print(f"Wrote {len(files)} module(s) to {os.path.realpath(dest)}"
                      + (" with provenance.json" if os.path.exists(os.path.join(dest, "provenance.json")) else ""))
            else:
                print(f"Wrote {dest}")
        else:
            sys.stdout.write(text)
            if not text.endswith("\n"):
                sys.stdout.write("\n")
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
