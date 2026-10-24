"""Planted-fault battery for the GDRE tests (tests/test_reverse_engineering_gdre.py).

Each arm mutates a COPY of gdre.py, or of the proof helpers the tests compare
with (proof/gdre/prove.py), in a temp tree; the real files are never touched.
Verdicts match native_tests.py (CAUGHT = the named test failed on an assertion).
The guard, parser, gate and fake-sandbox arms need no Docker; the scripts-only,
pack-engine and real-decompile arms need the re-gdre image. NULL changes a
comment only.

  systemd-run --user --scope -p MemoryMax=4G python gdre_tests.py [ARM NAME ...]
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
T = "test_reverse_engineering_gdre.py"
S = "scripts/gdre.py"
P = "proof/gdre/prove.py"

ARMS = [
    ("engine guard accepts anything", S,
     'ENGINE_OK = re.compile(r"^[1-4]\\.\\d{1,2}(\\.\\d{1,2})?\\Z")', 'ENGINE_OK = re.compile(r".*", re.S)',
     "test_engine_version_must_be_plain"),
    ("engine guard back to $ (a final newline passes)", S,
     'ENGINE_OK = re.compile(r"^[1-4]\\.\\d{1,2}(\\.\\d{1,2})?\\Z")',
     'ENGINE_OK = re.compile(r"^[1-4]\\.\\d{1,2}(\\.\\d{1,2})?$")',
     "test_engine_version_must_be_plain"),
    ("exit 0 with no output read as success", S,
     'if not os.path.lexists(os.path.join(out_dir, "script.gd")):      # GDRE exits 0 on failure', "if False:",
     "test_a_failed_decompile_that_exits_zero_is_an_error"),
    ("crash not told apart", S, 'if res["returncode"] >= 128:', "if False:",
     "test_a_crash_is_reported_with_its_signal"),
    ("output read through a plain open (follows a link)", S,
     "with sandbox.open_output(out_dir, rel) as fh:", 'with open(os.path.join(out_dir, rel), "rb") as fh:',
     "test_a_planted_link_is_not_read"),
    ("text form takes any number of outputs", S, "if len(made) != 1:", "if not made:",
     "test_to_text_needs_exactly_one_output"),
    ("a duplicated member is picked silently", S, "if len(hits) > 1:", "if False:",
     "test_member_missing_encrypted_or_twice_is_refused"),
    ("an encrypted member is read", S, 'if f["encrypted"]:', "if False:",
     "test_member_missing_encrypted_or_twice_is_refused"),
    ("member path keeps its res:// scheme", S,
     'want = member.split("://", 1)[1] if "://" in member else member', "want = member",
     "test_member_bytes_reads_the_stored_script"),
    ("gate takes any purpose", S, "if purpose not in purposes:", "if False:",
     "test_recover_gate_refuses_unlawful_purpose_and_blank_note"),
    ("gate takes a blank note", S, "if not note or not note.strip():", "if not note:",
     "test_recover_gate_refuses_unlawful_purpose_and_blank_note"),
    ("full folder accepted", S,
     "if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):", "if False:",
     "test_recover_refuses_a_folder_that_is_not_empty"),
    ("a failed recovery's exit code ignored", S,
     'if not os.path.isdir(os.path.join(work, "project")) or res["returncode"] != 0:',
     'if not os.path.isdir(os.path.join(work, "project")):',
     "test_recover_failure_is_an_error"),
    ("listing total not checked", S, "if total != len(paths):", "if False:", "test_parse_listing"),
    ("listing keeps non-path lines", S,
     'if ln.strip().startswith(("res://", "user://"))]', "if ln.strip()]", "test_parse_listing"),
    ("report misses hyphenated totals", S,
     'REPORT_LINE = re.compile(r"^([A-Z][A-Za-z -]+?):\\s+(\\d+)\\s*$")',
     'REPORT_LINE = re.compile(r"^([A-Z][A-Za-z ]+?):\\s+(\\d+)\\s*$")', "test_parse_report"),
    ("mount the whole folder, not the file", S,
     'return f"{path}:/in/{name}", f"/in/{name}"',
     'return f"{os.path.dirname(path)}:/in", f"/in/{os.path.basename(path)}"',
     "test_target_mounts_only_the_file"),
    ("--scripts-only dropped", S, '(["--scripts-only"] if scripts_only else [])', "([])",
     "test_recover_scripts_only"),
    ("a member decompiles with one fixed table, not its pack's", S,
     "return decompile(path, engine or pack_engine, timeout=timeout)",
     'return decompile(path, engine or "4.5.0", timeout=timeout)',
     "test_every_engine_decompiles_to_its_source"),
    ("proof: hex left as written", P, "return str(int(s[2:], 16))", "return s",
     "test_notation_is_canonical_but_values_are_not"),
    ("proof: string prefix (& ^) dropped", P, "toks.append(prefix + json.dumps(value, ensure_ascii=False))",
     "toks.append(json.dumps(value, ensure_ascii=False))", "test_notation_is_canonical_but_values_are_not"),
    ("proof: scene ids kept", P, "SCENE_NOISE = re.compile(r' (?:uid=\"[^\"]*\"|unique_id=\\d+|load_steps=\\d+)')",
     "SCENE_NOISE = re.compile(r'(?!)')", "test_scene_lines_ignore_ids_and_order_but_not_values"),
    ("proof: scene values ignored", P, "return [(head, sorted(props)) for head, props in sections]",
     "return [(head, len(props)) for head, props in sections]",
     "test_scene_lines_ignore_ids_and_order_but_not_values"),
    ("NULL control (comment only)", S, 'MAX_TEXT = 16 << 20 ', 'MAX_TEXT = 16 << 20  # (unchanged) ', None),
]

SUMMARY = re.compile(r"\d+ (passed|failed)")
FAILED = re.compile(r"^(?:FAILED|SUBFAILED\(.*?\)) (\S+)(?: - ([\w.]+))?")


def run_arm(name, rel, old, new, expect, timeout=900):
    src = (SKILL / rel).read_text()
    n = src.count(old)
    if n != 1:
        return "VOID", f"anchor found {n} times"
    with tempfile.TemporaryDirectory(prefix="re-gdre-battery-") as tmp:
        os.makedirs(f"{tmp}/tests")
        shutil.copy(REPO / "tests" / T, f"{tmp}/tests/")
        shutil.copytree(REPO / "tests" / "fixtures" / "reverse_engineering", f"{tmp}/tests/fixtures/reverse_engineering")
        shutil.copytree(SKILL / "scripts", f"{tmp}/skills/reverse-engineering/scripts")
        shutil.copytree(SKILL / "proof" / "gdre", f"{tmp}/skills/reverse-engineering/proof/gdre")
        Path(f"{tmp}/skills/reverse-engineering/{rel}").write_text(src.replace(old, new))
        print(f"  [{name}]", flush=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="1000")
        with tempfile.TemporaryFile() as out:
            try:
                subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf",
                                f"{tmp}/tests/{T}"], stdout=out, stderr=subprocess.STDOUT,
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
        print(f"{name:58} {verdict:9} {detail}", flush=True)
    faults = [r for r in results if r[2]]
    caught = sum(1 for r in faults if r[1] == "CAUGHT")
    null = next((v for n, v, _ in results if n.startswith("NULL")), "not run")
    print(f"== {caught}/{len(faults)} fault arms caught by their named test; null control: {null}")


if __name__ == "__main__":
    main()
