"""reverse-engineering skill, Electron .asar reader (asar.py).

  - independent corroboration: the pure-Python reader's view of a real archive
    made here with @electron/asar matches the files put in, and each file's
    SHA-256 integrity hash re-hashes from the bytes it points at;
  - format coverage: a hand-built archive drives unpacked entries, symlink
    entries, an out-of-bounds offset, a traversal path and a too-deep tree that
    the real sample does not have;
  - the gate is pinned: extraction refuses without a lawful purpose and note and
    refuses a non-empty folder, and a res://-style ../ path is never written out.

The real archive (tests/fixtures/reverse_engineering/app.asar) was packed with
@electron/asar; the synthetic ones are built here so the test needs no node.
"""
import hashlib
import importlib.util
import json
import os
import struct
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"
REAL = ROOT / "fixtures" / "reverse_engineering" / "app.asar"


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


asar = load("asar")


def build_asar(entries):
    """entries: list of dicts, each {path, data} | {path, unpacked, size} | {path, link}.
    Returns the archive bytes with the Pickle header @electron/asar writes."""
    blob = bytearray()
    tree = {"files": {}}

    def put(path, meta):
        parts = path.split("/")
        node = tree["files"]
        for p in parts[:-1]:
            node = node.setdefault(p, {"files": {}})["files"]
        node[parts[-1]] = meta

    for e in entries:
        if "link" in e:
            put(e["path"], {"link": e["link"]})
        elif e.get("unpacked"):
            put(e["path"], {"size": e["size"], "offset": "0", "unpacked": True})
        else:
            data = e["data"]
            meta = {"size": len(data), "offset": str(len(blob)),
                    "integrity": {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest()}}
            put(e["path"], meta)
            blob += data
    jb = json.dumps(tree).encode()
    padded = jb + b"\x00" * ((-len(jb)) % 4)
    payload_size = 4 + len(padded)
    header_size = 4 + payload_size
    return struct.pack("<IIII", 4, header_size, payload_size, len(jb)) + padded + bytes(blob)


def scratch(test, prefix):
    """A temp folder removed when the test ends (these tests once left one in /tmp per run).

    Resolved, because extract() writes to the realpath of its output dir and the
    callers compare against it: on macOS TMPDIR is /var/folders/... linked to
    /private/var/folders/..., so an unresolved path would never match."""
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return os.path.realpath(d)


class RealArchive(unittest.TestCase):
    def setUp(self):
        self.data = REAL.read_bytes()
        self.rep = asar.parse(self.data)

    def test_lists_the_bundled_files(self):
        self.assertEqual({f["path"] for f in self.rep["files"]},
                         {"index.js", "package.json", "sub/util.js"})

    def test_integrity_rehashes_from_the_bytes(self):
        for f in self.rep["files"]:
            self.assertTrue(f["in_bounds"], f["path"])
            self.assertIs(asar.verify_sha256(self.data, f), True, f["path"])

    def test_a_flipped_byte_is_a_mismatch(self):
        f = next(f for f in self.rep["files"] if f["path"] == "index.js")
        bad = bytearray(self.data)
        bad[f["offset"]] ^= 0xFF
        self.assertIs(asar.verify_sha256(bytes(bad), f), False)

    def test_extracts_the_tree(self):
        out = scratch(self, "asar-")
        os.rmdir(out)
        written, skipped = asar.extract(self.data, out, "interoperability", "reading the app's own JS")
        self.assertEqual(skipped, [])
        self.assertEqual(Path(out, "sub", "util.js").read_text().strip(), "export const x = 42;")


class Synthetic(unittest.TestCase):
    def test_unpacked_and_link_entries_are_reported_not_read(self):
        data = build_asar([
            {"path": "a.js", "data": b"real"},
            {"path": "native.node", "unpacked": True, "size": 999},
            {"path": "ln", "link": "a.js"},
        ])
        rep = asar.parse(data)
        by = {f["path"]: f for f in rep["files"]}
        self.assertTrue(by["native.node"]["unpacked"])
        self.assertEqual(by["ln"]["link"], "a.js")
        out = scratch(self, "asar-")
        os.rmdir(out)
        written, skipped = asar.extract(data, out, "own_work", "bounds")
        self.assertEqual([os.path.relpath(w, out) for w in written], ["a.js"])
        self.assertTrue(any("unpacked" in why for _, why in skipped))
        self.assertTrue(any("symlink" in why for _, why in skipped))

    def test_out_of_bounds_offset_is_skipped(self):
        data = bytearray(build_asar([{"path": "a.js", "data": b"hello"}]))
        rep = asar.parse(bytes(data))
        f = rep["files"][0]
        chopped = bytes(data[:f["offset"] + 1])        # truncate the data region
        rep2 = asar.parse(chopped)
        self.assertFalse(rep2["files"][0]["in_bounds"])

    def test_traversal_path_never_escapes(self):
        data = build_asar([{"path": "../../etc/evil", "data": b"PWNED"}, {"path": "ok.js", "data": b"fine"}])
        out = scratch(self, "asar-trav-")
        os.rmdir(out)
        written, skipped = asar.extract(data, out, "own_work", "path safety")
        self.assertEqual([os.path.relpath(w, out) for w in written], ["ok.js"])
        got = [os.path.relpath(os.path.join(dp, n), out) for dp, _, ns in os.walk(out) for n in ns]
        self.assertEqual(got, ["ok.js"])

    def test_safe_rel_refuses_dotdot(self):
        for bad in ("../x", "a/../../b", ".."):
            with self.assertRaises(asar.AsarError):
                asar._safe_rel(bad)

    def test_chunked_copy_round_trips(self):
        body = bytes(range(256)) * 20
        data = build_asar([{"path": "big.bin", "data": body}])
        rep = asar.parse(data)
        old = asar.COPY_CHUNK
        asar.COPY_CHUNK = 7
        try:
            self.assertIs(asar.verify_sha256(data, rep["files"][0]), True)
            out = scratch(self, "asar-chunk-")
            os.rmdir(out)
            written, _ = asar.extract(data, out, "own_work", "chunk boundary")
            self.assertEqual(Path(written[0]).read_bytes(), body)
        finally:
            asar.COPY_CHUNK = old


class Malformed(unittest.TestCase):
    def test_not_an_asar(self):
        with self.assertRaises(asar.AsarError):
            asar.parse(b"\x01\x00\x00\x00" + b"\x00" * 40)      # first word not 4
        # an otherwise-valid archive whose only fault is the first word: pins that guard
        # (the rest parses, so nothing else can be what refuses it)
        good = bytearray(build_asar([{"path": "a.js", "data": b"x"}]))
        struct.pack_into("<I", good, 0, 5)
        with self.assertRaises(asar.AsarError):
            asar.parse(bytes(good))

    def test_header_without_files_is_refused(self):
        jb = json.dumps({"notfiles": {}}).encode()
        padded = jb + b"\x00" * ((-len(jb)) % 4)
        data = struct.pack("<IIII", 4, 8 + len(padded), 4 + len(padded), len(jb)) + padded
        with self.assertRaises(asar.AsarError):
            asar.parse(data)

    def test_header_not_json(self):
        bad = struct.pack("<IIII", 4, 20, 16, 8) + b"not-json" + b"\x00" * 20
        with self.assertRaises(asar.AsarError):
            asar.parse(bad)

    def test_absurd_header_size_is_refused(self):
        # a json length past the cap must be refused by the size guard (its own message),
        # not fall through to a confusing JSON error -- the guard stops a huge d[16:16+N]
        # slice that would copy gigabytes out of a large mmap'd file.
        bad = struct.pack("<IIII", 4, 20, 16, asar.MAX_HEADER + 1) + b"{}" + b"\x00" * 40
        with self.assertRaisesRegex(asar.AsarError, "out of range"):
            asar.parse(bad)

    def test_too_deep_tree_does_not_crash(self):
        # a files tree nested past MAX_DEPTH must be reported, not recurse to death
        node = {"files": {"f.js": {"size": 0, "offset": "0"}}}
        for _ in range(asar.MAX_DEPTH + 5):
            node = {"files": {"d": node}}
        jb = json.dumps(node).encode()
        padded = jb + b"\x00" * ((-len(jb)) % 4)
        data = struct.pack("<IIII", 4, 8 + len(padded), 4 + len(padded), len(jb)) + padded
        rep = asar.parse(data)
        self.assertTrue(rep["truncated"] or rep["errors"])


class Gate(unittest.TestCase):
    def setUp(self):
        self.data = REAL.read_bytes()
        self.out = scratch(self, "asar-gate-")
        os.rmdir(self.out)

    def test_refuses_unlawful_purpose(self):
        with self.assertRaises(asar.AsarError):
            asar.extract(self.data, self.out, "piracy", "x")

    def test_refuses_empty_note(self):
        with self.assertRaises(asar.AsarError):
            asar.extract(self.data, self.out, "own_work", "  ")

    def test_refuses_non_empty_folder(self):
        os.makedirs(self.out)
        Path(self.out, "x").write_text("here")
        with self.assertRaises(asar.AsarError):
            asar.extract(self.data, self.out, "own_work", "reading the app")

    def test_lawful_purposes_from_schema(self):
        schema = json.loads((SCRIPTS / "dossier-schema.json").read_text())
        enum = schema["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]
        self.assertEqual(asar.lawful_purposes(), enum)


if __name__ == "__main__":
    unittest.main()
