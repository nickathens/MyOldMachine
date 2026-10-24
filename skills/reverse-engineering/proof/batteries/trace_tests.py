"""Planted-fault battery for the runtime-recorder tests (tests/test_reverse_engineering_trace.py).

Each arm mutates a COPY of trace.py in a temp tree; the real files are never touched.
Verdicts match native_tests.py (CAUGHT = the named test failed on an assertion).
The parser and ELF-guard arms need no Docker. NULL changes a comment only.

  systemd-run --user --scope -p MemoryMax=4G python trace_tests.py [ARM NAME ...]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "reverse-engineering"
TRACE = "test_reverse_engineering_trace.py"
S = "scripts/"

ARMS = [
    ("created flag never set", S + "trace.py", 'created = "O_CREAT" in flags', "created = False",
     "test_created_file_is_flagged", [TRACE]),
    ("connect address dropped", S + "trace.py",
     "addr = (addr_m.group(1) or addr_m.group(2)) if addr_m else None", "addr = None",
     "test_network_attempt_with_address_and_port", [TRACE]),
    ("connect port dropped", S + "trace.py", "port = pm.group(1) if pm else None", "port = None",
     "test_network_attempt_with_address_and_port", [TRACE]),
    ("connect result always ok", S + "trace.py", '"ok": int(ret) >= 0, "error": err})',
     '"ok": True, "error": err})', "test_network_attempt_with_address_and_port", [TRACE]),
    ("exec path blanked", S + "trace.py", '"path": m.group(1), "argv": argv',
     '"path": "", "argv": argv', "test_exec_and_unlink", [TRACE]),
    ("elf guard bypassed", S + "trace.py", 'if head[:4] != b"\\x7fELF":', "if False:",
     "test_non_elf_is_refused", [TRACE]),
    ("NULL control (comment only)", S + "trace.py", "TARGET = \"/in/target\"",
     "TARGET = \"/in/target\"  # the binary, mounted read-only", None, [TRACE]),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=420):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-trace-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        for t in test_files:
            shutil.copy(REPO / "tests" / t, f"{tmp}/tests/")
        shutil.copytree(SKILL / "scripts", f"{tmp}/skills/reverse-engineering/scripts")
        Path(f"{tmp}/skills/reverse-engineering/{rel}").write_text(src.replace(old, new))
        print(f"  [{name}]", flush=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="1000")
        with tempfile.TemporaryFile() as out:
            try:
                subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf",
                                *[f"{tmp}/tests/{t}" for t in test_files]], stdout=out, stderr=subprocess.STDOUT,
                               timeout=timeout, env=env, cwd=tmp)
            except subprocess.TimeoutExpired:
                return "TIMEOUT", f"no end inside {timeout} s"
            out.seek(max(0, out.seek(0, 2) - 20000))
            text = out.read().decode("utf-8", errors="replace")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    summary = next((ln for ln in reversed(lines) if SUMMARY.search(ln)), None)
    failed = {}
    for ln in lines:
        m = FAILED.match(ln)
        if m:
            failed.setdefault(m.group(1).split("::")[-1], set()).add(m.group(2) or "?")
    if summary is None:
        return "CRASHED", lines[-1][:200] if lines else "no output"
    if not failed:
        return "SURVIVED", summary
    if expect and expect in failed:
        kinds = failed[expect]
        return ("CAUGHT" if "AssertionError" in kinds else "ERRORED"), f"{summary} | {expect}: {sorted(kinds)}"
    return "WRONG", f"{summary} | failed: {', '.join(failed)}"


def main():
    only = sys.argv[1:]
    results = []
    for name, rel, old, new, expect, files in ARMS:
        if only and name not in only:
            continue
        verdict, detail = run_arm(name, rel, old, new, expect, files)
        results.append((name, verdict, expect))
        print(f"{name:40} {verdict:9} {detail}", flush=True)
    faults = [r for r in results if r[2]]
    caught = sum(1 for r in faults if r[1] == "CAUGHT")
    null = next((v for n, v, _ in results if n.startswith("NULL")), "not run")
    print(f"== {caught}/{len(faults)} fault arms caught by their named test; null control: {null}")


if __name__ == "__main__":
    main()
