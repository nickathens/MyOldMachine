"""reverse-engineering skill, games tier: Godot pack reader.

Evidence, matching the verification spine:
  - independent corroboration: the pure-Python reader's view of a real pack made
    by Godot 4.7.2 here is checked against the known files put into it, and every
    file's md5 in the directory is re-hashed from the bytes it points at.
  - every format from a real editor: packs exported here by Godot 3.6.3 (format
    1), 4.4.1 (2), 4.6.2 (3) and 4.7.2 (4) (tests/fixtures/reverse_engineering/gdre,
    made by proof/gdre/prove.py build) must each read with the file list GDRE's own
    reader gives and every md5 re-hashing.
  - format coverage: a hand-built v2 (Godot 4.0 to 4.4) pack and packs embedded in
    a mock executable (the end-of-file trailer; for format 1 with the absolute
    offsets Godot 3 writes when it embeds) drive the locate branches the
    standalone samples do not.
  - bounded safety: a truncated or non-pack file must raise cleanly, an absurd
    file count must cap, and a hostile path (res://../../etc/x) must never be
    written outside the output folder.
  - the gate is pinned: extraction refuses without a lawful purpose and note and
    refuses a folder that is not empty.

The real pack (tests/fixtures/reverse_engineering/godot4.pck) was made with
Godot 4.7.2's PCKPacker; the synthetic packs are built here so the test needs no
Godot on the machine running it.
"""
import hashlib
import importlib.util
import os
import struct
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"
FIXTURES = ROOT / "fixtures" / "reverse_engineering"
REAL_PCK = FIXTURES / "godot4.pck"
GDRE_FIXTURES = FIXTURES / "gdre"
# What each editor wrote, as GDRE's own --list-files reads the same packs (a second reader).
FILES_4 = {".godot/exported/133200997/export-3070c538c03ee49b7677ff960a3f5195-main.scn",
           ".godot/exported/133200997/export-9c798deb4b444e75f415926f8ed59880-style.res",
           ".godot/global_script_class_cache.cfg", ".godot/uid_cache.bin", "main.gd.remap", "main.gdc",
           "main.tscn.remap", "project.binary", "style.tres.remap"}
REAL_PACKS = {   # file: (format version, engine version, stored paths)
    "godot3.6.3.pck": (1, "3.6.3", {"res://main.gd.remap", "res://main.gdc", "res://main.tscn",
                                    "res://project.binary"}),
    "godot4.4.1.pck": (2, "4.4.1", FILES_4),
    "godot4.6.2.pck": (3, "4.6.2", FILES_4),
    "godot4.7.2.pck": (4, "4.7.2", FILES_4),
}


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gp = load("godot_pck")


def build_v2(entries, rel_base=False):
    """A standalone Godot 4.0 to 4.4 (format v2) pack: header, 16 reserved u32,
    inline directory, then the file data. `entries` is a list of (path, data)."""
    hdr = bytearray(gp.MAGIC)
    hdr += struct.pack("<I", 2)                 # version V2
    hdr += struct.pack("<III", 4, 2, 0)         # engine 4.2.0
    hdr += struct.pack("<I", gp.PACK_REL_FILEBASE if rel_base else 0)
    file_base_pos = len(hdr)
    hdr += struct.pack("<Q", 0)                 # file_base, filled once the layout is known
    hdr += b"\x00" * (16 * 4)                   # 16 reserved u32

    def pad(path):
        b = path.encode()
        pl = (len(b) + 3) & ~3
        return b + b"\x00" * (pl - len(b)), pl

    dirsize = 4 + sum(4 + pad(p)[1] + 8 + 8 + 16 + 4 for p, _ in entries)
    data_start = len(hdr) + dirsize
    file_base = 0 if rel_base else data_start   # rel: data_start = file_base + ofs, so ofs carries it
    d = bytearray(struct.pack("<I", len(entries)))
    blob = bytearray()
    cur = 0
    for path, data in entries:
        pb, pl = pad(path)
        ofs = data_start + cur if rel_base else cur
        d += struct.pack("<I", pl) + pb
        d += struct.pack("<Q", ofs) + struct.pack("<Q", len(data))
        d += hashlib.md5(data).digest() + struct.pack("<I", 0)
        blob += data
        cur += len(data)
    struct.pack_into("<Q", hdr, file_base_pos, file_base)
    return bytes(hdr) + bytes(d) + bytes(blob)


def embed_in_exe(pck, stub=b"\x7fELF" + b"\x00" * 200):
    """A pack appended to a mock executable, with Godot's trailer (u64 size + magic)."""
    return stub + pck + struct.pack("<Q", len(pck)) + gp.MAGIC


def shift_v1(pck, delta):
    """A format-1 (Godot 3) pack with every file offset moved by delta, read and
    rewritten by hand here (not by the reader under test). Godot 3 stores absolute
    positions in the file, so when it appends a pack to an executable it writes
    them with the executable's length already added; this makes that layout."""
    out = bytearray(pck)
    count = struct.unpack_from("<I", out, 84)[0]         # magic, version, 3 engine u32, 16 reserved
    p = 88
    for _ in range(count):
        sl = struct.unpack_from("<I", out, p)[0]
        p += 4 + sl
        struct.pack_into("<Q", out, p, struct.unpack_from("<Q", out, p)[0] + delta)
        p += 8 + 8 + 16                                    # offset, size, md5; no per-file flags in v1
    return bytes(out)


def scratch(test, prefix):
    """A temp folder removed when the test ends (these tests once left one in /tmp per run).

    Resolved, because extract() writes to the realpath of its output dir and the
    callers compare against it: on macOS TMPDIR is /var/folders/... linked to
    /private/var/folders/..., so an unresolved path would never match."""
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return os.path.realpath(d)


class RealPackV4(unittest.TestCase):
    def setUp(self):
        self.data = REAL_PCK.read_bytes()
        self.rep = gp.parse(self.data)

    def test_header_matches_the_real_pack(self):
        self.assertEqual(self.rep["format_version"], 4)
        self.assertEqual(self.rep["engine_version"], "4.7.2")
        self.assertEqual(self.rep["located"], "standalone")
        self.assertFalse(self.rep["dir_encrypted"])

    def test_lists_the_two_files_put_in(self):
        paths = {f["path"] for f in self.rep["files"]}
        self.assertEqual(paths, {"src/main.gd", "data.json"})

    def test_every_md5_rehashes_from_the_bytes_it_points_at(self):
        self.assertTrue(self.rep["files"])
        for f in self.rep["files"]:
            self.assertTrue(f["in_bounds"], f["path"])
            self.assertIs(gp.verify_md5(self.data, f), True, f["path"])

    def test_a_flipped_byte_is_an_md5_mismatch(self):
        f = next(f for f in self.rep["files"] if f["path"] == "data.json")
        bad = bytearray(self.data)
        bad[f["offset"]] ^= 0xFF
        self.assertIs(gp.verify_md5(bytes(bad), f), False)


class SyntheticV2(unittest.TestCase):
    def test_v2_absolute_filebase(self):
        pck = build_v2([("res://scene.tscn", b"[gd_scene]\n"), ("cfg.ini", b"a=1\n")])
        rep = gp.parse(pck)
        self.assertEqual(rep["format_version"], 2)
        self.assertEqual(rep["engine_version"], "4.2.0")
        self.assertEqual([f["path"] for f in rep["files"]], ["res://scene.tscn", "cfg.ini"])
        for f in rep["files"]:
            self.assertIs(gp.verify_md5(pck, f), True, f["path"])

    def test_v2_relative_filebase(self):
        pck = build_v2([("a.txt", b"hello")], rel_base=True)
        rep = gp.parse(pck)
        f = rep["files"][0]
        self.assertTrue(f["in_bounds"])
        self.assertEqual(pck[f["offset"]:f["offset"] + f["size"]], b"hello")


class EmbeddedTrailer(unittest.TestCase):
    def test_pack_appended_to_an_executable_is_found(self):
        stub = b"\x7fELF" + b"\x00" * 200
        data = embed_in_exe(REAL_PCK.read_bytes(), stub)
        try:
            rep = gp.parse(data)
        except gp.PckError as e:                   # a wrong locate raises; make that a test failure, not an error
            self.fail(f"embedded pack should have been found: {e}")
        self.assertEqual(rep["located"], "embedded in executable")
        self.assertEqual(rep["pack_start"], len(stub))
        self.assertEqual(rep["format_version"], 4)
        self.assertEqual({f["path"] for f in rep["files"]}, {"src/main.gd", "data.json"})
        for f in rep["files"]:                      # the relative file base only bites when pack_start != 0
            self.assertIs(gp.verify_md5(data, f), True)


class EveryRealFormat(unittest.TestCase):
    """Packs exported by four real editors, one per pack format."""

    def test_each_format_reads_its_header_and_files(self):
        for name, (version, engine, paths) in REAL_PACKS.items():
            with self.subTest(pack=name):
                try:
                    rep = gp.parse((GDRE_FIXTURES / name).read_bytes())
                except gp.PckError as e:           # a refused format is a failure, not an error
                    self.fail(f"{name}: {e}")
                self.assertEqual(rep["format_version"], version)
                self.assertEqual(rep["engine_version"], engine)
                self.assertEqual({f["path"] for f in rep["files"]}, paths)
                self.assertEqual(rep["errors"], [])

    def test_every_md5_rehashes_in_every_format(self):
        for name in REAL_PACKS:
            with self.subTest(pack=name):
                data = (GDRE_FIXTURES / name).read_bytes()
                try:
                    rep = gp.parse(data)
                except gp.PckError as e:
                    self.fail(f"{name}: {e}")
                for f in rep["files"]:
                    self.assertTrue(f["in_bounds"], f["path"])
                    self.assertIs(gp.verify_md5(data, f), True, f["path"])

    def test_godot3_script_bytes_are_where_the_directory_says(self):
        data = (GDRE_FIXTURES / "godot3.6.3.pck").read_bytes()
        try:
            rep = gp.parse(data)
        except gp.PckError as e:
            self.fail(str(e))
        gdc = next((f for f in rep["files"] if f["path"] == "res://main.gdc"), None)
        self.assertIsNotNone(gdc, [f["path"] for f in rep["files"]])         # a misread directory loses the name
        self.assertEqual(data[gdc["offset"]:gdc["offset"] + 4], b"GDSC")   # compiled GDScript magic

    def test_godot3_pack_embedded_with_absolute_offsets(self):
        stub = b"\x7fELF" + b"\x00" * 300
        pck = (GDRE_FIXTURES / "godot3.6.3.pck").read_bytes()
        data = embed_in_exe(shift_v1(pck, len(stub)), stub)
        try:
            rep = gp.parse(data)
        except gp.PckError as e:
            self.fail(f"embedded Godot 3 pack should have been found: {e}")
        self.assertEqual(rep["located"], "embedded in executable")
        self.assertEqual(rep["pack_start"], len(stub))
        # format 1 offsets are already absolute: adding the pack start again would read past them
        for f in rep["files"]:
            self.assertIs(gp.verify_md5(data, f), True, f["path"])

    def test_formats_outside_1_to_4_are_refused(self):
        # the version guard names the formats it reads; a format 0 or 5 is still refused
        pck = bytearray((GDRE_FIXTURES / "godot3.6.3.pck").read_bytes())
        for bad in (0, 5):
            struct.pack_into("<I", pck, 4, bad)
            with self.assertRaises(gp.PckError):
                gp.parse(bytes(pck))


class Bounded(unittest.TestCase):
    def test_reader_refuses_a_short_read(self):
        # the only bounds guard: slicing past the end returns a short slice silently,
        # so _chk must raise instead (pins the off-by-one directly).
        with self.assertRaises(gp.PckError):
            gp.Reader(b"abc").u32(0)
        self.assertEqual(gp.Reader(b"abcd").u32(0), struct.unpack("<I", b"abcd")[0])

    def test_non_pack_raises_cleanly(self):
        with self.assertRaises(gp.PckError):
            gp.parse(b"not a godot pack at all, no magic here........")
        # a blob with a VALID version word but the wrong magic: only the start-magic
        # guard can refuse it, so this pins that guard (the version check cannot).
        fake = (b"JUNK" + struct.pack("<I", 2) + struct.pack("<III", 3, 4, 0)
                + struct.pack("<I", 0) + struct.pack("<Q", 0) + b"\x00" * (16 * 4) + struct.pack("<I", 0))
        with self.assertRaises(gp.PckError):
            gp.parse(fake)

    def test_truncated_directory_does_not_crash(self):
        pck = build_v2([("a.txt", b"x" * 10)])
        rep = gp.parse(pck[:len(pck) - 25])        # chop into the directory/data
        self.assertTrue(rep["errors"] or rep["truncated"] or not rep["files"][0]["in_bounds"])

    def test_absurd_file_count_is_capped(self):
        pck = bytearray(build_v2([("a.txt", b"x")]))
        # overwrite the inline file_count (right after the 16 reserved u32) with a huge number
        dir_pos = 4 + 4 + 12 + 4 + 8 + 16 * 4
        struct.pack_into("<I", pck, dir_pos, 10 ** 9)
        rep = gp.parse(bytes(pck))
        # the count cap fires with its own message; a per-entry bounds error has different text,
        # so asserting the specific message pins the cap, not the bounds check behind it.
        self.assertTrue(any("directory claims" in e for e in rep["errors"]), rep["errors"])
        self.assertTrue(rep["truncated"])
        self.assertLessEqual(len(rep["files"]), gp.MAX_FILES)


class Gate(unittest.TestCase):
    def setUp(self):
        self.data = REAL_PCK.read_bytes()
        self.out = scratch(self, "pck-gate-")
        os.rmdir(self.out)

    def test_refuses_unlawful_purpose(self):
        with self.assertRaises(gp.PckError):
            gp.extract(self.data, self.out, "piracy", "x")

    def test_refuses_empty_note(self):
        with self.assertRaises(gp.PckError):
            gp.extract(self.data, self.out, "own_work", "   ")

    def test_refuses_a_folder_that_is_not_empty(self):
        os.makedirs(self.out)
        Path(self.out, "already").write_text("here")
        with self.assertRaises(gp.PckError):
            gp.extract(self.data, self.out, "own_work", "reading my own pack")

    def test_lawful_purposes_come_from_the_schema(self):
        import json
        schema = json.loads((SCRIPTS / "dossier-schema.json").read_text())
        enum = schema["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]
        self.assertEqual(gp.lawful_purposes(), enum)

    def test_extracts_the_real_files(self):
        written, skipped = gp.extract(self.data, self.out, "own_work", "reading my own Godot project's layout")
        rels = sorted(os.path.relpath(w, self.out) for w in written)
        self.assertEqual(rels, sorted(["data.json", os.path.join("src", "main.gd")]))
        self.assertEqual(skipped, [])
        self.assertIn(b"hello from packed script", Path(self.out, "src", "main.gd").read_bytes())


class ChunkedCopy(unittest.TestCase):
    def test_a_file_larger_than_the_copy_chunk_round_trips(self):
        # the body copy and the md5 both read in COPY_CHUNK windows; drive several
        # iterations with a tiny chunk so an off-by-one in the loop would corrupt or
        # truncate the output.
        body = bytes(range(256)) * 40          # 10240 bytes, non-repeating-ish
        pck = build_v2([("big.bin", body)])
        rep = gp.parse(pck)
        f = rep["files"][0]
        old = gp.COPY_CHUNK
        gp.COPY_CHUNK = 7                       # not a divisor of the size, so the last window is partial
        try:
            self.assertIs(gp.verify_md5(pck, f), True)
            out = scratch(self, "pck-chunk-")
            os.rmdir(out)
            written, _ = gp.extract(pck, out, "own_work", "chunk boundary check")
            self.assertEqual(Path(written[0]).read_bytes(), body)
        finally:
            gp.COPY_CHUNK = old


class Traversal(unittest.TestCase):
    def test_safe_rel_refuses_dotdot(self):
        # the first of two guards (the second is the realpath check in extract);
        # pin this one directly so defence in depth does not mask its removal.
        for bad in ("res://../../etc/evil", "..", "a/../../b"):
            with self.assertRaises(gp.PckError):
                gp._safe_rel(bad)
        self.assertEqual(gp._safe_rel("res://src/main.gd"), os.path.join("src", "main.gd"))
        self.assertEqual(gp._safe_rel("/abs/path"), os.path.join("abs", "path"))  # clamped, not escaping

    def test_a_hostile_path_is_never_written_outside_the_folder(self):
        pck = build_v2([("res://../../etc/evil", b"PWNED"), ("ok.txt", b"fine")])
        out = scratch(self, "pck-trav-")
        os.rmdir(out)
        written, skipped = gp.extract(pck, out, "own_work", "testing path safety")
        self.assertEqual([os.path.relpath(w, out) for w in written], ["ok.txt"])
        self.assertTrue(any("evil" in p for p, _ in skipped))
        # the whole write stayed inside out: only ok.txt exists under it, nothing climbed out
        got = [os.path.relpath(os.path.join(dp, n), out)
               for dp, _, ns in os.walk(out) for n in ns]
        self.assertEqual(got, ["ok.txt"])


class OutOfBounds(unittest.TestCase):
    def test_an_entry_pointing_past_the_file_is_skipped(self):
        pck = build_v2([("good.txt", b"ok"), ("bad.txt", b"zz")])
        rep = gp.parse(pck)
        bad = next(f for f in rep["files"] if f["path"] == "bad.txt")
        chopped = pck[:bad["offset"]]                  # drop bad.txt's data, so its entry runs off the end
        rep2 = gp.parse(chopped)
        self.assertTrue([f for f in rep2["files"] if not f["in_bounds"]])
        out = scratch(self, "pck-oob-")
        os.rmdir(out)
        _written, skipped = gp.extract(chopped, out, "own_work", "bounds check")
        self.assertTrue(any("outside" in why for _, why in skipped))


if __name__ == "__main__":
    unittest.main()
