"""Planted-fault battery for the Tier 2/3 tests (tests/test_reverse_engineering_native.py,
and tests/test_reverse_engineering_sandbox.py for the arms in sandbox.py and unminify.py).

Each arm mutates a COPY of one script in a temp tree; the real files are never touched.
Verdicts:
  CAUGHT    the named test failed on an assertion
  ERRORED   the named test failed, but by an exception other than an assertion (not a catch)
  SURVIVED  every test passed
  WRONG     tests failed, but not the named one
  CRASHED   no pytest summary line
  TIMEOUT   the run did not end inside the arm's time limit (the tests have no bound of their own)
The NULL arm changes a comment only and must survive.

  systemd-run --user --scope -p MemoryMax=4G python native_tests.py [ARM NAME ...]

Last run 2026-10-12 (the whole-skill audit): 28/36 CAUGHT on the first run. The 8 misses were all
weak tests (3 could never fail, Ghidra's filter test passed for the wrong reason, a missing function
had no test, 2 pipe tests hung instead of failing, the per-file write limit had no test); after the
tests were fixed, those 8 arms ran again: 8/8 CAUGHT. NULL SURVIVED both times.
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
NATIVE = "test_reverse_engineering_native.py"
SANDBOX = "test_reverse_engineering_sandbox.py"
S = "scripts/"

# (name, file, old, new, expected test, test files to run)
ARMS = [
    ("macho endianness inverted", S + "bin_triage.py",
     'big = data[:4] in (b"\\xfe\\xed\\xfa\\xce", b"\\xfe\\xed\\xfa\\xcf")',
     'big = data[:4] in (b"\\xce\\xfa\\xed\\xfe", b"\\xcf\\xfa\\xed\\xfe")', "test_macho_thin_dylib", [NATIVE]),
    ("elf entry from the wrong field", S + "bin_triage.py", 'rep["entry"] = r.u64(24)', 'rep["entry"] = r.u64(32)',
     "test_reads_elf_identity_fields", [NATIVE]),
    ("DT_NEEDED tag wrong", S + "bin_triage.py", "if tag == 1:            # DT_NEEDED",
     "if tag == 2:            # DT_NEEDED", "test_bin_ls_matches_readelf", [NATIVE]),
    ("section cap removed", S + "bin_triage.py", "for i in range(min(shnum, MAX_SECTIONS)):",
     "for i in range(shnum):", "test_absurd_section_count_is_capped", [NATIVE]),
    ("reader bound off by one", S + "bin_triage.py", "if off < 0 or n < 0 or off + n > self.end:",
     "if off < 0 or n < 0 or off + n > self.end + 1:", "test_reader_raises_past_end", [NATIVE]),
    ("PE32 read as PE32+", S + "bin_triage.py", "pe64 = magic == 0x20B", "pe64 = magic != 0x20B",
     "test_pe_matches_file_tool", [NATIVE]),
    ("DLL flag wrong", S + "bin_triage.py", 'rep["type"] = "DLL" if (chars & 0x2000) else',
     'rep["type"] = "DLL" if (chars & 0x0002) else', "test_pe_exe_and_dll_are_told_apart", [NATIVE]),
    ("strings only in the first window", S + "bin_triage.py", "for m in pat.finditer(data):",
     "for m in pat.finditer(data[:4 << 20]):", "test_a_string_across_a_4mb_seam_is_whole", [NATIVE]),
    ("strings cut short", S + "bin_triage.py", "MAX_RUN = 8192 ", "MAX_RUN = 16 ",
     "test_a_string_across_a_4mb_seam_is_whole", [NATIVE]),
    ("PE validator accepts anything", S + "carve.py",
     'if 0 < lfanew < size and p + 4 <= size and mm[p:p + 4] == b"PE\\x00\\x00":', "if True:",
     "test_mz_validator_rejects_text_accepts_pe", [NATIVE]),
    ("TrueType searched blind", S + "carve.py", "if off + len(magic) <= size and mm[off:off + len(magic)] == magic:",
     "if off + len(magic) <= size and mm.find(magic) != -1:", "test_no_false_truetype_in_png", [NATIVE]),
    ("hit offset off by one", S + "carve.py", 'hits.append({"offset": i, "magic"',
     'hits.append({"offset": i + 1, "magic"', "test_finds_png_and_embedded_zip_at_right_offsets", [NATIVE]),
    ("cut list not reported", S + "carve.py", "capped.append(label)       # one more",
     "pass  # capped.append(label)       # one more", "test_a_cut_list_says_it_was_cut", [NATIVE]),
    ("candidate budget removed", S + "carve.py",
     "if found >= MAX_HITS_PER_SIG or seen >= MAX_CANDIDATES_PER_SIG or len(hits) >= MAX_HITS_TOTAL:",
     "if found >= MAX_HITS_PER_SIG or len(hits) >= MAX_HITS_TOTAL:", "test_a_flood_of_false_mz_is_cut_and_said",
     [NATIVE]),
    ("JPEG read blind", S + "carve.py", '(b"\\xff\\xd8\\xff", "JPEG image", _jpeg_validator)',
     '(b"\\xff\\xd8\\xff", "JPEG image", None)', "test_short_magics_do_not_fire_on_random_data", [NATIVE]),
    ("gzip read blind", S + "carve.py", '(b"\\x1f\\x8b\\x08", "gzip", _gzip_validator)',
     '(b"\\x1f\\x8b\\x08", "gzip", None)', "test_short_magics_do_not_fire_on_random_data", [NATIVE]),
    ("bzip2 at block size 9 only", S + "carve.py", 'mm[off + 3] in b"123456789"', 'mm[off + 3] in b"9"',
     "test_real_jpeg_gzip_and_bzip2_are_still_found", [NATIVE]),
    ("gate takes any purpose", S + "carve.py", "if purpose not in purposes:", "if purpose is None:",
     "test_extract_refuses_unlawful_purpose", [NATIVE]),
    ("gate takes a blank note", S + "carve.py", "if not note or not note.strip():", "if not note:",
     "test_extract_refuses_empty_note", [NATIVE]),
    ("full folder accepted", S + "carve.py",
     "if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):", "if False:",
     "test_extract_refuses_a_folder_that_is_not_empty", [NATIVE]),
    ("escaping links kept", S + "carve.py", "if (os.path.islink(p) and os.readlink(p)",
     "if (False and os.path.islink(p) and os.readlink(p)", "test_carve_removes_links_that_leave_the_folder",
     [NATIVE]),
    ("extraction cap not passed", S + "carve.py", "with_passwd=True, max_write=cap)",
     "with_passwd=True, max_write=None)", "test_extraction_stops_at_its_size_cap", [NATIVE]),
    ("'@' back in the radare2 filter", S + "r2.py", r'[A-Za-z_][\w.$:]*)\Z")', r'[A-Za-z_][\w.$:@]*)\Z")',
     "test_a_foreach_iterator_cannot_ride_in_on_a_name", [NATIVE]),
    ("';' and '!' in the radare2 filter", S + "r2.py", r'[A-Za-z_][\w.$:]*)\Z")', r'[A-Za-z_][\w.$:;!]*)\Z")',
     "test_symbol_injection_is_rejected", [NATIVE]),
    ("radare2 filter back to $ (a final newline passes)", S + "r2.py", r'[A-Za-z_][\w.$:]*)\Z")',
     r'[A-Za-z_][\w.$:]*)$")', "test_a_foreach_iterator_cannot_ride_in_on_a_name", [NATIVE]),
    ("';' in the Ghidra filter", S + "ghidra.py", r'[A-Za-z_][\w.$@:]*)\Z")', r'[A-Za-z_][\w.$@:;]*)\Z")',
     "test_selector_validation", [NATIVE]),
    ("Ghidra filter back to $ (a final newline passes)", S + "ghidra.py", r'[A-Za-z_][\w.$@:]*)\Z")',
     r'[A-Za-z_][\w.$@:]*)$")', "test_a_selector_with_a_trailing_newline_is_refused", [NATIVE]),
    ("Ghidra result read through links", S + "ghidra.py", 'with sandbox.open_output(out_dir, "result.txt") as fh:',
     'with open(os.path.join(out_dir, "result.txt"), "rb") as fh:', "test_ghidra_never_reads_a_planted_link",
     [NATIVE]),
    ("missing function not raised", S + "ghidra.py", 'if text.startswith("NO_FUNCTION"):', "if False:",
     "test_a_missing_function_is_an_error", [NATIVE]),
    ("Ghidra bytes lose leading zeros", S + "GhidraDecompile.java", 'String.format("%02x", x & 0xff)',
     'String.format("%x", x & 0xff)', "test_three_tools_agree_on_add_bytes", [NATIVE]),
    ("Ghidra ground-truth lines dropped", S + "GhidraDecompile.java",
     'sb.append("// ---- instructions (ground truth: address + bytes) ----\\n");',
     'sb.append("// ---- instructions ----\\n");', "test_decompile_add_reads_the_expression", [NATIVE]),
    ("read-back follows a linked folder", S + "sandbox.py",
     "if not path.startswith(root + os.sep) or os.path.realpath(path) != path:",
     "if not path.startswith(root + os.sep):", "test_links_and_pipes_are_refused", [SANDBOX]),
    ("read-back opens anything", S + "sandbox.py", "if not stat.S_ISREG(os.fstat(fd).st_mode):", "if False:",
     "test_ghidra_does_not_hang_on_a_planted_pipe", [NATIVE]),
    ("read-back blocks on a pipe", S + "sandbox.py", "os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK",
     "os.O_RDONLY | os.O_NOFOLLOW", "test_ghidra_does_not_hang_on_a_planted_pipe", [NATIVE]),
    ("tree copy keeps links", S + "sandbox.py",
     "            except SandboxError:\n                skipped.append(item)",
     "            except SandboxError:\n                shutil.copyfile(os.path.join(top, item), os.path.join(dst, item))",
     "test_tree_copy_keeps_files_and_drops_links", [SANDBOX]),
    ("disk not polled", S + "sandbox.py", "elif watch and _disk_use(watch) > max_write:", "elif False:",
     "test_extraction_stops_at_its_size_cap", [NATIVE]),
    ("no per-file size limit", S + "sandbox.py",
     '        argv += ["--ulimit", f"fsize={int(max_write)}"]', "        pass",
     "test_a_run_cannot_write_past_its_cap", [SANDBOX]),
    ("webcrack result read through links", S + "unminify.py",
     'with sandbox.open_output(res_dir, os.path.join("modules", "deobfuscated.js")) as fh:',
     'with open(os.path.join(res_dir, "modules", "deobfuscated.js"), "rb") as fh:',
     "test_unminify_never_returns_a_planted_link", [SANDBOX]),
    # second whole-skill audit (2026-10-12, evening)
    ("radare2 JSON cut at the tail", S + "r2.py", "memory=memory, timeout=timeout, max_output=MAX_JSON)",
     "memory=memory, timeout=timeout)", "test_a_list_longer_than_the_output_tail_is_read_whole", [NATIVE]),
    ("no limit inside the container", S + "sandbox.py",
     'argv += [image, "timeout", "-k", "5", str(int(timeout) + INNER_GRACE)] + list(cmd)',
     "argv += [image] + list(cmd)", "test_the_time_limit_holds_when_the_caller_dies", [SANDBOX]),
    ("Ghidra result cut silently", S + "ghidra.py", "        if len(raw) > MAX_RESULT:", "        if False:",
     "test_a_ghidra_result_past_the_read_cap_says_it_was_cut", [NATIVE]),
    ("cut notice drops a long line", S + "ghidra.py",
     '(text[:end + 1] if end >= 0 else text + "\\n")', 'text[:end + 1]',
     "test_a_ghidra_result_past_the_read_cap_says_it_was_cut", [NATIVE]),
    ("stderr takes the raised bound", S + "sandbox.py", '"stderr": _tail(err)[0], "argv": argv}',
     '"stderr": _tail(err, max_output)[0], "argv": argv}', "test_a_raised_output_bound_is_for_stdout_only",
     [SANDBOX]),
    ("pipes listed as extracted", S + "carve.py",
     "(extracted if os.path.islink(p) or stat.S_ISREG(os.lstat(p).st_mode) else special).append(p)",
     "extracted.append(p)", "test_carve_never_lists_a_pipe_as_an_extracted_file", [NATIVE]),
    ("runpath uncapped", S + "bin_triage.py",
     """            if len(rep["runpath"]) >= MAX_IMPORTS:   # 8192 entries of "a:a:..." once made 16 million
                break
            try:
                parts = [p for p in sr.cstr(strtab_off + off).split(":") if p]
            except BinError:
                continue
            rep["runpath"].extend(parts[:MAX_IMPORTS - len(rep["runpath"])])""",
     """            try:
                rep["runpath"].extend(p for p in sr.cstr(strtab_off + off).split(":") if p)
            except BinError:
                pass""", "test_runpath_entries_are_capped", [NATIVE]),
    # fat/universal Mach-O slice selection (added with the --slice support)
    ("fat slice offset from the wrong field", S + "bin_triage.py",
     "offset, size, align = r.u32(base + 8), r.u32(base + 12), r.u32(base + 16)",
     "offset, size, align = r.u32(base + 12), r.u32(base + 8), r.u32(base + 16)",
     "test_lists_slices_with_offsets_and_sizes", [NATIVE]),
    ("fat64 entry stride wrong", S + "bin_triage.py", "step = 32 if is64 else 20", "step = 20",
     "test_fat64_offsets", [NATIVE]),
    ("slice bounds check removed", S + "bin_triage.py", 'if not s["in_bounds"]:', "if False and s:",
     "test_missing_and_out_of_bounds_are_errors", [NATIVE]),
    ("missing slice falls back to the first", S + "bin_triage.py", "if not matches:", "if False and not matches:",
     "test_missing_and_out_of_bounds_are_errors", [NATIVE]),
    ("ARM64 subtype ignored", S + "bin_triage.py", "sub = cpusubtype & 0x00FFFFFF", "sub = 0",
     "test_subtype_names_the_variant", [NATIVE]),
    ("ARM64 subtype feature flags kept", S + "bin_triage.py", "sub = cpusubtype & 0x00FFFFFF", "sub = cpusubtype",
     "test_subtype_names_the_variant", [NATIVE]),
    ("fat subtype from the wrong field", S + "bin_triage.py",
     "cputype, cpusubtype = r.u32(base), r.u32(base + 4)", "cputype, cpusubtype = r.u32(base), r.u32(base + 12)",
     "test_arm64e_slices_are_told_apart_and_selectable", [NATIVE]),
    ("NULL control (comment only)", S + "bin_triage.py", "# Caps: a hostile file cannot make any of these unbounded.",
     "# Caps (reworded): a hostile file cannot make any of these unbounded.", None, [NATIVE]),
]
SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, test_files, timeout=420):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-native-battery-") as tmp:
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
        # Wide, or pytest drops the " - AssertionError: ..." part of a long summary line.
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="1000")
        with tempfile.TemporaryFile() as out:     # a file, not a pipe: a runaway arm cannot fill this process
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
    failed = {}                                   # test name -> the exception types it failed with
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
        print(f"{name:38} {verdict:9} {detail}", flush=True)
    faults = [r for r in results if r[2]]
    caught = sum(1 for r in faults if r[1] == "CAUGHT")
    null = next((v for n, v, _ in results if n.startswith("NULL")), "not run")
    print(f"== {caught}/{len(faults)} fault arms caught by their named test; null control: {null}")


if __name__ == "__main__":
    main()
