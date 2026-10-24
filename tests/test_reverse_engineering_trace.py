"""reverse-engineering skill, native runtime recorder (trace.py).

The recorder runs a Linux program under strace in the sandbox and reports what it
did. Two layers:
  - the strace PARSER is pinned on captured strace lines (no Docker): a file open,
    a network connect attempt with its address and port, an execve, an unlink, and
    a created file are each pulled out, and a non-ELF input is refused;
  - a live run (Docker + the re-trace image) of a program compiled here records
    its open of /etc/hostname, its connect attempt to a fixed address:port (which
    fails, there being no network -- the phone-home evidence), and the file it
    creates.

Needs gcc for the live run; the parser tests always run.
"""
import importlib.util
import os
import shutil
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


tr = load("trace")
sbx = load("sandbox")

DOCKER_OK, _ = sbx.docker_available()
TRACE_OK = DOCKER_OK and subprocess.run(
    ["docker", "image", "inspect", sbx.image_tag(sbx.TRACE_DOCKERFILE)], capture_output=True).returncode == 0
GCC = shutil.which("gcc")

SAMPLE = r'''
openat(AT_FDCWD</>, "/etc/hostname", O_RDONLY) = 3</etc/hostname>
socket(AF_INET, SOCK_STREAM, IPPROTO_IP) = 4
connect(4<socket:[1]>, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("93.184.216.34")}, 16) = -1 ENETUNREACH (Network is unreachable)
openat(AT_FDCWD, "/tmp/licence.key", O_WRONLY|O_CREAT, 0644) = 5</tmp/licence.key>
write(5</tmp/licence.key>, "paid", 4) = 4
execve("/bin/sh", ["sh", "-c", "id"], 0x7ff /* 20 vars */) = 0
unlink("/tmp/old.lock") = 0
'''


class Parser(unittest.TestCase):
    def setUp(self):
        self.rec = tr.parse_strace(SAMPLE)

    def test_file_open_is_parsed(self):
        paths = {o["path"] for o in self.rec["files_opened"]}
        self.assertIn("/etc/hostname", paths)
        self.assertIn("/tmp/licence.key", paths)

    def test_created_file_is_flagged(self):
        created = [o["path"] for o in self.rec["files_opened"] if o["created"]]
        self.assertEqual(created, ["/tmp/licence.key"])
        self.assertEqual(self.rec["files_created"], 1)

    def test_network_attempt_with_address_and_port(self):
        self.assertEqual(len(self.rec["network"]), 1)
        n = self.rec["network"][0]
        self.assertEqual((n["family"], n["address"], n["port"]), ("AF_INET", "93.184.216.34", 443))
        self.assertFalse(n["ok"])
        self.assertEqual(n["error"], "ENETUNREACH")

    def test_exec_and_unlink(self):
        self.assertEqual(self.rec["exec"][0]["path"], "/bin/sh")
        self.assertIn("id", self.rec["exec"][0]["argv"])
        self.assertEqual(self.rec["unlinked"][0]["path"], "/tmp/old.lock")

    def test_empty_trace_is_empty(self):
        rec = tr.parse_strace("")
        self.assertEqual(rec["network"], [])
        self.assertEqual(rec["files_opened"], [])


class ElfGuard(unittest.TestCase):
    def test_non_elf_is_refused(self):
        d = tempfile.mkdtemp(prefix="tr-")
        p = os.path.join(d, "script.sh")
        Path(p).write_text("#!/bin/sh\necho hi\n")
        self.assertFalse(tr._is_linux_elf(p)[0])       # pin the guard itself (docker-free)
        with self.assertRaises(tr.TraceError):
            tr.run(p)
        shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(TRACE_OK and GCC, "docker, the re-trace image, or gcc is not present")
class LiveRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="tr-live-")
        src = r'''
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
int main(void){
    int fd = open("/etc/hostname", O_RDONLY); (void)fd;
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a; a.sin_family = AF_INET; a.sin_port = htons(443);
    inet_pton(AF_INET, "93.184.216.34", &a.sin_addr);
    connect(s, (struct sockaddr*)&a, sizeof a);
    int w = open("/tmp/licence.key", O_WRONLY|O_CREAT, 0644);
    if (w >= 0) { write(w, "paid", 4); }
    return 0;
}'''
        Path(cls.d, "probe.c").write_text(src)
        cls.bin = os.path.join(cls.d, "probe")
        subprocess.run([GCC, "-O0", "-o", cls.bin, os.path.join(cls.d, "probe.c")], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_records_the_run(self):
        rec = tr.run(self.bin, timeout=60)
        self.assertTrue(any(o["path"] == "/etc/hostname" for o in rec["files_opened"]))
        self.assertTrue(any(o["path"] == "/tmp/licence.key" and o["created"] for o in rec["files_opened"]))
        net = rec["network"]
        self.assertTrue(net)
        self.assertEqual(net[0]["address"], "93.184.216.34")
        self.assertEqual(net[0]["port"], 443)
        self.assertFalse(net[0]["ok"])            # no network in the sandbox: the attempt is the evidence

    def test_a_do_nothing_program_has_no_network(self):
        q = os.path.join(self.d, "quiet")
        Path(self.d, "quiet.c").write_text("int main(void){return 0;}")
        subprocess.run([GCC, "-O0", "-o", q, os.path.join(self.d, "quiet.c")], check=True, capture_output=True)
        rec = tr.run(q, timeout=60)
        self.assertEqual(rec["network"], [])


if __name__ == "__main__":
    unittest.main()
