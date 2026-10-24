# Reverse Engineering Skill

Understand how a thing works: a website (stack, network, what moves and why, libraries
inside minified code, code recovery, WebAssembly), a desktop app or a page behind a login,
a compiled binary (format, disassembly, decompilation, and proving a rebuild matches it),
a game pack (Godot, Unity), or a blob (what files are hidden inside it).

Built on stable primary tools in our own house style, with a legal gate and a verification
spine that keep it honest. It is not a piracy tool (see the gate below).

## What it is

Three tiers, one discipline:

- **Tier 1, web (built, proven).** Fingerprint a stack, capture its network, watch what
  moves and why, name the libraries inside minified code, recover or decompile the code,
  read WebAssembly down to single functions, and rebuild a mechanism to check the reading.
  Plus a reader for an Electron desktop app (`.asar`) and a saved logged-in session (`.har`).
- **Tier 2, native (built, proven).** A pure-Python reader for ELF/PE/Mach-O (with
  fat/universal slice selection) that is safe on any file, then radare2 and Ghidra headless
  over compiled programs and plugins, each in the offline sandbox, a native oracle that
  proves a rebuilt function matches the original by emulation, and a strace recorder of what a
  program does at runtime. See the Tier 2 section.
- **Tier 3, carving and games (built, proven).** Find (and under the gate, extract) the files
  hidden inside a blob: a pure-Python signature scan plus binwalk in the sandbox. Plus engine
  readers: a Godot pack reader, a Godot script and scene decompiler (GDRE Tools) and a Unity/.NET
  C# decompiler. See the Tier 3 and Games sections.

The `security-audit` skill is the sibling for auditing source code you own or may review.
This skill is for understanding mechanism.

## Legal and ethics gate (MANDATORY)

State the lawful basis before you start. The dossier records it and the validator enforces
the allowed set. Full detail with statute citations is in
[`references/legal.md`](references/legal.md); the short form:

**Serves:** interoperability (EU Software Directive 2009/24/EC Article 6), security
research, understanding an undocumented format, your own or a contracted target, and
education (studying a public reference to inform your own build, observation only).

**Refuses, every time:** circumventing DRM or copy protection, defeating licensing or
activation or online entitlement, extracting copyrighted assets to redistribute, bypassing
anti-cheat for advantage, and anything whose honest one sentence basis is "to avoid paying
for it." If a request lands here, say so plainly and stop.

Every dossier carries:

```json
"lawful_basis": { "purpose": "interoperability", "note": "<one concrete sentence>" }
```

A rebuild made to check a reading (Tier 1, step 8) is our own code and our own media. Never
copy the target's code or assets into it.

## Verification spine (the core)

A reverse engineering tool that guesses confidently is worse than none. Full method in
[`references/verification.md`](references/verification.md); the rule:

Every statement in a dossier is a claim tagged by `kind`.

- **ground_truth:** what a tool literally showed. Cites `tool`, `locator` and the exact
  `literal`. States what is there, never what it means. For code, cite the file and the byte
  offset; for a disassembler, the address and the bytes.
- **hypothesis:** what the ground truth is believed to mean. Names its basis and the claims
  it rests on. Written so it could be proven wrong.
- **behavioural:** what the target does at runtime. Static reading can suggest a behaviour
  but never confirm it. `confirmed` needs a runtime, dynamic or sandbox observation; without
  one the claim stays `unconfirmed`, a labelled guess.

The contract is executable, and every instrument in this skill has been shown to fail on
planted faults before being trusted (batteries in `tests/test_reverse_engineering*.py`):

```bash
RE=skills/reverse-engineering/scripts
python $RE/validate_dossier.py dossiers/<target>.json                      # schema + semantic rules
python $RE/validate_dossier.py dossiers/<target>.json --evidence <folder>  # + re-read every locator
python $RE/dossier.py          dossiers/<target>.json --out dossiers/<target>.md
```

`--evidence` re-reads each `<file> byte <N>` and `<file> @ char <N>` locator in the folder of
downloaded files and fails when the literal is not exactly there, when the cited file is not
in the folder, or when a locator names a file there in any other form. A libscan claim lists
its markers joined by ` | `: the first is re-read at its offset and every other one must be in
some file of the folder. A citation from memory cannot pass it. Keep each target's downloaded files in `~/.cache/re-evidence/<target>/` with
a MANIFEST.json of URLs and sha256 (outside the repo: they are the target's code, kept only
so the dossier can be re-checked).

## Where untrusted code runs

Static tools that read bytes in bounded, memory-safe Python are safe on anything, trusted or
not: `fingerprint`, `libscan`, `wasm_inspect`'s own parser, `bin_triage` (ELF/PE/Mach-O), and
`carve`'s signature scan. Bounded means every list they build is capped, not just the bytes
they read: a .wasm entry of three or four bytes is a Python object of hundreds, and 4 MB of empty
imports once took 574 MB (about 55 MB of module would have taken 8 GB). wasm_inspect caps
imports, exports, types and data segments at 100,000 and functions at 1,000,000, a measured
worst case, every list at its cap, of 732 MB. They read regular files only: a pipe named `x.js`
in a folder once blocked libscan for ever, and a link to `/dev/zero` read until memory ran out.
`unminify` without `--deobfuscate` is close: it reads code and never runs it, but through
wakaru (a Rust decompiler, under a time cap and, on Linux, a memory cap) or js-beautify (Node,
under a time cap) on the host, not in the sandbox. Native parsers of untrusted files run in the sandbox:
`wasm_inspect`'s `--disasm`/`--decompile` (wabt), all of `r2.py` (radare2), `ghidra.py`
(Ghidra) and `carve.py`'s `--binwalk`/`--extract` (binwalk), each unless `--host-tools` is
given for a target you trust (radare2 and Ghidra have no host copy here, so the sandbox is the
normal path). `csharp.py` (ilspycmd) and `gdre.py` (GDRE Tools) have no host mode at all. The native oracle (`oracle.py`) is the same posture taken further: it loads an
untrusted binary's real machine code but only ever *emulates* it in Unicorn, inside the
`re-oracle` sandbox, so the target's instructions never run on the machine at all; the parsers
it leans on (pefile, readelf) and the `--rebuild-src` compile run there too.

The one exception: tools that run a page (capture_net, watch_page) run its JavaScript in
headless Chromium on the host, inside Chromium's own sandbox: each renderer gets its own
user and network namespace and a seccomp filter. Playwright turns that sandbox off by default
and recent Ubuntu's AppArmor blocks the unprivileged user namespaces the sandbox needs, so the
tools ask for it (`chromium_sandbox=True`) and need an AppArmor profile that grants Playwright's
Chromium builds those namespaces. On Ubuntu 23.10+ install one at
`/etc/apparmor.d/playwright-chromium`, same shape as Ubuntu's own `chrome` profile
(`flags=(unconfined)` with a `userns,` rule, attached to the
`~/.cache/ms-playwright/chromium*/chrome-linux*/chrome` and `chrome-headless-shell` paths),
then `sudo apparmor_parser -r /etc/apparmor.d/playwright-chromium`. Without that profile the two
page tools die at launch (SIGTRAP) rather than run unsandboxed, so they fail safe; the rest of
the skill does not need it. A memory ulimit (`ulimit -v`) kills Chromium the same way. A test
reads the running processes, not the launch call, to prove the sandbox is on. On macOS the
sandbox is the system's own (Seatbelt) and needs no profile: the same test reads it there from
the kernel (`sandbox_check` on each renderer), and fails with the sandbox switched off. The
sandbox is one layer, not a container: an exploit would need
a renderer bug and a sandbox escape to reach the bot's user. So these tools are for pages you
may analyse (a public site, your own build, a client's), never a page you have reason to think
is hostile. Anything else
that executes code we do not trust goes through `sandbox.py`: a Docker container with no
network, a read-only system, no capabilities, a non-root user, memory, process and time
limits, and only the mounts given. `python $RE/sandbox.py check` proves the isolation by
trying to break it (8 probes; each was shown to report FAIL in a deliberately leaky
container).

What a container writes is untrusted on the way out. The tool inside runs as our uid with its
output folder writable, so a compromised one can leave a link to any host file (a
credentials file, say) or a pipe where its result belongs. Results are read back only through
`sandbox.open_output` and `copy_output_tree`: regular files inside the folder, reached through
no link, opened without blocking. Never `open`, move or copy a container's output any other way
(before this rule, `ghidra.py` printed a planted link's target as "decompiled C"). Every run
that writes to the host is capped on what it may write: wabt's text and webcrack 2 GB, Ghidra's
throwaway project 8 GB, `carve --extract` its `--max-mb`.

Downloads do not read a redirect's body. requests reads and unpacks it whole, even with
redirects off, so 597 KB of gzip in a 302 cost 618 MB; `webfetch.py` follows redirects by hand
at the transport level, and every web tool fetches through it. Each fetch also ends at a 180 s
deadline: the timeout is per read, so a server sending one byte inside each timeout never let a
download end (5 MB at five bytes a second is eleven days). The deadline is a SIGALRM timer, so
it holds in a tool's main thread, which is where every web tool calls it. A page's own tags are
scanned in one linear pass, so a hostile page of unclosed `<script` tags cannot stall libscan
or fingerprint (it once took hours).

Do not use `systemd-run --user -p PrivateNetwork=yes` as isolation: on some hosts it is
silently ignored and the network stays reachable. Downloaded artifacts are untrusted: put
each set in its own empty folder, pass paths as arguments, run Python readers with `-I`.

## Running heavy tools safely

No persistent daemon. Ghidra runs headless one-shot, radare2 one command at a time. The heavy
native tools run as Docker containers, outside the bot's own processes, so an out-of-memory
kill inside one cannot take the bot down the way a direct subprocess can; each container is
still memory capped (`--memory`), Ghidra's JVM heap is capped on top (`GHIDRA_HEADLESS_MAXMEM`),
and every run has a time limit, kept twice:
by the caller and, 5 s later, inside the container. A caller killed mid-run (a turn cut short)
once left its container running past the limit. On Linux, pure-Python batteries you run
directly still belong in `systemd-run --user --scope -p MemoryMax=4G ...`; macOS has no
systemd-run, so run them there as plain `python`.
Single process only: no subagents.

## Tier 1: web

Full playbook with the gotchas in [`references/web-techniques.md`](references/web-techniques.md).

```bash
# 1. fingerprint (static fetch, no JS runs)
python $RE/fingerprint.py --url https://site.com
# 2. capture the network (runs the page)
python $RE/capture_net.py --url https://site.com --out net.json           # idle load
python $RE/capture_net.py --url https://site.com --scroll --out net2.json # with scrolling
# 3. watch what moves, and whether time, scroll, wheel, a key or a click moves it
python $RE/watch_page.py --url https://site.com --seconds 25 --control \
    --action "4:wheel:800" --action "8:key:ArrowRight" --action "13:click:button.next" --out run.json
# 4. name the libraries inside the shipped JS (minify-proof, static)
python $RE/libscan.py --capture net.json --all
# 5. read the code: source map, else decompile (wakaru); hidden string tables: --deobfuscate
python $RE/unminify.py --file chunk.js --out-file chunk.readable.js
python $RE/unminify.py --file bundle.js --unpack --out-dir modules/      # + provenance.json
python $RE/unminify.py --file obf.js --deobfuscate --out-file obf.clean.js   # webcrack, in the sandbox
# 6. WebAssembly: functions, strings, toolchain; one function's disassembly or decompiled text
python $RE/wasm_inspect.py module.wasm
python $RE/wasm_inspect.py module.wasm --disasm 202
# 7. write the dossier, validate it, re-read its locators, render it
python $RE/validate_dossier.py dossiers/site.json --evidence downloads/
# 8. rebuild the mechanism (our own code) and measure original and rebuild with the same instrument
```

Step 3 is what separates a mechanism from a guess. `watch_page.py` logs, on the page's own
clock, every class and style change, every video play and pause, every CSS transition with
its real duration, every timer the page sets (and whether it fired or was cancelled), and the
inputs it received; `--control` runs the page idle as well and says, for each input, whether
anything changed that the idle run did not change. The pointer rests at the centre of the
viewport in every run, so a wheel lands in the middle of the screen, and each comparison
names the element that received the input: on the production site the first wheel test,
fired at the page's top-left corner, landed on the site header and never reached the reel. An action that
failed (a selector never found) gets no verdict rather than "no change".

Step 4's markers are proven, not guessed: `proof/libscan/prove.py` builds every covered
library with esbuild and webpack and takes its published file, old and new major versions,
and requires each library to be found in exactly the builds that contain it (last run:
105/105, `proof/libscan/result.json`). Covered: React, Vue 3, Motion (framer-motion), GSAP,
ScrollTrigger, Lenis, Locomotive Scroll 4 and 5, Three.js, PixiJS 7 and 8, Swiper, Embla 8
and 9, anime.js 3 and 4, Lottie, hls.js, Video.js 7, 8 and 10, Plyr, Barba, jQuery 3 and 4.
Add a library only by adding its entry to the proof and passing it.

### Desktop apps (Electron)

Many desktop apps (Notion, Discord, Figma, Obsidian and the like) are a web app packed into one
`.asar` archive, which the web tools above cannot open directly. `asar.py` reads that archive and,
under the gate, unpacks it so `fingerprint`, `libscan` and `unminify` can run on the app's real
code.

```bash
python $RE/asar.py app.asar                  # the file tree, offsets and sizes
python $RE/asar.py app.asar --verify          # re-hash each file against its SHA-256
python $RE/asar.py app.asar --extract OUT --purpose interoperability --note "..."
```

Pure Python, bounded and safe on a file you do not trust, like `godot_pck`. It reports a file
marked `unpacked` (its data lives outside the archive, in `app.asar.unpacked`) and a `link`
(symlink) entry rather than reading them, re-hashes each file against its SHA-256 integrity hash
on `--verify`, and extracts under the carving gate (lawful `--purpose`, a `--note`, a size cap,
no write outside the folder, a `../` path refused). Format confirmed against `@electron/asar`
output; the proof is a real archive packed here plus hand-built unpacked/symlink/traversal cases.

### Logged-in pages (a saved HAR)

`capture_net.py` drives a fresh browser with no session, so it cannot reach a page behind a login.
For that, save a HAR in the browser's network panel while logged in and read it with `har_import.py`:
it already holds the requests and the response bodies.

```bash
python $RE/har_import.py session.har                 # what was fetched (redacted)
python $RE/har_import.py session.har --out net.json   # a capture_net-shaped JSON
python $RE/har_import.py session.har --extract OUT --purpose own_work --note "..."
```

Secrets are dropped by construction: only method, URL, resource type, status, content type and
size are read from each entry, never the request's headers, cookies or POST body, and a URL query
value whose name looks like a token or password becomes `REDACTED`. `--extract` writes the
response bodies out (under the gate), so `libscan.py` and `unminify.py` read the real captured
code locally instead of re-fetching it (which the logged-in page would refuse). Pure Python and
safe on a file you do not trust; a HAR over 1 GB is refused (it is read whole into memory).

## Tier 2: native

The RE core for compiled programs and plugins. Full playbook with the gotchas in
[`references/native-techniques.md`](references/native-techniques.md).

```bash
# 1. triage: pure-Python ELF/PE/Mach-O reader, safe on any file, no container
python $RE/bin_triage.py program              # format, arch, bits, entry, loader, needed libs
python $RE/bin_triage.py program --strings    # + readable strings with offsets
python $RE/bin_triage.py universal --slice arm64 --write-slice thin  # carve one slice of a fat/universal Mach-O
# 2. radare2 (in the sandbox): headers, functions, strings, imports, one function's disasm
python $RE/r2.py program --info               # arch, bits, endian, stripped, protections
python $RE/r2.py program --functions          # analyse, then list functions
python $RE/r2.py program --disasm main        # addr + bytes + opcode per instruction (ground truth)
python $RE/r2.py program --imports            # imported symbols
python $RE/r2.py universal --slice arm64 --info   # open one slice of a fat/universal Mach-O (ghidra.py too)
# 3. Ghidra headless (in the sandbox): decompile one function, or list them
python $RE/ghidra.py program --list           # every function Ghidra finds
python $RE/ghidra.py program --function main  # decompiled C (hypothesis) + instruction bytes (ground truth)
# 4. write the dossier with tier "native", validate it, render it
```

Each native statement keeps its kind: a `ground_truth` claim cites the instruction address
and the bytes (from `r2.py --disasm` or Ghidra's instruction lines); what a function does, read
off the disassembly or the decompiled C, is a `hypothesis`; what the program does at runtime
is a `behavioural` claim, confirmed only by running it in the sandbox. The decompiled C is a
reading aid, never the bytes: three tools (objdump on the host, radare2 and Ghidra in the
sandbox) were shown to agree byte for byte on the same function, which is how a native
ground_truth claim is corroborated, since the `--evidence` byte re-read is for text files.

Already present and usable on a binary you trust: the binutils baseline (objdump, readelf,
nm, strings, file, gdb, xxd). `bin_triage` is the safe first look and an independent second
reading of what radare2 reports (two differently derived sources).

## Proving a rebuild matches: the native oracle

The native counterpart of the web tier's rebuild check (step 8 of
`references/web-techniques.md`). Reading a function is a hypothesis about what it does; the
oracle turns that into a measured, confirmed behavioural claim by running the function and
checking the answer.

```bash
# a reimplementation you compiled, against the original function:
python $RE/oracle.py --original libthing.so --rebuild mine.so --function crc32 \
    --arg uint:4 --ret uint:4 --fuzz 5000
# or give the rebuild as C source and let the sandbox compile it (gcc, or MinGW for --abi win64):
python $RE/oracle.py --original game.dll --rebuild-src pan.c --function pan --abi win64 \
    --arg int:4 --arg outbuf:4 --arg outbuf:4 --ret void --fuzz 3000
```

It runs the **original function's real machine code** inside the Unicorn CPU emulator over a
corpus (explicit seeds, per-argument boundary values, and seeded fuzz) and compares its return
value and any output buffers, input by input, against the rebuild run the same way. A match over
the whole corpus is a confirmed behavioural claim; a difference reports the first diverging input.
The original's bytes are interpreted, never executed on the machine, and all of it (emulation, and
compiling a `--rebuild-src`) happens in the offline `re-oracle` sandbox.

This is REA's one serious trick (the DX-Ball sound-pan showcase) done inside our own spine, proven
on our own compiled code before it ever touches a real target: a faithful rebuild matches, a wrong
constant or a wrong shift is caught at its first boundary, for both the System V (ELF) and Win64
(PE) ABIs. Arguments are integers (passed in registers) and output buffers (a pointer the function
writes). Limits, say them in a dossier: **x86-64 only**; a **leaf function** or one whose callees
are inside the binary (a call to an external like a libc import is reported, not emulated, so hook
it out of scope or rebuild that piece too); integer and pointer-out arguments, not floats (xmm)
yet; and **Mach-O stays unproven** until a Mac binary can be built or supplied. A ground-truth
disassembly of the original function (address + bytes + mnemonic, from capstone) accompanies the
verdict.

## Recording a native run

The spine says a native behavioural claim is confirmed only by observing a run; `trace.py` is that
observation, the native counterpart of `watch_page.py`. It runs a Linux program under strace in
the offline `re-trace` sandbox and records what it did: the files it opened, the network it tried
to reach, the files it created, the programs it started.

```bash
python $RE/trace.py ./program                      # record a run
python $RE/trace.py ./program --arg --flag --stdin-file input.bin --json run.json
```

Unlike the oracle, which *emulates* the original bytes, this **executes** the program, so it runs
only in the sandbox (no network, read-only, non-root, capped) and is for a Linux x86-64 target you
are authorized to run. Because there is no network, a `connect()` shows up as an *attempt* that
fails with `ENETUNREACH` -- which is exactly the evidence of where the program wanted to phone
home (proven here: a probe's connect to `93.184.216.34:443` and its creation of `/tmp/licence.key`
are both recorded). Two honest limits, say them in a dossier: it is Linux ELF only, and the trace
is evidence about a program that is not forging its own trace (the target shares the output mount,
as with `watch_page`'s in-page recorder).

## Tier 3: carving

Find what is hidden inside a blob (firmware, a game asset pack, a container file) and, under
the gate, pull the pieces out.

```bash
python $RE/carve.py blob.bin                   # pure-Python signature scan (safe on anything)
python $RE/carve.py blob.bin --binwalk         # binwalk's signature scan, in the sandbox
python $RE/carve.py blob.bin --binwalk --entropy          # + an entropy profile
python $RE/carve.py blob.bin --extract OUT --purpose format_understanding --note "..."
python $RE/carve.py blob.bin --extract OUT ... --max-mb 512   # stop once 512 MB are written
```

Extraction is the gate: it writes real files out, so it refuses unless you name a lawful
`--purpose` (the dossier's set, read from the schema) and a one-sentence `--note`. It never
serves pulling copyrighted assets out to ship or redistribute, defeating copy protection, or
bypassing anti-cheat; those are refused here the same way the dossier refuses them. The gate
makes you state the basis out loud; it cannot judge your honesty.

`OUT` must be a new or empty folder: it is mounted writable into the sandbox. Extraction stops
once it has written `--max-mb` (default 2048), so an archive bomb cannot fill the disk (a 1 MB
blob once wrote 1 GB in 9 s), and any link it leaves that points outside `OUT` is removed.

## Games: engine asset packs

A game ships its content in an engine-specific pack, and the generic carve scan only sees it as
one opaque blob. These readers understand the pack format itself: the file table, the engine
version, and (under the same gate as carving) the stored files.

```bash
# Godot: a .pck, or a pack appended to an exported game's executable
python $RE/godot_pck.py game.pck                       # format, engine version, every file + md5
python $RE/godot_pck.py game.x86_64 --verify-md5        # a pack embedded in the game binary
python $RE/godot_pck.py game.pck --extract OUT --purpose own_work --note "..."
```

`godot_pck.py` is pure Python, bounded and memory-safe like `bin_triage`, so it is safe on a
file you do not trust; it is ground truth about the directory and nothing more. It reads every
pack format there is: 1 (Godot 3), 2 (Godot 4.0 to 4.4), 3 (4.5 and 4.6) and 4 (4.7). It finds a
pack whether it stands alone or is appended to the game executable (the end-of-file trailer),
re-hashes each file against the directory's md5 on `--verify-md5`, and reports an encrypted
directory or file rather than guessing at it. Extraction is the carving gate: a lawful
`--purpose` and a one-sentence `--note`, a size cap, no write outside the output folder, and a
hostile `res://../../` path refused. It pulls the stored bytes out; what a compiled script or a
binary scene says is `gdre.py`'s job (next section).

Each format was confirmed field for field against the source of the release that writes it
(`core/io/file_access_pack.*`: Godot 3.6 for format 1, 4.0 to 4.4 for 2, 4.5 and 4.6 for 3, 4.7
for 4) and is proven on a pack exported here by that editor (Godot 3.6.3, 4.4.1, 4.6.2 and
4.7.2): the header read, every md5 re-hashed, and the file list equal to GDRE's own reading of the
same pack. Plus a `PCKPacker` pack, a hand-built format-2 sample and packs embedded in a mock
executable (`tests/test_reverse_engineering_games.py`). Format 1 stores absolute offsets, so a
Godot 3 pack appended to an executable is read with the executable's length already in them.
(Until 2026-10-24 this reader said "Godot 3 through 4.7" and refused format 1, which every real
Godot 3 pack is: no real Godot 3 pack had been tried.)

### Godot scripts and scenes (GDRE Tools)

An exported Godot game ships its GDScript compiled (`.gdc`: Godot 3 bytecode, or Godot 4's binary
tokens) and its scenes and resources converted to binary (`.scn`, `.res`). `gdre.py` turns them
back into text with GDRE Tools 2.7.4 (MIT), in the offline `re-gdre` sandbox.

```bash
python $RE/gdre.py game.pck --list                         # GDRE's own reading of the file list
python $RE/gdre.py game.pck --member res://main.gdc        # one script back to GDScript (stdout)
python $RE/gdre.py main.gdc --engine 4.7.2                 # a loose .gdc (engine version: godot_pck)
python $RE/gdre.py game.pck --member res://.godot/exported/.../main.scn   # one binary scene as text
python $RE/gdre.py game.pck --recover OUT --purpose own_work --note "..."   # GATED: the whole project
python $RE/gdre.py game.pck --recover OUT --scripts-only --purpose own_work --note "..."
```

A pack is a `.pck` or an exported game with its pack appended (GDRE finds it by content, not by
name). Reading one script or one scene is ungated; `--recover` writes a whole project
(decompiled scripts, scenes and resources in the editor's text form, `project.godot`) and is gated
like the other extractors: a lawful `--purpose`, a one-sentence `--note`, a new or empty folder, a
size cap (`--max-mb`). A script inside a pack decompiles with the engine version its pack header
names; GDRE maps that to its newest bytecode table at or below it (4.7.2 reads with the
4.5.0-stable table, 3.6.3 with 3.5.0-stable), and the table used is printed so a dossier can cite
it.

What comes back is proven on four packs exported here, one per engine and pack format (Godot
3.6.3, 4.4.1, 4.6.2 and 4.7.2; `proof/gdre/`): every statement on its source line, with the same
names, control flow and values. Three instruments agree. The decompiled script matches its source
token for token once comments and literal notation are set aside (`0xFF` comes back as `255`,
`r"a\b"` as `"a\\b"`). GDRE's listing of each pack equals `godot_pck`'s. And the original and
the decompiled script, run side by side in the engine that compiled them, print the same 69
results, and the same script constants byte for byte: all 22 in Godot 3, and all 25 but one in
Godot 4 (the loss below). Each comparison is shown able to fail on every run: a planted `- 99` in
one function moves 10 of the 69 results. Scenes and resources
recovered from a pack match their sources once the ids an editor rewrites on save (uid,
unique_id, load_steps) and property order are set aside.

One measured loss, say it in a dossier: in Godot 4 a literal `-0.0` comes back as `0.0`. Godot 4's
tokenizer folds the minus into the literal and GDRE prints that zero without its sign; Godot 3
keeps the minus as its own token, so it survives there. Comments are gone (a compiled script has
none). A difference between a recovered scene and what you expected can be the engine's, not
GDRE's: Godot 4.4's Label has no focus style, so a 4.4 export dropped that property before GDRE
saw it. Compare against the stored form (`--member` on the `.scn`) before blaming the decompiler.

GDRE's exit code is not a verdict: a decompile that fails still exits 0 and writes nothing, so
`gdre.py` treats a missing output as the error and shows GDRE's own reason (a wrong engine table
reads "Binary GDScript is too recent"). An engine version outside 1.x to 4.x is refused before
GDRE runs (9.9.9 crashed it). An encrypted pack or script is not opened: this tool takes no key. A
C# Godot game keeps its code in a managed assembly beside the pack; read that with `csharp.py`.
`--member` on a `.scn` gives the scene in its raw stored form (a PackedScene's `_bundled` data);
`--recover` writes the editor's `[node]` form.

### Unity and .NET code

A Unity game built on the Mono backend ships its C# as managed .NET assemblies (`Assembly-CSharp.dll`
and friends); any .NET program is the same shape. `csharp.py` decompiles a managed assembly back to
C# with ILSpy's `ilspycmd`, run in the offline `re-dotnet` sandbox.

```bash
python $RE/csharp.py Assembly-CSharp.dll                  # the whole assembly as C#
python $RE/csharp.py Assembly-CSharp.dll --type Game.Player   # just one type
python $RE/csharp.py Assembly-CSharp.dll --project OUT --purpose interoperability --note "..."
python $RE/csharp.py global-metadata.dat --il2cpp          # recognise an IL2CPP build
```

Reading the C# (to stdout, or one type) is ungated; writing a whole decompiled project to disk is
gated like the other extractors. The assembly is only ever read, in the sandbox. Scope: `ilspycmd`
reads **managed** assemblies (the Mono backend). A Unity game built with **IL2CPP** has no managed
assemblies (its C# is compiled to native code beside a `global-metadata.dat`); `--il2cpp` recognises
that file and reports its version, but reconstructing IL2CPP types needs Il2CppDumper, which is not
bundled (its output is stubs and it churns), so say that in a dossier rather than imply this reads
IL2CPP. Proven by decompiling a managed assembly built here and recovering its known method, type
and constant (`tests/test_reverse_engineering_csharp.py`).

## Scripts

- `scripts/fingerprint.py` : stack detection from literal evidence (static or captured HTML).
- `scripts/capture_net.py` : record every request a page makes; `--scroll` drives scroll loads.
- `scripts/watch_page.py` : record what changes, when, and in answer to what; `--control`.
- `scripts/libscan.py` : name libraries in minified JS from proven markers; file and offset per hit.
- `scripts/unminify.py` : source map recovery, wakaru decompiling and unpacking, sandboxed webcrack.
- `scripts/wasm_inspect.py` : WebAssembly sections, functions, strings, toolchain; wabt per function.
- `scripts/bin_triage.py` : pure-Python ELF/PE/Mach-O reader; format, arch, loader, needed libs, strings.
- `scripts/r2.py` : radare2 in the sandbox; info, functions, strings, imports, disasm (addr+bytes), xrefs.
- `scripts/ghidra.py`, `scripts/GhidraDecompile.java` : Ghidra headless decompile + list, in the sandbox.
- `scripts/carve.py` : signature scan (pure Python) and binwalk scan/gated-extraction in the sandbox.
- `scripts/godot_pck.py` : pure-Python Godot pack reader (every format, 1 to 4: Godot 3 to 4.7; standalone or embedded); file table, md5 re-hash, gated extraction.
- `scripts/gdre.py` : Godot scripts and scenes back to text with GDRE Tools (in the re-gdre sandbox): a pack's listing, one script or scene, or a gated whole-project recovery.
- `scripts/asar.py` : pure-Python Electron .asar reader; file tree, SHA-256 re-hash, gated extraction (so the web tools can read a desktop app).
- `scripts/csharp.py` : decompile a managed .NET/Unity assembly to C# with ilspycmd (in the re-dotnet sandbox); IL2CPP recognition.
- `scripts/har_import.py` : read a saved browser session (.har) as a network capture; redacts secrets, gated body extraction (for pages behind a login).
- `scripts/oracle.py`, `scripts/oracle_runner.py` : the native rebuild oracle; runs the original function's bytes in Unicorn and compares to a rebuild over a corpus (in the re-oracle sandbox).
- `scripts/trace.py` : native runtime recorder; runs a Linux program under strace in the re-trace sandbox and records the files, network and programs it touched.
- `scripts/sandbox.py` : the offline container, `check` (the proof that it holds), and
  `open_output`/`copy_output_tree`, the only way to read what a container wrote.
- `scripts/webfetch.py` : the one bounded GET the web tools share; redirects followed unread, and
  a deadline on the whole fetch.
- `scripts/re-tools.Dockerfile` : wabt (Tier 1 wasm). `re-native` (radare2), `re-ghidra` (JDK+Ghidra),
  `re-carve` (binwalk), `re-oracle` (Unicorn + capstone + pefile + gcc/MinGW), `re-dotnet` (ilspycmd),
  `re-trace` (strace), `re-gdre` (GDRE Tools): the Tier 2/3, games and oracle images, each built on
  first use and tagged by its Dockerfile hash.
- `scripts/validate_dossier.py` : the verification gate; `--evidence` re-reads locators.
- `scripts/dossier.py` : dossier JSON to a Markdown report.
- `scripts/dossier-schema.json`, `scripts/dossier.example.json` : the claim contract and a template.
- `proof/libscan/` : pinned libraries, usage entries, `prove.py`, and the last `result.json`.
- `proof/gdre/` : `prove.py` (`build` exports the probe projects with four Godot editors; `verify`
  checks both pack readers, every script and scene, and runs original and decompiled side by side
  with planted controls), the two probe harnesses, and the last `result.json`.
- `proof/batteries/` : planted-fault batteries for the tests themselves: `static_tests.py`
  (Tier 1's static tools), `watch_tests.py` (watch_page),
  `native_tests.py` (Tier 2/3 and the sandbox), `games_tests.py` (the game-pack readers),
  `oracle_tests.py` (the native rebuild oracle), `asar_tests.py` (the Electron reader),
  `csharp_tests.py` (the .NET/Unity decompiler), `har_tests.py` (the HAR importer),
  `trace_tests.py` (the native runtime recorder), `gdre_tests.py` (the Godot script and scene
  reader, and the proof's own comparison helpers).
- `scripts/dossier.example.json` : a worked example dossier in the schema, used as the
  template and as the clean case in the validator's tests.

Pinned tools install on first use into `~/.local/share/re-tools/node` (wakaru 1.14.0, webcrack
2.16.0; npm, install scripts off). Sandbox images, each on `debian:trixie-slim` pinned by
digest and tagged by its Dockerfile's hash so an edit rebuilds it: `re-tools` (wabt 1.0.36),
`re-native` (radare2 6.2.4 from the official .deb, sha256 pinned; + binutils), `re-ghidra`
(OpenJDK 21 + Ghidra 12.1.4, sha256 pinned; ~1.3 GB, built on first Ghidra use), `re-carve`
(binwalk 2.4.3 + the free extractors), `re-oracle` (Unicorn 2.1.4, capstone 5.0.9 and pefile
2024.8.26 as sha256-pinned wheels, plus gcc and the MinGW cross-compiler; ~1 GB, for the native
oracle), `re-dotnet` (the .NET SDK 9.0 pinned by digest + ilspycmd 9.1 pinned by version; ~1 GB,
for the C#/Unity decompiler), `re-trace` (strace; ~80 MB, for the runtime recorder), `re-gdre`
(GDRE Tools 2.7.4, the release zip sha256 pinned; ~180 MB, for the Godot script and scene reader).
`node:24-slim` is pinned by digest. The host also has
apt `wabt` 1.0.34 for `--host-tools`; it has no radare2 or Ghidra, so those always use the
sandbox.

## Limits (say these out loud in a dossier)

- `absent` from libscan means "not in the scanned files". A chunk loaded later (a click, a
  route change, deep scroll) is only scanned if a capture caught it. Libraries outside the
  covered list are never reported.
- watch_page cannot hide the browser tab, so behaviour tied to `visibilitychange` stays a
  static reading unless tested another way. Its recorder runs in the page's own world, so a
  page that wants to can see it, rewrite it or feed it false records: its log is evidence about
  pages that are not working against you, not about one that is.
- wakaru never runs code and so cannot decode a hidden string table; `--deobfuscate` can,
  because webcrack runs the decoder, which is why it only runs in the sandbox.
- wasm-decompile's text is a reading aid (it can rename parameters inconsistently); cite
  `--disasm` lines for ground truth. `--disasm` takes a function's index or exact name;
  `--decompile` takes the name as wasm-decompile prints it, which rewrites names that are
  not plain identifiers (`--decompile-out` shows them).
- Ghidra's decompiled C and radare2's function boundaries are the tools' analysis, a
  hypothesis; the instruction address and bytes are the ground truth. Cite the bytes.
  `r2.py` reads up to 64 MB of radare2's JSON and refuses more (the sandbox's default 200 KB
  output tail once broke every list from a mid-sized binary); `ghidra.py` reads up to 4 MB of
  its result and ends a longer one with a line saying where it was cut.
- radare2's `aflj` keys a function's address as `addr` (not `offset`); its `pdfj` ops carry
  `addr`, `bytes`, `opcode`. A function argument to `r2.py`/`ghidra.py` is restricted to a
  bare name or an `0x`-address, so a name with unusual characters must be given as its address.
  `r2.py` also refuses `@`: radare2 reads `@@` as a foreach iterator, and `entry0@@c:ij` (only
  letters, digits, `@` and `:`) made it run `ij`. No radare2 function name carries an `@`.
- `bin_triage` reads ELF in full; PE to triage depth (format, arch, sections, imported
  libraries); Mach-O to format, arch (arm64e and its variants named from the cpusubtype, as
  lipo names them), file type, load commands and linked libraries. For a fat/universal Mach-O
  it lists each slice's arch, offset and size (both the 32-bit `fat_arch` and 64-bit
  `fat_arch_64` forms), and `--slice <arch|index>` carves
  one thin slice out; `r2.py` and `ghidra.py` take the same `--slice` to open one arch of a
  universal binary. `\xca\xfe\xba\xbe` is both a Mach-O fat header and a Java class, so a
  `.class` can misread as a fat binary; confirm from the surrounding structure. The slice
  selection is proven on synthetic universal binaries built in the tests, and the reading on real
  ones: on a Mac, 400 binaries (1,108 slices) read exactly as lipo and otool read them, every
  slice's offset, size and arch and every linked library. Running radare2 or Ghidra on a slice of
  a *real* Mac universal binary is still unproven (that Mac had no Docker), so say that in a
  dossier.
- `carve`'s pure-Python scan reports a magic wherever it sits. A magic of four or more
  distinctive bytes stands alone; a shorter or low-entropy one is checked against the structure
  after it (MZ must point at a real PE header; JPEG's `FF D8 FF` needs a segment that lands on
  the next marker; gzip's `1F 8B 08` needs valid flags; bzip2 needs a block size and its block
  magic) or pinned to offset 0 (TrueType). Read blind, the 3-byte JPEG and gzip magics fired 8
  times in 64 MB of random bytes. A list that stopped early (256 hits of one kind, or 100,000
  candidates a check refused) says so: `truncated` is set and `capped` names the signature.
  binwalk's far larger set runs under `--binwalk`.
- `gdre.py` is proven on packs exported here by Godot 3.6.3, 4.4.1, 4.6.2 and 4.7.2, and on no
  real shipped game yet. GDRE also carries tables for Godot 1, 2 and older 3.x bytecode; those are
  its claim, unproven here. A recovered project is a reading aid: cite a script's lines as what the
  decompile says, and the pack's bytes (godot_pck) as ground truth.
- Docker containers run outside the bot's own processes, so a native tool's memory is accounted
  to Docker and capped by `--memory`. Confirm the image exists before claiming a tool ran; the
  first Ghidra use builds a 1.3 GB image (needs the network).
- The images build for the machine's own processor, and four are tied to x86-64. `re-native`
  (radare2's amd64 .deb), `re-gdre` (GDRE's x86_64 build) and `re-oracle` (the x86_64 Unicorn
  and Capstone wheels) fail to build on an ARM machine (an Apple silicon Mac, a Raspberry Pi), and
  `re-ghidra` builds there but Ghidra's release ships its decompiler for x86-64 only. The other
  four take Debian's own packages or a multi-architecture SDK. On an ARM machine, say so before
  anyone installs Docker for radare2, Ghidra, GDRE or the oracle.

## Status

All three tiers are built and proven, plus the games, desktop-app, native-oracle,
logged-in-page and Godot script and scene additions (2026-10-24).

- **Tier 1 (web)** proven end to end during development on a real production site (its
  dossier is not shipped here): a stack fingerprint, a network capture with an idle control,
  a watch of what moves and why, libraries named inside minified code by proven markers,
  recovered code, and a differential rebuild that matched the original on every measured
  check. Plus: an Electron `.asar` reader and a saved-session `.har` importer, so the
  web tools reach a desktop app and a page behind a login.
- **Tier 2 (native):** `bin_triage` corroborated against readelf (ELF `/bin/ls`, every field),
  file+objdump (a real PE) and, on a Mac, lipo and otool (400 real Mach-O binaries); radare2
  and Ghidra run in their sandbox images; objdump, radare2 and Ghidra agree byte for byte on the
  same function's instructions. A fat/universal
  Mach-O's slices are listed with offsets and opened one at a time (`--slice`). The **native
  oracle** runs the original function's bytes in Unicorn and confirms a rebuild matches over a
  corpus (System V and Win64), and `trace.py` records a Linux program's runtime behaviour
  (files, network, child programs) under strace, the native counterpart of `watch_page`.
- **Tier 3 (carving + games):** the pure-Python scan finds an embedded ZIP at its exact offset
  with no false hits; binwalk scans and (under the gate) extracts in the sandbox. Game/engine
  readers: a Godot pack reader (`.pck` or embedded, every format, each proven on a pack from a
  real editor: Godot 3.6.3, 4.4.1, 4.6.2, 4.7.2), a Godot script and scene decompiler (GDRE Tools,
  proven on the same four packs: scripts token for token, and original and decompiled run side by
  side in each engine; `proof/gdre/result.json`), and a Unity/.NET C# decompiler (ilspycmd, with
  IL2CPP recognition).

Tests: 290 in `tests/test_reverse_engineering*.py` (static 74, native 55, sandbox 17, watch 25,
games 25, oracle 13, asar 18, csharp 10, har 17, trace 8, gdre 28). The 285 before the Mac
review's three Mach-O tests all passed 2026-10-24 on Linux in one
run of every file (915 subtests; the Docker-gated classes skip without their images). After
the Mac and Linux reviews all 290 ran there again and passed, but for the lipo check, which
needs a Mac. The count
given earlier that day, 254, was one too high: the trace file holds 8 tests, not 9. Every
instrument is battery-tested: `proof/batteries/` holds `static_tests.py` (40/40), `native_tests.py`
(with the fat-Mach-O arms; its five name-filter arms rerun 5/5 after the trailing-newline fix),
`watch_tests.py` (14/14), and the additions' batteries
`games_tests.py` (16/16; its md5 arm had silently gone void after a fix and was re-anchored),
`oracle_tests.py` (10/10), `asar_tests.py` (12/12), `csharp_tests.py` (7/7), `har_tests.py` (8/8),
`trace_tests.py` (6/6) and `gdre_tests.py` (23/23), each with a null control that survives. Two
whole-skill audits on 2026-10-12 fixed 14 defects each; the 2026-10-24 additions each went
through tests red-first, a planted-fault battery, and three audit passes. See
`references/verification.md`.
