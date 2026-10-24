"""Planted-fault battery for the HAR importer tests (tests/test_reverse_engineering_har.py).

Each arm mutates a COPY of har_import.py in a temp tree; the real files are never
touched. Verdicts match native_tests.py (CAUGHT = the named test failed on an
assertion). No Docker needed. NULL changes a comment only.

  systemd-run --user --scope -p MemoryMax=4G python har_tests.py [ARM NAME ...]
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
HAR = "test_reverse_engineering_har.py"
S = "scripts/"

ARMS = [
    ("url secrets not redacted", S + "har_import.py",
     'q = [(k, "REDACTED" if k.lower() in SECRET_PARAMS else v) for k, v in '
     "parse_qsl(parts.query, keep_blank_values=True)]",
     "q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]",
     "test_secret_params_redacted_others_kept", [HAR]),
    ("base64 body not decoded", S + "har_import.py", 'if content.get("encoding") == "base64":', "if False:",
     "test_bad_base64_body_is_skipped", [HAR]),
    ("traversal dotdot kept", S + "har_import.py", 'if any(s == ".." for s in segs):',
     "if False and segs:", "test_safe_rel_strips_dotdot", [HAR]),
    ("body size cap removed", S + "har_import.py", "if len(body) > MAX_BODY or total + len(body) > cap:",
     "if total + len(body) > cap:", "test_body_over_the_cap_is_skipped", [HAR]),
    ("gate takes any purpose", S + "har_import.py", "if purpose not in purposes:", "if False:",
     "test_gate_refusals", [HAR]),
    ("gate takes a blank note", S + "har_import.py", "if not note or not note.strip():", "if not note:",
     "test_gate_refusals", [HAR]),
    ("full folder accepted", S + "har_import.py",
     "if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):", "if False:",
     "test_gate_refuses_non_empty_folder", [HAR]),
    ("entries-is-a-list check removed", S + "har_import.py",
     'if not isinstance(log, dict) or not isinstance(log.get("entries"), list):',
     "if not isinstance(log, dict):", "test_entries_not_a_list", [HAR]),
    ("NULL control (comment only)", S + "har_import.py", "MAX_ENTRIES = 1_000_000",
     "MAX_ENTRIES = 1_000_000  # cap", None, [HAR]),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=300):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-har-battery-") as tmp:
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
