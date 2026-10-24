#!/usr/bin/env python3
"""
Record what a Linux program DOES when it runs: the files it opens, the network
it tries to reach, and the other programs it starts. This is the native
counterpart of watch_page.py for the web tier, and the thing the verification
spine needs to turn a native behavioural claim from "unconfirmed" (a static
guess) into "confirmed" (observed in a sandbox run).

  python trace.py ./program                         # trace a run
  python trace.py ./program --arg --flag --arg input.bin
  python trace.py ./program --stdin-file data --json run.json

IMPORTANT (trust boundary): unlike oracle.py, which EMULATES the original bytes,
this EXECUTES the program. It runs only in the offline re-trace sandbox (no
network, read-only root, no capabilities, non-root, memory/pids/time capped);
strace traces its own child, which needs no extra capability. The sandbox is the
boundary: a native exploit chained with a container escape would still reach the
bot's user, so run only a Linux x86-64 target you are authorized to run. Because
there is no network, a connect() is seen as an ATTEMPT that fails -- which is
exactly the evidence of where the program wanted to phone home.

A recorded syscall is ground truth about the run. That the program "phones home"
or "writes a licence file" is a behavioural claim this run can legitimately
confirm (it is a dynamic observation). Linux ELF only.

Like watch_page.py on the web side, the trace is evidence about a program that
is not actively working against the recorder: the target runs as our uid and
could, in principle, write to the same output mount strace uses and forge its
own trace, so a target you believe is hostile and forgery-capable is beyond what
this confirms. The sandbox still contains it; the record is just no longer trusted.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bin_triage  # noqa: E402
import sandbox  # noqa: E402

TARGET = "/in/target"
# strace lines we parse. -y annotates fds with their path; -f follows children.
RE_OPEN = re.compile(r'openat\(.*?,\s*"((?:[^"\\]|\\.)*)"(?:,\s*([A-Z_|]+))?.*?\)\s*=\s*(-?\d+)(?:\s+(\w+))?')
RE_CONNECT = re.compile(r'connect\(\d+(?:<[^>]*>)?,\s*\{sa_family=(AF_\w+)[^}]*\}.*?\)\s*=\s*(-?\d+)(?:\s+(\w+))?')
RE_INET_SIMPLE = re.compile(r'inet_addr\("([^"]+)"\)|inet_pton\([^,]*,\s*"([^"]+)"')
RE_PORT = re.compile(r'sin6?_port=htons\((\d+)\)')
RE_EXEC = re.compile(r'execve\("((?:[^"\\]|\\.)*)",\s*\[([^\]]*)\].*?\)\s*=\s*(-?\d+)')
RE_UNLINK = re.compile(r'unlink(?:at)?\(.*?"((?:[^"\\]|\\.)*)".*?\)\s*=\s*(-?\d+)')
MAX_EVENTS = 200_000


class TraceError(RuntimeError):
    pass


def _is_linux_elf(path):
    with open(path, "rb") as fh:
        head = fh.read(20)
    if head[:4] != b"\x7fELF":
        return False, "not an ELF binary"
    rep = bin_triage.inspect(head + b"\x00" * max(0, 64 - len(head)))
    return True, rep.get("arch", "?")


def parse_strace(text):
    """Turn strace output into a structured record of what the run did."""
    opens, nets, execs, unlinks = [], [], [], []
    writes_creating = 0
    for line in text.splitlines():
        if len(opens) + len(nets) + len(execs) + len(unlinks) > MAX_EVENTS:
            break
        m = RE_OPEN.search(line)
        if m:
            path, flags, ret, err = m.group(1), m.group(2) or "", m.group(3), m.group(4)
            created = "O_CREAT" in flags
            opens.append({"path": path, "flags": flags, "ok": int(ret) >= 0, "error": err, "created": created})
            if created:
                writes_creating += 1
            continue
        m = RE_CONNECT.search(line)
        if m:
            family, ret, err = m.group(1), m.group(2), m.group(3)
            pm = RE_PORT.search(line)
            port = pm.group(1) if pm else None
            addr_m = RE_INET_SIMPLE.search(line)
            addr = (addr_m.group(1) or addr_m.group(2)) if addr_m else None
            nets.append({"family": family, "address": addr, "port": int(port) if port else None,
                         "ok": int(ret) >= 0, "error": err})
            continue
        m = RE_EXEC.search(line)
        if m:
            argv = [a.strip().strip('"') for a in m.group(2).split(",") if a.strip()]
            execs.append({"path": m.group(1), "argv": argv, "ok": int(m.group(3)) >= 0})
            continue
        m = RE_UNLINK.search(line)
        if m:
            unlinks.append({"path": m.group(1), "ok": int(m.group(2)) >= 0})
    return {"files_opened": opens, "network": nets, "exec": execs, "unlinked": unlinks,
            "files_created": writes_creating}


def run(binary, args=(), stdin_file=None, timeout=60):
    """Run `binary` under strace in the sandbox and return the structured record
    plus the program's exit status."""
    binary = os.path.abspath(binary)
    if not os.path.isfile(binary):
        raise TraceError(f"no such binary: {binary}")
    ok, arch = _is_linux_elf(binary)
    if not ok:
        raise TraceError(f"{binary}: {arch} -- trace.py runs Linux ELF binaries only")
    image = sandbox.ensure_image(sandbox.TRACE_DOCKERFILE)
    with tempfile.TemporaryDirectory(prefix="re-trace-") as work:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        dst = os.path.join(in_dir, "target")
        shutil.copyfile(binary, dst)
        os.chmod(dst, 0o755)
        ro = [f"{in_dir}:/in"]
        stdin_in = None
        if stdin_file:
            shutil.copyfile(stdin_file, os.path.join(in_dir, "stdin"))
            stdin_in = "/in/stdin"
        # -f follow children, -qq quiet, -y annotate fds with paths, -s cap string length,
        # trace the file/network/process syscall classes. Output to the rw mount, read back safely.
        strace_cmd = ["strace", "-f", "-qq", "-y", "-s", "200",
                      "-e", "trace=%file,%network,%process", "-o", "/out/trace.txt", TARGET, *args]
        inner = " ".join(_shq(a) for a in strace_cmd)
        if stdin_in:
            inner += f" < {_shq(stdin_in)}"
        res = sandbox.run(["sh", "-c", inner], image=image, ro=ro, rw=[f"{out_dir}:/out"],
                          memory="1g", timeout=timeout, max_write=256 << 20)
        if res["timed_out"]:
            raise TraceError(f"the run did not finish inside {timeout} s (killed in the sandbox)")
        if not os.path.lexists(os.path.join(out_dir, "trace.txt")):
            raise TraceError("strace wrote no trace:\n" + res["stderr"][-800:])
        with sandbox.open_output(out_dir, "trace.txt") as fh:
            text = fh.read(64 << 20).decode("utf-8", errors="replace")
    record = parse_strace(text)
    record["exit_status"] = res["returncode"]
    record["arch"] = arch
    return record


def _shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def main():
    ap = argparse.ArgumentParser(description="Record a Linux program's runtime behaviour with strace, in the sandbox.")
    ap.add_argument("file", help="Path to the Linux ELF binary.")
    ap.add_argument("--arg", action="append", default=[], help="An argument to pass the program (repeatable).")
    ap.add_argument("--stdin-file", help="Feed this file to the program's stdin.")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--json", help="Write the full record as JSON here.")
    ap.add_argument("--limit", type=int, default=40, help="Rows per list in the summary.")
    args = ap.parse_args()

    try:
        rec = run(args.file, args=args.arg, stdin_file=args.stdin_file, timeout=args.timeout)
    except (TraceError, sandbox.SandboxError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    print(f"exit {rec['exit_status']} ({rec['arch']}); {len(rec['files_opened'])} file open(s), "
          f"{len(rec['network'])} network attempt(s), {len(rec['exec'])} exec(s), "
          f"{rec['files_created']} file(s) created")
    net = rec["network"]
    if net:
        print("  network it tried to reach:")
        for n in net[:args.limit]:
            print(f"    {n['family']} {n['address']}:{n['port']}  ({'ok' if n['ok'] else n['error'] or 'failed'})")
    created = [o for o in rec["files_opened"] if o["created"]]
    if created:
        print("  files it created:")
        for o in created[:args.limit]:
            print(f"    {o['path']}")
    if rec["exec"]:
        print("  programs it started:")
        for e in rec["exec"][:args.limit]:
            print(f"    {e['path']} {e['argv'][1:]}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        print(f"Wrote {args.json}")
    sys.exit(0)


if __name__ == "__main__":
    main()
