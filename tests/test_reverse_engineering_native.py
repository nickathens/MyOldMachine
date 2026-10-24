"""reverse-engineering skill, Tier 2 (native) and Tier 3 (carving).

Three kinds of evidence, matching the verification spine:
  - independent corroboration: bin_triage's pure-Python reading is checked
    against readelf/file/objdump, and the instruction bytes r2, Ghidra and
    objdump report for the same function must be byte-identical (three tools,
    one ground truth).
  - bounded safety: malformed or hostile headers must not crash, hang, or
    over-read; the caps hold.
  - the carve false-positive fix is pinned: the TrueType magic must not fire on
    ordinary data, and an 'MZ' is only a PE when its header says so.

The container tests skip when Docker or the pinned image is not present; the
Ghidra and cross-tool tests also need gcc to build a tiny subject binary.
"""
import importlib.util
import io
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"
FIXTURES = ROOT / "fixtures" / "reverse_engineering"
IS_LINUX = sys.platform.startswith("linux")


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bt = load("bin_triage")
r2 = load("r2")
gh = load("ghidra")
carve = load("carve")
sbx = load("sandbox")

DOCKER_OK, DOCKER_WHY = sbx.docker_available()
GCC = shutil.which("gcc")


def _image_present(dockerfile):
    if not DOCKER_OK:
        return False
    tag = sbx.image_tag(dockerfile)
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


NATIVE_OK = _image_present(sbx.NATIVE_DOCKERFILE)
GHIDRA_OK = _image_present(sbx.GHIDRA_DOCKERFILE)
CARVE_OK = _image_present(sbx.CARVE_DOCKERFILE)


def build_c(src, cflags=("-O0", "-g")):
    """Compile C to a temp ELF, return its path (inside a dir the caller cleans)."""
    d = tempfile.mkdtemp(prefix="re-native-test-")
    cpath, out = os.path.join(d, "p.c"), os.path.join(d, "p")
    Path(cpath).write_text(src)
    subprocess.run([GCC, *cflags, "-o", out, cpath], check=True, capture_output=True)
    return d, out


def objdump_func_bytes(binpath, func):
    """Concatenated instruction bytes of one function, from objdump -d. Ground truth."""
    p = subprocess.run(["objdump", "-d", "--insn-width=16", binpath], capture_output=True, text=True)
    out, grab, hexes = p.stdout, False, []
    for line in out.splitlines():
        if re.match(r"^[0-9a-f]+ <%s>:" % re.escape(func), line):
            grab = True
            continue
        if grab:
            if not line.strip() or re.match(r"^[0-9a-f]+ <", line):
                break
            m = re.match(r"^\s*[0-9a-f]+:\t([0-9a-f ]+?)\t", line)
            if m:
                hexes.append(m.group(1).replace(" ", ""))
    return "".join(hexes)


# ---------------- bin_triage: independent corroboration ----------------

@unittest.skipUnless(IS_LINUX and shutil.which("readelf"), "not Linux, or readelf not present")
class BinTriageELFCorroboration(unittest.TestCase):
    def test_bin_ls_matches_readelf(self):
        rep = bt.inspect(Path("/bin/ls").read_bytes())
        hd = subprocess.run(["readelf", "-hd", "/bin/ls"], capture_output=True, text=True).stdout
        self.assertEqual(rep["format"], "elf")
        self.assertEqual(rep["bits"], 64)
        self.assertEqual(rep["arch"], "x86-64")
        self.assertIn("DYN", rep["type"])
        entry = int(re.search(r"Entry point address:\s+0x([0-9a-f]+)", hd).group(1), 16)
        self.assertEqual(rep["entry"], entry)
        needed_readelf = set(re.findall(r"Shared library: \[([^\]]+)\]", hd))
        self.assertEqual(set(rep["needed"]), needed_readelf)
        self.assertEqual(rep["interpreter"], "/lib64/ld-linux-x86-64.so.2")
        notes = subprocess.run(["readelf", "-n", "/bin/ls"], capture_output=True, text=True).stdout
        bid = re.search(r"Build ID: ([0-9a-f]+)", notes)
        if bid:
            self.assertEqual(rep["build_id"], bid.group(1))


@unittest.skipUnless(shutil.which("file"), "file(1) not present")
class BinTriagePECorroboration(unittest.TestCase):
    def setUp(self):
        # The bundled fixture is a real PE32 .NET assembly; fall back to a host
        # Godot's bundled DLLs if the fixture is ever missing.
        bundled = FIXTURES / "Sample.dll"
        if bundled.is_file():
            self.sample = str(bundled)
            return
        matches = list(Path("/snap/godot-4").glob("*/GodotSharp/Tools/*.dll"))
        if not matches:
            self.skipTest("no PE sample available")
        self.sample = str(matches[0])

    def test_pe_matches_file_tool(self):
        rep = bt.inspect(Path(self.sample).read_bytes())
        ft = subprocess.run(["file", self.sample], capture_output=True, text=True).stdout
        self.assertEqual(rep["format"], "pe")
        self.assertIn("PE32", ft)
        self.assertEqual(rep["bits"], 64 if "PE32+" in ft else 32)
        if "(DLL)" in ft:
            self.assertEqual(rep["type"], "DLL")


# ---------------- bin_triage: synthetic (deterministic ground truth) ----------------

class BinTriageSynthetic(unittest.TestCase):
    def _elf64(self, machine=0x3E, etype=2, entry=0x401000):
        h = bytearray(64)
        h[0:4] = b"\x7fELF"
        h[4], h[5], h[6], h[7] = 2, 1, 1, 3          # 64-bit, LE, v1, Linux
        struct.pack_into("<H", h, 16, etype)
        struct.pack_into("<H", h, 18, machine)
        struct.pack_into("<Q", h, 24, entry)
        # phoff/shoff 0, counts 0: a header-only ELF, enough to read the identity fields.
        struct.pack_into("<H", h, 54, 56)             # e_phentsize
        struct.pack_into("<H", h, 58, 64)             # e_shentsize
        return bytes(h)

    def test_reads_elf_identity_fields(self):
        rep = bt.inspect(self._elf64(machine=0xB7, entry=0xDEAD0))
        self.assertEqual((rep["format"], rep["bits"], rep["endianness"]), ("elf", 64, "little"))
        self.assertEqual(rep["arch"], "AArch64")
        self.assertEqual(rep["entry"], 0xDEAD0)
        self.assertIn("EXEC", rep["type"])          # _elf64 sets e_type = 2 (ET_EXEC)

    def _pe(self, machine, chars, pe64):
        opt_size = 240 if pe64 else 224
        buf = bytearray(0x44 + 20 + opt_size)
        buf[0:2] = b"MZ"
        struct.pack_into("<I", buf, 0x3C, 0x40)
        buf[0x40:0x44] = b"PE\x00\x00"
        struct.pack_into("<HHIIIHH", buf, 0x44, machine, 0, 0, 0, 0, opt_size, chars)
        opt = 0x44 + 20
        struct.pack_into("<H", buf, opt, 0x20B if pe64 else 0x10B)
        struct.pack_into("<I", buf, opt + 16, 0x1234)                      # AddressOfEntryPoint
        struct.pack_into("<H", buf, opt + 68, 3)                           # console
        struct.pack_into("<I", buf, opt + (108 if pe64 else 92), 16)       # NumberOfRvaAndSizes
        return bytes(buf)

    def test_pe_exe_and_dll_are_told_apart(self):
        # The only real PE on this machine is a DLL, so a check that called everything a DLL passed.
        exe = bt.inspect(self._pe(0x8664, 0x0022, True))      # EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE
        dll = bt.inspect(self._pe(0x014C, 0x2102, False))     # DLL | 32BIT_MACHINE | EXECUTABLE_IMAGE
        self.assertEqual((exe["type"], exe["bits"], exe["arch"], exe["entry"], exe["subsystem"]),
                         ("executable", 64, "x86-64", 0x1234, "console"))
        self.assertEqual((dll["type"], dll["bits"], dll["arch"]), ("DLL", 32, "x86"))

    def test_macho_thin_dylib(self):
        name = b"/usr/lib/libSystem.B.dylib\x00"
        name = name + b"\x00" * ((-len(name)) % 4)
        lc = struct.pack("<IIIIII", 0x0C, 24 + len(name), 24, 0, 0x10000, 0x10000) + name
        hdr = struct.pack("<IIIIIII", 0xFEEDFACF, 0x01000007, 3, 2, 1, len(lc), 0) + b"\x00" * 4
        rep = bt.inspect(hdr + lc)
        self.assertEqual(rep["format"], "mach-o")
        self.assertEqual(rep["arch"], "x86-64")
        self.assertEqual(rep["type"], "EXECUTE")
        self.assertIn("/usr/lib/libSystem.B.dylib", rep["needed"])


def _thin_macho(cpu):
    """A minimal 64-bit thin Mach-O (little-endian) with one LOAD_DYLIB command."""
    name = b"/usr/lib/libSystem.B.dylib\x00"
    name += b"\x00" * ((-len(name)) % 4)
    lc = struct.pack("<IIIIII", 0x0C, 24 + len(name), 24, 0, 0x10000, 0x10000) + name
    hdr = struct.pack("<IIIIIII", 0xFEEDFACF, cpu, 3, 2, 1, len(lc), 0) + b"\x00" * 4
    return hdr + lc


def _fat_macho(slices, fat64=False, align=0x4000):
    """A fat/universal Mach-O wrapping the given (cputype, thin-bytes) slices."""
    magic = 0xCAFEBABF if fat64 else 0xCAFEBABE
    entry = 32 if fat64 else 20
    head_len = 8 + entry * len(slices)
    out = bytearray(struct.pack(">II", magic, len(slices)))
    placed, off = [], ((head_len + align - 1) // align) * align
    for cpu, body in slices:
        placed.append((cpu, off, body))
        off = ((off + len(body) + align - 1) // align) * align
    for cpu, o, body in placed:
        if fat64:
            out += struct.pack(">IIQQII", cpu, 3, o, len(body), 14, 0)
        else:
            out += struct.pack(">IIIII", cpu, 3, o, len(body), 14)
    buf = bytearray(out)
    for _cpu, o, body in placed:
        buf += b"\x00" * (o - len(buf))
        buf += body
    return bytes(buf)


class FatMachO(unittest.TestCase):
    X86_64, ARM64 = 0x01000007, 0x0100000C

    def setUp(self):
        self.s1, self.s2 = _thin_macho(self.X86_64), _thin_macho(self.ARM64)
        self.fat = _fat_macho([(self.X86_64, self.s1), (self.ARM64, self.s2)])

    def test_lists_slices_with_offsets_and_sizes(self):
        rep = bt.inspect(self.fat)
        self.assertEqual(rep["format"], "mach-o (fat/universal)")
        self.assertEqual([s["arch"] for s in rep["slices"]], ["x86-64", "ARM64"])
        for s in rep["slices"]:
            self.assertTrue(s["in_bounds"])
            self.assertEqual(self.fat[s["offset"]:s["offset"] + s["size"]],
                             self.s1 if s["arch"] == "x86-64" else self.s2)

    def test_fat64_offsets(self):
        fat = _fat_macho([(self.X86_64, self.s1), (self.ARM64, self.s2)], fat64=True)
        rep = bt.inspect(fat)
        self.assertTrue(rep["fat64"])
        self.assertEqual({s["arch"] for s in rep["slices"]}, {"x86-64", "ARM64"})
        off, size, _ = bt.macho_slice(fat, "ARM64")
        self.assertEqual(fat[off:off + size], self.s2)

    def test_macho_slice_by_arch_and_index(self):
        self.assertEqual(self.fat[slice(*self._span(bt.macho_slice(self.fat, "x86-64")))], self.s1)
        self.assertEqual(self.fat[slice(*self._span(bt.macho_slice(self.fat, "1")))], self.s2)

    @staticmethod
    def _span(res):
        off, size, _ = res
        return off, off + size

    def test_missing_and_out_of_bounds_are_errors(self):
        with self.assertRaises(bt.BinError):
            bt.macho_slice(self.fat, "MIPS")
        with self.assertRaises(bt.BinError):
            bt.macho_slice(self.fat, "9")
        # a slice pointing past the file
        bad = bytearray(struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(">IIIII", self.X86_64, 3, 9_000_000, 84, 14))
        with self.assertRaises(bt.BinError):
            bt.macho_slice(bytes(bad), "x86-64")

    def test_write_slice_to_temp(self):
        d = tempfile.mkdtemp(prefix="re-fat-")
        p = os.path.join(d, "universal")
        Path(p).write_bytes(self.fat)
        tmp, arch = bt.write_slice(p, "ARM64")
        try:
            self.assertEqual(arch, "ARM64")
            self.assertEqual(Path(tmp).read_bytes(), self.s2)
        finally:
            os.unlink(tmp)
            shutil.rmtree(d, ignore_errors=True)

    def test_not_fat_is_an_error(self):
        with self.assertRaises(bt.BinError):
            bt.macho_slice(self.s1, "x86-64")      # a thin binary is not fat


# ---------------- bin_triage: bounded safety (hostile input) ----------------

class BinTriageBounded(unittest.TestCase):
    def test_truncated_elf_does_not_crash(self):
        rep = bt.inspect(b"\x7fELF\x02\x01\x01\x03" + b"\x00" * 4)   # 12 bytes, header cut short
        self.assertEqual(rep["format"], "elf")
        self.assertTrue(rep.get("errors"))

    def test_absurd_section_count_is_capped(self):
        h = bytearray(64)
        h[0:4] = b"\x7fELF"
        h[4], h[5], h[6] = 2, 1, 1
        struct.pack_into("<H", h, 18, 0x3E)
        struct.pack_into("<Q", h, 40, 64)             # shoff points just past the header
        struct.pack_into("<H", h, 58, 64)             # shentsize
        struct.pack_into("<H", h, 60, 0xFFFF)         # shnum: absurd
        rep = bt.inspect(bytes(h) + b"\x00" * 64)     # only room for ~1 entry
        self.assertLessEqual(len(rep["sections"]), bt.MAX_SECTIONS)
        self.assertTrue(rep.get("errors"))
        # Room for more headers than the cap, so only the cap can stop the loop (the short file above
        # stops on its own overrun: a battery removed the cap and that check still passed).
        rep = bt.inspect(bytes(h) + b"\x00" * 64 * (bt.MAX_SECTIONS + 50))
        self.assertEqual(len(rep["sections"]), bt.MAX_SECTIONS)

    def test_runpath_entries_are_capped(self):
        # 8192 DT_RUNPATH entries, each naming the same 4000-character "a:a:...": uncapped, that is
        # 16 million list items from a 135 KB file.
        dynstr = b"a:" * 2000 + b"\x00"
        dyn_off, nent = 4096, 8192
        sh_off = dyn_off + nent * 16
        buf = bytearray(sh_off + 3 * 64)
        buf[0:4] = b"\x7fELF"
        buf[4], buf[5], buf[6] = 2, 1, 1
        struct.pack_into("<HH", buf, 16, 3, 0x3E)
        struct.pack_into("<Q", buf, 40, sh_off)
        struct.pack_into("<HHHH", buf, 58, 64, 3, 0, 0)          # shentsize, shnum, shstrndx (0)
        buf[64:64 + len(dynstr)] = dynstr
        for i in range(nent):
            struct.pack_into("<qQ", buf, dyn_off + i * 16, 29, 0)  # DT_RUNPATH -> dynstr offset 0
        # section 1: .dynamic (type 6) linking section 2: .dynstr (type 3)
        struct.pack_into("<IIQQQQIIQQ", buf, sh_off + 64, 0, 6, 0, 0, dyn_off, nent * 16, 2, 0, 8, 16)
        struct.pack_into("<IIQQQQIIQQ", buf, sh_off + 128, 0, 3, 0, 0, 64, len(dynstr), 0, 0, 1, 0)
        rep = bt.inspect(bytes(buf))
        self.assertLessEqual(len(rep["runpath"]), bt.MAX_IMPORTS, len(rep["runpath"]))

    def test_pe_bad_lfanew_does_not_crash(self):
        data = b"MZ" + b"\x00" * 0x3A + struct.pack("<I", 0x7FFFFFFF) + b"\x00" * 16
        rep = bt.inspect(data)
        self.assertIn(rep["format"], ("pe", "dos-mz"))

    def test_garbage_is_other_not_crash(self):
        rep = bt.inspect(bytes(range(32)))
        self.assertEqual(rep["format"], "other")

    def test_reader_raises_past_end(self):
        with self.assertRaises(bt.BinError):
            bt.Reader(b"\x00\x01", end=2).u32(0)
        # One byte past the end: four bytes past (above) also fails with an off-by-one bound.
        with self.assertRaises(bt.BinError):
            bt.Reader(b"\x00\x01\x02\x03").u32(1)
        self.assertEqual(bt.Reader(b"\x00\x01\x02\x03").u32(0), 0x03020100)

    @unittest.skipUnless(os.path.exists("/bin/ls"), "/bin/ls not present")
    def test_mmap_path_matches_bytes_path(self):
        # main() runs on an mmap, not bytes; the parse must be identical either way.
        import mmap as _mmap
        from_bytes = bt.inspect(Path("/bin/ls").read_bytes())
        with open("/bin/ls", "rb") as fh, _mmap.mmap(fh.fileno(), 0, access=_mmap.ACCESS_READ) as mm:
            from_mmap = bt.inspect(mm, want_strings=True, limit=20)
        self.assertEqual(from_mmap["arch"], from_bytes["arch"])
        self.assertEqual(from_mmap["needed"], from_bytes["needed"])
        self.assertEqual(from_mmap["entry"], from_bytes["entry"])
        self.assertTrue(from_mmap["strings"])

    def test_a_string_across_a_4mb_seam_is_whole(self):
        # The scan read 4 MB windows: a string over a window's edge came out cut at the edge
        # ('THIS_STRIN'), and the whole one was then dropped as already seen.
        seam = 4 << 20
        s1 = b"THIS_STRING_CROSSES_THE_SEAM_AND_MUST_STAY_WHOLE"
        s2 = b"L" * 3000                           # long, and starting before the next window's overlap
        data = bytearray(3 * seam)
        data[seam - 10:seam - 10 + len(s1)] = s1
        off2 = 2 * seam - 4096 - 1000
        data[off2:off2 + len(s2)] = s2
        got = {s["offset"]: s["text"] for s in bt.extract_strings(bytes(data), 6, 50)}
        self.assertEqual(got.get(seam - 10), s1.decode(), got)
        self.assertEqual(got.get(off2), s2.decode())
        self.assertEqual(len(got), 2, sorted(got))


# ---------------- carve: the false-positive fix is pinned ----------------

class CarvePureScan(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="re-carve-test-")
        png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
               + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0) + b"\x00\x00\x00\x00")
        self.png = os.path.join(self.d, "p.png")
        Path(self.png).write_bytes(png)
        import zipfile
        self.zip = os.path.join(self.d, "b.zip")
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("secret.txt", "hidden payload inside the pack")
        self.blob = os.path.join(self.d, "blob.bin")
        Path(self.blob).write_bytes(png + Path(self.zip).read_bytes())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_finds_png_and_embedded_zip_at_right_offsets(self):
        hits = carve.scan(self.blob)["hits"]
        types_at = {h["offset"]: h["type"] for h in hits}
        self.assertIn("PNG image", types_at.get(0, ""))
        png_len = os.path.getsize(self.png)
        self.assertTrue(any(o == png_len and "ZIP" in t for o, t in types_at.items()),
                        f"expected a ZIP hit at offset {png_len}: {types_at}")

    def test_no_false_truetype_in_png(self):
        # The regression this pins: TrueType magic 00 01 00 00 00 inside the PNG
        # header used to be reported as an embedded font. It must not be.
        hits = carve.scan(self.blob)["hits"]
        self.assertFalse([h for h in hits if "TrueType" in h["type"]], hits)

    def test_truetype_only_at_offset_zero(self):
        ttf = os.path.join(self.d, "f.ttf")
        Path(ttf).write_bytes(b"\x00\x01\x00\x00\x00" + b"\x00" * 40)
        self.assertTrue([h for h in carve.scan(ttf)["hits"] if "TrueType" in h["type"]])

    def test_mz_validator_rejects_text_accepts_pe(self):
        plain = os.path.join(self.d, "t.bin")
        Path(plain).write_bytes(b"MZ here is some text that merely contains MZ, not a real PE header at all!")
        self.assertFalse([h for h in carve.scan(plain)["hits"] if "PE" in h["type"]])
        pe = os.path.join(self.d, "mini.exe")
        buf = bytearray(b"MZ" + b"\x00" * 0x3E)
        struct.pack_into("<I", buf, 0x3C, 0x40)       # e_lfanew -> 0x40
        buf += b"PE\x00\x00"
        Path(pe).write_bytes(bytes(buf))
        self.assertTrue([h for h in carve.scan(pe)["hits"] if "PE" in h["type"]])

    def test_empty_file(self):
        empty = os.path.join(self.d, "e.bin")
        Path(empty).write_bytes(b"")
        self.assertEqual(carve.scan(empty)["hits"], [])

    def test_a_cut_list_says_it_was_cut(self):
        # 300 PNG magics, 256 reported: the scan used to say truncated False, so the count read as complete.
        many = os.path.join(self.d, "many.bin")
        Path(many).write_bytes((b"\x00" * 100 + b"\x89PNG\r\n\x1a\n") * 300)
        rep = carve.scan(many)
        self.assertEqual(len([h for h in rep["hits"] if h["type"] == "PNG image"]), carve.MAX_HITS_PER_SIG)
        self.assertTrue(rep["truncated"])

    def test_short_magics_do_not_fire_on_random_data(self):
        # JPEG (ff d8 ff) and gzip (1f 8b 08) are three bytes: blind, each turns up about twice in 32 MB
        # of random or compressed data, and the scan reported those as embedded files.
        noise = os.path.join(self.d, "noise.bin")
        Path(noise).write_bytes(random.Random(7).randbytes(32 << 20))
        hits = carve.scan(noise)["hits"]
        self.assertEqual([h for h in hits if h["type"] in ("JPEG image", "gzip")], [], hits)

    def test_real_jpeg_gzip_and_bzip2_are_still_found(self):
        import bz2
        import gzip
        import io
        from PIL import Image
        jpg = io.BytesIO()
        Image.new("RGB", (8, 8), (200, 30, 30)).save(jpg, "JPEG")
        parts = [jpg.getvalue(), gzip.compress(b"payload" * 50), bz2.compress(b"payload" * 50, compresslevel=1)]
        blob = os.path.join(self.d, "mixed.bin")
        offsets, data = [], b""
        for p in parts:
            data += b"\x00" * 64
            offsets.append(len(data))
            data += p
        Path(blob).write_bytes(data)
        found = {h["offset"]: h["type"] for h in carve.scan(blob)["hits"]}
        self.assertEqual(found.get(offsets[0]), "JPEG image", found)
        self.assertEqual(found.get(offsets[1]), "gzip", found)
        self.assertEqual(found.get(offsets[2]), "bzip2", found)   # block size 1: 'BZh1', not only 'BZh9'

    def test_a_flood_of_false_mz_is_cut_and_said(self):
        # Every 'MZ' fails the PE check, so the per-signature cap never counted them: 50 MB took 18 s.
        flood = os.path.join(self.d, "mz.bin")
        Path(flood).write_bytes(b"MZ" * (2 << 20))
        rep = carve.scan(flood)
        self.assertEqual(rep["hits"], [])
        self.assertTrue(rep["truncated"])
        self.assertIn("DOS/PE image", rep.get("capped", []))


class CarveGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="re-carve-gate-")
        self.blob = os.path.join(self.d, "x.bin")
        Path(self.blob).write_bytes(b"PK\x03\x04" + b"\x00" * 64)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_extract_refuses_without_purpose(self):
        with self.assertRaises(carve.CarveError):
            carve.binwalk_extract(self.blob, os.path.join(self.d, "o"), None, "note")

    def test_extract_refuses_unlawful_purpose(self):
        with self.assertRaises(carve.CarveError):
            carve.binwalk_extract(self.blob, os.path.join(self.d, "o"), "piracy", "to avoid paying")

    def test_extract_refuses_empty_note(self):
        with self.assertRaises(carve.CarveError):
            carve.binwalk_extract(self.blob, os.path.join(self.d, "o"), "own_work", "   ")

    def test_lawful_purposes_come_from_schema(self):
        self.assertEqual(set(carve.lawful_purposes()),
                         {"interoperability", "security_research", "format_understanding",
                          "own_work", "education"})

    def test_extract_refuses_a_folder_that_is_not_empty(self):
        # The folder is mounted writable into the sandbox, and whatever was in it was listed as extracted.
        mine = os.path.join(self.d, "mine")
        os.makedirs(mine)
        Path(mine, "notes.txt").write_text("my own file")
        with self.assertRaisesRegex(carve.CarveError, "new or empty"):
            carve.binwalk_extract(self.blob, mine, "own_work", "my own pack")
        self.assertEqual(os.listdir(mine), ["notes.txt"])


def within(seconds, fn, *args):
    """Run fn in a daemon thread for at most `seconds`: ('returned', value), ('raised', error),
    or ('blocked', None). A read that blocks on a pipe then fails its test instead of hanging the run."""
    import threading
    box = []

    def call():
        try:
            box.append(("returned", fn(*args)))
        except Exception as e:  # noqa: BLE001 - the caller judges what was raised
            box.append(("raised", e))
    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if box else ("blocked", None)


def _fake_run(plant):
    """A stand-in for sandbox.run that does what a compromised tool could: write into the output
    mount (here a link or a pipe where the result should be). No container is started."""
    def run(cmd, rw=(), **kw):
        out = [m.rpartition(":")[0] for m in rw if m.endswith(":/out")][0]
        plant(out)
        return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": "", "argv": []}
    return run


class SandboxOutputIsNotTrusted(unittest.TestCase):
    """A tool inside the sandbox runs as our uid with its output folder writable, so a
    compromised one can leave a link to any host file where its result belongs. The host
    must never follow it: ghidra.py once printed the linked file as 'decompiled C'."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="re-out-trust-")
        self.secret = os.path.join(self.d, "host-secret.txt")
        Path(self.secret).write_text("HOST-SECRET-CANARY")
        self.bin = os.path.join(self.d, "target")
        Path(self.bin).write_bytes(b"\x7fELF" + b"\x00" * 60)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_ghidra_never_reads_a_planted_link(self):
        plant = _fake_run(lambda out: os.symlink(self.secret, os.path.join(out, "result.txt")))
        with mock.patch.object(gh.sandbox, "run", plant), \
                mock.patch.object(gh.sandbox, "ensure_image", lambda *a, **k: "img"):
            with self.assertRaises(gh.GhidraError) as cm:
                gh.decompile(self.bin, "main")
        self.assertNotIn("HOST-SECRET-CANARY", str(cm.exception))

    def test_ghidra_does_not_hang_on_a_planted_pipe(self):
        plant = _fake_run(lambda out: os.mkfifo(os.path.join(out, "result.txt")))
        with mock.patch.object(gh.sandbox, "run", plant), \
                mock.patch.object(gh.sandbox, "ensure_image", lambda *a, **k: "img"):
            kind, value = within(20, gh.decompile, self.bin, "main")
        self.assertEqual(kind, "raised", f"{kind}: {value!r}")
        self.assertIsInstance(value, gh.GhidraError)

    def test_carve_removes_links_that_leave_the_folder(self):
        def plant(out):
            os.makedirs(os.path.join(out, "_target.extracted"))
            os.symlink(self.secret, os.path.join(out, "_target.extracted", "evil"))
            os.symlink("inside.txt", os.path.join(out, "_target.extracted", "fine"))
            Path(out, "_target.extracted", "inside.txt").write_text("extracted")
        out = os.path.join(self.d, "out")
        with mock.patch.object(carve.sandbox, "run", _fake_run(plant)), \
                mock.patch.object(carve.sandbox, "ensure_image", lambda *a, **k: "img"):
            _, files = carve.binwalk_extract(self.bin, out, "own_work", "test")
        self.assertFalse(os.path.lexists(os.path.join(out, "_target.extracted", "evil")))
        self.assertTrue(os.path.islink(os.path.join(out, "_target.extracted", "fine")))
        self.assertEqual(Path(self.secret).read_text(), "HOST-SECRET-CANARY")


    def test_carve_never_lists_a_pipe_as_an_extracted_file(self):
        # A tar inside a firmware image can carry pipes, and the extractor makes them; any later
        # read of one blocks for ever.
        def plant(out):
            os.makedirs(os.path.join(out, "_target.extracted"))
            os.mkfifo(os.path.join(out, "_target.extracted", "initctl"))
            Path(out, "_target.extracted", "inside.txt").write_text("extracted")
        out = os.path.join(self.d, "out")
        err = io.StringIO()
        with mock.patch.object(carve.sandbox, "run", _fake_run(plant)), \
                mock.patch.object(carve.sandbox, "ensure_image", lambda *a, **k: "img"), \
                mock.patch("sys.stderr", err):
            _, files = carve.binwalk_extract(self.bin, out, "own_work", "test")
        self.assertEqual([os.path.basename(f) for f in files], ["inside.txt"])
        self.assertIn("initctl", err.getvalue())

    def test_a_ghidra_result_past_the_read_cap_says_it_was_cut(self):
        line = "00101000\tfn\t12\n"

        def plant(out):
            with open(os.path.join(out, "result.txt"), "w") as fh:
                fh.write(line * (gh.MAX_RESULT // len(line) + 1000))     # past the cap by 1000 lines
        with mock.patch.object(gh.sandbox, "run", _fake_run(plant)), \
                mock.patch.object(gh.sandbox, "ensure_image", lambda *a, **k: "img"):
            text = gh.decompile(self.bin, "::list::")
        self.assertIn(f"cut at {gh.MAX_RESULT} bytes", text[-300:])
        # One long line past the cap keeps what was read, rather than cutting back to nothing.
        with mock.patch.object(gh.sandbox, "run", _fake_run(
                lambda out: Path(out, "result.txt").write_text("x" * (gh.MAX_RESULT + 10)))), \
                mock.patch.object(gh.sandbox, "ensure_image", lambda *a, **k: "img"):
            one = gh.decompile(self.bin, "::list::")
        self.assertTrue(one.startswith("x" * 1000) and "cut at" in one[-200:], one[-200:])
        self.assertTrue(text.endswith("...]\n") and text.count(line) == text.rfind(line) // len(line) + 1)


@unittest.skipUnless(DOCKER_OK and CARVE_OK, "docker or re-carve image not present")
class CarveSandbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="re-carve-sbx-")
        import zipfile
        zpath = os.path.join(self.d, "b.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("secret.txt", "hidden payload inside the pack")
        png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
               + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0) + b"\x00\x00\x00\x00")
        self.blob = os.path.join(self.d, "blob.bin")
        Path(self.blob).write_bytes(png + Path(zpath).read_bytes())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_binwalk_scan_finds_the_zip(self):
        out = carve.binwalk_scan(self.blob)
        self.assertIn("Zip archive", out)
        self.assertIn("secret.txt", out)

    def test_gated_extraction_recovers_the_hidden_file(self):
        out, files = carve.binwalk_extract(self.blob, os.path.join(self.d, "o"),
                                           "format_understanding", "recover my own files")
        recovered = [f for f in files if os.path.basename(f) == "secret.txt"]
        self.assertTrue(recovered, files)
        self.assertEqual(Path(recovered[0]).read_text(), "hidden payload inside the pack")

    def test_extraction_stops_at_its_size_cap(self):
        # A 1 MB blob once wrote 1 GB to the disk in 9 s: nothing bounded what extraction writes.
        import zipfile
        bomb = os.path.join(self.d, "bomb.zip")
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            with zf.open("zeros.bin", "w", force_zip64=True) as fh:
                for _ in range(16):
                    fh.write(b"\0" * (16 << 20))   # 256 MB of zeros, about 256 KB packed
        blob = os.path.join(self.d, "bomb.bin")
        Path(blob).write_bytes(b"\x00" * 64 + Path(bomb).read_bytes())
        out = os.path.join(self.d, "bomb-out")
        with mock.patch.object(carve, "MAX_EXTRACT_BYTES", 16 << 20, create=True):
            with self.assertRaisesRegex(carve.CarveError, "cap"):
                carve.binwalk_extract(blob, out, "format_understanding", "is extraction bounded")
        used = sum(os.lstat(os.path.join(r, f)).st_blocks * 512 for r, _, fs in os.walk(out) for f in fs)
        self.assertLess(used, 128 << 20, f"{used >> 20} MB written past a 16 MB cap")


# ---------------- radare2 (sandbox) ----------------

@unittest.skipUnless(DOCKER_OK and NATIVE_OK, "docker or re-native image not present")
class R2Sandbox(unittest.TestCase):
    def test_info_arch_agrees_with_bin_triage(self):
        d = r2.info("/bin/ls")
        self.assertEqual(d["bin"]["arch"], "x86")
        self.assertEqual(d["bin"]["bits"], 64)
        rep = bt.inspect(Path("/bin/ls").read_bytes())
        self.assertEqual(rep["arch"], "x86-64")   # same machine, each tool's own spelling

    def test_disasm_entry_bytes_match_the_file(self):
        d = r2.disasm("/bin/ls", "entry0", level="quick")
        first = d["ops"][0]
        addr = first.get("addr", first.get("offset"))
        raw = Path("/bin/ls").read_bytes()
        nbytes = len(first["bytes"]) // 2
        self.assertEqual(raw[addr:addr + nbytes].hex(), first["bytes"])

    def test_a_list_longer_than_the_output_tail_is_read_whole(self):
        # radare2 prints its JSON on stdout and the sandbox kept a 200 KB tail of it: every list
        # past that failed to parse (an 8 MB python3.12 gave 1.1 MB of functions, 8.8 MB of strings).
        d = tempfile.mkdtemp(prefix="re-r2-big-")
        try:
            target = os.path.join(d, "ls-with-strings")
            extra = b"".join(b"\x00planted_string_%05d_" % i + b"z" * 60 for i in range(5000))
            Path(target).write_bytes(Path("/bin/ls").read_bytes() + extra)
            try:
                found = r2.strings(target)
            except r2.R2Error as e:
                self.fail(f"the string list did not come back whole: {str(e)[:200]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        planted = [s for s in found if s.get("string", "").startswith("planted_string_")]
        self.assertEqual(len(planted), 5000)

    def test_symbol_injection_is_rejected(self):
        # Refused before radare2 runs: radare2 printing no JSON raises R2Error too, so the message is checked.
        for bad in ("a;!id", "sym;px", "x`id`", "0x10 ; p8 4", "entry0@@c:ij"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(r2.R2Error, "radare2 commands are not allowed"):
                    r2.disasm("/bin/ls", bad)


class R2SelectorFilter(unittest.TestCase):
    """No container needed: the filter is what stands between a name and radare2's command line."""

    def test_a_foreach_iterator_cannot_ride_in_on_a_name(self):
        # 'entry0@@c:ij' is letters, digits, '@' and ':', and radare2 ran 'ij' from it
        # (its JSON came back as "Invalid number '{"core":...'), inside the sandbox.
        for bad in ("entry0@@c:ij", "main@@c:iz", "x@@f", "sym.main@0x10", "a;!id", "x`id`", "a b", "a|b",
                    "main\n", "0x10\n"):        # '$' matches before a final newline; the guard must not
            with self.subTest(bad=bad):
                with self.assertRaises(r2.R2Error):
                    r2._check_sym(bad)
        for good in ("main", "sym.imp.printf", "entry0", "0x6d30", "fcn.00001234", "method.Foo.bar",
                     "sym.std::foo", "sym.Outer$Inner"):
            with self.subTest(good=good):
                self.assertEqual(r2._check_sym(good), good)


class GhidraSelectorFilter(unittest.TestCase):
    """No container needed: the selector is checked before Ghidra is started."""

    def test_a_selector_with_a_trailing_newline_is_refused(self):
        for bad in ("main\n", "::list::\n", "0x10\n", "a;b"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(gh.GhidraError, "is not a bare function name"):
                    gh.decompile("/bin/ls", bad)
        self.assertTrue(gh.SELECTOR_OK.match("sym.Outer$Inner@plt"))


# ---------------- Ghidra + cross-tool (sandbox) ----------------

@unittest.skipUnless(DOCKER_OK and GHIDRA_OK and GCC, "docker, re-ghidra image, or gcc not present")
class GhidraSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.bin = build_c("int add(int a,int b){return a+b*3;}\n"
                                 "int main(int c,char**v){return add(c,7);}\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_decompile_add_reads_the_expression(self):
        text = gh.decompile(self.bin, "add", timeout=600)
        self.assertIn("b * 3", text)
        self.assertIn("ground truth", text)

    def test_list_contains_add_and_main(self):
        text = gh.decompile(self.bin, "::list::", timeout=600)
        names = {line.split("\t")[1] for line in text.splitlines() if line.count("\t") >= 2}
        self.assertIn("add", names)
        self.assertIn("main", names)

    def test_selector_validation(self):
        # Refused before Ghidra runs: with ';' let through, Ghidra found no function 'a;b' and raised
        # the same error type, so a bare assertRaises passed (found by the battery).
        with self.assertRaisesRegex(gh.GhidraError, "is not a bare function name"):
            gh.decompile(self.bin, "a;b")

    def test_a_missing_function_is_an_error(self):
        with self.assertRaisesRegex(gh.GhidraError, "found no function"):
            gh.decompile(self.bin, "no_such_function_here", timeout=600)


@unittest.skipUnless(DOCKER_OK and NATIVE_OK and GHIDRA_OK and GCC and shutil.which("objdump"),
                     "need docker, both images, gcc and objdump")
class CrossToolGroundTruth(unittest.TestCase):
    """objdump (host), radare2 (sandbox) and Ghidra (sandbox) must report the same
    instruction bytes for the same function: one ground truth, three derivations."""

    @classmethod
    def setUpClass(cls):
        cls.d, cls.bin = build_c("int add(int a,int b){return a+b*3;}\n"
                                 "int main(int c,char**v){return add(c,7);}\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_three_tools_agree_on_add_bytes(self):
        od = objdump_func_bytes(self.bin, "add")
        self.assertTrue(od, "objdump produced no bytes for add")

        funcs = r2.functions(self.bin, level="quick")
        fn = next((f for f in funcs if f.get("name", "").endswith("add")), None)
        self.assertIsNotNone(fn, "radare2 did not find add")
        ops = r2.disasm(self.bin, "0x%x" % fn["addr"], level="quick")["ops"]
        r2_bytes = "".join(o["bytes"] for o in ops)

        text = gh.decompile(self.bin, "add", timeout=600)
        gh_bytes = ""
        in_insns = False
        for line in text.splitlines():
            if "instructions (ground truth" in line:
                in_insns = True
                continue
            if in_insns:
                parts = line.split("  ")
                if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]+", parts[1]):
                    gh_bytes += parts[1]

        self.assertEqual(od, r2_bytes, "objdump vs radare2 disagree")
        self.assertEqual(od, gh_bytes, "objdump vs Ghidra disagree")


if __name__ == "__main__":
    unittest.main()
