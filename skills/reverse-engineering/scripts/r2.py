#!/usr/bin/env python3
"""
Drive radare2 over a compiled binary and return its JSON, run inside the offline
sandbox so a hostile file is only ever parsed in isolation.

  python r2.py target --info                 # headers, arch, bits (no analysis)
  python r2.py target --functions            # analyse, then list functions
  python r2.py target --strings              # strings in the whole file
  python r2.py target --imports              # imported symbols
  python r2.py target --disasm main          # one function: addr + bytes + opcode (ground truth)
  python r2.py target --disasm 0x6d30        # or by address
  python r2.py target --xrefs sym.main       # who references this
  python r2.py target --survey --out t.json  # info + functions + strings + imports as JSON

radare2 is native code parsing an untrusted binary, so by default it runs in
sandbox.py's re-native image (no network, read-only root, no capabilities,
non-root, memory capped). --host-tools runs the host's radare2 instead, only
for a binary you trust; this machine has no host radare2, so the sandbox is the
normal path.

What counts as what (the verification spine):
  ground truth   the headers (`ij`), every string and its offset (`izzj`), the
                 import list (`iij`), and each disassembled instruction with its
                 address and bytes (`pdfj`). Cite the address and the bytes.
  hypothesis     what a function does, read off the disassembly or the names.
  behaviour      what the program does when run; only a sandbox run shows that.

radare2's own analysis (`aaa`) is a heuristic: a function boundary or a guessed
type is a hypothesis, not ground truth. The instruction bytes at an address are
ground truth; what radare2 calls the function containing them is not.

Nothing from the binary ever reaches a shell: the binary is mounted read-only
at a fixed path and radare2's commands are fixed templates. A function argument
you pass is checked against a strict character set (or an address) first, so it
cannot inject a radare2 command. '@' is not in that set: radare2 reads '@@' as a
foreach iterator, and 'entry0@@c:ij' (only letters, digits, '@' and ':') made it
run 'ij'. No radare2 function or flag name carries an '@'.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bin_triage  # noqa: E402
import sandbox  # noqa: E402

TARGET = "/in/target"
MAX_JSON = 64 << 20   # radare2 prints its JSON on stdout; 8 MB of python3.12 gave 8.8 MB of strings
SYM_OK = re.compile(r"^(?:0x[0-9a-fA-F]+|[A-Za-z_][\w.$:]*)\Z")   # \Z: $ would let a final newline in
ANALYSIS = {"quick": "aa", "deep": "aaa"}


class R2Error(RuntimeError):
    pass


def _check_sym(sym):
    if not SYM_OK.match(sym):
        raise R2Error(f"{sym!r} is not a bare symbol name or 0x-address; radare2 commands are not allowed here "
                      f"(use the function's address, e.g. 0x1234, for a name with unusual characters)")
    return sym


def run_r2_json(binpath, r2cmd, host=False, memory="2g", timeout=300):
    """Run one radare2 command that prints JSON, return the parsed object.
    `r2cmd` is built only from fixed templates and checked arguments."""
    flags = ["-q", "-e", "scr.color=0", "-e", "scr.interactive=0", "-e", "scr.utf8=0", "-c", r2cmd]
    if host:
        exe = shutil.which("radare2") or shutil.which("r2")
        if not exe:
            raise R2Error("no radare2 on the host (drop --host-tools to use the sandbox)")
        try:
            p = subprocess.run([exe, *flags, binpath], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise R2Error(f"radare2 timed out after {timeout} s")
        out, err, rc = p.stdout, p.stderr, p.returncode
    else:
        with tempfile.TemporaryDirectory(prefix="re-r2-") as work:
            in_dir = os.path.join(work, "in")
            os.makedirs(in_dir)
            shutil.copyfile(binpath, os.path.join(in_dir, "target"))
            try:
                image = sandbox.ensure_image(sandbox.NATIVE_DOCKERFILE)
                res = sandbox.run(["radare2", *flags, TARGET], image=image, ro=[f"{in_dir}:/in"],
                                  memory=memory, timeout=timeout, max_output=MAX_JSON)
            except sandbox.SandboxError as e:
                raise R2Error(f"{e} (use --host-tools for a binary you trust)")
            if res["timed_out"]:
                raise R2Error(f"radare2 timed out after {timeout} s")
            if res.get("stdout_cut"):     # the default 200 KB tail cut every list of a mid-sized binary
                raise R2Error(f"radare2 printed more than {MAX_JSON >> 20} MB of JSON; too much to read here")
            out, err, rc = res["stdout"], res["stderr"], res["returncode"]
    text = out.strip()
    if not text:
        raise R2Error(f"radare2 printed no JSON (exit {rc}): {err.strip()[-300:]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # A stray analysis line can precede the JSON; take the last JSON value on stdout.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line[:1] in "[{":
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise R2Error(f"could not parse radare2 JSON (exit {rc}); first 300 chars: {text[:300]!r}")


def info(binpath, **kw):
    return run_r2_json(binpath, "ij", **kw)


def functions(binpath, level="deep", **kw):
    data = run_r2_json(binpath, f"{ANALYSIS[level]};aflj", **kw)
    return data if isinstance(data, list) else []


def strings(binpath, **kw):
    data = run_r2_json(binpath, "izzj", **kw)
    if isinstance(data, dict):
        return data.get("strings", [])
    return data if isinstance(data, list) else []


def imports(binpath, **kw):
    data = run_r2_json(binpath, "iij", **kw)
    return data if isinstance(data, list) else []


def disasm(binpath, sym, level="deep", **kw):
    """The ops of one function: a list of {addr, bytes, opcode, size}. Ground truth."""
    s = _check_sym(sym)
    data = run_r2_json(binpath, f"{ANALYSIS[level]};pdfj @ {s}", **kw)
    if not isinstance(data, dict) or "ops" not in data:
        raise R2Error(f"no function at {sym!r} (radare2 returned no ops)")
    return data


def xrefs(binpath, sym, level="deep", **kw):
    s = _check_sym(sym)
    data = run_r2_json(binpath, f"{ANALYSIS[level]};axtj {s}", **kw)
    return data if isinstance(data, list) else []


def survey(binpath, level="deep", **kw):
    return {"info": info(binpath, **kw), "functions": functions(binpath, level=level, **kw),
            "strings": strings(binpath, **kw), "imports": imports(binpath, **kw)}


def _print_info(d):
    core, b = d.get("core", {}), d.get("bin", {})
    print(f"{b.get('bintype', '?')} {b.get('bits', '?')}-bit {b.get('arch', '?')} "
          f"{b.get('class', '')} {b.get('os', '')}".strip())
    print(f"  type: {core.get('type', '?')}; endian: {b.get('endian', '?')}; "
          f"stripped: {b.get('stripped', '?')}; pic: {b.get('pic', '?')}")
    for k in ("canary", "nx", "relro", "lang", "compiler"):
        if b.get(k) not in (None, ""):
            print(f"  {k}: {b[k]}")


def _print_disasm(d):
    print(f"// {d.get('name', '?')}  @ 0x{d.get('addr', 0):x}  ({d.get('size', 0)} bytes, {len(d['ops'])} ops)")
    for o in d["ops"]:
        addr = o.get("addr", o.get("offset", 0))
        print(f"  0x{addr:08x}  {o.get('bytes', ''):<24} {o.get('opcode', o.get('disasm', ''))}")


def main():
    ap = argparse.ArgumentParser(description="Drive radare2 over a binary inside the offline sandbox.")
    ap.add_argument("file", help="Path to the binary.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--info", action="store_true", help="Headers, arch, bits (no analysis).")
    g.add_argument("--functions", action="store_true", help="Analyse, then list functions.")
    g.add_argument("--strings", action="store_true", help="Strings in the whole file.")
    g.add_argument("--imports", action="store_true", help="Imported symbols.")
    g.add_argument("--disasm", metavar="SYM", help="Disassemble one function (name or 0x-address).")
    g.add_argument("--xrefs", metavar="SYM", help="References to a symbol or address.")
    g.add_argument("--survey", action="store_true", help="info + functions + strings + imports.")
    ap.add_argument("--quick", action="store_true", help="Lighter analysis (aa) instead of the default (aaa).")
    ap.add_argument("--host-tools", action="store_true", help="Use the host's radare2 (only for a binary you trust).")
    ap.add_argument("--slice", metavar="ARCH", help="For a fat/universal Mach-O: the slice (arch or index) to open.")
    ap.add_argument("--out", help="Write the result as JSON here.")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--limit", type=int, default=60, help="Rows per list in the console summary.")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)
    level = "quick" if args.quick else "deep"
    kw = {"host": args.host_tools, "timeout": args.timeout}
    L = args.limit

    target, tmp_slice = args.file, None
    if args.slice:
        try:
            target, arch = bin_triage.write_slice(args.file, args.slice)
            tmp_slice = target
            print(f"[slice {arch}]", file=sys.stderr)
        except (bin_triage.BinError, OSError) as e:
            print(f"--slice: {e}", file=sys.stderr)
            sys.exit(2)

    try:
        if args.info:
            d = info(target, **kw)
            _print_info(d)
        elif args.functions:
            d = functions(target, level=level, **kw)
            print(f"{len(d)} function(s):")
            for f in d[:L]:
                print(f"  0x{f.get('addr', 0):08x}  {f.get('size', 0):>6} B  {f.get('name', '')}")
        elif args.strings:
            d = strings(target, **kw)
            print(f"{len(d)} string(s):")
            for s in d[:L]:
                print(f"  @0x{s.get('vaddr', s.get('paddr', 0)):x}  {s.get('string', '')!r}")
        elif args.imports:
            d = imports(target, **kw)
            print(f"{len(d)} import(s):")
            for im in d[:L]:
                print(f"  {im.get('name', '')}  ({im.get('type', '')}, {im.get('bind', '')})")
        elif args.disasm:
            d = disasm(target, args.disasm, level=level, **kw)
            _print_disasm(d)
        elif args.xrefs:
            d = xrefs(target, args.xrefs, level=level, **kw)
            print(f"{len(d)} reference(s) to {args.xrefs}:")
            for x in d[:L]:
                print(f"  from 0x{x.get('from', 0):x}  {x.get('type', '')}  {x.get('fcn_name', '')}")
        elif args.survey:
            d = survey(target, level=level, **kw)
            _print_info(d["info"])
            print(f"  functions: {len(d['functions'])}; strings: {len(d['strings'])}; imports: {len(d['imports'])}")
    except R2Error as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    finally:
        if tmp_slice and os.path.exists(tmp_slice):
            os.unlink(tmp_slice)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
        print(f"Wrote {args.out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
