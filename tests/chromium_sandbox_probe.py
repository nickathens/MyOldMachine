"""Read, from the running processes, whether the Chromium under a process is really sandboxed.

Used by the reverse-engineering tests. A launch flag proves
nothing on its own: Playwright adds --no-sandbox by default, and a sandbox that
fails to start kills the launch. So this looks at the running processes: a
sandboxed renderer carries no --no-sandbox and, on Linux, sits in its own user
and network namespace and runs under a seccomp filter (Seccomp: 2), read from /proc.

macOS has no /proc, namespaces or seccomp. There the processes come from ps, and
a renderer is sandboxed when the kernel holds it in its Seatbelt sandbox:
sandbox_check(pid, NULL, 0) answers 1. It also answers 1 for a pid that no longer
exists, so an answer counts only if the same process is still there after it.

Chromium rewrites a child's argv into one space-joined string, so flags are
matched as substrings of the whole command line, never as list items.
"""

import ctypes
import os
import re
import subprocess
import sys
import time

MACOS = sys.platform == "darwin"


def _ps(field):
    """pid -> one ps column. The column comes last, so its value may hold spaces."""
    out = subprocess.run(["ps", "-A", "-o", f"pid=,{field}="], capture_output=True, text=True,
                         timeout=30).stdout
    rows = {}
    for line in out.splitlines():
        pid, _, value = line.strip().partition(" ")
        if pid.isdigit():
            rows[int(pid)] = value.strip()
    return rows


def _children():
    kids = {}
    if MACOS:
        for pid, ppid in _ps("ppid").items():
            if ppid.isdigit():
                kids.setdefault(int(ppid), []).append(pid)
        return kids
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                ppid = int(fh.read().rsplit(b")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        kids.setdefault(ppid, []).append(int(name))
    return kids


def _sandbox_check(pid):
    fn = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
    fn.restype, fn.argtypes = ctypes.c_int, [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    return fn(pid, None, 0)


def _mac_tree(root):
    kids, exe, cmd = _children(), _ps("comm"), _ps("command")
    todo, found = [root], []
    while todo:
        for pid in kids.get(todo.pop(), []):
            todo.append(pid)
            if "chrom" in os.path.basename(exe.get(pid, "")).lower():
                found.append((pid, _sandbox_check(pid)))
    after = _ps("command")
    out = []
    for pid, answer in found:
        raw = cmd.get(pid, "")
        if after.get(pid) != raw:
            continue  # exited while we looked: its answer is about no process
        m = re.search(r"--type=([\w-]+)", raw)
        out.append({"pid": pid, "type": m.group(1) if m else "browser", "no_sandbox": "--no-sandbox" in raw,
                    "sandboxed": answer == 1})
    return out


def chrome_tree(root):
    """Every Chromium process below root: type, flag, and its sandbox (namespaces and
    seccomp on Linux, the kernel's own answer on macOS)."""
    if MACOS:
        return _mac_tree(root)
    kids, todo, out = _children(), [root], []
    while todo:
        for pid in kids.get(todo.pop(), []):
            todo.append(pid)
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    raw = fh.read().replace(b"\0", b" ").decode(errors="replace")
                userns = os.readlink(f"/proc/{pid}/ns/user")
                netns = os.readlink(f"/proc/{pid}/ns/net")
                with open(f"/proc/{pid}/status") as fh:
                    seccomp = next((ln.split()[1] for ln in fh if ln.startswith("Seccomp:")), None)
            except OSError:
                continue  # exited while we looked
            if "chrome" not in os.path.basename(raw.split(" ")[0]):
                continue
            m = re.search(r"--type=([\w-]+)", raw)
            out.append({"pid": pid, "type": m.group(1) if m else "browser", "no_sandbox": "--no-sandbox" in raw,
                        "userns": userns, "netns": netns, "seccomp": seccomp})
    return out


def wait_for_renderer(root, timeout=30.0, settle=5.0):
    """The Chromium tree under root once a renderer is up, or [] on timeout. A renderer
    applies its sandbox itself as it starts (on Linux the seccomp filter came 2.6 to 5 ms
    after the process, on macOS Seatbelt), so a renderer seen unsandboxed is given
    `settle` seconds to report in before the tree is returned as it was."""
    end = time.monotonic() + timeout
    seen, last = None, []
    while time.monotonic() < end:
        tree = chrome_tree(root)
        if any(p["type"] == "renderer" for p in tree):
            if not sandbox_faults(tree):
                return tree
            seen, last = seen or time.monotonic(), tree
            if time.monotonic() - seen >= settle:
                return last
        elif seen is not None:
            return last  # the renderer is gone: report it as it was last seen
        time.sleep(0.1)
    return last


def sandbox_faults(tree):
    """What is wrong with a tree's sandbox; an empty list means sandboxed."""
    if not any(p["type"] == "renderer" for p in tree):
        return ["no renderer seen"]
    faults = [f"{p['type']} {p['pid']} runs with --no-sandbox" for p in tree if p["no_sandbox"]]
    if MACOS:
        faults += [f"renderer {p['pid']} is not in a sandbox (sandbox_check answered 0)" for p in tree
                   if p["type"] == "renderer" and not p["sandboxed"]]
        return faults
    me = os.readlink("/proc/self/ns/user")
    for p in tree:
        if p["type"] == "renderer":
            if p["userns"] == me:
                faults.append(f"renderer {p['pid']} shares our user namespace")
            if p["seccomp"] != "2":
                faults.append(f"renderer {p['pid']} has no seccomp filter (Seccomp: {p['seccomp']})")
    return faults
