#!/usr/bin/env python3
"""
Prove gdre.py (GDRE Tools) on real packs made by real Godot editors, every pack
format and every compiled-script format there is.

  python prove.py build --godot3 BIN --godot44 BIN --godot46 BIN --godot47 BIN
  python prove.py verify                     # readers agree, scripts and scenes come back
  python prove.py verify --godot3 BIN ...    # + run original and decompiled side by side
  python prove.py verify ... --write         # also record the run in result.json here

build exports the probe projects in tests/fixtures/reverse_engineering/gdre/src3
(Godot 3 syntax) and src4 (Godot 4 syntax) with four editors and writes the
packs beside them:

  godot3.6.3.pck   pack format 1, compiled GDScript (bytecode 13 era)
  godot4.4.1.pck   pack format 2, binary tokens v100, zstd compressed
  godot4.6.2.pck   pack format 3, binary tokens v101, uncompressed
  godot4.7.2.pck   pack format 4, binary tokens v101, zstd compressed

The four editors are the official builds: 3.6.3 is godotengine/godot-builds'
Godot_v3.6.3-stable_linux_headless.64.zip (sha256 5164849c9a5942a952720f3f9232506c
590b10cca880591a1c1081acb617ebe4, its own headless editor build), 4.4.1 and 4.6.2
the standard linux.x86_64 editors, 4.7.2 the godot-4 snap (a mono build; these
probes use no C#). Run a downloaded editor from a folder of its own.

Exporting a pack needs no export templates (only a full game export does), and
a binary-token export only tokenizes, so build also loads each original probe
under its own engine to prove it parses and runs.

The behaviour step runs GDRE's decompiled text in a host engine, so it runs only
when that text is the committed source apart from the known losses (step 2).

verify checks, for each pack:
  1. two readers agree on the file list: godot_pck.py (pure Python) and GDRE's own;
  2. the decompiled main.gdc matches the source token for token (whitespace and
     comments dropped, literal notation canonicalised: 0xFF is 255, r"a" is "a"),
     except for the known losses listed in KNOWN, which must be exactly what differs;
  3. a recovered project carries the same script and the same scene;
  4. with the engines given: the original and the decompiled script run under the
     same engine print the same constants (exact bytes) and the same results.

The engines run on the host with a time limit; run the whole thing outside the
bot's memory group:

  systemd-run --user --scope -p MemoryMax=4G python prove.py verify ... --write

The Godot 4.7 snap cannot see /tmp or hidden folders, so the work folder sits in
the home folder (--work, default ~/re-gdre-work) and is removed afterwards.
"""
import argparse
import json
import mmap
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent.parent / "scripts"
REPO = HERE.parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "reverse_engineering" / "gdre"
sys.path.insert(0, str(SCRIPTS))
import gdre  # noqa: E402
import godot_pck  # noqa: E402

# name, engine flag, source folder, script_export_mode, harness, GDRE revision expected
PACKS = [
    ("godot3.6.3", "godot3", "src3", 1, "harness3.gd", "3.5.0-stable (a7aad78)"),
    ("godot4.4.1", "godot44", "src4", 2, "harness4.gd", "4.3.0-stable (77af6ca)"),
    ("godot4.6.2", "godot46", "src4", 1, "harness4.gd", "4.5.0-stable (ebc36a7)"),
    ("godot4.7.2", "godot47", "src4", 2, "harness4.gd", "4.5.0-stable (ebc36a7)"),
]
# Measured losses: what the decompile is allowed (and required) to get wrong, by
# source line. Godot 4 folds '-0.0' into one literal token and GDRE 2.7.4 writes
# it back as 0.0, so the sign of that zero is lost (Godot 3 keeps the minus as
# its own token, so it survives there).
NEG_ZERO_4 = {20: ("const NEG_ZERO : = - 0.0", "const NEG_ZERO : = 0.0")}   # tokens joined by spaces
KNOWN = {"godot3.6.3": {}, "godot4.4.1": NEG_ZERO_4, "godot4.6.2": NEG_ZERO_4, "godot4.7.2": NEG_ZERO_4}
CONST_KNOWN = {"godot3.6.3": set(), "godot4.4.1": {"NEG_ZERO"}, "godot4.6.2": {"NEG_ZERO"},
               "godot4.7.2": {"NEG_ZERO"}}
ENGINE_TIMEOUT = 240
# One planted change per comparison (present in both probes and their decompiles):
# a comparison that cannot see it proves nothing when it passes.
PLANT_SCRIPT = ("/ width - 100", "/ width - 99")
PLANT_SCENE = ("wait_time = 0.25", "wait_time = 0.5")

# ---------------------------------------------------------------- comparing

TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>\#.*)
  | (?P<str>[r&^]?(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))
  | (?P<num>0[xX][0-9A-Fa-f_]+|0[bB][01_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d[\d_]*)?|\.\d[\d_]*(?:[eE][+-]?\d[\d_]*)?)
  | (?P<name>[^\W\d]\w*)
  | (?P<op>.)
""", re.X)
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
           '"': '"', "'": "'", "\\": "\\"}


def _string_value(lit):
    """(prefix, value) of a GDScript string literal: & is a StringName, ^ a NodePath,
    r a raw string (backslashes kept); escapes decoded so notation does not matter."""
    prefix = lit[0] if lit[0] in "r&^" else ""
    body = lit[len(prefix) + 1:-1]
    if prefix == "r":
        return "", body                     # raw: backslashes are characters, not escapes
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nx = body[i + 1]
            if nx in ESCAPES:
                out.append(ESCAPES[nx])
                i += 2
                continue
            width = {"u": 4, "U": 6}.get(nx)
            if width and re.fullmatch(r"[0-9A-Fa-f]{%d}" % width, body[i + 2:i + 2 + width] or "x"):
                out.append(chr(int(body[i + 2:i + 2 + width], 16)))
                i += 2 + width
                continue
        out.append(ch)
        i += 1
    return prefix, "".join(out)


def _number_value(lit):
    s = lit.replace("_", "")
    if s[:2] in ("0x", "0X"):
        return str(int(s[2:], 16))
    if s[:2] in ("0b", "0B"):
        return str(int(s[2:], 2))
    if any(c in s for c in ".eE"):
        return repr(float(s))
    return str(int(s))


def canonical_line(line):
    """One source line as a tuple of tokens with whitespace and comments dropped and
    literals written one way (numbers by value, strings by decoded value)."""
    toks = []
    for m in TOKEN.finditer(line):
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        text = m.group()
        if kind == "num":
            toks.append(_number_value(text))
        elif kind == "str":
            prefix, value = _string_value(text)
            toks.append(prefix + json.dumps(value, ensure_ascii=False))
        else:
            toks.append(text)
    return tuple(toks)


def script_diff(source, decompiled):
    """{source line number: (source tokens, decompiled tokens)} for every line that
    differs once notation is canonicalised. Line numbers line up because GDRE keeps
    each statement on its source line (comment lines come back blank)."""
    a = [canonical_line(x) for x in source.splitlines()]
    b = [canonical_line(x) for x in decompiled.splitlines()]
    while a and not a[-1]:
        a.pop()
    while b and not b[-1]:
        b.pop()
    out = {}
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else ()
        y = b[i] if i < len(b) else ()
        if x != y:
            out[i + 1] = (" ".join(x), " ".join(y))
    return out


SCENE_NOISE = re.compile(r' (?:uid="[^"]*"|unique_id=\d+|load_steps=\d+)')


def scene_lines(text):
    """A text scene as its sections in order, each a header and its property lines
    sorted: the ids an editor rewrites on every save (uid, unique_id, load_steps)
    dropped, spacing inside Godot 3's brackets ('ExtResource( 1 )') closed up, and
    property order ignored (the saver writes a node's properties in its class's
    order, not the order they were typed in)."""
    sections = []
    for ln in text.splitlines():
        ln = re.sub(r"\(\s+|\s+\)", lambda m: m.group().strip(), SCENE_NOISE.sub("", ln).strip())
        if not ln:
            continue
        if ln.startswith("[") or not sections:
            sections.append((ln, []))
        else:
            sections[-1][1].append(ln)
    return [(head, sorted(props)) for head, props in sections]

# ---------------------------------------------------------------- engines


def _engine(binary, project, args, godot3=False, env=None, timeout=ENGINE_TIMEOUT):
    """Run a Godot editor binary on a project folder; returns (rc, output). A Godot 3
    headless build has no --headless flag (it is headless by build)."""
    cmd = [binary] + ([] if godot3 else ["--headless"]) + ["--path", str(project)] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, **(env or {})), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout} s"
    return p.returncode, p.stdout + p.stderr


def _copy_project(src, dst, mode=None):
    shutil.copytree(src, dst)
    if mode is not None:
        cfg = Path(dst, "export_presets.cfg")
        cfg.write_text(re.sub(r"(?m)^script_export_mode=\d+$", f"script_export_mode={mode}", cfg.read_text()))


def _run_probe(binary, project, harness, script="res://main.gd"):
    """CONST and RESULT lines of the harness run on one probe script, in order."""
    shutil.copy(HERE / harness, Path(project, harness))
    rc, out = _engine(binary, project, ["--script", f"res://{harness}"], godot3=harness == "harness3.gd",
                      env={"RE_PROBE": script})
    lines = [ln for ln in out.splitlines() if ln.startswith(("CONST ", "RESULT "))]
    if rc != 0 or not any(ln.startswith("RESULT ") for ln in lines):
        raise RuntimeError(f"probe run failed under {binary} (exit {rc}):\n{out[-1500:]}")
    return lines


def build(engines, work):
    made = {}
    for name, flag, src, mode, harness, _rev in PACKS:
        binary = engines[flag]
        proj = Path(work, name)
        _copy_project(FIXTURES / src, proj, mode)
        out = Path(work, f"{name}.pck")
        rc, log = _engine(binary, proj, ["--export-pack", "Linux", str(out)], godot3=src == "src3")
        if rc != 0 or not out.is_file():
            raise RuntimeError(f"{name}: export failed (exit {rc}):\n{log[-1500:]}")
        results = _run_probe(binary, proj, harness)          # the original parses and runs here
        shutil.copy(out, FIXTURES / f"{name}.pck")
        made[name] = {"bytes": out.stat().st_size, "probe_lines": len(results)}
        print(f"{name}: {out.stat().st_size} bytes, original probe ran ({len(results)} lines)")
    return made

# ---------------------------------------------------------------- verify


def _pck_files(pack):
    with open(pack, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        rep = godot_pck.parse(mm)
    paths = {f["path"] if f["path"].startswith("res://") else "res://" + f["path"] for f in rep["files"]}
    return rep, paths


def _behaviour(binary, project, harness, text):
    """Constants (name: exact bytes) and RESULT lines of one script run under an engine."""
    Path(project, "main.gd").write_text(text, encoding="utf-8")
    lines = _run_probe(binary, project, harness)
    consts = dict(ln.split(" ", 2)[1:] for ln in lines if ln.startswith("CONST "))
    return consts, [ln for ln in lines if ln.startswith("RESULT ")]


def _moved(a, b):
    """Names whose values differ between two constant tables, and the positions at
    which two result lists differ (order and count matter)."""
    names = sorted(k for k in a[0].keys() | b[0].keys() if a[0].get(k) != b[0].get(k))
    rows = [i for i in range(max(len(a[1]), len(b[1]))) if a[1][i:i + 1] != b[1][i:i + 1]]
    return names, rows


def _scene_diff(a, b):
    """Positions of the sections that differ between two scene_lines() results."""
    return [i for i in range(max(len(a), len(b))) if a[i:i + 1] != b[i:i + 1]]


def verify(engines, work):
    out = {"verdict": "PASS", "failures": [], "packs": {}}

    def fail(msg):
        out["failures"].append(msg)
        out["verdict"] = "FAIL"
        print("  FAIL", msg)

    for name, flag, src, _mode, harness, revision in PACKS:
        pack = FIXTURES / f"{name}.pck"
        source = (FIXTURES / src / "main.gd").read_text(encoding="utf-8")
        row = out["packs"][name] = {}
        print(name)
        rep, ours = _pck_files(pack)
        theirs = set(gdre.list_files(str(pack)))
        row["pack_format"], row["engine"], row["files"] = rep["format_version"], rep["engine_version"], len(ours)
        row["readers_agree"] = ours == theirs
        if ours != theirs:
            fail(f"{name}: godot_pck and GDRE disagree on the file list: {sorted(ours ^ theirs)}")
        text, used = gdre.decompile_member(str(pack), "res://main.gdc")
        row["revision"] = used
        if used != revision:
            fail(f"{name}: GDRE used the {used} table, expected {revision}")
        diff = script_diff(source, text)
        row["script_lines"] = len(source.splitlines())
        row["script_differences"] = {str(k): v for k, v in diff.items()}
        if diff != KNOWN[name]:
            fail(f"{name}: decompiled script differs from the source beyond the known losses: {diff}")
        # Controls: every comparison must see one planted change, or its pass means nothing.
        planted = text.replace(*PLANT_SCRIPT)
        if planted == text:
            fail(f"{name}: control: {PLANT_SCRIPT[0]!r} is not in the decompiled script")
        elif not set(script_diff(source, planted)) - set(diff):
            fail(f"{name}: control: the script comparison did not see a planted change")
        with tempfile.TemporaryDirectory(prefix="re-gdre-prove-") as tmp:
            dst = os.path.join(tmp, "project")
            written, _skipped, report = gdre.recover(str(pack), dst, "own_work",
                                                     "proving gdre.py on a probe project built here")
            row["recovered_files"] = len(written)
            row["report"] = report
            if Path(dst, "main.gd").read_text(encoding="utf-8") != text:
                fail(f"{name}: the recovered main.gd is not the decompiled one")
            got_text = Path(dst, "main.tscn").read_text(encoding="utf-8")
            scene_src = scene_lines((FIXTURES / src / "main.tscn").read_text(encoding="utf-8"))
            scene_got = scene_lines(got_text)
            row["scene_matches"] = scene_src == scene_got
            if scene_src != scene_got:
                fail(f"{name}: the recovered scene differs in sections {_scene_diff(scene_src, scene_got)}")
            if PLANT_SCENE[0] not in got_text or scene_lines(got_text.replace(*PLANT_SCENE)) == scene_got:
                fail(f"{name}: control: the scene comparison did not see a planted change")
        if engines.get(flag) and diff != KNOWN[name]:
            # The decompiled text is GDRE's output, run here on the host engine: run it only
            # when it is our own source apart from the known loss, never text we did not write.
            row["behaviour"] = "not run (the decompiled text is not the source; not executed on the host)"
        elif engines.get(flag):
            proj = Path(work, name)
            _copy_project(FIXTURES / src, proj)
            original = _behaviour(engines[flag], proj, harness, source)
            decompiled = _behaviour(engines[flag], proj, harness, text)
            moved, rows = _moved(original, decompiled)
            row["behaviour"] = {"constants": len(original[0]), "results": len(original[1]),
                                "constants_moved": moved, "results_moved": rows}
            if set(moved) != CONST_KNOWN[name] or rows:
                fail(f"{name}: running the decompiled script moved constants {moved} and results {rows}")
            if planted != text:
                _, rows_planted = _moved(original, _behaviour(engines[flag], proj, harness, planted))
                row["behaviour"]["control_results_moved"] = len(rows_planted)
                if not rows_planted:
                    fail(f"{name}: control: the run comparison did not see a planted change")
        else:
            row["behaviour"] = "not run (no engine given)"
        print(f"  pack v{row['pack_format']} engine {row['engine']}, {row['files']} files, readers agree "
              f"{row['readers_agree']}, table {used}, script differences {sorted(diff)}, scene "
              f"{row['scene_matches']}, behaviour {row['behaviour'] if isinstance(row['behaviour'], str) else 'run'}")
    print(f"== {out['verdict']}" + (f": {len(out['failures'])} failure(s)" if out["failures"] else ""))
    return out


def main():
    ap = argparse.ArgumentParser(description="Prove gdre.py on packs made by real Godot editors.")
    ap.add_argument("cmd", choices=("build", "verify"))
    for flag in ("godot3", "godot44", "godot46", "godot47"):
        ap.add_argument(f"--{flag}", metavar="BIN", help=f"Path to the {flag} editor binary.")
    ap.add_argument("--work", default=str(Path.home() / "re-gdre-work"),
                    help="Work folder the engines can all read (the snap cannot see /tmp or hidden folders).")
    ap.add_argument("--write", action="store_true", help="With verify: record the run in result.json.")
    args = ap.parse_args()
    engines = {f: getattr(args, f) for f in ("godot3", "godot44", "godot46", "godot47")}
    if args.cmd == "build" and not all(engines.values()):
        ap.error("build needs all four engines: --godot3 --godot44 --godot46 --godot47")
    if os.path.lexists(args.work):
        ap.error(f"--work {args.work} already exists; give a new folder")
    os.makedirs(args.work)
    try:
        if args.cmd == "build":
            build(engines, args.work)
            return
        res = verify(engines, args.work)
        if args.write:
            (HERE / "result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
            print(f"wrote {HERE / 'result.json'}")
        sys.exit(0 if res["verdict"] == "PASS" else 1)
    finally:
        shutil.rmtree(args.work, ignore_errors=True)


if __name__ == "__main__":
    main()
