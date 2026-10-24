#!/usr/bin/env python3
"""
Runs INSIDE the re-oracle sandbox (no network, read-only, non-root). Never run
on the host: it loads an untrusted binary's real machine code into the Unicorn
CPU emulator. The original bytes are interpreted, never executed natively.

Given a spec (JSON on argv[1]), it resolves one function in each of two binaries
(the original and a rebuild), maps their sections into an emulator, calls the
function on a shared corpus of inputs, and reports whether the two agree on
every input, with the first input where they diverge.

Only x86-64 for now; integer arguments (passed in registers per the chosen ABI)
and fixed-size output buffers (a pointer argument the function writes through).
A function that calls out to code that is not mapped (an imported libc symbol)
stops that run with an error unless the call target is given in `hooks`; the
clean case is a leaf function or one whose callees are also in the binary.
"""
import json
import random
import struct
import subprocess
import sys

import capstone
import pefile
from unicorn import (
    UC_ARCH_X86, UC_ERR_FETCH_PROT, UC_ERR_FETCH_UNMAPPED, UC_HOOK_MEM_UNMAPPED,
    UC_MODE_64, UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE, Uc, UcError,
)
from unicorn.x86_const import (
    UC_X86_REG_R8, UC_X86_REG_R9, UC_X86_REG_RAX, UC_X86_REG_RCX, UC_X86_REG_RDI,
    UC_X86_REG_RDX, UC_X86_REG_RIP, UC_X86_REG_RSI, UC_X86_REG_RSP,
)

PAGE = 0x1000
SENTINEL = 0x5E7D0000          # the fake return address; emulation stops when RIP reaches it
STACK_ADDR = 0x7F0000000000
STACK_SIZE = 2 * 1024 * 1024
SCRATCH_ADDR = 0x7E0000000000  # output buffers and other pointer targets live here
SCRATCH_SIZE = 1024 * 1024
ARG_REGS = {
    "sysv": [UC_X86_REG_RDI, UC_X86_REG_RSI, UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8, UC_X86_REG_R9],
    "win64": [UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9],
}


class OracleError(RuntimeError):
    pass


def _align_down(x, a=PAGE):
    return x - (x % a)


def _align_up(x, a=PAGE):
    return (x + a - 1) // a * a


# ---------------- loading a binary into a mapping plan ----------------

def load_sections(path):
    """Return (sections, resolver) where sections is a list of
    {addr, data, exec, write} to map, and resolver(name) gives a function's
    entry address. ELF and PE are handled separately but return the same shape."""
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head[:4] == b"\x7fELF":
        return _load_elf(path)
    if head[:2] == b"MZ":
        return _load_pe(path)
    raise OracleError(f"{path}: not an ELF or PE binary (magic {head.hex()})")


def _readelf(path, *args):
    p = subprocess.run(["readelf", "-W", *args, path], capture_output=True, text=True)
    if p.returncode != 0:
        raise OracleError(f"readelf {args} failed: {p.stderr.strip()[:200]}")
    return p.stdout


def _load_elf(path):
    data = open(path, "rb").read()
    sections = []
    # readelf -S: Nr Name Type Address Off Size ES Flg Lk Inf Al
    for line in _readelf(path, "-S").splitlines():
        line = line.replace("]", " ").replace("[", " ")
        parts = line.split()
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        name, stype = parts[1], parts[2]
        try:
            addr = int(parts[3], 16)
            off = int(parts[4], 16)
            size = int(parts[5], 16)
        except ValueError:
            continue
        # Flags column holds letters like 'AX', 'WA'; find the token made only of flag letters.
        flags = next((t for t in parts[6:] if t and set(t) <= set("WAXMSILOGTCpomDEl")), "")
        if "A" not in flags or addr == 0 or size == 0:
            continue
        body = b"\x00" * size if stype == "NOBITS" else data[off:off + size]
        sections.append({"addr": addr, "data": body, "exec": "X" in flags, "write": "W" in flags})

    syms = {}
    for line in _readelf(path, "-s").splitlines():
        parts = line.split()
        # Num: Value Size Type Bind Vis Ndx Name
        if len(parts) >= 8 and parts[0].rstrip(":").isdigit() and parts[3] in ("FUNC", "IFUNC", "NOTYPE"):
            try:
                val = int(parts[1], 16)
            except ValueError:
                continue
            name = parts[7]
            if name and val and name not in syms:
                syms[name] = val

    def resolve(name):
        if name.startswith("0x"):
            return int(name, 16)
        if name in syms:
            return syms[name]
        raise OracleError(f"no function symbol {name!r} in {path} (have: {', '.join(sorted(syms)[:20])})")

    return sections, resolve


def _load_pe(path):
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    base = pe.OPTIONAL_HEADER.ImageBase
    sections = []
    for s in pe.sections:
        ch = s.Characteristics
        sections.append({
            "addr": base + s.VirtualAddress,
            "data": s.get_data(),
            "exec": bool(ch & 0x20000000),       # IMAGE_SCN_MEM_EXECUTE
            "write": bool(ch & 0x80000000),       # IMAGE_SCN_MEM_WRITE
        })
    exports = {}
    if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            if exp.name:
                exports[exp.name.decode("ascii", "replace")] = base + exp.address

    def resolve(name):
        if name.startswith("0x"):
            return int(name, 16)
        if name in exports:
            return exports[name]
        raise OracleError(f"no exported function {name!r} in {path} (exports: {', '.join(sorted(exports)[:20])})")

    return sections, resolve


# ---------------- the emulator ----------------

class Subject:
    def __init__(self, path, func, abi):
        self.path = path
        self.abi = abi
        self.sections, resolve = load_sections(path)
        self.entry = resolve(func)

    def _fresh_uc(self):
        uc = Uc(UC_ARCH_X86, UC_MODE_64)
        # Map each page once, with the OR of the permissions of every section that
        # touches it, THEN write all section bytes. Mapping per section instead
        # drops a section that shares a page with an earlier one (e.g. .text behind
        # .init), leaving the wrong bytes where the function should be.
        page_prot = {}
        for sec in self.sections:
            start = _align_down(sec["addr"])
            end = _align_up(sec["addr"] + len(sec["data"]))
            prot = UC_PROT_READ
            if sec["exec"]:
                prot |= UC_PROT_EXEC
            if sec["write"]:
                prot |= UC_PROT_WRITE
            for p in range(start, end, PAGE):
                page_prot[p] = page_prot.get(p, 0) | prot
        for p, prot in page_prot.items():
            uc.mem_map(p, PAGE, prot or UC_PROT_READ)
        for sec in self.sections:
            uc.mem_write(sec["addr"], sec["data"])
        uc.mem_map(STACK_ADDR, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        uc.mem_map(SCRATCH_ADDR, SCRATCH_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        uc.mem_map(_align_down(SENTINEL), PAGE, UC_PROT_READ | UC_PROT_EXEC)
        return uc

    def call(self, int_args, outbufs, max_insns):
        """One call. int_args: the register argument values (an outbuf arg's slot
        already holds its scratch address). outbufs: (addr, size) to read back
        after. Returns (rax, [outbuf bytes], error|None)."""
        uc = self._fresh_uc()
        regs = ARG_REGS[self.abi]
        if len(int_args) > len(regs):
            return None, [], f"more arguments ({len(int_args)}) than this ABI passes in registers"
        for reg, val in zip(regs, int_args):
            uc.reg_write(reg, val & 0xFFFFFFFFFFFFFFFF)
        rsp = STACK_ADDR + STACK_SIZE - PAGE
        rsp &= ~0xF
        if self.abi == "win64":
            rsp -= 32          # shadow space the callee may use
        rsp -= 8
        uc.mem_write(rsp, struct.pack("<Q", SENTINEL))
        uc.reg_write(UC_X86_REG_RSP, rsp)

        stray = {"hit": None}

        def on_unmapped(uc_, access, address, size, value, user):
            # A jump/call to an address in no mapped section: an external callee
            # (imported libc). v1 handles leaf functions; report it rather than guess.
            stray["hit"] = address
            uc_.emu_stop()
            return False
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)

        try:
            uc.emu_start(self.entry, SENTINEL, count=max_insns)
        except UcError as e:
            rip = uc.reg_read(UC_X86_REG_RIP)
            if stray["hit"] is not None:
                return None, [], f"jumped to an external callee at {hex(stray['hit'])} (unmapped; a libc import?)"
            if e.errno in (UC_ERR_FETCH_UNMAPPED, UC_ERR_FETCH_PROT):
                return None, [], f"jumped to an external callee at {hex(rip)} (code not in the binary; a libc import?)"
            return None, [], f"emulation fault at {hex(rip)}: {e}"
        if uc.reg_read(UC_X86_REG_RIP) != SENTINEL:
            return None, [], f"did not return within {max_insns} instructions (loop or deep call)"
        rax = uc.reg_read(UC_X86_REG_RAX)
        outs = [bytes(uc.mem_read(addr, size)) for addr, size in outbufs]
        return rax, outs, None


# ---------------- driving the corpus ----------------

def boundary_values(width, signed):
    bits = width * 8
    if signed:
        hi = (1 << (bits - 1)) - 1
        lo = -(1 << (bits - 1))
        return [0, 1, -1, 2, hi, lo, hi - 1, lo + 1]
    return [0, 1, 2, (1 << bits) - 1, (1 << bits) - 2, 1 << (bits - 1)]


def to_reg(value, width, signed):
    mask = (1 << (width * 8)) - 1
    return value & mask


def build_corpus(int_specs, corpus):
    """A list of input tuples (one int per int arg). Explicit seeds, then
    per-arg boundary values (position-wise, padded), then seeded fuzz."""
    tuples = []
    for seed in corpus.get("seeds", []):
        tuples.append(list(seed))
    if corpus.get("boundaries", True) and int_specs:
        per = [boundary_values(s["width"], s.get("signed", True)) for s in int_specs]
        for i in range(max(len(p) for p in per)):
            tuples.append([per[j][i % len(per[j])] for j in range(len(int_specs))])
    fz = corpus.get("fuzz")
    if fz and int_specs:
        rng = random.Random(fz.get("seed", 1234))
        for _ in range(fz.get("count", 0)):
            row = []
            for s in int_specs:
                bits = s["width"] * 8
                if s.get("signed", True):
                    row.append(rng.randrange(-(1 << (bits - 1)), 1 << (bits - 1)))
                else:
                    row.append(rng.randrange(0, 1 << bits))
            tuples.append(row)
    # dedup, cap
    seen, out = set(), []
    for t in tuples:
        key = tuple(t)
        if key not in seen:
            seen.add(key)
            out.append(list(t))
        if len(out) >= corpus.get("max", 5000):
            break
    return out


def place_args(arg_specs, int_row):
    """Turn one corpus row (ints, in the order of the int args) plus the outbuf
    specs into the register argument list and the outbuf (addr,size) list. Scratch
    addresses are assigned left to right."""
    reg_vals, outbufs = [], []
    next_scratch = SCRATCH_ADDR
    it = iter(int_row)
    for a in arg_specs:
        if a["type"] == "int":
            reg_vals.append(to_reg(next(it), a["width"], a.get("signed", True)))
        elif a["type"] == "outbuf":
            size = a["size"]
            reg_vals.append(next_scratch)
            outbufs.append((next_scratch, size))
            next_scratch = _align_up(next_scratch + size)
        else:
            raise OracleError(f"unknown arg type {a['type']!r}")
    return reg_vals, outbufs


def disasm(subject, count=40):
    """Capstone listing of the original function's first `count` instructions, as
    ground truth (address + bytes + mnemonic). Reads from the mapped sections."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    code = None
    for sec in subject.sections:
        if sec["exec"] and sec["addr"] <= subject.entry < sec["addr"] + len(sec["data"]):
            code = sec["data"][subject.entry - sec["addr"]:]
            break
    if code is None:
        return []
    out = []
    for insn in md.disasm(code[:1024], subject.entry):
        out.append({"addr": insn.address, "bytes": insn.bytes.hex(), "text": f"{insn.mnemonic} {insn.op_str}".strip()})
        if len(out) >= count or insn.mnemonic in ("ret", "retq"):
            break
    return out


def main():
    spec = json.loads(open(sys.argv[1]).read())
    abi = spec.get("abi", "sysv")
    arg_specs = spec.get("args", [])
    int_specs = [a for a in arg_specs if a["type"] == "int"]
    ret = spec.get("ret", {"type": "int", "width": 4, "signed": True})
    max_insns = int(spec.get("max_insns", 200000))

    orig = Subject(spec["subjects"]["original"], spec.get("function_original", spec["function"]), abi)
    rebuild = Subject(spec["subjects"]["rebuild"], spec.get("function_rebuild", spec["function"]), abi)

    corpus = build_corpus(int_specs, spec.get("corpus", {}))
    agree = 0
    first_div = None
    errors = {"original": 0, "rebuild": 0}
    err_samples = []
    samples = []        # the original's own output for the first few inputs: an absolute anchor
                        # (the match check is differential, so a harness bug hitting both
                        # subjects equally is invisible to it; a known input->output is not)

    def norm_ret(rax):
        if ret.get("type") == "void":
            return None
        w = ret.get("width", 4)
        v = rax & ((1 << (w * 8)) - 1)
        if ret.get("signed", True) and v >= (1 << (w * 8 - 1)):
            v -= 1 << (w * 8)
        return v

    for row in corpus:
        reg_vals, outbufs = place_args(arg_specs, row)
        ro, oo, eo = orig.call(reg_vals, outbufs, max_insns)
        rr, orr, er = rebuild.call(reg_vals, outbufs, max_insns)
        if eo is None and len(samples) < 8:
            samples.append({"input": row, "ret": norm_ret(ro), "outbufs": [b.hex() for b in oo]})
        if eo or er:
            errors["original"] += bool(eo)
            errors["rebuild"] += bool(er)
            if len(err_samples) < 5:
                err_samples.append({"input": row, "original_error": eo, "rebuild_error": er})
            continue
        same = norm_ret(ro) == norm_ret(rr) and oo == orr
        if same:
            agree += 1
        elif first_div is None:
            first_div = {
                "input": row,
                "original": {"ret": norm_ret(ro), "outbufs": [b.hex() for b in oo]},
                "rebuild": {"ret": norm_ret(rr), "outbufs": [b.hex() for b in orr]},
            }

    tested = len(corpus) - max(errors["original"], errors["rebuild"])
    result = {
        "arch": "x86_64", "abi": abi,
        "function": spec["function"],
        "original_entry": hex(orig.entry), "rebuild_entry": hex(rebuild.entry),
        "corpus_size": len(corpus), "tested": tested, "agreed": agree,
        "match": first_div is None and errors["original"] == 0 and errors["rebuild"] == 0 and agree > 0,
        "first_divergence": first_div,
        "errors": errors, "error_samples": err_samples,
        "samples": samples,
        "original_disasm": disasm(orig),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
