#!/usr/bin/env python3
"""
Inspect a WebAssembly (.wasm) binary: its sections, imports and exports, every
function (name, signature, where its body sits in the file), the readable
strings in its data, the toolchain that built it, and, through wabt, the
disassembly or decompiled text of any one function.

Usage:
  python wasm_inspect.py module.wasm                    # summary
  python wasm_inspect.py module.wasm --out report.json  # everything, as JSON
  python wasm_inspect.py module.wasm --disasm next_slide   # one function, offset + bytes per instruction
  python wasm_inspect.py module.wasm --decompile next_slide  # one function as C-like text (a reading aid)
  python wasm_inspect.py module.wasm --wat-out m.wat --decompile-out m.dcmp

Pure Python for everything except --disasm, --decompile and the *-out files,
which use wabt (wasm-objdump, wasm-decompile, wasm2wat). wabt is native code
parsing an untrusted file, so it runs inside sandbox.py's container (image
built from re-tools.Dockerfile on first use); --host-tools runs the host's
wabt instead, only for a module you trust.

What counts as what (the verification spine):
  ground truth   offsets, sizes, counts, names, imports, exports, strings, and
                 each disassembled instruction with its file offset and bytes;
  hypothesis     what a function does, including anything read off the
                 decompiled text. wasm-decompile (1.0.34 and 1.0.36) names a
                 function's parameters a, b in the signature and then i, n in
                 the body, with stray local declarations: it is a reading aid;
  behaviour      what the module does at runtime; only a run in the sandbox
                 can show that.

The import list is still the clearest single signal of what a module can
reach: a module importing wasi_snapshot_preview1.fd_write can write output; one
importing nothing but env.memory is pure computation.

A .wasm is untrusted input. The file is mapped, not read, and every section and
body is a view into the map, never a copy, so a large module costs the bot's
shared memory nothing of its own (a 200 MB module once peaked at 612 MB). Every
read is bounds-checked, each section is parsed from its own slice so a malformed
one cannot derail the walk, counts and string lists are capped, and the wabt
tools run with a time, memory and output limit and write to temporary files
that are read back line by line, the sandbox's only as regular files.
"""
import argparse
import json
import mmap
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile

SECTION_NAMES = {
    0: "custom", 1: "type", 2: "import", 3: "function", 4: "table", 5: "memory",
    6: "global", 7: "export", 8: "start", 9: "element", 10: "code", 11: "data",
    12: "datacount", 13: "tag",
}
# Sections whose payload begins with a LEB128 vector count.
VEC_SECTIONS = {1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 13}
EXTERNAL_KIND = {0: "func", 1: "table", 2: "mem", 3: "global", 4: "tag"}
VALTYPES = {0x7F: "i32", 0x7E: "i64", 0x7D: "f32", 0x7C: "f64", 0x7B: "v128", 0x70: "funcref",
            0x6F: "externref"}
MAX_SECTIONS = 100_000
MAX_FUNCS = 1_000_000
# Each entry is a few bytes of file and a Python object of a few hundred: uncapped, 3 MB of empty
# imports took 574 MB, so about 42 MB of module would have taken the bot's 8 GB. Real modules
# carry thousands of imports, exports, types and data segments, not hundreds of thousands.
MAX_ENTRIES = 100_000
MAX_STRINGS = 300
MIN_STRING = 6
TOOL_TIMEOUT = 300
TOOL_MEMORY = 4 * 1024 ** 3
TOOL_OUTPUT = 2 * 1024 ** 3   # what a sandboxed wabt run may write (its text output) before it is stopped


class Cursor:
    """Bounds-checked reader over bytes or a memoryview. Raises on any overrun."""

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def eof(self):
        return self.pos >= len(self.data)

    def u8(self):
        if self.pos >= len(self.data):
            raise ValueError("unexpected end of data")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def uleb(self):
        """Unsigned LEB128, capped at 5 bytes (u32) to reject malformed runs."""
        result = 0
        shift = 0
        for _ in range(5):
            b = self.u8()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7
        raise ValueError("LEB128 too long")

    def sleb(self, max_bytes=10):
        """Signed LEB128 (i32 constants use 5 bytes, i64 up to 10)."""
        result = 0
        shift = 0
        for _ in range(max_bytes):
            b = self.u8()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                if b & 0x40:
                    result -= 1 << shift
                return result
        raise ValueError("signed LEB128 too long")

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError(f"slice of {n} overruns buffer")
        s = self.data[self.pos:self.pos + n]
        self.pos += n
        return s

    def name(self):
        n = self.uleb()
        return bytes(self.take(n)).decode("utf-8", errors="replace")


def vec_count(cur, cap=MAX_ENTRIES, what="entries"):
    """A vector's length, refused past the cap (the section is then reported, not decoded)."""
    n = cur.uleb()
    if n > cap:
        raise ValueError(f"{n} {what} is over the {cap} cap")
    return n


def read_limits(cur):
    flags = cur.u8()
    cur.uleb()              # min
    if flags & 0x01:        # bit 0 set => a max follows (covers 0x01 and shared 0x03)
        cur.uleb()          # max


def read_valtype(cur):
    b = cur.u8()
    if b in VALTYPES:
        return VALTYPES[b]
    if b in (0x63, 0x64):   # (ref null ht) / (ref ht), function-references and GC proposals
        ht = cur.sleb(5)
        return ("ref null " if b == 0x63 else "ref ") + str(ht)
    raise ValueError(f"unknown value type 0x{b:02x}")


def parse_types(payload):
    cur = Cursor(payload)
    out = []
    for _ in range(vec_count(cur, what="types")):
        form = cur.u8()
        if form != 0x60:
            raise ValueError(f"type form 0x{form:02x} is not a plain function type (GC types are not decoded)")
        params = [read_valtype(cur) for _ in range(cur.uleb())]
        results = [read_valtype(cur) for _ in range(cur.uleb())]
        out.append((params, results))
    return out


def parse_imports(payload):
    cur = Cursor(payload)
    out = []
    for _ in range(vec_count(cur, what="imports")):
        module = cur.name()
        field = cur.name()
        kind = cur.u8()
        entry = {"module": module, "field": field, "kind": EXTERNAL_KIND.get(kind, str(kind))}
        if kind == 0:        # func: typeidx
            entry["type"] = cur.uleb()
        elif kind == 1:      # table: reftype + limits
            cur.u8()
            read_limits(cur)
        elif kind == 2:      # mem: limits
            read_limits(cur)
        elif kind == 3:      # global: valtype + mutability
            read_valtype(cur)
            cur.u8()
        elif kind == 4:      # tag: attribute + typeidx
            cur.u8()
            cur.uleb()
        else:
            raise ValueError(f"unknown import kind {kind}")
        out.append(entry)
    return out


def parse_exports(payload):
    cur = Cursor(payload)
    out = []
    for _ in range(vec_count(cur, what="exports")):
        name = cur.name()
        kind = cur.u8()
        index = cur.uleb()
        out.append({"name": name, "kind": EXTERNAL_KIND.get(kind, str(kind)), "index": index})
    return out


def parse_function_types(payload):
    cur = Cursor(payload)
    return [cur.uleb() for _ in range(vec_count(cur, MAX_FUNCS, "functions"))]


def parse_code(payload, base):
    """(file offset, size) of every function body. base = the payload's file offset."""
    cur = Cursor(payload)
    out = []
    for _ in range(vec_count(cur, MAX_FUNCS, "bodies")):
        size = cur.uleb()
        start = cur.pos
        cur.take(size)
        out.append((base + start, size))
    return out


def skip_const_expr(cur):
    """Walk a constant expression up to its `end` (0x0B)."""
    for _ in range(64):
        op = cur.u8()
        if op == 0x0B:
            return
        if op == 0x41:
            cur.sleb(5)
        elif op == 0x42:
            cur.sleb(10)
        elif op == 0x43:
            cur.take(4)
        elif op == 0x44:
            cur.take(8)
        elif op in (0x23, 0xD2):   # global.get idx, ref.func idx
            cur.uleb()
        elif op == 0xD0:           # ref.null heaptype
            cur.sleb(5)
        elif op in (0x6A, 0x6B, 0x6C, 0x7C, 0x7D, 0x7E):  # extended-const arithmetic
            continue
        else:
            raise ValueError(f"unsupported opcode 0x{op:02x} in a constant expression")
    raise ValueError("constant expression too long")


def parse_data(payload, base):
    """Each segment's (file offset, size), plus the readable strings inside."""
    cur = Cursor(payload)
    segments, strings = [], []
    for _ in range(vec_count(cur, what="data segments")):
        flags = cur.uleb()
        if flags == 0:
            skip_const_expr(cur)
        elif flags == 2:
            cur.uleb()
            skip_const_expr(cur)
        elif flags != 1:
            raise ValueError(f"unknown data segment flags {flags}")
        size = cur.uleb()
        start = cur.pos
        blob = cur.take(size)
        segments.append({"offset": base + start, "size": size})
        if len(strings) < MAX_STRINGS:
            for m in re.finditer(rb"[\x20-\x7e]{%d,}" % MIN_STRING, blob):
                # Sliced from the view, not m.group(0): a 20 MB run once copied 20 MB to show 120 characters.
                text = bytes(blob[m.start():min(m.end(), m.start() + 120)]).decode("ascii")
                strings.append({"offset": base + start + m.start(), "text": text})
                if len(strings) >= MAX_STRINGS:
                    break
    return segments, strings


def parse_name_section(cur):
    """Custom section "name" (cursor just past the section's own name)."""
    module, funcs = None, {}
    while not cur.eof():
        sub = cur.u8()
        size = cur.uleb()
        body = Cursor(cur.take(size))
        if sub == 0:
            module = body.name()
        elif sub == 1:
            for _ in range(vec_count(body, MAX_FUNCS, "function names")):
                idx = body.uleb()
                funcs[idx] = body.name()
                if len(funcs) > MAX_FUNCS:     # several name subsections add up
                    raise ValueError(f"more than {MAX_FUNCS} function names is over the cap")
    return module, funcs


def parse_producers(cur):
    """Custom section "producers": language, processed-by and sdk, each with versions."""
    out = {}
    for _ in range(vec_count(cur, what="producer fields")):
        field = cur.name()
        out[field] = [f"{cur.name()} {cur.name()}".strip() for _ in range(vec_count(cur, what="producer values"))]
    return out


def sig_text(sig):
    params, results = sig
    return f"({', '.join(params)}) -> ({', '.join(results)})"


def inspect(data):
    report = {
        "size": len(data), "magic_ok": False, "version": None, "sections": [], "imports": [], "exports": [],
        "functions": [], "data_segments": [], "data_strings": [], "producers": {}, "module_name": None,
        "flags": {"name_section": False, "source_map_url": None}, "errors": [],
    }
    if len(data) < 8 or data[:4] != b"\x00asm":
        report["errors"].append("not a WebAssembly module (bad magic)")
        return report
    report["magic_ok"] = True
    report["version"] = int.from_bytes(data[4:8], "little")

    types, func_types, bodies, func_names = [], [], [], {}
    cur = Cursor(data)
    cur.pos = 8
    guard = 0
    while not cur.eof():
        guard += 1
        if guard > MAX_SECTIONS:
            report["errors"].append("too many sections; aborting walk")
            break
        try:
            sec_id = cur.u8()
            size = cur.uleb()
            offset = cur.pos
            payload = cur.take(size)
        except ValueError as e:
            report["errors"].append(f"truncated section header/payload: {e}")
            break

        entry = {"id": sec_id, "name": SECTION_NAMES.get(sec_id, f"unknown({sec_id})"),
                 "offset": offset, "size": size}
        try:
            if sec_id in VEC_SECTIONS:
                entry["count"] = Cursor(payload).uleb()
            if sec_id == 1:
                types = parse_types(payload)
            elif sec_id == 2:
                report["imports"] = parse_imports(payload)
            elif sec_id == 3:
                func_types = parse_function_types(payload)
            elif sec_id == 7:
                report["exports"] = parse_exports(payload)
            elif sec_id == 10:
                bodies = parse_code(payload, offset)
            elif sec_id == 11:
                report["data_segments"], report["data_strings"] = parse_data(payload, offset)
            elif sec_id == 0:
                c = Cursor(payload)
                cname = c.name()
                entry["custom_name"] = cname
                if cname == "name":
                    report["flags"]["name_section"] = True
                    report["module_name"], func_names = parse_name_section(c)
                elif cname == "sourceMappingURL":
                    report["flags"]["source_map_url"] = c.name()
                elif cname == "producers":
                    report["producers"] = parse_producers(c)
        except ValueError as e:
            entry["parse_error"] = str(e)
            report["errors"].append(f"section {entry['name']}@{offset}: {e}")
        report["sections"].append(entry)

    # One function index space: imported functions first, then the module's own.
    exported = {}
    for ex in report["exports"]:
        if ex["kind"] == "func":
            exported.setdefault(ex["index"], []).append(ex["name"])
    idx = 0
    for imp in report["imports"]:
        if imp["kind"] != "func":
            continue
        t = imp.get("type")
        sig = types[t] if t is not None and 0 <= t < len(types) else None
        report["functions"].append({"index": idx, "name": func_names.get(idx), "imported": True,
                                    "import": f"{imp['module']}.{imp['field']}",
                                    "signature": sig_text(sig) if sig else None,
                                    "exports": exported.get(idx, [])})
        idx += 1
    if func_types and bodies and len(func_types) != len(bodies):
        report["errors"].append(f"function section lists {len(func_types)} functions, code section "
                                f"{len(bodies)} bodies")
    for i, t in enumerate(func_types):
        sig = types[t] if t < len(types) else None
        body = bodies[i] if i < len(bodies) else (None, None)
        report["functions"].append({"index": idx, "name": func_names.get(idx), "imported": False,
                                    "signature": sig_text(sig) if sig else None,
                                    "body_offset": body[0], "body_size": body[1],
                                    "exports": exported.get(idx, [])})
        idx += 1
    return report


# ---------- wabt ----------

def _cap_memory():
    # RLIMIT_AS is the OOM backstop on Linux. macOS does not enforce it the same
    # way and a low address-space cap makes the child fail to exec, so cap only
    # on Linux; the tool timeout is the backstop elsewhere.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (TOOL_MEMORY, TOOL_MEMORY))


def wabt_to_file(tool, flags, src_path, out_path, host=False):
    """Run a wabt tool on src_path with its stdout going to out_path. Returns an
    error string or None. wabt is native code parsing an untrusted file, so by
    default it runs inside sandbox.py's container (the tools image); host=True
    uses the host's wabt, for modules we trust (our own builds)."""
    if host:
        exe = shutil.which(tool)
        if not exe:
            return f"{tool} not found (install the wabt package)"
        with open(out_path, "wb") as fh:
            try:
                proc = subprocess.run([exe, *flags, src_path], stdout=fh, stderr=subprocess.PIPE,
                                      timeout=TOOL_TIMEOUT, preexec_fn=_cap_memory)
            except subprocess.TimeoutExpired:
                return f"{tool} timed out after {TOOL_TIMEOUT} s"
        if proc.returncode != 0:
            return f"{tool} failed: " + proc.stderr.decode("utf-8", errors="replace")[-300:]
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sandbox
    with tempfile.TemporaryDirectory(prefix="re-wabt-") as work:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        shutil.copyfile(src_path, os.path.join(in_dir, "module.wasm"))
        # Fixed names only: nothing from the input reaches the shell line.
        line = " ".join([tool, *flags, "/in/module.wasm", ">", "/out/result.txt"])
        try:
            image = sandbox.ensure_tools_image()
            res = sandbox.run(["sh", "-c", line], image=image, ro=[f"{in_dir}:/in"], rw=[f"{out_dir}:/out"],
                              memory="2g", timeout=TOOL_TIMEOUT, max_write=TOOL_OUTPUT)
        except sandbox.SandboxError as e:
            return f"{e} (use --host-tools for a module you trust)"
        if res["timed_out"]:
            return f"{tool} timed out after {TOOL_TIMEOUT} s"
        if res.get("over_cap"):
            return f"{tool} wrote more than {TOOL_OUTPUT >> 20} MB and was stopped"
        if res["returncode"] != 0:
            return f"{tool} failed: " + res["stderr"][-300:]
        # Copied, never moved: a moved link stays a link to whatever host file it names.
        try:
            with sandbox.open_output(out_dir, "result.txt") as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except sandbox.SandboxError as e:
            return f"{tool}: {e}"
    return None


# "0008b0 func[7] <name>:" for a named function, "01ad86 func[202]:" for an unnamed one. Names from
# Rust and C++ carry brackets of their own ("00001e func[1] <Vec<u8>>:"), so the name runs to the
# last ">:" on the line.
FUNC_HEAD = re.compile(r"^([0-9a-f]+) func\[(\d+)\](?: <(.*)>)?:$")


def disasm_function(path, want, host=False):
    """Lines of wasm-objdump -d for one function, chosen by index or name.
    Each instruction line reads 'offset: bytes | instruction'."""
    with tempfile.TemporaryDirectory(prefix="re-wasm-") as tmp:
        out = os.path.join(tmp, "objdump.txt")
        err = wabt_to_file("wasm-objdump", ["-d"], path, out, host)
        if err:
            return None, err
        lines, inside = [], False
        with open(out, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = FUNC_HEAD.match(line)
                if m:
                    if inside:
                        break
                    inside = want in (m.group(2), m.group(3) or None)
                if inside:
                    lines.append(line.rstrip("\n"))
                    if len(lines) > 20000:
                        lines.append("... (cut at 20000 lines)")
                        break
    return (lines, None) if lines else (None, f"no function {want!r} in the disassembly")


def decompile_function(path, want, host=False):
    """wasm-decompile's text for one function, chosen by name."""
    with tempfile.TemporaryDirectory(prefix="re-wasm-") as tmp:
        out = os.path.join(tmp, "decompiled.txt")
        err = wabt_to_file("wasm-decompile", [], path, out, host)
        if err:
            return None, err
        head = re.compile(r"^(?:export )?function " + re.escape(want) + r"\(")
        lines, depth, inside, opened = [], 0, False, False
        with open(out, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not inside and head.match(line):
                    inside = True
                if inside:
                    lines.append(line.rstrip("\n"))
                    depth += line.count("{") - line.count("}")
                    opened = opened or "{" in line
                    if opened and depth <= 0:
                        break
                    if len(lines) > 20000:
                        lines.append("... (cut at 20000 lines)")
                        break
    return (lines, None) if lines else (None, f"no function {want!r} in the decompiled text (wasm-decompile "
                                              f"rewrites names that are not plain identifiers; --decompile-out "
                                              f"shows the names it uses)")


def load_module(path):
    """The file as a read-only view over a memory map: inspect() slices views out of
    it and copies nothing, so the module's size never lands in the bot's memory."""
    with open(path, "rb") as fh:
        if os.fstat(fh.fileno()).st_size == 0:
            return memoryview(b"")
        return memoryview(mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ))


def print_summary(r, limit):
    print(f"WebAssembly v{r['version']}, {r['size']} bytes, {len(r['sections'])} sections"
          + (f", module name {r['module_name']!r}" if r["module_name"] else ""))
    if r["producers"]:
        print("  built with: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in r["producers"].items()))
    print(f"  imports ({len(r['imports'])}):")
    for imp in r["imports"][:limit]:
        print(f"    {imp['module']}.{imp['field']} ({imp['kind']})")
    print(f"  exports ({len(r['exports'])}): " + ", ".join(e["name"] for e in r["exports"][:limit]))
    own = [f for f in r["functions"] if not f["imported"]]
    print(f"  functions: {len(r['functions'])} ({len(own)} defined here); largest bodies:")
    for f in sorted(own, key=lambda f: -(f["body_size"] or 0))[:min(limit, 15)]:
        print(f"    [{f['index']}] {f['name'] or '(no name)'} {f['signature'] or ''} "
              f"@{f['body_offset']} {f['body_size']} B" + (f" exported as {f['exports']}" if f["exports"] else ""))
    print(f"  data: {len(r['data_segments'])} segment(s), {len(r['data_strings'])} readable string(s)")
    for s in r["data_strings"][:min(limit, 15)]:
        print(f"    @{s['offset']}: {s['text']!r}")
    print(f"  debug names: {'yes' if r['flags']['name_section'] else 'no'}; source map: "
          f"{r['flags']['source_map_url'] or 'none'}")
    for e in r["errors"]:
        print(f"  error: {e}")


def main():
    ap = argparse.ArgumentParser(description="Inspect a .wasm binary.")
    ap.add_argument("file", help="Path to the .wasm file.")
    ap.add_argument("--out", help="Write the full report as JSON here.")
    ap.add_argument("--limit", type=int, default=40, help="Rows per list in the summary.")
    ap.add_argument("--disasm", metavar="FUNC", help="Disassemble one function (index or name), via wasm-objdump.")
    ap.add_argument("--decompile", metavar="FUNC", help="Decompile one function (name), via wasm-decompile.")
    ap.add_argument("--wat-out", help="Write the whole module as WAT text (wasm2wat).")
    ap.add_argument("--decompile-out", help="Write the whole module as decompiled text (wasm-decompile).")
    ap.add_argument("--host-tools", action="store_true",
                    help="Run wabt on the host instead of in the sandbox (only for modules you trust).")
    args = ap.parse_args()

    try:
        data = load_module(args.file)
    except Exception as e:  # noqa: BLE001
        print(f"Read failed: {e}", file=sys.stderr)
        sys.exit(1)

    report = inspect(data)
    if not report["magic_ok"]:
        print(report["errors"][0], file=sys.stderr)
        sys.exit(2)

    path = os.path.abspath(args.file)
    status = 0
    if args.disasm:
        lines, err = disasm_function(path, args.disasm, args.host_tools)
        print("\n".join(lines) if lines else err, file=sys.stdout if lines else sys.stderr)
        status = 0 if lines else 1
    elif args.decompile:
        lines, err = decompile_function(path, args.decompile, args.host_tools)
        print("\n".join(lines) if lines else err, file=sys.stdout if lines else sys.stderr)
        status = 0 if lines else 1
    else:
        print_summary(report, args.limit)
    for tool, dest in (("wasm2wat", args.wat_out), ("wasm-decompile", args.decompile_out)):
        if dest:
            err = wabt_to_file(tool, [], path, os.path.abspath(dest), args.host_tools)
            print(err or f"Wrote {dest}", file=sys.stderr if err else sys.stdout)
            status = status or (1 if err else 0)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        print(f"Wrote {args.out}")
    sys.exit(status)


if __name__ == "__main__":
    main()
