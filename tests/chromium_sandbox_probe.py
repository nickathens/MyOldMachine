"""Read, from /proc, whether the Chromium under a process is really sandboxed.

Shared by the reverse-engineering and browser tests. A launch flag proves
nothing on its own: Playwright adds --no-sandbox by default, and a sandbox that
fails to start kills the launch. So this looks at the running processes: a
sandboxed renderer carries no --no-sandbox, sits in its own user and network
namespace, and runs under a seccomp filter (Seccomp: 2).

Chromium rewrites a child's argv into one space-joined string, so flags are
matched as substrings of the whole command line, never as list items.
"""

import os
import re
import time


def _children():
    kids = {}
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


def chrome_tree(root):
    """Every Chromium process below root: type, flag, namespaces, seccomp."""
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


def wait_for_renderer(root, timeout=30.0):
    """The Chromium tree under root once a renderer is up, or [] on timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        tree = chrome_tree(root)
        if any(p["type"] == "renderer" for p in tree):
            return tree
        time.sleep(0.1)
    return []


def sandbox_faults(tree):
    """What is wrong with a tree's sandbox; an empty list means sandboxed."""
    if not any(p["type"] == "renderer" for p in tree):
        return ["no renderer seen"]
    me = os.readlink("/proc/self/ns/user")
    faults = [f"{p['type']} {p['pid']} runs with --no-sandbox" for p in tree if p["no_sandbox"]]
    for p in tree:
        if p["type"] == "renderer":
            if p["userns"] == me:
                faults.append(f"renderer {p['pid']} shares our user namespace")
            if p["seccomp"] != "2":
                faults.append(f"renderer {p['pid']} has no seccomp filter (Seccomp: {p['seccomp']})")
    return faults
