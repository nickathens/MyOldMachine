"""Planted-fault battery for the C# decompiler tests (tests/test_reverse_engineering_csharp.py).

Each arm mutates a COPY of csharp.py in a temp tree; the real files are never touched.
Verdicts match native_tests.py (CAUGHT = the named test failed on an assertion).
The gate, type-guard and IL2CPP arms need no Docker; the --type arm needs the
re-dotnet image. NULL changes a comment only.

  systemd-run --user --scope -p MemoryMax=4G python csharp_tests.py [ARM NAME ...]
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
CS = "test_reverse_engineering_csharp.py"
S = "scripts/"

ARMS = [
    ("type guard accepts anything", S + "csharp.py",
     'TYPE_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_.+`<>,\\[\\] ]*\\Z")',
     'TYPE_OK = re.compile(r"^.*$", re.S)',
     "test_type_argument_must_be_a_plain_type_name", [CS]),
    ("type guard back to $ (a final newline passes)", S + "csharp.py", ']*\\Z")   # a .NET type name',
     ']*$")   # a .NET type name', "test_type_argument_must_be_a_plain_type_name", [CS]),
    ("gate takes any purpose", S + "csharp.py", "if purpose not in purposes:", "if False:",
     "test_project_gate_refuses_unlawful_purpose_and_blank_note", [CS]),
    ("gate takes a blank note", S + "csharp.py", "if not note or not note.strip():", "if not note:",
     "test_project_gate_refuses_unlawful_purpose_and_blank_note", [CS]),
    ("full folder accepted", S + "csharp.py",
     "if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):", "if False:",
     "test_project_gate_refuses_non_empty_folder", [CS]),
    ("il2cpp magic not checked", S + "csharp.py", "if magic != IL2CPP_MAGIC:", "if False:",
     "test_non_il2cpp_is_refused", [CS]),
    ("--type ignored (decompiles the whole assembly)", S + "csharp.py",
     'cmd = ["ilspycmd", "/in/asm.dll"] + (["-t", type_name] if type_name else [])',
     'cmd = ["ilspycmd", "/in/asm.dll"]',
     "test_one_type", [CS]),
    ("NULL control (comment only)", S + "csharp.py", "IL2CPP_MAGIC = 0xFAB11BAF",
     "IL2CPP_MAGIC = 0xFAB11BAF  # Unity IL2CPP global-metadata.dat", None, [CS]),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=600):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-cs-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        for t in test_files:
            shutil.copy(REPO / "tests" / t, f"{tmp}/tests/")
        shutil.copytree(REPO / "tests" / "fixtures" / "reverse_engineering", f"{tmp}/tests/fixtures/reverse_engineering")
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
        print(f"{name:46} {verdict:9} {detail}", flush=True)
    faults = [r for r in results if r[2]]
    caught = sum(1 for r in faults if r[1] == "CAUGHT")
    null = next((v for n, v, _ in results if n.startswith("NULL")), "not run")
    print(f"== {caught}/{len(faults)} fault arms caught by their named test; null control: {null}")


if __name__ == "__main__":
    main()
