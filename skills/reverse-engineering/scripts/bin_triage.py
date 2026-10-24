#!/usr/bin/env python3
"""
Triage a compiled binary without running any native tool on it: the format
(ELF, PE, Mach-O, or other), architecture, bitness and endianness, entry point,
the dynamic loader it asks for, the shared libraries it needs, its sections, a
build id, and the readable strings in it.

  python bin_triage.py program                 # summary
  python bin_triage.py program --out t.json     # the full report as JSON
  python bin_triage.py program --strings --min 8 --limit 200   # more strings

This is to a binary what wasm_inspect's own parser is to a .wasm and what
fingerprint.py is to a web page: a bounded, pure-Python reader in memory-safe
code, so it is safe to run on anything, including a file you do not trust. It
produces ground truth (what is literally in the bytes) and nothing more; what a
function does and what the program does at runtime are for r2.py, ghidra.py and
the sandbox. It also stands as an independent second reading of the same facts
radare2 reports (two differently derived sources), which the verification spine
counts as its own kind of proof.

Every read is bounds-checked, every table and string list is capped, and a
malformed field raises rather than looping, so a hostile file cannot hang it or
walk it off the end of the buffer.
"""
import argparse
import json
import mmap
import os
import re
import sys
import tempfile

# Caps: a hostile file cannot make any of these unbounded.
MAX_SEGMENTS = 4096
MAX_SECTIONS = 4096
MAX_DYNAMIC = 8192
MAX_IMPORTS = 4096
MAX_LOADCMDS = 4096
MAX_STRINGS = 300
MIN_STRING = 6
MAX_RUN = 8192            # a longer printable run is reported as consecutive pieces
MAX_NAME = 4096           # a single null-terminated name is never longer than this

ELF_MACHINES = {
    0x00: "none", 0x02: "SPARC", 0x03: "x86", 0x08: "MIPS", 0x14: "PPC", 0x15: "PPC64",
    0x16: "S390", 0x28: "ARM", 0x2A: "SuperH", 0x32: "IA-64", 0x3E: "x86-64", 0x66: "CRIS",
    0xB7: "AArch64", 0xF3: "RISC-V", 0x101: "WDC 65C816", 0xBA: "OpenRISC",
}
ELF_TYPES = {0: "NONE", 1: "REL (object)", 2: "EXEC (executable)", 3: "DYN (shared/PIE)", 4: "CORE"}
ELF_OSABI = {0: "System V", 3: "Linux", 6: "Solaris", 9: "FreeBSD", 12: "OpenBSD", 255: "standalone"}
MACHO_CPU = {7: "x86", 0x01000007: "x86-64", 12: "ARM", 0x0100000C: "ARM64", 0x0200000C: "ARM64_32",
             18: "PPC", 0x01000012: "PPC64"}
# An ARM64 variant is named by the cpusubtype, never the cputype (mach/machine.h); the
# subtype's top byte is feature flags (CPU_SUBTYPE_MASK), such as arm64e's pointer
# authentication ABI bit. Names follow lipo's: arm64e.x1 is the third slice of /bin/ls.
MACHO_ARM64_SUBTYPES = {0: "ARM64", 1: "ARM64", 2: "ARM64e", 3: "ARM64.x1", 12: "ARM64e.x1"}
MACHO_TYPES = {1: "OBJECT", 2: "EXECUTE", 3: "FVMLIB", 4: "CORE", 5: "PRELOAD", 6: "DYLIB", 7: "DYLINKER",
               8: "BUNDLE", 10: "DSYM"}
PE_MACHINES = {0x014C: "x86", 0x8664: "x86-64", 0x01C0: "ARM", 0xAA64: "ARM64", 0x01C4: "ARMNT",
               0x0200: "IA-64", 0x0EBC: "EFI byte code"}
PE_SUBSYSTEMS = {1: "native", 2: "GUI", 3: "console", 5: "OS/2", 7: "POSIX", 9: "Windows CE",
                 10: "EFI application", 12: "EFI runtime driver", 14: "Xbox"}


class Reader:
    """Absolute, bounds-checked reads over a bytes buffer. Raises BinError on any
    overrun; `end` sets the inclusive upper bound for a given view (a section's
    slice), so one malformed section header cannot read into the next."""

    def __init__(self, data, little=True, end=None):
        self.d = data
        self.little = little
        self.end = len(data) if end is None else min(end, len(data))

    def _chk(self, off, n):
        if off < 0 or n < 0 or off + n > self.end:
            raise BinError(f"read of {n} byte(s) at {off} overruns the buffer (end {self.end})")

    def u(self, off, n):
        self._chk(off, n)
        return int.from_bytes(self.d[off:off + n], "little" if self.little else "big")

    def u16(self, off):
        return self.u(off, 2)

    def u32(self, off):
        return self.u(off, 4)

    def u64(self, off):
        return self.u(off, 8)

    def cstr(self, off, cap=MAX_NAME):
        """A null-terminated string starting at off, read as UTF-8 (lossy), capped."""
        if off < 0 or off >= self.end:
            raise BinError(f"string at {off} is outside the buffer")
        nul = self.d.find(b"\x00", off, min(off + cap, self.end))
        raw = self.d[off:nul if nul != -1 else min(off + cap, self.end)]
        return raw.decode("utf-8", errors="replace")


class BinError(ValueError):
    pass


def detect_format(data):
    if data[:4] == b"\x7fELF":
        return "elf"
    if data[:2] == b"MZ":
        return "pe"            # confirmed later by the PE signature; MZ alone is DOS/PE
    if data[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "macho"
    if data[:4] in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",      # fat_arch (32-bit offsets)
                    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):     # fat_arch_64
        return "macho-fat"
    return "other"


def extract_strings(data, min_len=MIN_STRING, cap=MAX_STRINGS):
    """Runs of printable ASCII of at least min_len, with their byte offset, up to the cap.
    The regex walks the bytes or the mmap in place (nothing is copied but each match),
    so no window seam can cut a string; 4 MB windows once reported a string over a seam
    cut at the seam. A run longer than MAX_RUN comes out as consecutive pieces, each at
    its own true offset."""
    pat = re.compile(rb"[\x20-\x7e]{%d,%d}" % (max(1, int(min_len)), MAX_RUN))
    out = []
    for m in pat.finditer(data):
        out.append({"offset": m.start(), "text": m.group().decode("ascii")})
        if len(out) >= cap:
            break
    return out


# ---------------- ELF ----------------

def parse_elf(data):
    r = Reader(data, little=(data[5] != 2))
    is64 = data[4] == 2
    rep = {"format": "elf", "bits": 64 if is64 else 32, "endianness": "little" if r.little else "big",
           "os_abi": ELF_OSABI.get(data[7], f"0x{data[7]:02x}"),
           "type": ELF_TYPES.get(r.u16(16), f"0x{r.u16(16):04x}"),
           "arch": ELF_MACHINES.get(r.u16(18), f"machine 0x{r.u16(18):04x}"),
           "segments": [], "sections": [], "needed": [], "soname": None, "runpath": [], "build_id": None,
           "interpreter": None, "errors": []}
    if is64:
        rep["entry"] = r.u64(24)
        phoff, shoff = r.u64(32), r.u64(40)
        phentsize, phnum = r.u16(54), r.u16(56)
        shentsize, shnum, shstrndx = r.u16(58), r.u16(60), r.u16(62)
    else:
        rep["entry"] = r.u32(24)
        phoff, shoff = r.u32(28), r.u32(32)
        phentsize, phnum = r.u16(42), r.u16(44)
        shentsize, shnum, shstrndx = r.u16(46), r.u16(48), r.u16(50)

    # Program headers: find the interpreter.
    for i in range(min(phnum, MAX_SEGMENTS)):
        base = phoff + i * phentsize
        try:
            if is64:
                p_type, p_offset, p_filesz = r.u32(base), r.u64(base + 8), r.u64(base + 32)
            else:
                p_type, p_offset, p_filesz = r.u32(base), r.u32(base + 4), r.u32(base + 16)
        except BinError as e:
            rep["errors"].append(f"program header {i}: {e}")
            break
        kind = {1: "LOAD", 2: "DYNAMIC", 3: "INTERP", 4: "NOTE", 6: "PHDR", 7: "TLS",
                0x6474E550: "GNU_EH_FRAME", 0x6474E551: "GNU_STACK", 0x6474E552: "GNU_RELRO"}.get(p_type, f"0x{p_type:x}")
        rep["segments"].append(kind)
        if p_type == 3 and 0 < p_filesz < MAX_NAME:   # PT_INTERP
            try:
                rep["interpreter"] = Reader(data, end=p_offset + p_filesz).cstr(p_offset)
            except BinError as e:
                rep["errors"].append(f"PT_INTERP: {e}")

    # Section headers: names from the section-header string table.
    sections = []
    shstr_off = None
    if shnum and shstrndx < shnum:
        try:
            shstr_off = (r.u64(shoff + shstrndx * shentsize + 24) if is64
                         else r.u32(shoff + shstrndx * shentsize + 16))
        except BinError as e:
            rep["errors"].append(f"section string table: {e}")
    for i in range(min(shnum, MAX_SECTIONS)):
        base = shoff + i * shentsize
        try:
            if is64:
                sh = {"name_off": r.u32(base), "type": r.u32(base + 4), "offset": r.u64(base + 24),
                      "size": r.u64(base + 32), "link": r.u32(base + 40), "entsize": r.u64(base + 56)}
            else:
                sh = {"name_off": r.u32(base), "type": r.u32(base + 4), "offset": r.u32(base + 16),
                      "size": r.u32(base + 20), "link": r.u32(base + 24), "entsize": r.u32(base + 36)}
        except BinError as e:
            rep["errors"].append(f"section header {i}: {e}")
            break
        name = ""
        if shstr_off is not None:
            try:
                name = Reader(data).cstr(shstr_off + sh["name_off"])
            except BinError:
                name = f"<name@{sh['name_off']}>"
        sh["name"] = name
        sections.append(sh)
        rep["sections"].append({"name": name, "size": sh["size"], "offset": sh["offset"]})

    _elf_dynamic(data, r, is64, sections, rep)
    _elf_build_id(data, r, sections, rep)
    return rep


def _elf_dynamic(data, r, is64, sections, rep):
    dynamic = next((s for s in sections if s["type"] == 6), None)   # SHT_DYNAMIC
    if not dynamic:
        return
    # The string table for the dynamic entries is the section .dynamic links to.
    strtab_off = None
    if dynamic["link"] < len(sections):
        strtab_off = sections[dynamic["link"]]["offset"]
    step = 16 if is64 else 8
    count = min(dynamic["size"] // step, MAX_DYNAMIC)
    needed_offs, soname_off, runpath_offs = [], None, []
    for i in range(count):
        base = dynamic["offset"] + i * step
        try:
            tag = r.u64(base) if is64 else r.u32(base)
            val = r.u64(base + 8) if is64 else r.u32(base + 4)
        except BinError as e:
            rep["errors"].append(f"dynamic entry {i}: {e}")
            break
        if tag == 0:            # DT_NULL
            break
        if tag == 1:            # DT_NEEDED
            needed_offs.append(val)
        elif tag == 14:         # DT_SONAME
            soname_off = val
        elif tag in (15, 29):   # DT_RPATH, DT_RUNPATH
            runpath_offs.append(val)
    if strtab_off is not None:
        sr = Reader(data)
        for off in needed_offs[:MAX_IMPORTS]:
            try:
                rep["needed"].append(sr.cstr(strtab_off + off))
            except BinError:
                pass
        for off in runpath_offs:
            if len(rep["runpath"]) >= MAX_IMPORTS:   # 8192 entries of "a:a:..." once made 16 million
                break
            try:
                parts = [p for p in sr.cstr(strtab_off + off).split(":") if p]
            except BinError:
                continue
            rep["runpath"].extend(parts[:MAX_IMPORTS - len(rep["runpath"])])
        if soname_off is not None:
            try:
                rep["soname"] = sr.cstr(strtab_off + soname_off)
            except BinError:
                pass


def _elf_build_id(data, r, sections, rep):
    note = next((s for s in sections if s["name"] == ".note.gnu.build-id"), None)
    if not note:
        return
    try:
        nr = Reader(data, little=r.little, end=note["offset"] + note["size"])
        base = note["offset"]
        namesz, descsz, ntype = nr.u32(base), nr.u32(base + 4), nr.u32(base + 8)
        name_start = base + 12
        desc_start = name_start + ((namesz + 3) & ~3)
        if ntype == 3 and descsz and desc_start + descsz <= nr.end:
            rep["build_id"] = data[desc_start:desc_start + descsz].hex()
    except BinError as e:
        rep["errors"].append(f"build-id note: {e}")


# ---------------- PE ----------------

def parse_pe(data):
    r = Reader(data, little=True)
    rep = {"format": "pe", "sections": [], "needed": [], "errors": []}
    try:
        pe_off = r.u32(0x3C)
        if data[pe_off:pe_off + 4] != b"PE\x00\x00":
            return {"format": "dos-mz", "note": "MZ header but no PE signature (DOS stub or non-PE)", "errors": []}
        coff = pe_off + 4
        machine = r.u16(coff)
        nsect = r.u16(coff + 2)
        opt_size = r.u16(coff + 16)
        chars = r.u16(coff + 18)
        opt = coff + 20
        magic = r.u16(opt)
        pe64 = magic == 0x20B
        rep["bits"] = 64 if pe64 else 32
        rep["arch"] = PE_MACHINES.get(machine, f"machine 0x{machine:04x}")
        rep["type"] = "DLL" if (chars & 0x2000) else ("executable" if (chars & 0x0002) else "object")
        rep["entry"] = r.u32(opt + 16)
        subsys = r.u16(opt + 68)
        rep["subsystem"] = PE_SUBSYSTEMS.get(subsys, f"0x{subsys:04x}")
        nrva_off = opt + (108 if pe64 else 92)
        datadir = opt + (112 if pe64 else 96)
        nrva = r.u32(nrva_off)

        sect_tab = opt + opt_size
        sections = []
        for i in range(min(nsect, MAX_SECTIONS)):
            b = sect_tab + i * 40
            try:
                name = r.cstr(b, cap=8).rstrip("\x00") or r.d[b:b + 8].rstrip(b"\x00").decode("latin-1")
                vsize, vaddr, rawsize, rawptr = r.u32(b + 8), r.u32(b + 12), r.u32(b + 16), r.u32(b + 20)
            except BinError as e:
                rep["errors"].append(f"section {i}: {e}")
                break
            sections.append({"name": name, "vaddr": vaddr, "vsize": vsize, "rawptr": rawptr, "rawsize": rawsize})
            rep["sections"].append({"name": name, "size": rawsize, "offset": rawptr})

        if nrva > 1:            # DataDirectory[1] = import table
            imp_rva = r.u32(datadir + 8)
            if imp_rva:
                _pe_imports(r, sections, imp_rva, rep)
    except BinError as e:
        rep["errors"].append(str(e))
    return rep


def _rva_to_off(sections, rva):
    for s in sections:
        if s["vaddr"] <= rva < s["vaddr"] + max(s["vsize"], s["rawsize"]):
            return s["rawptr"] + (rva - s["vaddr"])
    return None


def _pe_imports(r, sections, imp_rva, rep):
    base = _rva_to_off(sections, imp_rva)
    if base is None:
        rep["errors"].append("import table RVA maps to no section")
        return
    for i in range(MAX_IMPORTS):
        desc = base + i * 20          # IMAGE_IMPORT_DESCRIPTOR
        try:
            name_rva = r.u32(desc + 12)
            orig_thunk = r.u32(desc)
        except BinError:
            break
        if name_rva == 0 and orig_thunk == 0:
            break                     # the null terminating descriptor
        off = _rva_to_off(sections, name_rva)
        if off is not None:
            try:
                rep["needed"].append(r.cstr(off))
            except BinError:
                pass


# ---------------- Mach-O ----------------

def macho_arch(cputype, cpusubtype):
    if cputype == 0x0100000C:
        sub = cpusubtype & 0x00FFFFFF
        return MACHO_ARM64_SUBTYPES.get(sub, f"ARM64 (subtype {sub})")
    return MACHO_CPU.get(cputype, f"cputype 0x{cputype:08x}")


def parse_macho(data, fmt):
    if fmt == "macho-fat":
        return _parse_macho_fat(data)
    # The non-swapped magic (FE ED FA CE/CF on disk) is a big-endian file; the
    # byte-swapped magic (CE/CF FA ED FE) is little-endian, which is every Intel
    # and Apple-silicon Mach-O, so getting this backwards mis-reads the common case.
    big = data[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf")
    is64 = data[:4] in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")
    r = Reader(data, little=not big)
    cputype, cpusubtype = r.u32(4), r.u32(8)
    rep = {"format": "mach-o", "bits": 64 if is64 else 32, "endianness": "big" if big else "little",
           "arch": macho_arch(cputype, cpusubtype), "cputype": cputype, "cpusubtype": cpusubtype,
           "type": MACHO_TYPES.get(r.u32(12), f"0x{r.u32(12):x}"),
           "needed": [], "load_commands": {}, "errors": []}
    ncmds = r.u32(16)
    off = 32 if is64 else 28
    for _ in range(min(ncmds, MAX_LOADCMDS)):
        try:
            cmd, cmdsize = r.u32(off), r.u32(off + 4)
        except BinError as e:
            rep["errors"].append(f"load command at {off}: {e}")
            break
        if cmdsize < 8:
            rep["errors"].append(f"load command at {off}: size {cmdsize} too small")
            break
        cname = {0x19: "SEGMENT_64", 0x1: "SEGMENT", 0xC: "LOAD_DYLIB", 0x80000018: "LOAD_WEAK_DYLIB",
                 0x18: "LOAD_WEAK_DYLIB", 0xD: "ID_DYLIB", 0x80000028: "MAIN", 0xE: "LOAD_DYLINKER",
                 0x1B: "UUID", 0x2: "SYMTAB", 0x22: "DYLD_INFO_ONLY"}.get(cmd, f"0x{cmd:x}")
        rep["load_commands"][cname] = rep["load_commands"].get(cname, 0) + 1
        if cmd in (0xC, 0x18, 0x80000018) and len(rep["needed"]) < MAX_IMPORTS:
            try:
                name_off = r.u32(off + 8)        # dylib.name is an lc_str offset from the command start
                if 8 <= name_off < cmdsize:
                    rep["needed"].append(Reader(data, little=not big, end=off + cmdsize).cstr(off + name_off))
            except BinError as e:
                rep["errors"].append(f"dylib name at {off}: {e}")
        off += cmdsize
    return rep


def _parse_macho_fat(data):
    # Non-swapped magic (CA FE BA BE / BF) is big-endian, which fat headers are by
    # convention; the ...BF form is fat_arch_64 (64-bit offset and size fields).
    magic = data[:4]
    is64 = magic in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
    big = magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
    r = Reader(data, little=not big)
    nfat = r.u32(4)
    step = 32 if is64 else 20
    slices = []
    for i in range(min(nfat, 64)):
        base = 8 + i * step
        try:
            cputype, cpusubtype = r.u32(base), r.u32(base + 4)
            if is64:
                offset, size, align = r.u64(base + 8), r.u64(base + 16), r.u32(base + 24)
            else:
                offset, size, align = r.u32(base + 8), r.u32(base + 12), r.u32(base + 16)
        except BinError:
            break
        slices.append({"arch": macho_arch(cputype, cpusubtype), "cputype": cputype, "cpusubtype": cpusubtype,
                       "offset": offset, "size": size, "align": align,
                       "in_bounds": 0 <= offset and offset + size <= len(data)})
    return {"format": "mach-o (fat/universal)", "fat64": is64, "arches": [s["arch"] for s in slices],
            "slices": slices, "needed": [], "errors": []}


def macho_slice(data, selector):
    """(offset, size, arch) of the fat/universal slice named by arch (e.g. 'x86-64',
    'ARM64') or by index. Raises BinError if the file is not fat, the slice is
    missing, or it points outside the file."""
    if detect_format(data) != "macho-fat":
        raise BinError("not a fat/universal Mach-O")
    slices = _parse_macho_fat(data)["slices"]
    if not slices:
        raise BinError("fat Mach-O lists no slices")
    sel = str(selector)
    if sel.isdigit():
        i = int(sel)
        if not 0 <= i < len(slices):
            raise BinError(f"slice index {i} out of range (0..{len(slices) - 1})")
        s = slices[i]
    else:
        matches = [s for s in slices if s["arch"] == sel]
        if not matches:
            raise BinError(f"no {sel!r} slice (have: {', '.join(s['arch'] for s in slices)})")
        s = matches[0]
    if not s["in_bounds"]:
        raise BinError(f"slice {s['arch']} at {s['offset']}+{s['size']} runs past the {len(data)}-byte file")
    return s["offset"], s["size"], s["arch"]


def write_slice(path, selector):
    """Carve the named fat/universal slice to a temporary thin Mach-O and return
    (temp_path, arch). The caller deletes the temp. Used by r2.py/ghidra.py --slice
    so a universal binary can be analysed one arch at a time."""
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        off, size, arch = macho_slice(mm, selector)
        fd, tmp = tempfile.mkstemp(prefix="re-slice-")
        with os.fdopen(fd, "wb") as out:
            out.write(mm[off:off + size])
    return tmp, arch


# ---------------- dispatch + output ----------------

def inspect(data, want_strings=False, min_len=MIN_STRING, limit=MAX_STRINGS):
    fmt = detect_format(data)
    try:
        if fmt == "elf":
            rep = parse_elf(data)
        elif fmt == "pe":
            rep = parse_pe(data)
        elif fmt in ("macho", "macho-fat"):
            rep = parse_macho(data, fmt)
        else:
            rep = {"format": "other", "note": "not ELF, PE or Mach-O", "magic": data[:8].hex(), "errors": []}
    except BinError as e:
        rep = {"format": fmt, "errors": [f"parse failed: {e}"], "magic": data[:8].hex()}
    rep["size"] = len(data)
    if want_strings:
        rep["strings"] = extract_strings(data, min_len, limit)
    return rep


def print_summary(rep, limit):
    f = rep.get("format", "?")
    line = f"{f}"
    for k in ("bits", "arch", "endianness", "type"):
        if rep.get(k) is not None:
            line += f"  {rep[k]}" if k != "bits" else f"  {rep[k]}-bit"
    print(line + f"  ({rep.get('size', 0)} bytes)")
    if rep.get("slices"):
        print(f"  universal binary, {len(rep['slices'])} slice(s)" + (" (fat64)" if rep.get("fat64") else "") + ":")
        for s in rep["slices"]:
            oob = "" if s["in_bounds"] else "  OUT-OF-BOUNDS"
            print(f"    {s['arch']:<12} offset {s['offset']} size {s['size']} (align 2^{s['align']}){oob}")
    elif "arches" in rep:
        print("  universal binary, slices: " + ", ".join(rep.get("arches", [])))
    if rep.get("entry") is not None:
        print(f"  entry: 0x{rep['entry']:x}")
    if rep.get("os_abi"):
        print(f"  OS/ABI: {rep['os_abi']}")
    if rep.get("subsystem"):
        print(f"  subsystem: {rep['subsystem']}")
    if rep.get("interpreter"):
        print(f"  interpreter (dynamic loader): {rep['interpreter']}")
    if rep.get("soname"):
        print(f"  soname: {rep['soname']}")
    if rep.get("build_id"):
        print(f"  build id: {rep['build_id']}")
    if rep.get("runpath"):
        print(f"  runpath: {', '.join(rep['runpath'])}")
    if rep.get("needed"):
        print(f"  needs ({len(rep['needed'])}): " + ", ".join(rep["needed"][:limit]))
    if rep.get("load_commands"):
        print("  load commands: " + ", ".join(f"{k}x{v}" for k, v in rep["load_commands"].items()))
    secs = rep.get("sections") or []
    if secs:
        print(f"  sections ({len(secs)}): " + ", ".join(s["name"] for s in secs[:limit] if s["name"]))
    if rep.get("segments"):
        print("  segments: " + ", ".join(rep["segments"]))
    if rep.get("strings") is not None:
        print(f"  strings ({len(rep['strings'])}):")
        for s in rep["strings"][:min(limit, 40)]:
            print(f"    @{s['offset']}: {s['text']!r}")
    for e in rep.get("errors", []):
        print(f"  error: {e}")
    if rep.get("note"):
        print(f"  note: {rep['note']}")


def main():
    ap = argparse.ArgumentParser(description="Triage a compiled binary (ELF/PE/Mach-O) with a pure-Python reader.")
    ap.add_argument("file", help="Path to the binary.")
    ap.add_argument("--out", help="Write the full report as JSON here.")
    ap.add_argument("--strings", action="store_true", help="Include readable strings in the report.")
    ap.add_argument("--min", type=int, default=MIN_STRING, help="Minimum string length (default 6).")
    ap.add_argument("--limit", type=int, default=40, help="Rows per list in the summary.")
    ap.add_argument("--slice", metavar="ARCH", help="A fat/universal Mach-O slice to act on (arch name or index).")
    ap.add_argument("--write-slice", metavar="FILE", help="With --slice: write that thin Mach-O slice out here.")
    args = ap.parse_args()

    if args.slice:
        try:
            with open(args.file, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                off, size, arch = macho_slice(mm, args.slice)
                print(f"slice {arch}: offset {off}, size {size}")
                if args.write_slice:
                    with open(args.write_slice, "wb") as out:
                        out.write(mm[off:off + size])
                    print(f"Wrote {size} bytes to {args.write_slice}")
        except (BinError, OSError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    try:
        if os.path.getsize(args.file) < 8:
            print("File is too small to be a binary (under 8 bytes).", file=sys.stderr)
            sys.exit(2)
        # mmap, so header parsing pages in only what it touches and a large file is
        # never copied into the bot's memory (it shares the bot's cgroup).
        with open(args.file, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            rep = inspect(mm, want_strings=args.strings or bool(args.out), min_len=args.min,
                          limit=args.limit if not args.out else MAX_STRINGS)
    except OSError as e:
        print(f"Read failed: {e}", file=sys.stderr)
        sys.exit(1)

    print_summary(rep, args.limit)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
        print(f"Wrote {args.out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
