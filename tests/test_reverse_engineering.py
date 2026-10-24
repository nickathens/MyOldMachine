"""reverse-engineering skill, static parts: the dossier validator, the library
scanner and its proof, the WebAssembly inspector, the code recovery tool's
safety, and fingerprinting.

Each check names a fact the fixture guarantees. The validator and the proof
are instruments, so each has a planted-fault battery here: the instrument must
reject every planted fault and pass the clean input, or it proves nothing.
"""

import base64
import copy
import http.server
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent / "skills" / "reverse-engineering"
SCRIPTS = SKILL / "scripts"
PROOF = SKILL / "proof" / "libscan"
CACHE = Path.home() / ".cache" / "re-libproof"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vd = load(SCRIPTS / "validate_dossier.py", "re_validate_under_test")
ls = load(SCRIPTS / "libscan.py", "re_libscan_under_test")
wi = load(SCRIPTS / "wasm_inspect.py", "re_wasm_under_test")
um = load(SCRIPTS / "unminify.py", "re_unminify_under_test")
fp = load(SCRIPTS / "fingerprint.py", "re_fingerprint_under_test")
SCHEMA = json.loads((SCRIPTS / "dossier-schema.json").read_text())["dossier_schema"]

# Gate the one test that really runs the sandbox on the wabt image being present
# locally, never just on docker: on CI (docker present, image absent) a run would
# build it. Everything else here mocks the sandbox.
_sbx = load(SCRIPTS / "sandbox.py", "re_sandbox_gate")
TOOLS_IMAGE_OK = bool(shutil.which("docker")) and subprocess.run(
    ["docker", "image", "inspect", _sbx.image_tag(_sbx.TOOLS_DOCKERFILE)],
    capture_output=True).returncode == 0

# The fetch tools (fingerprint, libscan, unminify) pull pages through webfetch,
# which imports requests lazily; skip the network-fetch tests where it is absent.
HAS_REQUESTS = importlib.util.find_spec("requests") is not None


def _cap_as_on_linux():
    """Address-space cap for a spawned tool, Linux only. macOS does not enforce
    RLIMIT_AS the same way and a low cap makes the child fail to exec, so leave
    it unset there and rely on the subprocess timeout."""
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))


def validate(dossier):
    errors = vd.collect(dossier, SCHEMA, "$")
    vd.semantic_layer(dossier, errors)
    return errors


# ---------------------------------------------------------------- validator

class DossierValidatorBattery(unittest.TestCase):
    """The verification spine's gate. Every planted fault must be refused with
    its own message; the clean example and the real dossier must pass."""

    def setUp(self):
        self.good = json.loads((SCRIPTS / "dossier.example.json").read_text())

    def test_clean_example_passes(self):
        self.assertEqual(validate(self.good), [])

    def planted(self, mutate, expect):
        d = copy.deepcopy(self.good)
        mutate(d)
        errors = validate(d)
        self.assertTrue(any(re.search(expect, e) for e in errors), f"{expect!r} not in {errors}")

    def test_confirmed_behaviour_without_evidence(self):
        def m(d):
            c = next(c for c in d["claims"] if c["kind"] == "behavioural" and c["status"] == "confirmed")
            del c["evidence"]
        self.planted(m, r"confirmed.*no dynamic evidence")

    def test_confirmed_behaviour_on_static_evidence(self):
        def m(d):
            c = next(c for c in d["claims"] if c["kind"] == "behavioural" and c["status"] == "confirmed")
            c["evidence"]["method"] = "static"
        self.planted(m, r"method|invalid value \"static\"")
        # The schema's enum refuses it first; the semantic rule must refuse it on its own as well,
        # or an edit to the enum would open the gate (removing the rule once changed no test).
        d = copy.deepcopy(self.good)
        m(d)
        errors = []
        vd.semantic_layer(d, errors)
        self.assertTrue(any("is not a dynamic observation" in e for e in errors), errors)

    def test_duplicate_id(self):
        self.planted(lambda d: d["claims"][1].update(id=d["claims"][0]["id"]), r"duplicate")

    def test_dangling_support(self):
        def m(d):
            c = next(c for c in d["claims"] if c["kind"] == "hypothesis")
            c["supports"] = ["gt-not-there"]
        self.planted(m, r"not the id of any claim")

    def test_unknown_kind(self):
        self.planted(lambda d: d["claims"][0].update(kind="guess"), r'"kind" must be one of')

    def test_missing_lawful_basis(self):
        self.planted(lambda d: d.pop("lawful_basis"), r'missing required field "lawful_basis"')

    def test_unlawful_purpose(self):
        self.planted(lambda d: d["lawful_basis"].update(purpose="avoid_paying"), r"invalid value")

    def test_extra_field(self):
        self.planted(lambda d: d.update(confidence="high"), r'unexpected field "confidence"')

    def test_ground_truth_without_literal(self):
        def m(d):
            c = next(c for c in d["claims"] if c["kind"] == "ground_truth")
            del c["evidence"]["literal"]
        self.planted(m, r'missing required field "literal"')

    def test_ground_truth_with_an_empty_literal(self):
        def m(d):
            c = next(c for c in d["claims"] if c["kind"] == "ground_truth")
            c["evidence"]["literal"] = " "
        self.planted(m, r"literal is empty")

    def test_locators_are_reread_against_the_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "chunk.js").write_bytes("é// x;let t=p||u?1e4:6e4;".encode("utf-8"))
            off = "é// x;".encode("utf-8").__len__()
            claim = {"kind": "ground_truth", "id": "gt-t", "statement": "s",
                     "evidence": {"tool": "find", "locator": f"chunk.js byte {off}", "literal": "let t=p||u?1e4:6e4"}}
            d = {"target": "t", "tier": "web", "question": "q", "analyst_date": "2026-10-12",
                 "lawful_basis": {"purpose": "education", "note": "n"}, "claims": [claim]}
            errors = []
            self.assertEqual(vd.evidence_layer(d, tmp, errors), (1, []))
            self.assertEqual(errors, [])
            for bad in ({"locator": f"chunk.js byte {off + 1}"}, {"literal": "let t=p||u?1e4:9e4"},
                        {"locator": f"chunk.js @ char {off}"}):
                with self.subTest(bad=bad):
                    planted = copy.deepcopy(d)
                    planted["claims"][0]["evidence"].update(bad)
                    errs = []
                    vd.evidence_layer(planted, tmp, errs)
                    self.assertTrue(errs and "does not match the artifact" in errs[0], errs)
            char_ok = copy.deepcopy(d)
            char_ok["claims"][0]["evidence"]["locator"] = f"chunk.js @ char {off - 1}"
            errs = []
            vd.evidence_layer(char_ok, tmp, errs)
            self.assertEqual(errs, [], "a char locator counts characters, not bytes")
            # A cited file that is not in the folder, an empty literal, and a locator in another
            # shape used to pass with nothing re-read.
            gone = copy.deepcopy(d)
            gone["claims"][0]["evidence"]["locator"] = "missing.js byte 3"
            errs = []
            self.assertEqual(vd.evidence_layer(gone, tmp, errs), (0, ["missing.js"]))
            self.assertTrue(errs and "not in the evidence folder" in errs[0], errs)
            for bad in ({"literal": ""}, {"locator": "chunk.js offset 7"}, {"locator": "chunk.js:7"}):
                with self.subTest(bad=bad):
                    planted = copy.deepcopy(d)
                    planted["claims"][0]["evidence"].update(bad)
                    errs = []
                    vd.evidence_layer(planted, tmp, errs)
                    self.assertTrue(errs, bad)
            other = copy.deepcopy(d)
            other["claims"][0]["evidence"]["locator"] = "response header on the document request"
            errs = []
            self.assertEqual(vd.evidence_layer(other, tmp, errs), (0, []))
            self.assertEqual(errs, [], "a locator that names no evidence file is not a code citation")

    def test_a_named_file_missing_from_the_folder_fails_in_any_form(self):
        # "b.js:3" is not a re-readable form, and b.js is not in the folder: a name typed from
        # memory. A record kept in a subfolder (runs/r.json) is not a file citation and passes.
        d = tempfile.mkdtemp(prefix="re-evidence-names-")
        try:
            Path(d, "a.js").write_text("hello world")
            os.makedirs(os.path.join(d, "runs"))
            Path(d, "runs", "r.json").write_text("{}")
            claims = [{"kind": "ground_truth", "id": "typo", "statement": "s",
                       "evidence": {"tool": "t", "locator": "b.js:3", "literal": "llo"}},
                      {"kind": "ground_truth", "id": "record", "statement": "s",
                       "evidence": {"tool": "t", "locator": "end of the run in runs/r.json", "literal": "x"}},
                      {"kind": "ground_truth", "id": "stop", "statement": "s",
                       "evidence": {"tool": "t", "locator": "the call in c.js.", "literal": "x"}}]
            errors = []
            vd.evidence_layer({"claims": claims}, d, errors)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertTrue(any("typo" in e and "b.js" in e for e in errors), errors)
        self.assertTrue(any("stop" in e and "c.js" in e for e in errors), errors)   # a full stop after it
        self.assertFalse(any("record" in e for e in errors), errors)

    def test_every_marker_of_a_library_claim_is_reread(self):
        # libscan cites its first marker by offset and lists the rest; only the first was re-read,
        # so a marker typed wrong (or found nowhere) passed --evidence.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.js").write_text("xx__v_isRef yy")
            Path(tmp, "b.js").write_text("zz __v_isReactive")
            ev = {"tool": "libscan.py", "locator": "a.js @ char 2 (+1 more)", "literal": "__v_isRef | __v_isReactive"}
            d = {"target": "t", "tier": "web", "question": "q", "analyst_date": "2026-10-12",
                 "lawful_basis": {"purpose": "education", "note": "n"},
                 "claims": [{"kind": "ground_truth", "id": "gt-lib-vue", "statement": "s", "evidence": ev}]}
            errs = []
            vd.evidence_layer(d, tmp, errs)
            self.assertEqual(errs, [])
            bad = copy.deepcopy(d)
            bad["claims"][0]["evidence"]["literal"] = "__v_isRef | __v_isReactiveX"
            errs = []
            vd.evidence_layer(bad, tmp, errs)
            self.assertTrue(errs and "__v_isReactiveX" in errs[0], errs)

    def test_cli_exit_codes(self):
        ok = subprocess.run([sys.executable, str(SCRIPTS / "validate_dossier.py"),
                             str(SCRIPTS / "dossier.example.json")], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            bad = copy.deepcopy(self.good)
            bad["claims"] = []
            json.dump(bad, fh)
        try:
            res = subprocess.run([sys.executable, str(SCRIPTS / "validate_dossier.py"), fh.name],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn("at least 1", res.stderr)
        finally:
            os.unlink(fh.name)


# ---------------------------------------------------------------- libscan

class LibscanVerdicts(unittest.TestCase):
    def test_two_markers_found_one_trace_none_absent(self):
        gsap = next(lib for lib in ls.LIBRARIES if lib["id"] == "gsap")["markers"]
        text = "x" * 50 + gsap[0] + "y" * 10 + gsap[1]
        res = ls.scan_text(text)
        self.assertEqual(res["gsap"]["status"], "found")
        self.assertEqual(res["gsap"]["markers"][0], {"marker": gsap[0], "offset": 50})
        self.assertEqual(ls.scan_text(gsap[2])["gsap"]["status"], "trace")
        self.assertEqual(ls.scan_text("nothing here")["gsap"]["status"], "absent")

    def test_markers_pool_across_files(self):
        m = next(lib for lib in ls.LIBRARIES if lib["id"] == "three")["markers"]
        res = {r["id"]: r for r in ls.scan_sources([("a.js", "..." + m[0]), ("b.js", m[1] + "..." + m[0])])}
        three = res["three"]
        self.assertEqual(three["status"], "found")
        first = {h["marker"]: h for h in three["markers"]}
        self.assertEqual(first[m[0]]["file"], "a.js")
        self.assertEqual(first[m[0]]["files"], ["a.js", "b.js"])
        self.assertEqual(first[m[1]]["file"], "b.js")

    def test_every_library_needs_two_markers_to_be_found(self):
        for lib in ls.LIBRARIES:
            with self.subTest(lib=lib["id"]):
                self.assertGreaterEqual(len(lib["markers"]), ls.NEED)
                self.assertEqual(len(set(lib["markers"])), len(lib["markers"]), "duplicate marker")

    def test_no_marker_contains_the_claim_separator(self):
        # to_claims joins markers with SEP and --evidence splits on it.
        self.assertEqual(ls.SEP, " | ")
        for lib in ls.LIBRARIES:
            for m in lib["markers"]:
                self.assertNotIn(ls.SEP, m, (lib["id"], m))

    def test_claims_cite_the_file_name_of_a_url(self):
        m = next(lib for lib in ls.LIBRARIES if lib["id"] == "vue")["markers"]
        url = "https://site.test/_next/static/chunks/abc123.js?dpl=xyz"
        claims = ls.to_claims(ls.scan_sources([(url, m[0] + m[1])]), [{"source": url, "bytes": 9}])
        vue = next(c for c in claims if c["id"] == "gt-lib-vue")
        self.assertTrue(vue["evidence"]["locator"].startswith("abc123.js @ char 0"), vue)

    def test_page_scripts_finds_src_preload_and_inline(self):
        html = ('<script src="/a.js"></script><script type="module" src="https://cdn.x/b.mjs?v=1"></script>'
                '<link rel="modulepreload" href="/c.js"><link rel="preload" as="script" href="/d.js">'
                '<link rel="preload" as="style" href="/e.css"><script>var inline=1</script>'
                '<link rel=preload as=script href="/f">'
                '<script src="/a.js"></script>')
        urls, inline = ls.page_scripts(html, "https://site.test/page")
        self.assertEqual(urls, ["https://site.test/a.js", "https://cdn.x/b.mjs?v=1", "https://site.test/c.js",
                                "https://site.test/d.js", "https://site.test/f"])
        self.assertIn("var inline=1", inline)

    def test_page_scripts_keep_their_place_after_non_ascii_text(self):
        # The tag walk searches a lowercased copy: str.lower() turns 'İ' into two characters, so every
        # position after one was off by one and the script's address was missed (found reviewing the fix).
        urls, inline = ls.page_scripts('<p>İİİ</p><SCRIPT SRC="/a.js"></SCRIPT><script>var x=1</script>',
                                       "https://x.test/")
        self.assertEqual(urls, ["https://x.test/a.js"])
        self.assertEqual(inline, "var x=1")

    def test_special_files_in_a_folder_are_skipped(self):
        # A pipe named a.js blocked the read for ever; a link to /dev/zero read until memory ran out.
        d = tempfile.mkdtemp(prefix="re-libscan-special-")
        os.mkfifo(os.path.join(d, "a.js"))
        os.symlink("/dev/zero", os.path.join(d, "b.js"))
        Path(d, "c.js").write_text('x="_reactRootContainer";y="__reactInternalSnapshotBeforeUpdate";')
        try:
            p = subprocess.run([sys.executable, "-I", str(SCRIPTS / "libscan.py"), "--dir", d],
                               capture_output=True, text=True, timeout=30,
                               preexec_fn=_cap_as_on_linux)
        except subprocess.TimeoutExpired:
            self.fail("libscan blocked on a pipe in the folder")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertIn("React", p.stdout)
        self.assertEqual(p.stderr.count("not a regular file"), 2, p.stderr[-400:])

    def test_claims_are_valid_dossier_claims(self):
        m = next(lib for lib in ls.LIBRARIES if lib["id"] == "react")["markers"]
        results = ls.scan_sources([("chunk.js", m[0] + m[1])])
        meta = [{"source": "chunk.js", "bytes": 100, "sha256": "0" * 64}]
        claims = ls.to_claims(results, meta)
        d = {"target": "t", "tier": "web", "question": "q", "analyst_date": "2026-10-12",
             "lawful_basis": {"purpose": "education", "note": "n"}, "claims": claims}
        self.assertEqual(validate(d), [])
        react = next(c for c in claims if c["id"] == "gt-lib-react")
        self.assertIn("chunk.js @ char 0", react["evidence"]["locator"])


class LibscanProofRecord(unittest.TestCase):
    """The marker table may only hold markers the proof has seen: each one in
    its own library's builds and in no build without that library."""

    @classmethod
    def setUpClass(cls):
        cls.result = json.loads((PROOF / "result.json").read_text())

    def test_recorded_proof_passed(self):
        self.assertEqual(self.result["verdict"], "PASS")
        self.assertEqual(self.result["failures"], [])
        self.assertGreaterEqual(len(self.result["bundles"]), 100)

    def test_every_marker_in_the_table_was_proven(self):
        expected_by_bundle = {r["bundle"]: set(r["expected"]) for r in self.result["bundles"]}
        for lib in ls.LIBRARIES:
            for m in lib["markers"]:
                with self.subTest(lib=lib["id"], marker=m):
                    hits = self.result["marker_hits"].get(lib["id"], {}).get(m, [])
                    self.assertTrue(hits, "marker added without rerunning prove.py")
                    stray = [b for b in hits if lib["id"] not in expected_by_bundle[b]]
                    self.assertEqual(stray, [])

    def test_pins_match_the_package_file(self):
        pins = json.loads((PROOF / "package.json").read_text())["dependencies"]
        for dep, spec in pins.items():
            version = spec.rsplit("@", 1)[-1]
            with self.subTest(dep=dep):
                self.assertTrue(self.result["packages"][dep].endswith("@" + version), self.result["packages"][dep])


@unittest.skipUnless((CACHE / "dist").is_dir(), "proof bundles not built (python prove.py build)")
class LibscanProofBattery(unittest.TestCase):
    """prove.py verify must FAIL on each planted marker fault."""

    @classmethod
    def setUpClass(cls):
        cls.prove = load(PROOF / "prove.py", "re_prove_under_test")

    def verify_with(self, mutate):
        fake = load(SCRIPTS / "libscan.py", "re_libscan_planted")
        mutate({lib["id"]: lib for lib in fake.LIBRARIES})
        with mock.patch.object(self.prove, "load_libscan", lambda: fake):
            _, failures, _ = self.prove.verify(CACHE)
        return failures

    def test_patch_is_restored(self):
        self.verify_with(lambda L: None)
        self.assertEqual(self.prove.load_libscan.__name__, "load_libscan")

    def test_clean_table_passes(self):
        _, failures, _ = self.prove.verify(CACHE)
        self.assertEqual(failures, [])

    def test_marker_seen_nowhere(self):
        failures = self.verify_with(lambda L: L["react"]["markers"].append("zz-in-no-bundle-zz"))
        self.assertTrue(any("is in no bundle" in f for f in failures), failures)

    def test_generic_marker_strays_into_other_builds(self):
        failures = self.verify_with(lambda L: L["gsap"]["markers"].append("registerPlugin"))
        self.assertTrue(any("videojs-8" in f and "gsap" in f for f in failures), failures)

    def test_too_few_markers_misses_its_library(self):
        failures = self.verify_with(lambda L: L["swiper"].update(markers=L["swiper"]["markers"][:1]))
        self.assertTrue(any("missing ['swiper']" in f for f in failures), failures)

    def test_marker_written_in_an_entry_file(self):
        failures = self.verify_with(lambda L: L["locomotive-scroll"]["markers"].append("data-scroll-container"))
        self.assertTrue(any("written in an entry file" in f for f in failures), failures)


# ---------------------------------------------------------------- wasm

def uleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def vec(items):
    return uleb(len(items)) + b"".join(items)


def wname(s):
    b = s.encode()
    return uleb(len(b)) + b


def section(sid, payload):
    return bytes([sid]) + uleb(len(payload)) + payload


BODY1 = b"\x00" + b"\x20\x00\x41\x01\x6a\x20\x01\x70\x0b"   # (i + 1) % n
BODY2 = b"\x00" + b"\x41\x2a\x0b"                           # 42
DATA_TEXT = b"hello reel, ten second dwell"


def build_module():
    types = vec([b"\x60" + vec([b"\x7f", b"\x7f"]) + vec([b"\x7f"]), b"\x60" + vec([]) + vec([b"\x7f"])])
    imports = vec([wname("wasi_snapshot_preview1") + wname("fd_write") + b"\x00" + uleb(0),
                   wname("env") + wname("memory") + b"\x02\x00" + uleb(1)])
    funcs = vec([uleb(0), uleb(1)])
    exports = vec([wname("next_slide") + b"\x00" + uleb(1), wname("tick") + b"\x00" + uleb(2)])
    code = vec([uleb(len(BODY1)) + BODY1, uleb(len(BODY2)) + BODY2])
    data = vec([uleb(0) + b"\x41\x10\x0b" + uleb(len(DATA_TEXT)) + DATA_TEXT])
    names_sub = vec([uleb(1) + wname("next_slide"), uleb(2) + wname("tick")])
    name_sec = wname("name") + bytes([1]) + uleb(len(names_sub)) + names_sub
    producers = wname("producers") + vec([wname("language") + vec([wname("Rust") + wname("1.80")]),
                                          wname("processed-by") + vec([wname("rustc") + wname("1.80.0")])])
    return (b"\x00asm\x01\x00\x00\x00" + section(1, types) + section(2, imports) + section(3, funcs)
            + section(7, exports) + section(10, code) + section(11, data) + section(0, name_sec)
            + section(0, producers))


class WasmInspect(unittest.TestCase):
    def setUp(self):
        self.data = build_module()
        self.r = wi.inspect(self.data)

    def test_clean_module_has_no_errors(self):
        self.assertTrue(self.r["magic_ok"])
        self.assertEqual(self.r["errors"], [])

    def test_function_table(self):
        f = {x["index"]: x for x in self.r["functions"]}
        self.assertTrue(f[0]["imported"])
        self.assertEqual(f[0]["import"], "wasi_snapshot_preview1.fd_write")
        self.assertEqual(f[1]["name"], "next_slide")
        self.assertEqual(f[1]["signature"], "(i32, i32) -> (i32)")
        self.assertEqual(f[1]["exports"], ["next_slide"])
        self.assertEqual(f[2]["signature"], "() -> (i32)")

    def test_body_offsets_point_at_the_bodies(self):
        f = {x["index"]: x for x in self.r["functions"]}
        for idx, body in ((1, BODY1), (2, BODY2)):
            off, size = f[idx]["body_offset"], f[idx]["body_size"]
            self.assertEqual(self.data[off:off + size], body)

    def test_data_strings_carry_file_offsets(self):
        (s,) = self.r["data_strings"]
        self.assertEqual(s["text"], DATA_TEXT.decode())
        self.assertEqual(self.data[s["offset"]:s["offset"] + len(DATA_TEXT)], DATA_TEXT)

    def test_producers_and_names(self):
        self.assertEqual(self.r["producers"], {"language": ["Rust 1.80"], "processed-by": ["rustc 1.80.0"]})
        self.assertTrue(self.r["flags"]["name_section"])

    def test_every_truncation_is_reported_never_raised(self):
        for n in range(len(self.data)):
            with self.subTest(prefix=n):
                r = wi.inspect(self.data[:n])
                if n < 8:
                    self.assertFalse(r["magic_ok"])

    def test_random_corruption_is_contained(self):
        rnd = random.Random(20261012)
        for i in range(400):
            buf = bytearray(self.data)
            for _ in range(rnd.randint(1, 4)):
                buf[rnd.randrange(8, len(buf))] = rnd.randrange(256)
            with self.subTest(case=i):
                wi.inspect(bytes(buf))

    def test_import_with_a_missing_type_has_no_signature(self):
        imports = vec([wname("env") + wname("f") + b"\x00" + uleb(5)])
        types = vec([b"\x60" + vec([]) + vec([])])
        r = wi.inspect(b"\x00asm\x01\x00\x00\x00" + section(1, types) + section(2, imports))
        self.assertEqual(r["functions"][0]["signature"], None)

    @staticmethod
    def _one_section(sec_id, payload):
        def leb(n):
            out = bytearray()
            while True:
                b, n = n & 0x7F, n >> 7
                out.append(b | (0x80 if n else 0))
                if not n:
                    return bytes(out)
        return memoryview(b"\x00asm\x01\x00\x00\x00" + bytes([sec_id]) + leb(len(payload)) + payload), leb

    def test_a_flood_of_tiny_entries_is_capped(self):
        # Each entry is a few bytes of file and a Python object of hundreds: 3 MB of empty exports
        # took 201 MB, so a module of about 70 MB would have taken the bot's 8 GB.
        import tracemalloc
        _, leb = self._one_section(0, b"")
        n = 1_000_000
        # Function names are capped at the function cap itself (1,000,000, for real large modules),
        # so their flood is one past it.
        m = wi.MAX_FUNCS + 1
        entries = leb(m) + b"".join(leb(i) + b"\x00" for i in range(m))
        names = b"\x04name" + b"\x01" + leb(len(entries)) + entries
        floods = {"exports": (7, leb(n) + b"\x00\x00\x00" * n), "imports": (2, leb(n) + b"\x00\x00\x00\x00" * n),
                  "types": (1, leb(n) + b"\x60\x00\x00" * n), "data": (11, leb(n) + b"\x01\x00" * n),
                  "names": (0, names)}
        for label, (sec, payload) in floods.items():
            with self.subTest(section=label):
                module, _ = self._one_section(sec, payload)
                tracemalloc.start()
                try:
                    r = wi.inspect(module)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 64 << 20, f"{peak >> 20} MB for a {len(module) >> 20} MB module")
                self.assertTrue(any("cap" in e for e in r["errors"]), r["errors"][:3])

    def test_a_long_printable_run_is_not_copied(self):
        import tracemalloc
        blob = b"A" * (20 << 20)
        _, leb = self._one_section(0, b"")
        module, _ = self._one_section(11, leb(1) + b"\x01" + leb(len(blob)) + blob)
        tracemalloc.start()
        try:
            r = wi.inspect(module)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 4 << 20, f"{peak >> 20} MB held to report 120 characters")
        self.assertEqual(len(r["data_strings"][0]["text"]), 120)

    def test_absurd_function_count_is_capped(self):
        bomb = b"\x00asm\x01\x00\x00\x00" + section(3, uleb(0xFFFFFFF))
        r = wi.inspect(bomb)
        self.assertTrue(any("cap" in e or "overruns" in e for e in r["errors"]), r["errors"])

    def test_disassembly_header_pattern(self):
        drop = "<alloc::vec::Vec<T> as core::ops::drop::Drop>::drop"
        for line, want in (("0008b0 func[7] <Ub>:", ("7", "Ub")), ("01ad86 func[202]:", ("202", None)),
                           ("00001e func[1] <Vec<u8>>:\n", ("1", "Vec<u8>")),
                           (f"000023 func[2] <{drop}>:", ("2", drop))):
            with self.subTest(line=line):
                m = wi.FUNC_HEAD.match(line)
                self.assertIsNotNone(m, "a function header the pattern does not see")
                self.assertEqual((m.group(2), m.group(3)), want)

    @unittest.skipUnless(shutil.which("wasm-objdump"), "host wabt not installed")
    def test_one_function_stops_where_the_next_begins(self):
        # Rust and C++ name sections carry names like Vec<u8>. The header pattern once missed
        # them, so one function's disassembly ran on into the next ones.
        drop = "<alloc::vec::Vec<T> as core::ops::drop::Drop>::drop"
        bodies = [b"\x00\x41\x07\x0b", b"\x00\x41\x2a\x0b", b"\x00\x41\x63\x0b"]
        names = vec([uleb(i) + wname(n) for i, n in enumerate(["first", "Vec<u8>", drop])])
        module = (b"\x00asm\x01\x00\x00\x00" + section(1, vec([b"\x60" + vec([]) + vec([b"\x7f"])]))
                  + section(3, vec([uleb(0)] * 3)) + section(10, vec([uleb(len(b)) + b for b in bodies]))
                  + section(0, wname("name") + bytes([1]) + uleb(len(names)) + names))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.wasm")
            Path(path).write_bytes(module)
            lines, err = wi.disasm_function(path, "first", True)
            self.assertEqual(len(lines), 3, lines)
            lines, err = wi.disasm_function(path, "1", True)
            self.assertIsNone(err)
            self.assertIn("func[1] <Vec<u8>>:", lines[0])
            lines, err = wi.disasm_function(path, drop, True)
            self.assertIsNone(err)
            self.assertIn("41 63", lines[1])

    def check_disasm_and_decompile(self, host):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.wasm")
            Path(path).write_bytes(self.data)
            lines, err = wi.disasm_function(path, "next_slide", host)
            self.assertIsNone(err)
            self.assertIn("func[1] <next_slide>:", lines[0])
            self.assertTrue(any(re.search(r"^ [0-9a-f]+: 20 00 +\| local.get 0", ln) for ln in lines), lines)
            by_index, _ = wi.disasm_function(path, "2", host)
            self.assertIn("func[2] <tick>:", by_index[0])
            text, err = wi.decompile_function(path, "tick", host)
            self.assertIsNone(err)
            self.assertIn("return 42", "\n".join(text))
            self.assertIsNone(wi.disasm_function(path, "nope", host)[0])

    @unittest.skipUnless(shutil.which("wasm-objdump") and shutil.which("wasm-decompile"), "host wabt not installed")
    def test_disasm_and_decompile_on_the_host(self):
        self.check_disasm_and_decompile(host=True)

    @unittest.skipUnless(TOOLS_IMAGE_OK, "re-tools sandbox image not built")
    def test_disasm_and_decompile_in_the_sandbox(self):
        self.check_disasm_and_decompile(host=False)

    def test_a_planted_link_or_pipe_never_reaches_the_host(self):
        # wabt runs as our uid with /out writable: a compromised one can leave a link to a host file
        # (the result, and a --wat-out file, became that link) or a pipe (reading it hangs forever).
        sbx = load(SCRIPTS / "sandbox.py", "re_sandbox_for_wasm")
        with tempfile.TemporaryDirectory() as tmp:
            secret = os.path.join(tmp, "secret.txt")
            Path(secret).write_text("HOST-SECRET-CANARY")
            mod = os.path.join(tmp, "m.wasm")
            Path(mod).write_bytes(self.data)
            for kind in ("link", "pipe"):
                def run(cmd, rw=(), kind=kind, **kw):
                    out = [m.rpartition(":")[0] for m in rw if m.endswith(":/out")][0]
                    p = os.path.join(out, "result.txt")
                    os.symlink(secret, p) if kind == "link" else os.mkfifo(p)
                    return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": "", "argv": []}
                dest = os.path.join(tmp, f"{kind}.wat")
                with self.subTest(kind=kind), mock.patch.dict(sys.modules, {"sandbox": sbx}), \
                        mock.patch.object(sbx, "run", run), mock.patch.object(sbx, "ensure_tools_image", lambda: "i"):
                    done = []
                    t = threading.Thread(target=lambda: done.append(wi.wabt_to_file("wasm2wat", [], mod, dest)),
                                         daemon=True)
                    t.start()
                    t.join(20)                    # a pipe must not block: fail, never hang the run
                    self.assertTrue(done, "blocked on a planted pipe")
                    self.assertTrue(done[0], "a planted file was accepted as the result")
                    self.assertFalse(os.path.lexists(dest))

    def test_a_big_module_is_read_without_copying_it(self):
        # main() read the whole file, and each section and body was sliced out as a copy: 200 MB
        # peaked at 612 MB, so a multi-gigabyte module could take the bot's shared memory.
        import contextlib
        import io
        import tracemalloc
        n = 48 << 20
        body = b"\x00" + b"\x01" * (n - 2) + b"\x0b"
        module = (b"\x00asm\x01\x00\x00\x00" + section(1, vec([b"\x60" + vec([]) + vec([])]))
                  + section(3, vec([uleb(0)])) + section(10, vec([uleb(len(body)) + body])))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.wasm")
            Path(path).write_bytes(module)
            del body, module
            tracemalloc.start()
            try:
                with mock.patch.object(sys, "argv", ["wasm_inspect.py", path, "--out", os.path.join(tmp, "r.json")]), \
                        contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                    wi.main()
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            report = json.loads(Path(tmp, "r.json").read_text())
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(report["functions"][0]["body_size"], n)
        self.assertLess(peak, 8 << 20, f"peak {peak >> 20} MB for a {n >> 20} MB module")


# ---------------------------------------------------------------- unminify safety

class UnminifySafety(unittest.TestCase):
    def test_safe_rel_strips_escapes(self):
        cases = {"webpack:///src/app.js": "src/app.js", "../../etc/passwd": "etc/passwd",
                 "/abs/x.js": "abs/x.js", "a/./b/../c.js": "a/b/c.js", "https://x/y.js?v=1#h": "x/y.js",
                 "C:\\win\\z.js": "C:/win/z.js",
                 "..": None, "": None}
        for src, want in cases.items():
            with self.subTest(src=src):
                got = um.safe_rel(src)
                self.assertEqual(got if got is None else got.replace(os.sep, "/"), want)

    def test_map_recovery_never_writes_outside(self):
        smap = {"version": 3, "sources": ["src/app.js", "../../../escape.js", "webpack:///lib/x.js", 7],
                "sourcesContent": ["console.log('app')", "bad", "lib", "n"], "mappings": ""}
        b64 = base64.b64encode(json.dumps(smap).encode()).decode()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "in", "bundle.js")
            src.parent.mkdir()
            src.write_text("x;\n//# sourceMappingURL=data:application/json;base64," + b64)
            out = Path(tmp, "out")
            res = subprocess.run([sys.executable, "-I", str(SCRIPTS / "unminify.py"), "--file", str(src),
                                  "--out-dir", str(out)], capture_output=True, text=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
            written = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
            self.assertEqual(written, ["escape.js", "lib/x.js", "src/app.js"])
            self.assertEqual(Path(out, "src/app.js").read_text(), "console.log('app')")
            outside = [p for p in Path(tmp).rglob("escape.js") if out not in p.parents]
            self.assertEqual(outside, [])
            self.assertIn("source path is not a string", res.stderr)

    def test_a_local_map_reference_stays_in_the_files_folder(self):
        # The map's name comes from the untrusted file: an absolute path read any file of ours, and
        # sourceMappingURL=/dev/zero read without end until memory ran out (the bot's shared cgroup).
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "outside.map").write_text('{"version":3,"sources":[],"mappings":"","note":"SECRET"}')
            inside = Path(tmp, "in")
            inside.mkdir()
            Path(inside, "ok.js.map").write_text('{"version":3,"sources":[],"mappings":""}')
            src = str(inside / "app.js")
            for ref in (os.path.join(tmp, "outside.map"), "../outside.map", "/dev/null"):
                with self.subTest(ref=ref):
                    with self.assertRaisesRegex(ValueError, "not read"):
                        um.resolve_map(ref, src, False, 5, False)
            self.assertIn('"version":3', um.resolve_map("ok.js.map", src, False, 5, False))

    @unittest.skipUnless(all(um.installed_version(k) == v for k, v in um.PINNED.items()), "pinned tools absent")
    def test_a_link_inside_the_output_folder_cannot_carry_a_write_out(self):
        # safe_rel strips "..", but a link already inside --out-dir (src -> elsewhere) would carry
        # "src/app.js" out of it; the resolved-path check is what stops that, and no test held it.
        d = tempfile.mkdtemp(prefix="re-um-link-")
        try:
            out, elsewhere = Path(d, "out"), Path(d, "elsewhere")
            out.mkdir()
            elsewhere.mkdir()
            os.symlink(elsewhere, out / "src")
            m = {"version": 3, "sources": ["src/app.js"], "sourcesContent": ["X"], "mappings": ""}
            written, skipped, _ = um.reconstruct(json.dumps(m), str(out))
            leaked = list(elsewhere.iterdir())
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(leaked, [])
        self.assertEqual(written, [])
        self.assertIn("escaped", skipped[0][1])

    def test_sources_that_differ_only_by_a_query_both_survive(self):
        # webpack names colliding modules "file?hash"; the query is stripped, and the second
        # once overwrote the first while the tool reported 2/2 recovered.
        m = {"version": 3, "sources": ["webpack:///./src/App.vue?7ba5", "webpack:///./src/App.vue?3dfd"],
             "sourcesContent": ["TEMPLATE PART", "SCRIPT PART"], "mappings": ""}
        d = tempfile.mkdtemp(prefix="re-um-dup-")
        try:
            written, skipped, total = um.reconstruct(json.dumps(m), d)
            texts = sorted(Path(d, w).read_text() for w in written)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(len(set(written)), 2, written)
        self.assertEqual(texts, ["SCRIPT PART", "TEMPLATE PART"])

    def test_a_file_and_a_folder_of_one_name_do_not_stop_the_recovery(self):
        # "src/a" and "src/a/b.js" cannot both exist; the error once ended the whole recovery,
        # which then said the map "carried no usable sourcesContent".
        m = {"version": 3, "sources": ["src/a", "src/a/b.js", "src/c.js"],
             "sourcesContent": ["FILE A", "FILE B", "FILE C"], "mappings": ""}
        d = tempfile.mkdtemp(prefix="re-um-clash-")
        try:
            try:
                written, skipped, total = um.reconstruct(json.dumps(m), d)
            except OSError as e:
                self.fail(f"one clashing path stopped the whole recovery: {e}")
            texts = sorted(Path(d, w).read_text() for w in written)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(len(written) + len(skipped), 3, (written, skipped))
        self.assertIn("FILE A", texts)
        self.assertIn("FILE C", texts)

    def test_a_local_file_must_be_a_regular_file_under_the_cap(self):
        try:
            p = subprocess.run([sys.executable, "-I", str(SCRIPTS / "unminify.py"), "--file", "/dev/zero", "--no-map",
                                "--engine", "beautify"], capture_output=True, text=True, timeout=30,
                               preexec_fn=_cap_as_on_linux)
        except subprocess.TimeoutExpired:
            self.fail("unminify kept reading /dev/zero")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not a regular file", p.stderr)
        d = tempfile.mkdtemp(prefix="re-um-big-")
        try:
            big = Path(d, "big.js")
            big.write_bytes(b"x" * 4096)
            with mock.patch.object(um, "MAX_FETCH_BYTES", 1000):
                with self.assertRaisesRegex(ValueError, "1000"):
                    um.load(types.SimpleNamespace(url=None, file=str(big)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_map_never_lands_in_the_working_folder(self):
        # Asked for --out-file, a file with a source map once wrote ./recovered wherever the tool ran
        # (the bot's own folder) and never wrote the file asked for.
        smap = {"version": 3, "sources": ["src/app.js"], "sourcesContent": ["let a=1e4;"], "mappings": ""}
        b64 = base64.b64encode(json.dumps(smap).encode()).decode()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "chunk.js").write_text("var a=1e4;\n//# sourceMappingURL=data:application/json;base64," + b64)
            res = subprocess.run([sys.executable, "-I", str(SCRIPTS / "unminify.py"), "--file", "chunk.js",
                                  "--out-file", "chunk.readable.js", "--engine", "wakaru"],
                                 capture_output=True, text=True, timeout=120, cwd=tmp)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(Path(tmp, "recovered").exists())
            self.assertIn("10000", Path(tmp, "chunk.readable.js").read_text())
            self.assertIn("--out-dir", res.stderr)


# ---------------------------------------------------------------- fingerprint

class Fingerprint(unittest.TestCase):
    def test_definitional_and_header_markers(self):
        body = '<script id="__NEXT_DATA__" type="application/json">{}</script><script src="/_next/static/a.js">'
        dets = {d["name"]: d for d in fp.scan(body, "x-vercel-id: fra1::abc\nserver: Vercel")}
        self.assertEqual(dets["Next.js"]["certainty"], "definitional")
        self.assertIn("Vercel", dets)
        claims = fp.to_claims(list(dets.values()))
        d = {"target": "t", "tier": "web", "question": "q", "analyst_date": "2026-10-12",
             "lawful_basis": {"purpose": "education", "note": "n"}, "claims": claims}
        self.assertEqual(validate(d), [])

    def test_plain_page_detects_nothing(self):
        self.assertEqual(fp.scan("<html><body><p>hello</p></body></html>", "server: nginx"), [])

    def test_base64_noise_is_not_a_library(self):
        # Inline images and fonts carry "//" and, case-blind, "gsap", "lenis", "swiper": the URL
        # patterns once read any such run as a script address (GSAP from random image data).
        noise = '<img src="data:image/png;base64,iVBORw0KGgoAAAANS//AAAAxGsApQ+Zz9lenisX/swiperQ==">'
        self.assertEqual(fp.scan(noise, ""), [])

    def test_script_addresses_still_name_their_library(self):
        page = ('<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>'
                '<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/ScrollTrigger.min.js"></script>'
                '<script src="https://unpkg.com/lenis@1.3.4/dist/lenis.min.js"></script>'
                '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">'
                '<script src="https://unpkg.com/three@0.160.0/build/three.module.js"></script>'
                '<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>'
                '<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>')
        dets = {d["name"]: d for d in fp.scan(page, "")}
        for name in ("GSAP", "GSAP ScrollTrigger", "Lenis (smooth scroll)", "Swiper", "Three.js", "jQuery", "React"):
            with self.subTest(name=name):
                self.assertIn(name, dets)
        self.assertEqual((dets["GSAP"]["version"], dets["jQuery"]["version"]), ("3.12.5", "3.7.1"))
        cdnjs = {d["name"]: d for d in fp.scan(page.split("</script>")[1], "")}
        self.assertIn("/gsap/", cdnjs["GSAP"]["literal"])

    def test_a_framer_hosted_file_is_not_framer_motion(self):
        # No Motion build contains the Framer CDN's host name (0 of the proof's bundles).
        names = {d["name"] for d in fp.scan('<img src="https://framerusercontent.com/images/a.png">', "")}
        self.assertEqual(names, {"Framer (site builder)"})

    def test_a_big_inline_blob_scans_quickly(self):
        # Each "//" once started a scan to the end of the blob: 2 s at 400 KB, minutes at the 5 MB cap.
        blob = base64.b64encode(random.Random(5).randbytes(1_500_000)).decode()
        t0 = time.monotonic()
        fp.scan('<img src="data:image/png;base64,' + blob + '">', "")
        self.assertLess(time.monotonic() - t0, 10)

    def test_a_hostile_page_cannot_stall_the_scans(self):
        # Each unclosed tag opener once started a scan to the end of the page: 420 KB of
        # '<script type="module" ' took fingerprint 6 s (14 min at its cap), and 160 KB of
        # '<script>' or '<script ' took libscan's page reader 20 s and 57 s (hours at its cap).
        cases = [("fingerprint", fp.scan, ('<script type="module" ' * 20000, "")),
                 ("libscan <script>", ls.page_scripts, ("<script>" * 20000, "https://x.test/")),
                 ("libscan <script ", ls.page_scripts, ("<script " * 20000, "https://x.test/")),
                 ("libscan <link ", ls.page_scripts, ("<link rel=preload " * 20000, "https://x.test/"))]
        for name, fn, args in cases:
            with self.subTest(name=name):
                t0 = time.monotonic()
                fn(*args)
                self.assertLess(time.monotonic() - t0, 3)


# ---------------------------------------------------------------- downloads

class _Stream(http.server.BaseHTTPRequestHandler):
    """Answers every GET with up to 128 MB and records how much it managed to send."""
    sent = []

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        chunk, n = b"<p>" + b"x" * 65533, 0
        try:
            while n < 128_000_000:
                self.wfile.write(chunk)
                n += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        type(self).sent.append(n)

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_REQUESTS, "requests not installed")
class CappedDownloads(unittest.TestCase):
    """A response far larger than the cap must stop being read at the cap. The
    downloaders used to take the whole response into memory first: a 200 MB page
    took fingerprint.py to 423 MB, and an endless one would take the bot down."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stream)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/big"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def sent_after(self, before):
        deadline = time.monotonic() + 30
        while len(_Stream.sent) == before and time.monotonic() < deadline:
            time.sleep(0.05)
        return _Stream.sent[-1]

    def test_fingerprint_reads_at_most_its_cap(self):
        before = len(_Stream.sent)
        _, _, _, body = fp.fetch(self.url, 20, True)
        self.assertLessEqual(len(body), fp.MAX_BODY_BYTES)
        self.assertLess(self.sent_after(before), 64_000_000)

    def test_libscan_page_reads_at_most_its_cap(self):
        before = len(_Stream.sent)
        subprocess.run([sys.executable, "-I", str(SCRIPTS / "libscan.py"), "--url", self.url],
                       capture_output=True, timeout=120)
        self.assertLess(self.sent_after(before), 64_000_000)

    def test_unminify_refuses_a_file_past_its_cap(self):
        before = len(_Stream.sent)
        args = types.SimpleNamespace(url=self.url, file=None, timeout=20, insecure=False)
        with mock.patch.object(um, "MAX_FETCH_BYTES", 1_000_000, create=True):
            with self.assertRaises(ValueError) as cm:
                um.load(args)
        self.assertIn("1000000", str(cm.exception))
        self.assertLess(self.sent_after(before), 64_000_000)


class _Redirects(http.server.BaseHTTPRequestHandler):
    """/bomb answers 302 with a gzip body that unpacks to 96 MB; /loop redirects to itself."""
    BOMB = None

    def do_GET(self):
        if self.path == "/bomb":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(self.BOMB)))
            self.end_headers()
            self.wfile.write(self.BOMB)
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            body = b'<script src="/x.js"></script>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_REQUESTS, "requests not installed")
class RedirectBodies(unittest.TestCase):
    """requests reads and unpacks a redirect's own body whole before following it, whatever the
    cap on the final page: 597 KB of gzip in a 302 took the process up 618 MB, so a few MB from a
    hostile site could take the bot's shared memory. Redirects are now followed by hand."""

    @classmethod
    def setUpClass(cls):
        import gzip
        _Redirects.BOMB = gzip.compress(b"\0" * (96 << 20), compresslevel=9)
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirects)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def peak_of(self, fn, *args):
        import tracemalloc
        tracemalloc.start()
        try:
            out = fn(*args)
            return out, tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    def test_a_redirect_body_is_never_unpacked(self):
        for name, fn, args in (("fingerprint", fp.fetch, (self.base + "/bomb", 20, True)),
                               ("libscan", ls.fetch_page, (self.base + "/bomb", 20, True)),
                               ("unminify", um.fetch, (self.base + "/bomb", 20, False))):
            with self.subTest(tool=name):
                out, peak = self.peak_of(fn, *args)
                self.assertLess(peak, 16 << 20, f"{peak >> 20} MB held while following one redirect")
                self.assertIn("x.js", str(out))
        url = fp.fetch(self.base + "/bomb", 20, True)[0]
        self.assertTrue(url.endswith("/final"), url)

    def test_a_redirect_loop_ends(self):
        with self.assertRaises(Exception) as cm:
            fp.fetch(self.base + "/loop", 20, True)
        self.assertIn("redirect", str(cm.exception).lower())


class _Slow(http.server.BaseHTTPRequestHandler):
    """/drip sends its headers and then one byte every 0.2 s for ever; /drip-head sends even its
    header one byte at a time; /charset serves a Next.js page labelled with a codec that is not text."""

    def do_GET(self):
        try:
            if self.path == "/charset":
                body = b'<script src="/_next/static/chunks/x.js"></script>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=zlib")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/drip-head":
                for b in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"y" * 100_000:
                    self.wfile.write(bytes([b]))
                    time.sleep(0.2)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "100000000")
            self.end_headers()
            while True:
                self.wfile.write(b"x")
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_REQUESTS, "requests not installed")
class SlowAndOddServers(unittest.TestCase):
    """A server can answer one byte at a time, each inside the per-read timeout, so a download
    bounded only per read never ends (5 MB at five bytes a second is eleven days). And a response
    can name a codec that is not a text encoding as its charset."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_a_dripping_server_cannot_hold_a_download(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import webfetch; webfetch.DEADLINE = 2; "
                "webfetch.get(sys.argv[2], 1)")
        for path in ("/drip", "/drip-head"):
            with self.subTest(path=path):
                t0 = time.monotonic()
                try:
                    p = subprocess.run([sys.executable, "-I", "-c", code, str(SCRIPTS), self.base + path],
                                       capture_output=True, text=True, timeout=10)
                except subprocess.TimeoutExpired:
                    self.fail(f"{path}: still downloading after 10 s with a 1 s read timeout")
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("2 s", p.stderr[-300:])
                self.assertLess(time.monotonic() - t0, 8)

    def test_a_callers_own_timer_survives_a_fetch(self):
        import signal
        signal.setitimer(signal.ITIMER_REAL, 30)
        try:
            fp.fetch(self.base + "/charset", 20, True)
            left = signal.getitimer(signal.ITIMER_REAL)[0]
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        self.assertGreater(left, 25, "the fetch's deadline cancelled the caller's own timer")

    def test_a_charset_that_is_not_text_falls_back(self):
        try:
            _, _, _, body = fp.fetch(self.base + "/charset", 20, True)
        except (LookupError, UnicodeError) as e:
            self.fail(f"one charset header stopped the fetch: {e}")
        self.assertIn("Next.js", [d["name"] for d in fp.scan(body, "")])


# ---------------------------------------------------------------- report

class DossierReport(unittest.TestCase):
    def test_backticks_and_pipes_in_literals_survive(self):
        dr = load(SCRIPTS / "dossier.py", "re_dossier_under_test")
        d = {"target": "t", "tier": "web", "question": "q", "analyst_date": "2026-10-12",
             "lawful_basis": {"purpose": "education", "note": "n"},
             "claims": [{"kind": "ground_truth", "id": "g", "statement": "s",
                         "evidence": {"tool": "x", "locator": "a.js byte 1", "literal": "`a${b}` | c"}},
                        {"kind": "behavioural", "id": "b", "statement": "s", "status": "confirmed",
                         "evidence": {"method": "runtime", "locator": "run", "literal": "x`y | z"}}]}
        md = dr.render(d)
        self.assertIn("`` `a${b}` \\| c ``", md)   # table cell: a longer fence, and the pipe escaped
        self.assertIn("``x`y | z``", md)            # list item: a pipe needs no escape inside code


if __name__ == "__main__":
    unittest.main()
