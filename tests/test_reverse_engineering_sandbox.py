"""reverse-engineering skill: the offline sandbox, and the two code-recovery
engines that rely on installed tools (wakaru on the host, webcrack inside the
sandbox).

The sandbox is an instrument, so its self-check is proven both ways: in the
real sandbox every break-out probe must fail, and in a deliberately leaky
container the same probes must report each breach. Skipped when Docker or the
pinned tools are not present (the tools install with unminify.ensure_tools()).
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"
FIXTURES = ROOT / "fixtures" / "reverse_engineering"


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sbx = load("sandbox")
um = load("unminify")
DOCKER_OK, DOCKER_WHY = sbx.docker_available()
TOOLS_OK = all(um.installed_version(k) == v for k, v in um.PINNED.items())
# Gate on the base image already being present locally, never just on docker:
# the default image is a public pinned ref, so a run would pull it. On CI
# (docker present, image absent) these skip rather than reach the network.
IMAGE_OK = DOCKER_OK and subprocess.run(
    ["docker", "image", "inspect", sbx.DEFAULT_IMAGE], capture_output=True).returncode == 0


@unittest.skipUnless(IMAGE_OK, f"docker base image unavailable: {DOCKER_WHY}")
class SandboxHolds(unittest.TestCase):
    def test_every_probe_holds(self):
        res = sbx.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(res["lines"]), 8, res["lines"])

    def test_probes_report_a_leaky_container(self):
        home = os.path.realpath(os.path.expanduser("~"))
        leaky = ["docker", "run", "--rm", "--mount", f"type=bind,source={SCRIPTS},target={home},readonly",
                 sbx.DEFAULT_IMAGE, "node", "-e", sbx.CHECK_JS, home]
        out = subprocess.run(leaky, capture_output=True, text=True, timeout=90).stdout
        verdicts = dict(line.split(" ")[:2] for line in out.splitlines() if " PASS " in line or " FAIL " in line)
        self.assertEqual(len(verdicts), 8, out)
        self.assertEqual(set(verdicts.values()), {"FAIL"}, verdicts)

    def test_timeout_kills_the_container(self):
        t0 = time.monotonic()
        res = sbx.run(["sleep", "60"], timeout=3)
        self.assertTrue(res["timed_out"])
        self.assertLess(time.monotonic() - t0, 40)
        name = res["argv"][res["argv"].index("--name") + 1]
        ps = subprocess.run(["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(ps.stdout.strip(), "")

    def test_the_time_limit_holds_when_the_caller_dies(self):
        # The limit was kept by the calling process: killed (a turn cut short), it left the
        # container running to its natural end, 19 s into a 10 s limit.
        def running():
            out = subprocess.run(["docker", "ps", "--filter", "name=re-sbx-", "--format", "{{.Names}}"],
                                 capture_output=True, text=True, timeout=30).stdout
            return set(out.split())
        before = running()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import sandbox; "
                "sandbox.run(['sleep', '120'], timeout=3)")
        caller = subprocess.Popen([sys.executable, "-I", "-c", code, str(SCRIPTS)])
        deadline = time.monotonic() + 30
        while not (running() - before) and time.monotonic() < deadline:
            time.sleep(0.3)
        mine = running() - before
        self.assertTrue(mine, "the container never started")
        caller.kill()
        caller.wait()
        time.sleep(3 + 12)                      # the limit, then the grace for TERM and KILL
        left = running() & mine
        for name in left:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        self.assertFalse(left, "the container outlived its time limit once its caller was gone")

    def test_a_raised_output_bound_is_for_stdout_only(self):
        res = sbx.run(["node", "-e", "process.stdout.write('o'.repeat(1500000)); process.stderr.write('e'.repeat(1500000))"],
                      timeout=60, max_output=4 << 20)
        self.assertEqual(len(res["stdout"]), 1500000)
        self.assertFalse(res["stdout_cut"])
        self.assertLess(len(res["stderr"]), sbx.MAX_OUTPUT + 200)

    def test_output_is_capped(self):
        res = sbx.run(["node", "-e", "process.stdout.write('x'.repeat(1500000))"], timeout=60)
        self.assertEqual(res["returncode"], 0)
        self.assertLess(len(res["stdout"]), sbx.MAX_OUTPUT + 200)
        self.assertIn("bytes cut", res["stdout"])

    def test_a_run_cannot_write_past_its_cap(self):
        # The poll alone lets a fast writer through between two looks: 64 MB of zeros reach the page
        # cache in well under 0.1 s. The per-file limit stops it at the cap itself.
        with tempfile.TemporaryDirectory() as tmp:
            res = sbx.run(["sh", "-c", "head -c 64000000 /dev/zero > /out/big; echo done"], rw=[f"{tmp}:/out"],
                          max_write=1 << 20, timeout=60)
            big = os.path.join(tmp, "big")
            size = os.path.getsize(big) if os.path.exists(big) else 0
            self.assertLessEqual(size, 1 << 20, f"{size} bytes written past a 1 MB cap ({res['stderr'][-200:]})")

    def test_only_the_given_mount_is_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "in").mkdir()
            Path(tmp, "out").mkdir()
            Path(tmp, "in", "a.txt").write_text("input")
            js = ("const fs=require('fs');let r=[];"
                  "try{fs.writeFileSync('/in/b.txt','x');r.push('in-writable')}catch(e){r.push('in-'+e.code)}"
                  "fs.writeFileSync('/out/c.txt',fs.readFileSync('/in/a.txt'));r.push('out-ok');console.log(r.join(' '))")
            res = sbx.run(["node", "-e", js], ro=[f"{tmp}/in:/in"], rw=[f"{tmp}/out:/out"], timeout=60)
            self.assertEqual(res["stdout"].strip(), "in-EROFS out-ok", res)
            self.assertEqual(Path(tmp, "out", "c.txt").read_text(), "input")
            self.assertEqual(os.stat(Path(tmp, "out", "c.txt")).st_uid, os.getuid())


class SandboxRefusals(unittest.TestCase):
    def test_refuses_root_and_home_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            comma = Path(tmp, "a,b")
            comma.mkdir()
            with self.assertRaises(sbx.SandboxError):
                sbx._mount(f"{comma}:/in", "ro")
        # A folder that holds the home folder (/home) exposes all of it, the same as mounting ~ itself.
        parent = os.path.dirname(os.path.realpath(os.path.expanduser("~")))
        for spec in ("/:/host", os.path.expanduser("~") + ":/h", parent + ":/p", "relative-no-colon"):
            with self.subTest(spec=spec):
                with self.assertRaises(sbx.SandboxError):
                    sbx._mount(spec, "ro")


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


class SandboxOutputReadBack(unittest.TestCase):
    """What a container leaves in its output folder is untrusted on the way out: a compromised
    tool runs as our uid with the folder writable and can put a link or a pipe where its
    result belongs. open_output and copy_output_tree read only regular files reached through
    no link, and never block."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="re-readback-")
        self.out = os.path.join(self.d, "out")
        os.makedirs(os.path.join(self.out, "sub"))
        self.secret = os.path.join(self.d, "secret.txt")
        Path(self.secret).write_text("HOST-SECRET-CANARY")
        Path(self.out, "sub", "ok.txt").write_text("result")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_regular_result_is_read(self):
        with sbx.open_output(self.out, "sub/ok.txt") as fh:
            self.assertEqual(fh.read(), b"result")

    def test_links_and_pipes_are_refused(self):
        os.symlink(self.secret, os.path.join(self.out, "link.txt"))
        os.mkfifo(os.path.join(self.out, "pipe.txt"))
        os.symlink(self.d, os.path.join(self.out, "up"))              # a linked folder on the way
        for rel in ("link.txt", "pipe.txt", "up/secret.txt", "../secret.txt"):
            with self.subTest(rel=rel):
                kind, value = within(20, sbx.open_output, self.out, rel)   # a pipe must not block
                self.assertEqual(kind, "raised", f"{kind}: {value!r}")
                self.assertIsInstance(value, sbx.SandboxError)

    def test_tree_copy_keeps_files_and_drops_links(self):
        os.symlink(self.secret, os.path.join(self.out, "sub", "evil.txt"))
        os.symlink(self.d, os.path.join(self.out, "sub", "evil-dir"))
        dst = os.path.join(self.d, "dst")
        skipped = sbx.copy_output_tree(self.out, "sub", dst)
        self.assertEqual(sorted(os.listdir(dst)), ["ok.txt"])
        self.assertEqual(sorted(skipped), ["evil-dir", "evil.txt"])

    def test_unminify_never_returns_a_planted_link(self):
        def plant(cmd, rw=(), **kw):
            out = [m.rpartition(":")[0] for m in rw if m.endswith(":/out")][0]
            os.makedirs(os.path.join(out, "modules"))
            os.symlink(self.secret, os.path.join(out, "modules", "deobfuscated.js"))
            return {"returncode": 0, "timed_out": False, "stdout": "", "stderr": "", "argv": []}
        src = os.path.join(self.d, "in.js")
        Path(src).write_text("var a=1;")
        out_file = os.path.join(self.d, "clean.js")
        with mock.patch.object(sbx, "run", plant), mock.patch.object(um, "ensure_tools", lambda: None), \
                mock.patch.dict(sys.modules, {"sandbox": sbx}):
            for kw in ({}, {"out_file": out_file}, {"out_dir": os.path.join(self.d, "mods")}):
                with self.subTest(kw=kw):
                    text, note = um.run_webcrack_sandboxed(src, **kw)
                    self.assertIsNone(text, "a planted link was read as the result")
                    self.assertNotIn("HOST-SECRET-CANARY", str(note))
        self.assertFalse(os.path.exists(out_file))
        leaked = [p for p in Path(self.d, "mods").rglob("*")] if Path(self.d, "mods").exists() else []
        self.assertFalse(any(p.is_file() and "CANARY" in p.read_text() for p in leaked))


@unittest.skipUnless(TOOLS_OK, "pinned tools not installed (unminify.ensure_tools())")
class Wakaru(unittest.TestCase):
    def test_minifier_shorthand_is_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "m.js")
            src.write_text('var a=1e4,b=6e4;"u"<typeof document||setTimeout(function(){},a),b&&console.log(!0);')
            text, note = um.run_wakaru(str(src))
            self.assertIsNone(note)
            self.assertIn("10000", text)
            self.assertIn("60000", text)
            # "u"<typeof d is true only when d is undefined, so the guarded call runs when it is defined
            self.assertIn('if (typeof document !== "undefined")', text)
            self.assertIn("console.log(true)", text)


@unittest.skipUnless(IMAGE_OK and TOOLS_OK, "docker base image or pinned tools unavailable")
class WebcrackInSandbox(unittest.TestCase):
    def test_hidden_strings_are_recovered(self):
        src = FIXTURES / "obfuscated-reel.js"
        self.assertNotIn("now showing", src.read_text())
        text, note = um.run_webcrack_sandboxed(str(src))
        self.assertIsNone(note)
        for s in ("now showing", "First film", "Second film"):
            self.assertIn(s, text)

    def test_large_output_is_not_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "big.js")
            src.write_text("".join(f"function f{i}(a){{return a*{i}+'{i:06d}'}}\n" for i in range(9000)))
            text, note = um.run_webcrack_sandboxed(str(src))
            self.assertIsNone(note)
            self.assertNotIn("bytes cut", text)
            self.assertGreater(len(text), 250_000)
            self.assertIn("f8999", text)

    def test_wakaru_alone_leaves_them_hidden(self):
        text, note = um.run_wakaru(str(FIXTURES / "obfuscated-reel.js"))
        self.assertIsNone(note)
        self.assertNotIn("now showing", text)


if __name__ == "__main__":
    unittest.main()
