# Tier 2 and 3: native binaries and carving

The playbook for compiled programs (Tier 2) and blobs (Tier 3), with the gotchas. Tier 1
(web) is in [`web-techniques.md`](web-techniques.md); the spine that governs all of it is in
[`verification.md`](verification.md).

## The order of work

1. **Triage first, with the safe reader.** `bin_triage.py` is pure Python, bounded, and
   touches no native parser, so it is safe on a file you do not trust and needs no container.
   It tells you the format, architecture, bitness, endianness, entry point, the dynamic loader
   the program asks for, the libraries it needs, its sections, a build id, and its strings.
   Read this before you reach for a heavier tool: it is also an independent second reading of
   what radare2 will tell you, which the spine counts as corroboration.

2. **Disassemble with radare2, in the sandbox.** `r2.py --info` repeats the identity from the
   other side (radare2's own parser), `--functions` runs analysis and lists what it found,
   `--disasm <name|0xaddr>` gives one function as `addr + bytes + opcode` lines. The bytes at
   an address are ground truth; the function boundary radare2 drew and the name it guessed are
   analysis, a hypothesis. `--strings`, `--imports` and `--xrefs` fill in the rest. radare2
   prints its JSON on stdout, and the sandbox used to keep only a 200 KB tail of stdout: every
   list from a mid-sized binary failed to parse (python3.12, 8 MB, gave 1.1 MB of functions and
   8.8 MB of strings). `r2.py` now reads up to 64 MB of it and says so past that.

3. **Decompile with Ghidra, in the sandbox, when you want C.** `ghidra.py --list` enumerates
   functions, `--function <name|0xaddr>` imports the binary into a throwaway project,
   auto-analyses it, and writes the decompiled C of one function followed by its instruction
   bytes. The C is a reading aid: Ghidra renames locals, guesses types and signatures, and can
   be wrong. The instruction lines under it are ground truth. Cite the bytes, quote the C as a
   hypothesis. The result is read back up to 4 MB; a longer one (the `--list` of a big binary)
   ends with a line saying where it was cut.

4. **Write the dossier** with `tier: "native"`, the same three claim kinds, validate and
   render it exactly as for web.

## How a native ground_truth claim is cited and re-checked

For web, `validate_dossier.py --evidence` re-reads a `<file> byte <N>` locator out of the
downloaded text and matches the literal. That mechanism is for text; an instruction's "literal"
is hex, not the raw bytes in the file, and the file offset of an address depends on the
section mapping. So a native ground_truth claim cites the **instruction address and its bytes**
(`locator`: the address; `literal`: the hex bytes and the mnemonic), and it is re-checked by
**re-running the tool** and by **cross-tool agreement**, not by the `--evidence` byte re-read.
The strongest form: objdump (host), radare2 and Ghidra must report the same bytes for the same
function. They were shown to, byte for byte, on a compiled test function
(`tests/test_reverse_engineering_native.py::CrossToolGroundTruth`). When they disagree, that
disagreement is itself the finding.

## Gotchas, each one paid for

- **radare2 JSON keys.** `aflj` keys a function's address as `addr`, not `offset` (printing
  `offset` shows `0x0` for every function). `pdfj` ops carry `addr`, `bytes`, `opcode`, `size`.
- **Command injection.** radare2's `-c` and Ghidra's postScript take the function selector you
  pass. It is restricted to a bare name or an `0x`-address before it is used, so a `;` or a
  backtick cannot smuggle a radare2 or shell command. Nor can an `@`, for radare2: it reads
  `@@` as a foreach iterator, and `@@c:CMD` runs CMD, so `entry0@@c:ij`, made only of
  characters a name may hold, ran `ij` until `@` left the set. A name with unusual characters
  (C++ templates, say) must be given as its address instead. The test checks the refusal's
  message: radare2 printing no JSON raises the same error, so a bare "it raised" passed even
  while the injected command ran.
- **Ghidra 12 dropped Jython.** A `.py` headless postScript now needs PyGhidra (CPython)
  enabled, and fails with "Python is not available" otherwise. The postScript here is a Java
  `GhidraScript` (`GhidraDecompile.java`), which headless Ghidra compiles and runs with the
  bundled JDK, no extra setup.
- **A JVM in `--network none`.** Ghidra's `getLocalHost()` must resolve the container's
  hostname, and cannot with no DNS and a random hostname: it dies with `UnknownHostException`
  before any analysis. The sandbox gives Ghidra `--hostname localhost`, which resolves from
  `/etc/hosts` offline and does not touch the network isolation.
- **binwalk and getpwuid.** binwalk's extractor looks the running user up with `getpwuid`, and
  the container has no entry for the host uid, so extraction crashes with a `KeyError` before
  pulling anything out. The sandbox mounts a minimal `/etc/passwd`/`/etc/group` for the uid
  (`with_passwd=True`). binwalk also only *identifies* by itself; it shells out to unzip, 7z,
  xz and friends to extract, so the `re-carve` image carries the free extractors.
- **Mach-O endianness.** The non-swapped magic (`FE ED FA CE/CF` on disk) is a big-endian file;
  the byte-swapped magic (`CE/CF FA ED FE`) is little-endian, which is every Intel and
  Apple-silicon binary. Reading this backwards mis-reads the common case (the architecture
  comes out wrong). `bin_triage` was fixed and a synthetic test pins it.
- **Fat/universal Mach-O.** A Mac binary often holds two programs in one file, one per arch.
  `bin_triage` lists each slice's arch, offset and size (both the 32-bit `fat_arch` and the
  64-bit `fat_arch_64` layout, told apart by the `...BE` vs `...BF` magic), and `--slice`
  carves one thin slice out; `r2.py` and `ghidra.py` take `--slice <arch|index>` to open one
  arch. The fat header is big-endian by convention even for a little-endian slice inside. The
  slice selection is proven on synthetic universal binaries; radare2/Ghidra on a slice of a
  real Mac binary stays unproven until one is on hand (nothing here builds Mach-O).
- **Short magics carve badly.** A two-byte `MZ` or the low-entropy TrueType `00 01 00 00 00`
  matches ordinary data at arbitrary offsets, and so do three-byte magics: JPEG's `FF D8 FF`
  and gzip's `1F 8B 08` each turn up about once per 16 MB of random or compressed data (8
  false files in 64 MB). `carve.py` checks what follows each short magic (a PE header, a JPEG
  segment landing on the next marker, valid gzip flags, bzip2's block magic) and pins TrueType
  to offset 0, the same lesson Tier 1 learned from a library magic that fired on random base64.
  `BZh91` alone also missed every bzip2 file not packed at block size 9.
- **A container's output is hostile on the way out.** The tools run as our uid with `/out`
  writable, so a compromised radare2, Ghidra, wabt or binwalk could leave a link to any host
  file, or a pipe, where its result belongs. `ghidra.py` once read `/out/result.txt` with a
  plain `open` and printed a planted link's target as decompiled C; `wasm_inspect` moved the
  link into place as the `--wat-out` file. Read results only through `sandbox.open_output`
  (a regular file inside the folder, reached through no link, opened without blocking) or
  `copy_output_tree`, and never move a container's file: a moved link is still a link.
- **Container memory is not the bot's cgroup.** A `docker run` container is accounted to Docker,
  not to `example-app.service`, so a heavy Ghidra analysis cannot OOM the bot the way a
  direct subprocess can. It is still capped by `--memory`, and the JVM heap by
  `GHIDRA_HEADLESS_MAXMEM`, and every run has a time limit, kept twice: by the caller, and
  inside the container (`timeout`, 5 s later), because a caller killed mid-run (a turn cut
  short) once left a container running past its limit.

## The native rebuild oracle

Reading a function off its disassembly or decompiled C is a hypothesis about what it does.
`oracle.py` turns that into a confirmed behavioural claim the same way the web tier's
rebuild check does: it runs the **original function's real bytes** in the Unicorn CPU
emulator over a corpus and compares the return value and any output buffers, input by input,
against a rebuild run identically. A match over the whole corpus confirms the behaviour; the
first diverging input is the finding. Everything runs in the offline `re-oracle` sandbox, and the
original's bytes are interpreted, never executed on the machine.

How it works and why it is safe to point at an untrusted binary:

- Both subjects are **emulated**, never run natively, so even a hostile original is only ever
  interpreted. The sandbox (`re-oracle`: Unicorn, capstone, pefile, plus gcc and MinGW) is the
  second layer; the emulator is the first.
- The function's entry is resolved by symbol (ELF, via `readelf`) or export (PE, via `pefile`);
  its sections are mapped into the emulator and it is called with a fabricated return address, so
  emulation stops cleanly at the function's `ret` without needing the function's size.
- The corpus is explicit seeds + per-argument boundary values (0, 1, -1, the type's min and max)
  + seeded fuzz, so a difference that only shows at an edge (a special-cased `INT_MAX`) is found,
  not just random hits.

Gotchas, each one paid for building it:

- **Map every page once, then write all section bytes.** Mapping per section and skipping any
  that overlaps an already-mapped page drops a section that shares a page with an earlier one
  (`.text` behind `.init`), so the emulator runs the wrong bytes and faults on a write to
  address 0. Union the pages, OR their permissions, map once, then write.
- **A differential check hides a symmetric harness bug.** Swapping the argument registers, or
  not masking the return to its width, changes both subjects equally and the match still passes.
  The guard is an **absolute anchor**: the oracle reports the original's own output for a few
  known inputs, and a test asserts the real arithmetic (`mix(5, 9) == 91`, and a negative case
  to pin sign-extension). Without it the ABI and masking were untested.
- **An external call is reported, not guessed.** A call into an imported libc symbol lands in
  the PLT and then jumps through the GOT to code that is not in the binary; Unicorn faults with
  a fetch error, which the oracle turns into "jumped to an external callee". v1 is for leaf
  functions or ones whose callees are in the binary; a non-leaf is out of scope, not a wrong
  answer.
- **Win64 vs System V.** The argument registers differ (`rcx, rdx, r8, r9` vs
  `rdi, rsi, rdx, rcx, ...`) and Win64 wants 32 bytes of shadow space above the return address.
  Both are set from `--abi`; the PE path resolves the function from the export table.
- **Mach-O is unproven.** Nothing here builds a Mac binary, so the oracle has never been run on
  one; say so in the dossier rather than imply coverage.

## Tier 3: the carving gate

`carve.py` scans for free (the pure-Python scan is safe on anything; `--binwalk` is richer).
`--extract` writes real files out of the blob, so it is gated: it refuses without a lawful
`--purpose` (read from the dossier schema, so there is one source for the allowed set) and a
one-sentence `--note`. Extraction never serves pulling copyrighted assets out of a pack to ship
or redistribute, defeating copy protection, or bypassing anti-cheat; those are refused here the
same way the dossier refuses them. The gate makes the basis explicit; it cannot judge honesty.

Extraction also has a size gate. A 1 MB blob once wrote 1 GB to the disk in 9 s: nothing bounded
what binwalk's extractors write. Now no single file may pass the cap (`--ulimit fsize`), the
output folder is polled ten times a second and the run is stopped past `--max-mb` (default
2048; a 1.6 GB bomb stopped at 257 MB of a 256 MB cap), the folder must be new or empty (it is
mounted writable), and a link left pointing outside it is removed. A pipe the extraction makes
(a tar can carry them) is left out of the list of extracted files and named on stderr: reading
one blocks for ever. A carving dossier uses `tier: "carving"`.
