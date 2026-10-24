"""Planted-fault battery for the Tier 1 static tests (tests/test_reverse_engineering.py): the
dossier validator, libscan, fingerprint, webfetch, unminify, wasm_inspect and the report.

Until the second whole-skill audit (2026-10-12, evening) these 70-odd tests had never been
shown to fail on a planted fault. Each arm mutates a COPY of one script in a temp tree that
holds the skill's scripts, dossiers and proof; the real files are never touched. Verdicts are
native_tests.py's: CAUGHT only when the named test fails on an assertion; ERRORED, SURVIVED,
WRONG, CRASHED and TIMEOUT are reported apart. The NULL arm changes a comment only and must
survive.

  systemd-run --user --scope -p MemoryMax=4G python static_tests.py [ARM NAME ...]
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from native_tests import FAILED, REPO, SKILL, SUMMARY  # noqa: E402

STATIC = "test_reverse_engineering.py"
S = "scripts/"

# (name, file, old, new, expected test)
ARMS = [
    # validate_dossier.py
    ("duplicate ids pass", S + "validate_dossier.py", "        if cid in seen:", "        if False:",
     "test_duplicate_id"),
    ("dangling support passes", S + "validate_dossier.py", "if isinstance(ref, str) and ref not in seen:",
     "if False:", "test_dangling_support"),
    ("confirmed rule skipped", S + "validate_dossier.py",
     '        if c.get("status") != "confirmed":\n            continue',
     '        if True:\n            continue', "test_confirmed_behaviour_without_evidence"),
    ("static method confirms", S + "validate_dossier.py", 'if ev.get("method") not in DYNAMIC_METHODS:',
     "if False:", "test_confirmed_behaviour_on_static_evidence"),
    ("empty literal passes", S + "validate_dossier.py", "if isinstance(lit, str) and not lit.strip():",
     "if False:", "test_ground_truth_with_an_empty_literal"),
    ("extra field passes", S + "validate_dossier.py", 'elif schema.get("additionalProperties") is False:',
     "elif False:", "test_extra_field"),
    ("enum not checked", S + "validate_dossier.py", 'if "enum" in schema and value not in schema["enum"]:',
     "if False:", "test_unlawful_purpose"),
    ("byte re-read one off", S + "validate_dossier.py",
     "ok = bool(want) and data[off:off + len(want)] == want",
     "ok = bool(want) and data[off + 1:off + 1 + len(want)] == want", "test_locators_are_reread_against_the_evidence"),
    ("only the first marker re-read", S + "validate_dossier.py",
     "nowhere = [mk for mk in more if not mk or not any(mk in t for t in texts)]", "nowhere = []",
     "test_every_marker_of_a_library_claim_is_reread"),
    ("a missing named file passes", S + "validate_dossier.py", "            if absent:", "            if False:",
     "test_a_named_file_missing_from_the_folder_fails_in_any_form"),
    # libscan.py
    ("one marker is found", S + "libscan.py", "NEED = 2  # markers", "NEED = 1  # markers",
     "test_two_markers_found_one_trace_none_absent"),
    ("markers not pooled", S + "libscan.py", "    for label, text in sources:\n",
     "    for label, text in sources[:1]:\n", "test_markers_pool_across_files"),
    ("claim cites the whole URL", S + "libscan.py",
     'name = os.path.basename(urllib.parse.urlparse(first["file"]).path) or first["file"]',
     'name = first["file"]', "test_claims_cite_the_file_name_of_a_url"),
    ("page lowered by str.lower", S + "libscan.py", "low = html.translate(ASCII_LOWER)", "low = html.lower()",
     "test_page_scripts_keep_their_place_after_non_ascii_text"),
    ("special files read", S + "libscan.py", "            if not stat.S_ISREG(st.st_mode):",
     "            if False:", "test_special_files_in_a_folder_are_skipped"),
    ("a pipe is opened blocking", S + "libscan.py", "os.open(p, os.O_RDONLY | os.O_NONBLOCK)",
     "os.open(p, os.O_RDONLY)", "test_special_files_in_a_folder_are_skipped"),
    # fingerprint.py
    ("address run unbounded", S + "fingerprint.py", "URL = r\"(?:https?:)?//[^\\s\\\"'<>)]{0,300}?\"",
     "URL = r\"(?:https?:)?//[^\\s\\\"'<>)]*?\"", "test_a_big_inline_blob_scans_quickly"),
    ("gsap anywhere in an address", S + "fingerprint.py", 'URL + r"[/@.]gsap\\b" + REST', 'URL + r"gsap" + REST',
     "test_base64_noise_is_not_a_library"),
    ("Framer CDN read as Motion", S + "fingerprint.py", '"patterns": [r"framer-motion"]},',
     '"patterns": [r"framer-motion", r"framerusercontent\\.com"]},', "test_a_framer_hosted_file_is_not_framer_motion"),
    ("Vite pattern unbounded", S + "fingerprint.py", "r'<script type=\"module\"[^>]{1,300}/assets/'",
     "r'<script type=\"module\"[^>]+/assets/'", "test_a_hostile_page_cannot_stall_the_scans"),
    ("a codec charset stops the fetch", S + "fingerprint.py", "    except (LookupError, UnicodeError):",
     "    except UnicodeError:", "test_a_charset_that_is_not_text_falls_back"),
    # webfetch.py
    ("redirect body read", S + "webfetch.py", "                resp.close()                     # closed unread",
     "                resp.content\n                resp.close()                     # closed unread",
     "test_a_redirect_body_is_never_unpacked"),
    ("final body uncapped", S + "webfetch.py", "body = resp.raw.read(limit + 1, decode_content=True)",
     "body = resp.raw.read(decode_content=True)", "test_fingerprint_reads_at_most_its_cap"),
    ("no deadline timer", S + "webfetch.py", "outer, _ = signal.setitimer(signal.ITIMER_REAL, limit_s)",
     "outer, _ = signal.setitimer(signal.ITIMER_REAL, 0)", "test_a_dripping_server_cannot_hold_a_download"),
    ("caller's timer dropped", S + "webfetch.py", "        if outer:                       # a caller's own timer",
     "        if False:                       # a caller's own timer", "test_a_callers_own_timer_survives_a_fetch"),
    ("redirects never end", S + "webfetch.py", "    for _ in range(MAX_REDIRECTS + 1):", "    while True:",
     "test_a_redirect_loop_ends"),
    # unminify.py
    ("'..' kept", S + "unminify.py", 'if p not in ("", ".", "..")]', 'if p not in ("", ".")]',
     "test_safe_rel_strips_escapes"),
    ("map escape check off", S + "unminify.py",
     "        if dest != out_real and not dest.startswith(out_real + os.sep):", "        if False:",
     "test_a_link_inside_the_output_folder_cannot_carry_a_write_out"),
    ("local map read from anywhere", S + "unminify.py", "    if not path.startswith(folder + os.sep):",
     "    if False:", "test_a_local_map_reference_stays_in_the_files_folder"),
    ("duplicates overwrite", S + "unminify.py", "        while rel in taken:", "        while False:",
     "test_sources_that_differ_only_by_a_query_both_survive"),
    ("a clash stops the recovery", S + "unminify.py", "        except OSError as e:     # \"src/a\"",
     "        except ValueError as e:     # \"src/a\"", "test_a_file_and_a_folder_of_one_name_do_not_stop_the_recovery"),
    ("a device is read", S + "unminify.py", "    if not os.path.isfile(args.file):", "    if False:",
     "test_a_local_file_must_be_a_regular_file_under_the_cap"),
    # wasm_inspect.py
    ("vector cap 100x", S + "wasm_inspect.py", "    if n > cap:", "    if n > cap * 100:",
     "test_a_flood_of_tiny_entries_is_capped"),
    ("long run copied", S + "wasm_inspect.py",
     'text = bytes(blob[m.start():min(m.end(), m.start() + 120)]).decode("ascii")',
     'text = m.group(0)[:120].decode("ascii")', "test_a_long_printable_run_is_not_copied"),
    ("module read into memory", S + "wasm_inspect.py",
     "return memoryview(mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ))", "return memoryview(fh.read())",
     "test_a_big_module_is_read_without_copying_it"),
    ("body offset one off", S + "wasm_inspect.py", "out.append((base + start, size))",
     "out.append((base + start - 1, size))", "test_body_offsets_point_at_the_bodies"),
    ("names stop at the first '>'", S + "wasm_inspect.py", 'func\\[(\\d+)\\](?: <(.*)>)?:$"',
     'func\\[(\\d+)\\](?: <([^>]*)>)?:$"', "test_disassembly_header_pattern"),
    # First planted as the break alone, which changed nothing: the next header resets `inside`
    # anyway. The real fault keeps `inside` set once it is set.
    ("function runs into the next", S + "wasm_inspect.py",
     "                    if inside:\n                        break\n                    inside = want in",
     "                    inside = inside or want in", "test_one_function_stops_where_the_next_begins"),
    ("function cap off", S + "wasm_inspect.py", "vec_count(cur, MAX_FUNCS, \"functions\")",
     "vec_count(cur, 10 ** 12, \"functions\")", "test_absurd_function_count_is_capped"),
    # dossier.py
    ("single backtick fence", S + "dossier.py",
     '    fence = "`" * (max((len(r) for r in re.findall(r"`+", s)), default=0) + 1)', '    fence = "`"',
     "test_backticks_and_pipes_in_literals_survive"),
    ("NULL control (comment only)", S + "libscan.py", "# ---------- inputs ----------",
     "# ---------- inputs (reworded) ----------", None),
]


def run_arm(name, rel, old, new, expect, timeout=900):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-static-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        shutil.copy(REPO / "tests" / STATIC, f"{tmp}/tests/")
        ignore = shutil.ignore_patterns("__pycache__", "node_modules", "media")
        for part in ("scripts", "dossiers", "proof"):
            shutil.copytree(SKILL / part, f"{tmp}/skills/reverse-engineering/{part}", ignore=ignore)
        mutated = src.replace(old, new)
        Path(f"{tmp}/skills/reverse-engineering/{rel}").write_text(mutated)
        line = next(i + 1 for i, ln in enumerate(mutated.splitlines()) if new.splitlines()[0] in ln)
        print(f"  [{name}] mutated {rel}:{line}: {old.splitlines()[0].strip()[:70]!r} -> "
              f"{new.splitlines()[0].strip()[:70]!r}", flush=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="1000")
        with tempfile.TemporaryFile() as out:
            try:
                subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf",
                                f"{tmp}/tests/{STATIC}"], stdout=out, stderr=subprocess.STDOUT,
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
    for name, rel, old, new, expect in ARMS:
        if only and name not in only:
            continue
        verdict, detail = run_arm(name, rel, old, new, expect)
        results.append((name, verdict, expect))
        print(f"{name:38} {verdict:9} {detail}", flush=True)
    faults = [r for r in results if r[2]]
    caught = sum(1 for r in faults if r[1] == "CAUGHT")
    null = next((v for n, v, _ in results if n.startswith("NULL")), "not run")
    print(f"== {caught}/{len(faults)} fault arms caught by their named test; null control: {null}")


if __name__ == "__main__":
    main()
