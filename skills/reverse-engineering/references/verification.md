# The verification spine

This is the core of the skill and the reason to trust its output. A reverse engineering
tool that guesses confidently is worse than none, because its guesses wear the costume of
fact. The spine separates reading from believing, and makes every instrument prove it can
fail before its green result counts.

## Every statement carries a kind

A dossier is one object per target. Every statement in it is a claim with a `kind`.

### ground_truth

What a tool literally output. Not interpreted. It cites:

- `tool`: what produced it (libscan.py, watch_page.py, wasm-objdump, a response header).
- `locator`: where exactly. For code: `<file> byte <N>` (or `<file> @ char <N>`, which is
  what libscan reports); for a disassembler: the address; for a run: the run and the time.
- `literal`: the exact text or bytes. Quote it; do not paraphrase, round, or tidy.

A ground_truth statement says what is there, never what it means. "The module sets a
timeout whose delay is `1e4` when p or u holds" is ground truth. "The slideshow moves every
ten seconds while muted" is not; that is a hypothesis until a run shows it, and then a
behavioural claim.

### hypothesis

What the ground truth is believed to mean. Provisional. It names its `basis` and the claims
it rests on (`supports`). Write it so it could be proven wrong.

### behavioural

What the target does at runtime. `confirmed` requires a runtime, dynamic or sandbox
observation; static reading can only make it `unconfirmed`, a labelled guess.

## The contract is executable

```bash
RE=skills/reverse-engineering/scripts
python $RE/validate_dossier.py dossiers/<target>.json
python $RE/validate_dossier.py dossiers/<target>.json --evidence <downloaded files>
```

`scripts/dossier-schema.json` is the single source of truth. The validator adds a semantic
layer: ids are unique, every `supports` reference resolves, a confirmed behavioural claim
carries runtime evidence, a ground_truth literal is not empty. `--evidence` adds the locator
discipline: every `<file> byte <N>` and `<file> @ char <N>` locator is re-read and must hold
its literal at exactly that place, its file must be in the folder, and a locator that names
a file there in any other form fails (it could not be re-read).
On the production site's dossier the first pass of that check caught a locator written from
memory (byte 6440; the literal is at byte 6466).

## Every instrument is proven able to fail

A check that cannot fail is not a check. Each instrument here was run against planted
faults, and each battery is a committed test (`tests/test_reverse_engineering*.py`):

| Instrument | Planted faults it must catch |
|---|---|
| validate_dossier.py | confirmed behaviour with no or static evidence, duplicate id, dangling support, unknown kind, missing lawful basis, unlawful purpose, extra field, missing or empty literal; a locator one byte off, a wrong literal, a char offset given as a byte offset, a cited file missing from the folder, a locator in another form, a library claim listing a marker that no evidence file holds (only the first marker was once re-read), a locator like `b.js:3` naming a file the folder does not hold (it once passed unchecked; a request URL stays exempt) |
| proof/libscan/prove.py | a marker in no build, a generic marker that strays into another library's build, too few markers for a library, a marker written in an entry file |
| watch_page.py | a wheel on a page that ignores it (no change), a cancelled timer (not fired), a click and a scroll handler (answered), per-frame jitter on a shared element (not an answer), a wheel that must reach a scroller in the middle of the screen, a failed action (no verdict), a sideways scroller |
| fingerprint.py, libscan.py, unminify.py | a 128 MB response must stop being read at the cap; random base64 must not read as a library; a 302 carrying 96 MB of gzip must not be unpacked (it held 193 MB), and a redirect loop must end; a page of unclosed `<script` or `<link` tags must scan in under 3 s (it took 20 to 57 s at 160 KB); a local file's source map must not be read from outside its folder or from a device; a server sending a byte per 0.2 s, in the body or the header, must not hold a download past its deadline (it never ended); `charset=zlib` must not stop a fetch; a pipe and a `/dev/zero` link in a scanned folder must be skipped (one blocked for ever, one read until memory ran out); two map sources that strip to one path must both be written, and a file and a folder of one name must not end the recovery; a local `--file` must be a regular file under the cap |
| sandbox.py check | eight break-out probes, each shown to report FAIL in a deliberately leaky container (network, writable root, root user, capabilities, host home, executable /tmp) |
| sandbox.py read-back | a link, a linked folder on the way and a pipe where a result belongs must be refused without blocking; a tree copy must leave links behind; ghidra.py, wasm_inspect.py and unminify.py must not hand back a planted link's target (ghidra.py once printed it as decompiled C); a run must not write past `max_write`; a container whose caller is killed must still die at its time limit (one ran 19 s into a 10 s limit) |
| capture_net.py, watch_page.py, browser skill | the running renderer must have no `--no-sandbox`, its own user and network namespace and a seccomp filter, read from /proc (`tests/chromium_sandbox_probe.py`); the tests failed on the old launches for exactly that |
| wasm_inspect.py | a flood of the smallest legal entry in each vector (imports, exports, types, data segments, function names) must be capped under 64 MB (4 MB of empty imports took 574 MB); a 20 MB printable run must not be copied to show 120 characters; body offsets, names with brackets, one function's disassembly stopping at the next, a module read without copying it |
| bin_triage.py (native) | its reading of `/bin/ls` must equal readelf field for field, and a real PE must equal file+objdump; a truncated header, an absurd section count and a bad PE offset must not crash or over-read; a little-endian Mach-O must not read as big-endian; a string across a 4 MB seam must come out whole; 8192 runpath entries must stay capped (they made 16 million items); a fat/universal Mach-O must list each slice's offset and size (32- and 64-bit fat), and `macho_slice`/`write_slice` must return the right slice by arch and by index and refuse a missing or out-of-bounds one |
| r2.py (native) | a disassembled instruction's bytes must equal the file's bytes at that address; a symbol argument carrying a radare2 command (`a;!id`, backticks, and `entry0@@c:ij`, a foreach iterator made only of letters, digits, `@` and `:`) must be refused before radare2 runs, and so must a name with a trailing newline (the pattern's `$` let `main\n` through until 2026-10-24); a string list larger than the sandbox's 200 KB output tail must come back whole (every list from a mid-sized binary once failed to parse) |
| ghidra.py (native) | the decompiled function must read back the source expression; `--list` must contain the known functions; a selector with `;` or a trailing newline must be refused before Ghidra starts (with the old `$`, `main\n` really started Ghidra in the sandbox); a result past the 4 MB read cap must say where it was cut |
| carve.py (native) | the TrueType magic must not fire inside a PNG; an `MZ` that is not a PE must not be reported; an embedded ZIP must be found at its exact offset; 32 MB of random bytes must give no JPEG or gzip; a real JPEG, gzip and block-size-1 bzip2 must still be found; a cut list must say it was cut; a flood of false `MZ` must stop and say so; extraction must refuse without a lawful purpose and note, refuse a folder that is not empty, stop at its size cap, remove a link that leaves its folder, and never list a pipe it made as an extracted file |
| godot_pck.py (games) | the start magic must be checked (a non-pack with a valid version word must still be refused), the embedded-trailer offset computed right, the format-2 vs 3/4 branch read correctly, the relative file base only applied off a non-zero pack start, the file-count cap and the Reader bound each pinned alone, a flipped byte caught by the md5 re-hash, a `res://../../` path refused by `_safe_rel` on its own, and the extraction gate refuse an unlawful purpose, a blank note and a non-empty folder; format 1 (Godot 3) must be read, not refused, without pack flags or a file base, its entries without a flags word, and its offsets as the absolute positions Godot 3 writes even when the pack is appended to an executable; packs exported by real editors (Godot 3.6.3, 4.4.1, 4.6.2, 4.7.2, one per format) must read with GDRE's file list and every md5 re-hashing (`proof/batteries/games_tests.py`, 16/16, null survives; the md5 arm had been void since the chunked-copy fix and was re-anchored 2026-10-24) |
| asar.py (desktop apps) | the first-word pickle framing must be checked (an otherwise-valid archive with a wrong first word refused), a missing `files` tree and a header length past the cap refused by their own messages, a flipped byte caught by the SHA-256 re-hash, an out-of-bounds offset and a too-deep tree handled without a crash, an `unpacked` and a `link` entry reported and skipped on extract, a `../` path refused by `_safe_rel` alone, and the gate refuse an unlawful purpose, a blank note and a non-empty folder (`proof/batteries/asar_tests.py`, 12/12, null survives) |
| trace.py (native runtime recorder) | the strace parser must pull a file open, a created file, a connect attempt with its family/address/port and its ok/error, an execve and an unlink out of captured strace output; a non-ELF input must be refused by the ELF guard on its own; and a live run (gated) must record a probe's open of /etc/hostname, its connect attempt to a fixed address:port (failed, no network), and the file it creates (`proof/batteries/trace_tests.py`, 6/6, null survives) |
| har_import.py (logged-in pages) | a credential-looking URL query value must become REDACTED and no cookie/header/body secret may reach the output; a base64 body must be decoded and a bad one skipped; the gate must refuse an unlawful purpose, a blank note and a non-empty folder; a hostile URL path must stay inside the output folder; a per-body and total size cap must hold; and a non-list `entries` must be refused (`proof/batteries/har_tests.py`, 8/8, null survives) |
| csharp.py (.NET/Unity) | the decompile must recover a known method, type and constant from a managed assembly built here (to stdout, as one `--type`, and as a written project); the `--type` argument must be refused unless it is a plain type name (the pattern pinned as a unit, since argv already blocks option injection; a name with a trailing newline included, which the pattern's `$` let through until 2026-10-24); the project-write gate must refuse an unlawful purpose, a blank note and a non-empty folder; and an IL2CPP `global-metadata.dat` must be recognised by magic and version while a non-IL2CPP file is refused (`proof/batteries/csharp_tests.py`, 7/7, null survives) |
| gdre.py (Godot scripts and scenes) | each real compiled script (packs exported by Godot 3.6.3, 4.4.1, 4.6.2 and 4.7.2) must decompile to its committed source token for token apart from the one measured loss, with the bytecode table its engine maps to (a member decompiled with one fixed table instead of its pack's must fail); GDRE's listing must equal godot_pck's; a failed decompile that exits 0 must be an error (a missing output is not success), a crash must be reported with its signal, an output left as a link must never be read, a text conversion must produce exactly one file; a member must be found with or without `res://` and refused when missing, encrypted or listed twice; the engine guard must refuse anything but 1.x to 4.x, a trailing newline included; the recover gate must refuse an unlawful purpose, a blank note, a full folder and a failed recovery's exit code, and `--scripts-only` must leave the scenes out; the listing parser must check GDRE's own total and skip non-path lines; the report parser must read hyphenated totals; a pack must be mounted alone, not its folder; and the proof's own comparison must keep values (hex canonicalised, a `&`/`^` prefix kept, scene ids dropped, scene values compared) (`proof/batteries/gdre_tests.py`, 23/23, null survives). The behaviour half lives in `proof/gdre/prove.py`, whose planted controls fire on every run |
| oracle.py / oracle_runner.py (native oracle) | a faithful rebuild must match and a wrong constant, a wrong shift (seen only in an output buffer) and an edge-only difference (`INT_MAX`) must each be caught with the first diverging input; the match must compare output buffers, not only the return; the argument registers (sysv and win64) and the signed-return masking must be pinned by an absolute known-answer anchor (a differential check hides a symmetric bug); the original's bytes must actually be mapped (a run on zeros must not read as a match); an external callee, an endless loop and a missing function must each be reported, not guessed or hung (`proof/batteries/oracle_tests.py`, 10/10, null survives) |

Last runs (2026-10-12): the watch tests caught 14 of 14 planted faults in `watch_page.py`
(`proof/batteries/watch_tests.py`, after the audit fixes), each through the test written for
it, and the rebuild checker caught 7 of 7 (its wheel arm caught again after the audit's
pointer change); in both, a comment-only change survived as it must. One rebuild arm
first survived for the wrong reason: it removed the reduced-motion rule from the loader but
not from the player, so nothing observable changed. Re-planted as the real fault (both rules)
it was caught. A survivor is a claim about the checks only once the fault is shown to move
the measurement.

The Tier 2/3 tests had no battery until the whole-skill audit (2026-10-12):
`proof/batteries/native_tests.py` plants 36 faults in bin_triage, carve, r2, ghidra,
GhidraDecompile.java, sandbox.py and unminify.py. Its first run caught 28 by their named test;
all 8 misses were the tests' fault, and each taught something. Three tests could never fail
(a section-count check on a file with room for one header, a bounds check four bytes past the
end, a DLL check where the only real sample is a DLL). Two passed for the wrong reason or had
no test (Ghidra's filter test passed with `;` allowed because Ghidra then found no function
`a;b` and raised the same error; a missing function had no test). Two pipe tests hung the run
instead of failing (they now run under a deadline), and the per-file write limit had no test.
After the fixes: the 8 arms caught too, so all 36 are caught by their named test, and
the comment-only control survived both runs. Verdicts count a catch only when the named test fails on an
assertion; an exception, a hang (TIMEOUT) or another test failing are reported apart.

The Tier 1 static tests had no battery until the second whole-skill audit (2026-10-12,
evening): `proof/batteries/static_tests.py` plants 40 faults in validate_dossier, libscan,
fingerprint, webfetch, unminify, wasm_inspect and dossier.py. Its first run caught 35 of 39.
Two misses were real gaps: the validator's own rule that static evidence cannot confirm a
behaviour was never tested alone (the schema's enum refuses the same input first, so removing
the rule changed nothing), and no test put a link inside a source map's output folder, so the
resolved-path check could go. One test died on an AttributeError instead of failing, and one
arm changed nothing (the next function header resets the flag the arm meant to break); it was
re-planted as the real fault. On the final code all 40 are caught. The native battery grew to
43 faults with the second audit's fixes and catches all 43 on the final code (its radare2 arm
first failed by an escaping error, not an assertion, until the test caught the error). The
comment-only control survives in both.

If you change an instrument, rerun its battery. A green result you have not seen go red is
not yet evidence.

Independent corroboration counts as a second, different kind of proof: libscan's markers
were proven on builds made here, then checked on 16 public sites and on the production site's
Turbopack build, a minifier the proof never used. wasm_inspect's body offsets were checked
against wasm-objdump's on real modules. For native code the same principle is the primary check:
`bin_triage` (pure Python) is read against readelf, file and objdump, and a native
ground_truth claim is corroborated by three tools agreeing byte for byte on one function,
objdump on the host and radare2 and Ghidra in the sandbox
(`tests/test_reverse_engineering_native.py`, 45 tests, all passing 2026-10-12). The native
`--evidence` byte re-read does not apply (an instruction's literal is hex, and an address is
not a file offset), so re-running the tool and cross-tool agreement are what re-check it.

## The locator discipline

A finding does not exist until you can point to it in the current artifact. Never cite an
address, offset, line or function name from memory or an earlier run. Re-read the place,
confirm it, quote what you just read, then let `--evidence` re-read it again. Offsets live
only in ground_truth locators, where `--evidence` checks them; a hypothesis or statement
points to the claim id instead of repeating the number (a repeated number in prose is a
second copy nothing checks, and such a copy has gone in wrong in practice). Minified
chunks change on every deploy: record the file's sha256 in the target or locator.

## Four ways a first reading goes wrong

Each of these is a mistake a confident first pass makes, and a rule the spine now
enforces. They came out of a real Tier 1 teardown whose dossier is not shipped here;
the lessons are the part worth keeping.

1. **A name search that finds nothing.** Grepping a minified bundle for a library's
   package name (`some-library`) finds zero, because minifiers strip names, and the
   reading says "the library is absent." libscan's proven markers can still find it.
   Lesson: a zero from a name search is not an absence; only proven markers make
   "absent" mean anything, and even then only for the files scanned.
2. **A driven capture with no control.** "Scrolling causes N prefetches" was recorded
   without an idle capture; with no scrolling at all the same N requests fire on load.
   Lesson: every driven capture needs an idle control taken the same way.
3. **An unchecked premise.** "How scroll drives the animation" took the question's
   premise for granted; watch_page showed the document was one screen tall and nothing
   scrolled at all, the effect was a timed sequence on a runtime timer. Lesson: watch
   what actually moves before reading the code for why, and read the numbers the run
   showed rather than the ones the question assumed.
4. **An input test that never reached the target.** "The wheel changes nothing" was
   confirmed on a wheel event fired where Playwright's pointer starts, the page's
   top-left corner, which landed on a header outside the element under test. Lesson: an
   input check must show what received the input; watch_page now rests the pointer at
   the centre of the screen and every comparison lists `received_by`.

The discipline that catches all four is the same: a differential rebuild measures the
original and a reconstruction of it with one instrument and compares them check for check,
so a reading is only trusted when an independent build of it behaves the same.
