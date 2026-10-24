#!/usr/bin/env python3
"""
Run something we do not trust inside a throwaway Docker container with no
network, a read-only system, no privileges, and hard limits.

  python sandbox.py check                     # prove the isolation by trying to break it
  python sandbox.py run --ro ./in:/in --rw ./out:/out -- node /in/x.js
  python sandbox.py run --image debian:stable-slim --timeout 60 -- /in/tool --help

Every run gets:
  --network none          no network at all, not even loopback to the host
  --read-only             the container's own filesystem cannot be written
  --cap-drop ALL          no Linux capabilities
  no-new-privileges       setuid binaries cannot raise privileges
  --user UID:GID          the caller's own ids, never root
  --pids-limit, --memory  a fork bomb or a memory hog hits a wall, not the bot
  --tmpfs /tmp            a small scratch space, noexec unless --tmp-exec
  only the mounts given   --ro for inputs, --rw for one output folder
  a timeout               the container is killed, not just the client; the same limit
                          (plus a grace) also runs inside it, so it holds even if the
                          caller dies (a killed turn once left one running past it)

Why Docker: on this machine `systemd-run --user -p PrivateNetwork=yes` is
silently ignored (a connection from inside still succeeds), and `unshare -rn`
and `bwrap --unshare-net` are blocked by AppArmor. Docker's --network none is
the isolation that actually holds, and `check` proves it each time it matters.

Output is written to temporary files and only a bounded tail is read back, so
a target that prints without end cannot fill the bot's memory; a caller that
reads data off stdout (radare2's JSON) raises the bound with `max_output` and
checks `stdout_cut`. `max_write`
caps what a run may put in its writable mounts (polled while it runs), so an
archive bomb cannot fill the disk.

What a run leaves in a writable mount is untrusted on the way out: the tool
inside runs as our uid with that folder writable, so a compromised one can put
a link to any host file, or a pipe, where its result belongs. Read results
back only through open_output and copy_output_tree, which take regular files
reached through no link and never block.
"""
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

DEFAULT_IMAGE = "node:24-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-tools.Dockerfile")      # Tier 1: wabt (wasm)
NATIVE_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-native.Dockerfile")    # Tier 2: radare2 + binutils
GHIDRA_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-ghidra.Dockerfile")    # Tier 2: Ghidra headless + JDK
CARVE_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-carve.Dockerfile")      # Tier 3: binwalk
ORACLE_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-oracle.Dockerfile")    # native oracle: Unicorn + compilers
DOTNET_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-dotnet.Dockerfile")    # .NET/Unity decompiler: ilspycmd
TRACE_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-trace.Dockerfile")      # native runtime recorder: strace
GDRE_DOCKERFILE = os.path.join(SCRIPTS_DIR, "re-gdre.Dockerfile")        # games: GDRE Tools (Godot scripts, scenes)
MAX_OUTPUT = 200_000
INNER_GRACE = 5      # seconds the in-container limit trails the caller's, which normally fires first


class SandboxError(RuntimeError):
    pass


def docker_available():
    if not shutil.which("docker"):
        return False, "docker is not installed"
    try:
        p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True,
                           timeout=20)
    except subprocess.TimeoutExpired:
        return False, "docker info timed out"
    if p.returncode != 0:
        return False, (p.stderr.strip() or "docker info failed")[:200]
    return True, p.stdout.strip()


def image_tag(dockerfile):
    """Tag for a sandbox image: '<dockerfile stem>:<content hash>', so an edited
    Dockerfile gets a new tag and a fresh build. re-tools.Dockerfile -> re-tools:<h>."""
    with open(dockerfile, "rb") as fh:
        h = hashlib.sha256(fh.read()).hexdigest()[:12]
    base = os.path.basename(dockerfile)
    stem = base[:-len(".Dockerfile")] if base.endswith(".Dockerfile") else base
    return f"{stem}:{h}"


def ensure_image(dockerfile, timeout=1800):
    """Build the image for `dockerfile` once if it is not already present (the
    build needs the network; runs never do). Returns the tag. Heavier images
    (Ghidra) pass a larger timeout."""
    ok, why = docker_available()
    if not ok:
        raise SandboxError(f"sandbox unavailable: {why}")
    tag = image_tag(dockerfile)
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True, timeout=30).returncode == 0:
        return tag
    if not os.path.isfile(dockerfile):
        raise SandboxError(f"no Dockerfile at {dockerfile}")
    with tempfile.TemporaryDirectory(prefix="re-img-build-") as ctx:
        p = subprocess.run(["docker", "build", "-q", "-t", tag, "-f", dockerfile, ctx],
                           capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise SandboxError(f"image build failed ({os.path.basename(dockerfile)}): " + (p.stderr or p.stdout)[-400:])
    return tag


def tools_image():
    """Tag of the Tier 1 wasm tools image (wabt)."""
    return image_tag(TOOLS_DOCKERFILE)


def ensure_tools_image():
    """Build the Tier 1 wasm tools image once."""
    return ensure_image(TOOLS_DOCKERFILE, timeout=1200)


def _mount(spec, mode):
    host, sep, cont = spec.rpartition(":")
    if not sep or not host or not cont.startswith("/"):
        raise SandboxError(f"mount {spec!r}: expected HOST_PATH:/container/path")
    if "," in host or "," in cont:
        raise SandboxError(f"mount {spec!r}: a comma would break Docker's mount syntax")
    host = os.path.realpath(host)
    if not os.path.exists(host):
        raise SandboxError(f"mount {spec!r}: {host} does not exist")
    home = os.path.realpath(os.path.expanduser("~"))
    if host == home or home.startswith(host.rstrip("/") + "/"):  # ~ itself, or a folder holding it (/, /home)
        raise SandboxError(f"mount {spec!r}: refusing to mount {host} into a sandbox (it is or holds the home folder)")
    return ["--mount", f"type=bind,source={host},target={cont}" + (",readonly" if mode == "ro" else "")]


def _tail(fh, limit=MAX_OUTPUT):
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(max(0, size - limit))
    data = fh.read().decode("utf-8", errors="replace")
    return ((f"[... {size - limit} bytes cut ...]\n" + data) if size > limit else data), size > limit


def _disk_use(roots, max_entries=200_000):
    """Bytes on disk under the given folders, links not followed. Past max_entries
    the count is reported as unbounded, so the poll itself stays cheap."""
    total, seen, stack = 0, 0, list(roots)
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    seen += 1
                    if seen > max_entries:
                        return float("inf")
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += st.st_blocks * 512      # real blocks, so a sparse file counts what it holds
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(e.path)
        except OSError:
            continue
    return total


def open_output(out_dir, rel):
    """Open a file a sandbox run wrote, for reading (binary). Refuses anything but a
    regular file inside out_dir reached through no link: the tool ran as our uid with
    the folder writable, so a compromised one can leave a link to a host file there
    (following it hands that file to whoever reads the result) or a pipe (opening it
    would block forever)."""
    root = os.path.realpath(out_dir)
    path = os.path.normpath(os.path.join(root, rel))
    if not path.startswith(root + os.sep) or os.path.realpath(path) != path:
        raise SandboxError(f"sandbox output {rel!r} leaves its folder or goes through a link; not read")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        raise SandboxError(f"sandbox output {rel!r} cannot be opened: {e.strerror}") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise SandboxError(f"sandbox output {rel!r} is not a regular file; not read")
    return os.fdopen(fd, "rb")


def copy_output_tree(out_dir, rel, dst):
    """Copy a folder a sandbox run wrote into dst, regular files only (see
    open_output). Links and anything else are left behind; returns their names."""
    root = os.path.realpath(out_dir)
    top = os.path.normpath(os.path.join(root, rel))
    if not top.startswith(root + os.sep) or os.path.realpath(top) != top or not os.path.isdir(top):
        raise SandboxError(f"sandbox output folder {rel!r} is missing or goes through a link")
    skipped = []
    for dirpath, dirnames, filenames in os.walk(top):           # a linked folder is listed, never entered
        here = os.path.relpath(dirpath, top)
        os.makedirs(os.path.join(dst, here), exist_ok=True)
        for name in sorted(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                skipped.append(os.path.normpath(os.path.join(here, name)))
        for name in sorted(filenames):
            item = os.path.normpath(os.path.join(here, name))
            try:
                with open_output(top, item) as src, open(os.path.join(dst, item), "wb") as out:
                    shutil.copyfileobj(src, out)
            except SandboxError:
                skipped.append(item)
    return skipped


def run(cmd, image=DEFAULT_IMAGE, ro=(), rw=(), memory="1g", cpus="2", pids=256, timeout=120, workdir=None,
        tmp_size="256m", tmp_exec=False, env=None, hostname=None, with_passwd=False, max_write=None,
        max_output=MAX_OUTPUT, fsize_cap=True):
    """Run cmd (a list) in a locked-down container. Returns a dict with
    returncode, stdout (a tail of `max_output` bytes), stderr (always the default tail: it
    never carries data), stdout_cut,
    timed_out, over_cap, and the docker argv.
    `hostname` sets the container's hostname: pass "localhost" for a JVM (Ghidra),
    whose getLocalHost() must resolve a name and cannot with --network none and a
    random hostname. `with_passwd` mounts a minimal /etc/passwd and /etc/group for
    the running uid, for a tool that looks itself up (binwalk's extractor calls
    getpwuid and crashes on an unknown uid). Neither weakens the isolation.
    `max_write` (bytes) caps what the run may write into its rw mounts: they are
    polled while it runs and the container is killed past the cap (over_cap True).
    `fsize_cap` also sets an RLIMIT_FSIZE so no single file beats the poll; turn it
    off for a runtime that mis-handles that rlimit (the .NET runtime takes SIGXFSZ
    under a memory cgroup), leaving the poll as the cap."""
    ok, why = docker_available()
    if not ok:
        raise SandboxError(f"sandbox unavailable: {why}")
    name = "re-sbx-" + uuid.uuid4().hex[:12]
    tmp_opts = f"rw,nosuid,nodev,size={tmp_size}" + ("" if tmp_exec else ",noexec")
    argv = ["docker", "run", "--rm", "--name", name, "--init",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", str(int(pids)),
            "--memory", memory, "--memory-swap", memory, "--cpus", str(cpus),
            "--ulimit", "core=0", "--ulimit", "nofile=1024:1024",
            "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", f"/tmp:{tmp_opts}",
            "--env", "HOME=/tmp", "--log-driver", "none"]
    if hostname:
        argv += ["--hostname", hostname]
    if max_write and fsize_cap:
        argv += ["--ulimit", f"fsize={int(max_write)}"]     # no single file past the cap, before any poll
    for k, v in (env or {}).items():
        argv += ["--env", f"{k}={v}"]
    for spec in ro:
        argv += _mount(spec, "ro")
    for spec in rw:
        argv += _mount(spec, "rw")
    if workdir:
        argv += ["--workdir", workdir]

    passwd_dir = None
    if with_passwd:
        uid, gid = os.getuid(), os.getgid()
        passwd_dir = tempfile.mkdtemp(prefix="re-passwd-")
        with open(os.path.join(passwd_dir, "passwd"), "w") as fh:
            fh.write(f"root:x:0:0:root:/root:/bin/sh\nsandbox:x:{uid}:{gid}:sandbox:/tmp:/usr/sbin/nologin\n")
        with open(os.path.join(passwd_dir, "group"), "w") as fh:
            fh.write(f"root:x:0:\nsandbox:x:{gid}:\n")
        argv += _mount(f"{passwd_dir}/passwd:/etc/passwd", "ro")
        argv += _mount(f"{passwd_dir}/group:/etc/group", "ro")
    # The limit again, inside: if this process dies, nothing outside would ever kill the container.
    argv += [image, "timeout", "-k", "5", str(int(timeout) + INNER_GRACE)] + list(cmd)
    watch = [os.path.realpath(s.rpartition(":")[0]) for s in rw] if max_write else []
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL)
            deadline = time.monotonic() + timeout
            timed_out = over_cap = False
            while True:
                left = deadline - time.monotonic()
                try:
                    rc = proc.wait(timeout=max(0.01, min(left, 0.1) if watch else left))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        timed_out = True
                    elif watch and _disk_use(watch) > max_write:
                        over_cap = True
                    else:
                        continue
                subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
                try:
                    rc = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
                break
            stdout, stdout_cut = _tail(out, max_output)
            return {"returncode": rc, "timed_out": timed_out, "over_cap": over_cap, "stdout": stdout,
                    "stdout_cut": stdout_cut, "stderr": _tail(err)[0], "argv": argv}
    finally:
        if passwd_dir:
            shutil.rmtree(passwd_dir, ignore_errors=True)


# Each probe prints one line: NAME PASS|FAIL detail. They try to break out, so
# a PASS means the attempt failed.
CHECK_JS = r"""
const fs = require('fs'), net = require('net'), dns = require('dns');
const say = (n, ok, d) => console.log(`${n} ${ok ? 'PASS' : 'FAIL'} ${d}`);
let pending = 2;
const done = () => { if (--pending === 0) process.exit(0); };
const s = net.connect({host: '1.1.1.1', port: 443, timeout: 3000});
s.on('connect', () => { say('network-ip', false, 'connected to 1.1.1.1:443'); s.destroy(); done(); });
s.on('error', (e) => { say('network-ip', true, e.code); done(); });
s.on('timeout', () => { say('network-ip', true, 'timeout'); s.destroy(); done(); });
dns.lookup('example.com', (e, a) => { say('network-dns', !!e, e ? e.code : 'resolved ' + a); done(); });
try { fs.writeFileSync('/sbx-probe', 'x'); say('readonly-root', false, 'wrote /sbx-probe'); }
catch (e) { say('readonly-root', true, e.code); }
const st = fs.readFileSync('/proc/self/status', 'utf8');
const capEff = (st.match(/CapEff:\s*([0-9a-f]+)/) || [])[1];
say('no-capabilities', /^0+$/.test(capEff || 'x'), 'CapEff ' + capEff);
const nnp = (st.match(/NoNewPrivs:\s*(\d)/) || [])[1];
say('no-new-privileges', nnp === '1', 'NoNewPrivs ' + nnp);
say('not-root', process.getuid() !== 0, 'uid ' + process.getuid());
say('host-home-hidden', !fs.existsSync(process.argv[1]), 'checked ' + process.argv[1]);
try { fs.writeFileSync('/tmp/x.sh', '#!/bin/sh\necho ran'); fs.chmodSync('/tmp/x.sh', 0o755);
      require('child_process').execFileSync('/tmp/x.sh'); say('tmp-noexec', false, 'executed /tmp/x.sh'); }
catch (e) { say('tmp-noexec', true, (e.code || 'refused')); }
"""


def check(image=DEFAULT_IMAGE):
    home = os.path.realpath(os.path.expanduser("~"))
    res = run(["node", "-e", CHECK_JS, home], image=image, timeout=60)
    lines = [ln for ln in res["stdout"].splitlines() if ln.split(" ")[1:2] in (["PASS"], ["FAIL"])]
    expected = {"network-ip", "network-dns", "readonly-root", "no-capabilities", "no-new-privileges", "not-root",
                "host-home-hidden", "tmp-noexec"}
    seen = {ln.split(" ")[0] for ln in lines}
    missing = expected - seen
    failed = [ln for ln in lines if " FAIL " in ln]
    return {"ok": not failed and not missing and res["returncode"] == 0, "lines": lines,
            "missing": sorted(missing), "stderr": res["stderr"][-500:], "returncode": res["returncode"]}


def main():
    ap = argparse.ArgumentParser(description="Run untrusted code in a locked-down, offline container.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="Prove the isolation by trying to break out.")
    c.add_argument("--image", default=DEFAULT_IMAGE)
    r = sub.add_parser("run", help="Run a command in the sandbox.")
    r.add_argument("--image", default=DEFAULT_IMAGE)
    r.add_argument("--ro", action="append", default=[], help="HOST:CONTAINER read-only mount (repeatable).")
    r.add_argument("--rw", action="append", default=[], help="HOST:CONTAINER writable mount (repeatable).")
    r.add_argument("--memory", default="1g")
    r.add_argument("--cpus", default="2")
    r.add_argument("--pids", type=int, default=256)
    r.add_argument("--timeout", type=int, default=120)
    r.add_argument("--workdir")
    r.add_argument("--tmp-exec", action="store_true", help="Allow executing files written to /tmp.")
    r.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command and its arguments")
    args = ap.parse_args()

    try:
        if args.cmd == "check":
            res = check(args.image)
            for ln in res["lines"]:
                print(ln)
            for m in res["missing"]:
                print(f"{m} FAIL probe did not report")
            print("PASS: sandbox holds" if res["ok"] else f"FAIL: sandbox does not hold (exit {res['returncode']})")
            if res["stderr"].strip() and not res["ok"]:
                print(res["stderr"], file=sys.stderr)
            sys.exit(0 if res["ok"] else 1)
        cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not cmd:
            ap.error("give the command after --")
        res = run(cmd, image=args.image, ro=args.ro, rw=args.rw, memory=args.memory, cpus=args.cpus,
                  pids=args.pids, timeout=args.timeout, workdir=args.workdir, tmp_exec=args.tmp_exec)
    except SandboxError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    sys.stdout.write(res["stdout"])
    sys.stderr.write(res["stderr"])
    if res["timed_out"]:
        print(f"[sandbox] killed after {args.timeout} s", file=sys.stderr)
        sys.exit(124)
    sys.exit(res["returncode"])


if __name__ == "__main__":
    main()
