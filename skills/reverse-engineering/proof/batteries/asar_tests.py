"""Planted-fault battery for the asar reader tests (tests/test_reverse_engineering_asar.py).

Each arm mutates a COPY of asar.py in a temp tree; the real files are never touched.
Verdicts are the same as native_tests.py (CAUGHT = the named test failed on an
assertion). The NULL arm changes a comment only and must survive.

  systemd-run --user --scope -p MemoryMax=4G python asar_tests.py [ARM NAME ...]
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
ASAR = "test_reverse_engineering_asar.py"
S = "scripts/"

ARMS = [
    ("first word not checked", S + "asar.py", "if r.u32(0) != 4:", "if False:",
     "test_not_an_asar", [ASAR]),
    ("no 'files' tree check", S + "asar.py", 'if not isinstance(header, dict) or "files" not in header:',
     "if not isinstance(header, dict):", "test_header_without_files_is_refused", [ASAR]),
    ("absurd header size allowed", S + "asar.py",
     "if header_size <= 0 or header_size > MAX_HEADER or 16 + json_len > r.end or json_len > MAX_HEADER:",
     "if header_size <= 0:", "test_absurd_header_size_is_refused", [ASAR]),
    ("integrity compare always true", S + "asar.py", 'return h.hexdigest() == entry["integrity"]', "return True",
     "test_a_flipped_byte_is_a_mismatch", [ASAR]),
    ("in-bounds always true", S + "asar.py",
     '"in_bounds": unpacked or (offset is not None and 0 <= offset and offset + size <= total),',
     '"in_bounds": True,', "test_out_of_bounds_offset_is_skipped", [ASAR]),
    ("traversal guard removed", S + "asar.py", 'if not parts or any(seg == ".." for seg in parts):', "if False:",
     "test_safe_rel_refuses_dotdot", [ASAR]),
    ("unpacked entry not skipped", S + "asar.py", 'if f["unpacked"]:', "if False:",
     "test_unpacked_and_link_entries_are_reported_not_read", [ASAR]),
    ("symlink entry not skipped", S + "asar.py", 'if f["link"]:', "if False:",
     "test_unpacked_and_link_entries_are_reported_not_read", [ASAR]),
    ("depth cap removed", S + "asar.py", "if depth > MAX_DEPTH:", "if False:",
     "test_too_deep_tree_does_not_crash", [ASAR]),
    ("gate takes any purpose", S + "asar.py", "if purpose not in purposes:", "if False:",
     "test_refuses_unlawful_purpose", [ASAR]),
    ("gate takes a blank note", S + "asar.py", "if not note or not note.strip():", "if not note:",
     "test_refuses_empty_note", [ASAR]),
    ("full folder accepted", S + "asar.py",
     "if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):", "if False:",
     "test_refuses_non_empty_folder", [ASAR]),
    ("NULL control (comment only)", S + "asar.py", "COPY_CHUNK = 4 << 20", "COPY_CHUNK = 4 << 20  # 4 MiB",
     None, [ASAR]),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=300):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-asar-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        for t in test_files:
            shutil.copy(REPO / "tests" / t, f"{tmp}/tests/")
        shutil.copytree(REPO / "tests" / "fixtures" / "reverse_engineering", f"{tmp}/tests/fixtures/reverse_engineering")
        shutil.copytree(SKILL / "scripts", f"{tmp}/skills/reverse-engineering/scripts")
        mutated = src.replace(old, new)
        Path(f"{tmp}/skills/reverse-engineering/{rel}").write_text(mutated)
        line = next(i + 1 for i, ln in enumerate(mutated.splitlines()) if new.splitlines()[0] in ln)
        print(f"  [{name}] mutated {rel}:{line}", flush=True)
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
