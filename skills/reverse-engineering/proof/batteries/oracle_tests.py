"""Planted-fault battery for the native-oracle tests (tests/test_reverse_engineering_oracle.py).

Each arm mutates a COPY of oracle.py or oracle_runner.py in a temp tree; the real
files are never touched. Verdicts are the same as native_tests.py (CAUGHT = the
named test failed on an assertion). The NULL arm changes a comment only.

Needs Docker and the re-oracle image (the tests compile and emulate inside it);
without them every oracle test skips and the arms cannot be judged, so the runner
refuses rather than report a meaningless pass.

  systemd-run --user --scope -p MemoryMax=4G python oracle_tests.py [ARM NAME ...]
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
ORACLE = "test_reverse_engineering_oracle.py"
S = "scripts/"

# (name, file, old, new, expected test, test files to run)
ARMS = [
    ("comparison always equal", S + "oracle_runner.py",
     "same = norm_ret(ro) == norm_ret(rr) and oo == orr", "same = True",
     "test_sysv_off_by_one_mix_is_caught", [ORACLE]),
    ("output buffers dropped from the compare", S + "oracle_runner.py",
     "same = norm_ret(ro) == norm_ret(rr) and oo == orr", "same = norm_ret(ro) == norm_ret(rr)",
     "test_sysv_outbuf_wrong_shift_is_caught", [ORACLE]),
    ("sysv argument registers swapped", S + "oracle_runner.py",
     '"sysv": [UC_X86_REG_RDI, UC_X86_REG_RSI, UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8, UC_X86_REG_R9],',
     '"sysv": [UC_X86_REG_RSI, UC_X86_REG_RDI, UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8, UC_X86_REG_R9],',
     "test_known_values_sysv", [ORACLE]),
    ("win64 argument registers swapped", S + "oracle_runner.py",
     '"win64": [UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9],',
     '"win64": [UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8, UC_X86_REG_R9],',
     "test_known_values_win64", [ORACLE]),
    ("signed return not sign-extended", S + "oracle_runner.py",
     "if ret.get(\"signed\", True) and v >= (1 << (w * 8 - 1)):", "if False:",
     "test_known_values_sysv", [ORACLE]),
    ("emulation runs past the return", S + "oracle_runner.py",
     "uc.emu_start(self.entry, SENTINEL, count=max_insns)",
     "uc.emu_start(self.entry, SENTINEL + 0x1000, count=max_insns)",
     "test_sysv_faithful_mix_matches", [ORACLE]),
    ("section bytes never written", S + "oracle_runner.py",
     "for sec in self.sections:\n            uc.mem_write(sec[\"addr\"], sec[\"data\"])",
     "for sec in self.sections[:0]:\n            uc.mem_write(sec[\"addr\"], sec[\"data\"])",
     "test_sysv_faithful_mix_matches", [ORACLE]),
    ("boundary inputs not generated", S + "oracle_runner.py",
     'if corpus.get("boundaries", True) and int_specs:', "if False and int_specs:",
     "test_boundary_only_divergence_is_caught", [ORACLE]),
    ("ground-truth disasm finds nothing", S + "oracle_runner.py",
     "if sec[\"exec\"] and sec[\"addr\"] <= subject.entry < sec[\"addr\"] + len(sec[\"data\"]):", "if False:",
     "test_sysv_ground_truth_disasm_present", [ORACLE]),
    ("a missing function resolves to a bogus address", S + "oracle_runner.py",
     "raise OracleError(f\"no function symbol {name!r} in {path} (have: {', '.join(sorted(syms)[:20])})\")",
     "return 0x1000",
     "test_missing_function_is_an_error", [ORACLE]),
    ("NULL control (comment only)", S + "oracle_runner.py",
     "PAGE = 0x1000", "PAGE = 0x1000  # one page", None, [ORACLE]),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=600):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-oracle-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        for t in test_files:
            shutil.copy(REPO / "tests" / t, f"{tmp}/tests/")
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
    if summary and "no tests ran" in summary:
        return "NOIMAGE", summary
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
    sys.path.insert(0, str(SKILL / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("sbx", str(SKILL / "scripts" / "sandbox.py"))
    sbx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbx)
    ok, _ = sbx.docker_available()
    if not ok or subprocess.run(["docker", "image", "inspect", sbx.image_tag(sbx.ORACLE_DOCKERFILE)],
                                capture_output=True).returncode != 0:
        print("re-oracle image or docker not present; build it first (the tests cannot run without it).")
        sys.exit(2)

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
