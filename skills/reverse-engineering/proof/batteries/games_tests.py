"""Planted-fault battery for the games-tier tests (tests/test_reverse_engineering_games.py).

Each arm mutates a COPY of one script in a temp tree; the real files are never touched.
Verdicts (same as native_tests.py):
  CAUGHT    the named test failed on an assertion
  ERRORED   the named test failed, but by an exception other than an assertion (not a catch)
  SURVIVED  every test passed
  WRONG     tests failed, but not the named one
  CRASHED   no pytest summary line
  TIMEOUT   the run did not end inside the arm's time limit
The NULL arm changes a comment only and must survive.

  systemd-run --user --scope -p MemoryMax=4G python games_tests.py [ARM NAME ...]
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
GAMES = "test_reverse_engineering_games.py"
S = "scripts/"

# (name, file, old, new, expected test, test files to run)
ARMS = [
    ("magic not checked at start", S + "godot_pck.py",
     'if r.end >= 4 and r.raw(0, 4) == MAGIC:', "if r.end >= 4:",
     "test_non_pack_raises_cleanly", [GAMES]),
    ("trailer arithmetic wrong", S + "godot_pck.py", "start = r.end - ds - TRAILER", "start = r.end - ds",
     "test_pack_appended_to_an_executable_is_found", [GAMES]),
    ("version branch ignored (v2 reads a dir offset)", S + "godot_pck.py", "if version >= V3:",
     "if version >= V2:", "test_v2_absolute_filebase", [GAMES]),
    ("relative filebase inverted", S + "godot_pck.py",
     "rel_base = version >= V3 or (pack_flags & PACK_REL_FILEBASE)",
     "rel_base = version < V3 and not (pack_flags & PACK_REL_FILEBASE)",
     "test_pack_appended_to_an_executable_is_found", [GAMES]),
    ("file count cap removed", S + "godot_pck.py", "if count > MAX_FILES:", "if False:",
     "test_absurd_file_count_is_capped", [GAMES]),
    ("md5 compare always true", S + "godot_pck.py", 'return h.hexdigest() == f["md5"]', "return True",
     "test_a_flipped_byte_is_an_md5_mismatch", [GAMES]),
    ("format 1 (Godot 3) refused", S + "godot_pck.py", "if version not in (V1, V2, V3, V4):",
     "if version not in (V2, V3, V4):", "test_each_format_reads_its_header_and_files", [GAMES]),
    ("format 1 read as format 2 (flags and file base)", S + "godot_pck.py", "    if version == V1:\n",
     "    if False:\n", "test_godot3_script_bytes_are_where_the_directory_says", [GAMES]),
    ("format 1 entries read a flags word", S + "godot_pck.py", "entry_flags=version >= V2)",
     "entry_flags=True)", "test_each_format_reads_its_header_and_files", [GAMES]),
    ("format 1 offsets made relative to the pack start", S + "godot_pck.py", "pack_flags, file_base = 0, 0",
     "pack_flags, file_base = 0, start", "test_godot3_pack_embedded_with_absolute_offsets", [GAMES]),
    ("in-bounds check always true", S + "godot_pck.py",
     '"in_bounds": 0 <= abs_off and abs_off + size <= r.end,', '"in_bounds": True,',
     "test_an_entry_pointing_past_the_file_is_skipped", [GAMES]),
    ("traversal guard removed", S + "godot_pck.py", 'if not parts or any(seg == ".." for seg in parts):',
     "if False:", "test_safe_rel_refuses_dotdot", [GAMES]),
    ("gate takes any purpose", S + "godot_pck.py", "if purpose not in purposes:", "if False:",
     "test_refuses_unlawful_purpose", [GAMES]),
    ("gate takes a blank note", S + "godot_pck.py", "if not note or not note.strip():", "if not note:",
     "test_refuses_empty_note", [GAMES]),
    ("full folder accepted", S + "godot_pck.py",
     "if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):", "if False:",
     "test_refuses_a_folder_that_is_not_empty", [GAMES]),
    ("reader bound off by one", S + "godot_pck.py", "if off < 0 or n < 0 or off + n > self.end:",
     "if off < 0 or n < 0 or off + n > self.end + 64:", "test_reader_refuses_a_short_read", [GAMES]),
    ("NULL control (comment only)", S + "godot_pck.py",
     "# Pack (directory) flags.", "# Pack (directory) flags (unchanged).", None, [GAMES]),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=300):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-games-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        for t in test_files:
            shutil.copy(REPO / "tests" / t, f"{tmp}/tests/")
        shutil.copytree(REPO / "tests" / "fixtures" / "reverse_engineering", f"{tmp}/tests/fixtures/reverse_engineering")
        shutil.copytree(SKILL / "scripts", f"{tmp}/skills/reverse-engineering/scripts")
        mutated = src.replace(old, new)
        Path(f"{tmp}/skills/reverse-engineering/{rel}").write_text(mutated)
        line = next(i + 1 for i, ln in enumerate(mutated.splitlines()) if new.splitlines()[0] in ln)
        print(f"  [{name}] mutated {rel}:{line}: {old.splitlines()[0].strip()[:70]!r} -> "
              f"{new.splitlines()[0].strip()[:70]!r}", flush=True)
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
