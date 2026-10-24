"""reverse-engineering skill, native behaviour oracle (oracle.py + oracle_runner.py).

The oracle runs the ORIGINAL function's real machine code in the Unicorn emulator
and compares it, input by input, against a rebuild. These tests are the native
counterpart of the web tier's rebuild-check proof:

  - a faithful rebuild MATCHES the original over the whole corpus (return value
    and output buffers), for both the System V (ELF) and Win64 (PE) ABIs;
  - a rebuild with a wrong constant, a wrong shift, or a dropped case is found
    DIFFERENT, and the first diverging input is reported;
  - an external call (a non-leaf function), an endless loop, and a missing
    function name are each reported cleanly rather than guessed or hung.

Everything compiles and runs in the offline re-oracle sandbox, so the suite
needs Docker and that image and is skipped without them (it builds on first use;
to prebuild: python skills/reverse-engineering/scripts/oracle.py --help then run
once, or docker build the re-oracle.Dockerfile).
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


oracle = load("oracle")
sbx = load("sandbox")

DOCKER_OK, _WHY = sbx.docker_available()
ORACLE_OK = DOCKER_OK and subprocess.run(
    ["docker", "image", "inspect", sbx.image_tag(sbx.ORACLE_DOCKERFILE)], capture_output=True).returncode == 0

ORIG = """
int mix(int a, int b){ return a*3 + (b ^ 0x5A) - 7; }
void split(unsigned x, unsigned* lo, unsigned* hi){ *lo = x & 0xFFFF; *hi = x >> 16; }
int edge(int x){ return x + 1; }
int viaext(int n){ extern int puts(const char*); return puts("hi") + n; }
int spin(int n){ volatile int i = 0; while (i == 0) {} return n; }
"""
GOOD = """
int mix(int a, int b){ int t = b ^ 0x5A; return a*3 + t - 7; }
void split(unsigned x, unsigned* lo, unsigned* hi){ *hi = x >> 16; *lo = x & 0xFFFF; }
int edge(int x){ return x + 1; }
"""
BAD = """
int mix(int a, int b){ return a*3 + (b ^ 0x5A) - 6; }
void split(unsigned x, unsigned* lo, unsigned* hi){ *lo = x & 0xFFFF; *hi = x >> 15; }
int edge(int x){ return x == 2147483647 ? 0 : x + 1; }   /* differs only at INT_MAX */
"""


@unittest.skipUnless(ORACLE_OK, "docker or the re-oracle image is not present")
class OracleProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="re-oracle-test-")
        cls.bins = {}
        for abi, ext in (("sysv", "so"), ("win64", "dll")):
            for name, src in (("orig", ORIG), ("good", GOOD), ("bad", BAD)):
                srcpath = os.path.join(cls.tmp, f"{name}.c")
                Path(srcpath).write_text(src)
                data, _ = oracle.compile_source(srcpath, abi, "mix")
                out = os.path.join(cls.tmp, f"{name}.{ext}")
                Path(out).write_bytes(data)
                cls.bins[(abi, name)] = out

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, abi, rebuild, function, args, ret, corpus=None, **kw):
        return oracle.run_oracle(self.bins[(abi, "orig")], self.bins[(abi, rebuild)], function, args, ret,
                                 abi=abi, corpus=corpus or {"boundaries": True, "fuzz": {"count": 200, "seed": 7}},
                                 **kw)

    def _known(self, abi):
        # absolute anchor: the original's own outputs for known inputs, so a harness
        # bug that would hit both subjects equally (swapped arg regs, unmasked return)
        # cannot hide behind the differential match. mix(5,9)=91, mix(0,0)=83, mix(-1,2)=78.
        r = self._run(abi, "orig", "mix",
                      [{"type": "int", "width": 4, "signed": True}] * 2, {"type": "int", "width": 4, "signed": True},
                      corpus={"seeds": [[5, 9], [0, 0], [-1, 2], [-50, 0]], "boundaries": False})
        return {tuple(s["input"]): s["ret"] for s in r["samples"]}

    def test_known_values_sysv(self):
        got = self._known("sysv")
        self.assertEqual(got[(5, 9)], 91)
        self.assertEqual(got[(0, 0)], 83)
        self.assertEqual(got[(-1, 2)], 78)
        self.assertEqual(got[(-50, 0)], -67)       # negative result: pins the signed-return handling

    def test_known_values_win64(self):
        got = self._known("win64")
        self.assertEqual(got[(5, 9)], 91)
        self.assertEqual(got[(0, 0)], 83)

    # ---- System V (ELF) ----
    def test_sysv_faithful_mix_matches(self):
        r = self._run("sysv", "good", "mix",
                      [{"type": "int", "width": 4, "signed": True}] * 2, {"type": "int", "width": 4, "signed": True})
        self.assertTrue(r["match"], r)
        self.assertEqual(r["agreed"], r["tested"])
        self.assertGreater(r["tested"], 0)

    def test_sysv_off_by_one_mix_is_caught(self):
        r = self._run("sysv", "bad", "mix",
                      [{"type": "int", "width": 4, "signed": True}] * 2, {"type": "int", "width": 4, "signed": True})
        self.assertFalse(r["match"])
        self.assertIsNotNone(r["first_divergence"])
        d = r["first_divergence"]
        self.assertEqual(d["original"]["ret"] - d["rebuild"]["ret"], -1)   # orig = rebuild - 1 (bad adds one)

    def test_sysv_outbuf_faithful_matches(self):
        r = self._run("sysv", "good", "split",
                      [{"type": "int", "width": 4, "signed": False}, {"type": "outbuf", "size": 4},
                       {"type": "outbuf", "size": 4}], {"type": "void"})
        self.assertTrue(r["match"], r)

    def test_sysv_outbuf_wrong_shift_is_caught(self):
        r = self._run("sysv", "bad", "split",
                      [{"type": "int", "width": 4, "signed": False}, {"type": "outbuf", "size": 4},
                       {"type": "outbuf", "size": 4}], {"type": "void"})
        self.assertFalse(r["match"])
        self.assertIsNotNone(r["first_divergence"])
        # the divergence is in an output buffer, not the (void) return
        self.assertNotEqual(r["first_divergence"]["original"]["outbufs"],
                            r["first_divergence"]["rebuild"]["outbufs"])

    def test_boundary_only_divergence_is_caught(self):
        # edge() differs from the original only at INT_MAX; random fuzz almost never
        # hits it, so this passes only because the boundary corpus includes INT_MAX.
        r = self._run("sysv", "bad", "edge",
                      [{"type": "int", "width": 4, "signed": True}], {"type": "int", "width": 4, "signed": True},
                      corpus={"boundaries": True, "fuzz": {"count": 50, "seed": 1}})
        self.assertFalse(r["match"])
        self.assertEqual(r["first_divergence"]["input"], [2147483647])

    def test_sysv_ground_truth_disasm_present(self):
        r = self._run("sysv", "good", "mix",
                      [{"type": "int", "width": 4, "signed": True}] * 2, {"type": "int", "width": 4, "signed": True})
        texts = " ".join(i["text"] for i in r["original_disasm"])
        self.assertIn("ret", texts)
        self.assertTrue(all(c in "0123456789abcdef" for c in r["original_disasm"][0]["bytes"]))

    # ---- Win64 (PE) ----
    def test_win64_faithful_matches(self):
        r = self._run("win64", "good", "mix",
                      [{"type": "int", "width": 4, "signed": True}] * 2, {"type": "int", "width": 4, "signed": True})
        self.assertTrue(r["match"], r)

    def test_win64_off_by_one_is_caught(self):
        r = self._run("win64", "bad", "mix",
                      [{"type": "int", "width": 4, "signed": True}] * 2, {"type": "int", "width": 4, "signed": True})
        self.assertFalse(r["match"])
        self.assertIsNotNone(r["first_divergence"])

    # ---- clean failures, not guesses or hangs ----
    def test_external_call_is_reported_not_guessed(self):
        # viaext/spin live only in the original; compare it to itself -- the point
        # is that emulation of the original reports the external call, not guesses.
        r = self._run("sysv", "orig", "viaext",
                      [{"type": "int", "width": 4, "signed": True}], {"type": "int", "width": 4, "signed": True})
        self.assertGreater(r["errors"]["original"], 0)
        self.assertFalse(r["match"])
        self.assertTrue(any("external callee" in (s["original_error"] or "") for s in r["error_samples"]))

    def test_endless_loop_is_bounded(self):
        r = self._run("sysv", "orig", "spin",
                      [{"type": "int", "width": 4, "signed": True}], {"type": "int", "width": 4, "signed": True},
                      max_insns=5000)
        self.assertGreater(r["errors"]["original"], 0)
        self.assertTrue(any("did not return" in (s["original_error"] or "") for s in r["error_samples"]))

    def test_missing_function_is_an_error(self):
        with self.assertRaises(oracle.OracleError):
            self._run("sysv", "good", "no_such_function_here",
                      [{"type": "int", "width": 4, "signed": True}], {"type": "int", "width": 4, "signed": True})


if __name__ == "__main__":
    unittest.main()
