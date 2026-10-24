#!/usr/bin/env python3
"""
Decompile a function of a compiled binary with Ghidra's headless analyzer, run
inside the offline sandbox so a hostile binary is only ever imported and
analysed in isolation and a heavy analysis cannot threaten the bot.

  python ghidra.py target --list                 # every function Ghidra finds
  python ghidra.py target --function main         # decompile one function (name)
  python ghidra.py target --function 0x6d30       # or by address
  python ghidra.py target --function main --out main.c

Ghidra (12.1.4) and its JDK live in sandbox.py's re-ghidra image. Each run is
`analyzeHeadless`: it imports the binary into a throwaway project, auto-analyses
it under a time bound, then runs GhidraDecompile.java (the postScript) to write
the result, which is read back only as a regular file inside the output folder
(sandbox.open_output). The project is deleted afterwards. The JVM heap is capped with
GHIDRA_HEADLESS_MAXMEM and the whole container is memory capped, so the heavy
step stays contained (the first run builds the 1.3 GB image, which needs the
network; analysis runs never do).

Spine: the decompiled C is a HYPOTHESIS (a reading aid; Ghidra renames and
guesses types), the instruction lines (address + bytes) under it are GROUND
TRUTH. The decompiler is excellent but it is not the bytes.

--host-tools would use a host Ghidra; this machine has none, so the sandbox is
the only path and --host-tools errors out by design.
"""
import argparse
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bin_triage  # noqa: E402
import sandbox  # noqa: E402

SELECTOR_OK = re.compile(r"^(?:::list::|0x[0-9a-fA-F]+|[A-Za-z_][\w.$@:]*)\Z")   # \Z, not $ (final newline)
POSTSCRIPT = "GhidraDecompile.java"     # a Java GhidraScript: no PyGhidra/Jython needed
MAX_RESULT = 4_000_000          # how much of the written result to read back
MAX_WRITE = 8 * 1024 ** 3       # the throwaway project and the result together; a run past it is stopped


class GhidraError(RuntimeError):
    pass


def _mem_to_bytes(s):
    s = str(s).strip().lower()
    mult = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
    return int(float(s[:-1]) * mult[s[-1]]) if s and s[-1] in mult else int(s)


def decompile(binpath, selector, maxmem="2G", timeout=900, container_mem=None):
    """Run analyzeHeadless + the postScript for `selector`; return the result text.
    `selector` is 'name', '0xADDR', or '::list::' and is checked before use."""
    if not SELECTOR_OK.match(selector):
        raise GhidraError(f"{selector!r} is not a bare function name, 0x-address, or ::list::")
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isfile(os.path.join(scripts_dir, POSTSCRIPT)):
        raise GhidraError(f"{POSTSCRIPT} is missing from {scripts_dir}")
    if container_mem is None:
        container_mem = str(_mem_to_bytes(maxmem) + 1024 ** 3)   # JVM heap + 1 GiB headroom

    with tempfile.TemporaryDirectory(prefix="re-ghidra-") as work:
        in_dir, out_dir, script_dir = (os.path.join(work, d) for d in ("in", "out", "scriptdir"))
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        os.makedirs(script_dir)
        shutil.copyfile(binpath, os.path.join(in_dir, "target"))
        # Mount only the one Ghidra script, so Ghidra does not index the whole scripts folder.
        shutil.copyfile(os.path.join(scripts_dir, POSTSCRIPT), os.path.join(script_dir, POSTSCRIPT))
        analysis_timeout = max(60, timeout - 120)
        # Fixed tokens only; `selector` is validated above and the paths are fixed.
        line = (
            "mkdir -p /out/proj && /opt/ghidra/support/analyzeHeadless /out/proj REproj "
            "-import /in/target -scriptPath /scripts -postScript " + POSTSCRIPT + " '" + selector + "' "
            "/out/result.txt -deleteProject -analysisTimeoutPerFile " + str(analysis_timeout)
        )
        try:
            image = sandbox.ensure_image(sandbox.GHIDRA_DOCKERFILE, timeout=2400)
            res = sandbox.run(
                ["sh", "-c", line], image=image,
                ro=[f"{in_dir}:/in", f"{script_dir}:/scripts"], rw=[f"{out_dir}:/out"],
                memory=container_mem, timeout=timeout, hostname="localhost", max_write=MAX_WRITE,
                env={"GHIDRA_HEADLESS_MAXMEM": maxmem, "XDG_CACHE_HOME": "/tmp/.cache",
                     "XDG_CONFIG_HOME": "/tmp/.config"},
            )
        except sandbox.SandboxError as e:
            raise GhidraError(str(e))
        if res["timed_out"]:
            raise GhidraError(f"Ghidra timed out after {timeout} s (raise --timeout, or the binary is very large)")
        if res.get("over_cap"):
            raise GhidraError(f"Ghidra wrote more than {MAX_WRITE >> 30} GB and was stopped")
        if not os.path.lexists(os.path.join(out_dir, "result.txt")):
            raise GhidraError("Ghidra wrote no result; analyzeHeadless log tail:\n" + res["stderr"][-1500:])
        try:
            with sandbox.open_output(out_dir, "result.txt") as fh:
                raw = fh.read(MAX_RESULT + 1)
        except sandbox.SandboxError as e:
            raise GhidraError(str(e))
        text = raw[:MAX_RESULT].decode("utf-8", errors="replace")
        if len(raw) > MAX_RESULT:       # a function list of a big binary runs past it; never cut silently
            end = text.rfind("\n")              # at a line's end, unless the result is one long line
            text = (text[:end + 1] if end >= 0 else text + "\n") + \
                f"[... cut at {MAX_RESULT} bytes; the rest was not read ...]\n"
        if text.startswith("NO_FUNCTION"):
            raise GhidraError(f"Ghidra found no function for {selector!r} "
                              f"(try --list, or give the entry address as 0x...)")
        return text


def main():
    ap = argparse.ArgumentParser(description="Decompile a binary's function with Ghidra headless, in the sandbox.")
    ap.add_argument("file", help="Path to the binary.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--function", metavar="SYM", help="Function to decompile (name or 0x-address).")
    g.add_argument("--list", action="store_true", help="List every function Ghidra finds.")
    ap.add_argument("--host-tools", action="store_true", help="Use a host Ghidra (none on this machine).")
    ap.add_argument("--slice", metavar="ARCH", help="For a fat/universal Mach-O: the slice (arch or index) to open.")
    ap.add_argument("--maxmem", default="2G", help="JVM max heap for Ghidra (default 2G).")
    ap.add_argument("--timeout", type=int, default=900, help="Whole-run time bound in seconds (default 900).")
    ap.add_argument("--out", help="Write the result text here as well as printing it.")
    args = ap.parse_args()

    if args.host_tools:
        print("No host Ghidra on this machine; this tool runs Ghidra only in the sandbox.", file=sys.stderr)
        sys.exit(2)
    if not os.path.isfile(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)

    target, tmp_slice = args.file, None
    if args.slice:
        try:
            target, arch = bin_triage.write_slice(args.file, args.slice)
            tmp_slice = target
            print(f"[slice {arch}]", file=sys.stderr)
        except (bin_triage.BinError, OSError) as e:
            print(f"--slice: {e}", file=sys.stderr)
            sys.exit(2)

    selector = "::list::" if args.list else args.function
    try:
        text = decompile(target, selector, maxmem=args.maxmem, timeout=args.timeout)
    except GhidraError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    finally:
        if tmp_slice and os.path.exists(tmp_slice):
            os.unlink(tmp_slice)

    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
