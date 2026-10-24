#!/usr/bin/env python3
"""
Native behaviour oracle: prove a rebuilt function behaves like the original by
running the ORIGINAL's real machine code, input by input, against the rebuild
and checking every answer agrees. The native counterpart of the web tier's
rebuild check.

The original's bytes are never executed on this machine; they are interpreted
inside the Unicorn CPU emulator, in the offline re-oracle sandbox (no network,
read-only, non-root, memory capped). A rebuild given as source is compiled in
that same sandbox, not on the host.

  # a reimplementation you compiled yourself, against the original:
  python oracle.py --original game.dll --rebuild mine.so --function pan \
      --abi win64 --arg int:4 --arg outbuf:4 --arg outbuf:4 --ret void --fuzz 2000

  # or give the rebuild as C source and let the sandbox compile it:
  python oracle.py --original libthing.so --rebuild-src mine.c --function crc32 \
      --arg uint:4 --ret uint:4 --fuzz 5000 --out oracle.json

Arguments (in order, with --arg):
  int:W     signed W-byte integer, passed in the next integer register
  uint:W    unsigned W-byte integer
  outbuf:N  a pointer to an N-byte buffer the function writes; its bytes are
            compared after each call (the register gets the buffer's address)
--ret is int:W | uint:W | void. Only x86-64; only register arguments.

What this proves and does not: it confirms input/output agreement over a corpus
(fixed seeds + per-argument boundaries + seeded fuzz), so a surviving difference
is a real behavioural gap and the first diverging input is reported. It does not
run the whole program, does not follow a call into an external library (a leaf
function, or one whose callees are inside the binary, is the clean case; a call
to unmapped code is reported, not guessed), and cannot judge a Mac (Mach-O)
binary until one can be built or supplied. A match is a behavioural claim
confirmed by a sandbox run, exactly what the spine asks for.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402

RUNNER = "oracle_runner.py"


class OracleError(RuntimeError):
    pass


def _parse_typed(spec):
    """'int:4' / 'uint:8' / 'outbuf:16' / 'void' -> an arg dict."""
    if spec == "void":
        return {"type": "void"}
    kind, _, n = spec.partition(":")
    if kind in ("int", "uint"):
        if n not in ("1", "2", "4", "8"):
            raise OracleError(f"{spec!r}: width must be 1, 2, 4 or 8")
        return {"type": "int", "width": int(n), "signed": kind == "int"}
    if kind == "outbuf":
        if not n.isdigit() or int(n) <= 0 or int(n) > 4096:
            raise OracleError(f"{spec!r}: outbuf size must be 1..4096")
        return {"type": "outbuf", "size": int(n)}
    raise OracleError(f"{spec!r}: expected int:W, uint:W, outbuf:N or void")


def compile_source(src_path, abi, func, timeout=300):
    """Compile a C source to a shared object (sysv) or DLL (win64) INSIDE the
    sandbox, with the function visible, and return the bytes of the result.
    Compilation runs offline; the source is mounted read-only."""
    src_path = os.path.abspath(src_path)
    if not os.path.isfile(src_path):
        raise OracleError(f"no source at {src_path}")
    image = sandbox.ensure_image(sandbox.ORACLE_DOCKERFILE, timeout=2400)
    with tempfile.TemporaryDirectory(prefix="re-oracle-cc-") as work:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        shutil.copyfile(src_path, os.path.join(in_dir, "src.c"))
        if abi == "win64":
            cc, out = "x86_64-w64-mingw32-gcc", "rebuild.dll"
            flags = ["-shared", "-O2", "-fno-stack-protector", "-Wl,--export-all-symbols", "-o", f"/out/{out}"]
        else:
            cc, out = "gcc", "rebuild.so"
            flags = ["-shared", "-fPIC", "-O2", "-fno-stack-protector", "-o", f"/out/{out}"]
        res = sandbox.run([cc, *flags, "/in/src.c"], image=image, ro=[f"{in_dir}:/in"], rw=[f"{out_dir}:/out"],
                          memory="1g", timeout=timeout, max_write=256 << 20)
        if res["timed_out"]:
            raise OracleError(f"compile timed out after {timeout} s")
        if res["returncode"] != 0 or not os.path.lexists(os.path.join(out_dir, out)):
            raise OracleError("compile failed:\n" + (res["stderr"] or res["stdout"])[-1500:])
        with sandbox.open_output(out_dir, out) as fh:
            data = fh.read()
    return data, out


def run_oracle(original, rebuild, function, args, ret, abi="sysv", corpus=None, timeout=300, max_insns=200000,
               function_original=None, function_rebuild=None):
    """Emulate `function` in both binaries over the corpus and return the result
    dict (see oracle_runner.py). `original` and `rebuild` are host paths to
    compiled binaries; `args`/`ret` are typed dicts from _parse_typed."""
    for p in (original, rebuild):
        if not os.path.isfile(p):
            raise OracleError(f"no such binary: {p}")
    if abi not in ("sysv", "win64"):
        raise OracleError("abi must be sysv or win64")
    image = sandbox.ensure_image(sandbox.ORACLE_DOCKERFILE, timeout=2400)
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    with tempfile.TemporaryDirectory(prefix="re-oracle-run-") as work:
        in_dir, sc_dir, sp_dir = (os.path.join(work, d) for d in ("in", "scripts", "spec"))
        for d in (in_dir, sc_dir, sp_dir):
            os.makedirs(d)
        shutil.copyfile(original, os.path.join(in_dir, "original"))
        shutil.copyfile(rebuild, os.path.join(in_dir, "rebuild"))
        shutil.copyfile(os.path.join(scripts_dir, RUNNER), os.path.join(sc_dir, RUNNER))
        spec = {
            "arch": "x86_64", "abi": abi, "function": function,
            "function_original": function_original or function,
            "function_rebuild": function_rebuild or function,
            "subjects": {"original": "/in/original", "rebuild": "/in/rebuild"},
            "args": args, "ret": ret, "max_insns": max_insns,
            "corpus": corpus or {"boundaries": True, "fuzz": {"count": 2000, "seed": 1234}},
        }
        with open(os.path.join(sp_dir, "spec.json"), "w") as fh:
            json.dump(spec, fh)
        res = sandbox.run(["python3", f"/scripts/{RUNNER}", "/spec/spec.json"], image=image,
                          ro=[f"{in_dir}:/in", f"{sc_dir}:/scripts", f"{sp_dir}:/spec"],
                          memory="2g", timeout=timeout)
    if res["timed_out"]:
        raise OracleError(f"oracle timed out after {timeout} s")
    out = res["stdout"].strip()
    tail = out.rsplit("\n", 1)[-1] if out else ""
    try:
        return json.loads(tail)
    except json.JSONDecodeError:
        raise OracleError("oracle produced no result:\n" + (res["stderr"] or out)[-1500:])


def _format(result):
    lines = [
        f"function {result['function']}  ({result['arch']}, {result['abi']} ABI)",
        f"  original @ {result['original_entry']}   rebuild @ {result['rebuild_entry']}",
        f"  corpus {result['corpus_size']}, tested {result['tested']}, agreed {result['agreed']}",
    ]
    errs = result.get("errors", {})
    if errs.get("original") or errs.get("rebuild"):
        lines.append(f"  errors: original {errs['original']}, rebuild {errs['rebuild']}")
        for s in result.get("error_samples", [])[:3]:
            lines.append(f"    input {s['input']}: orig={s['original_error']} rebuild={s['rebuild_error']}")
    if result["match"]:
        lines.append("  VERDICT: MATCH -- the rebuild agrees with the original on every tested input")
    else:
        lines.append("  VERDICT: DIFFERENT")
        d = result.get("first_divergence")
        if d:
            lines.append(f"    first divergence at input {d['input']}:")
            lines.append(f"      original: ret={d['original']['ret']} outbufs={d['original']['outbufs']}")
            lines.append(f"      rebuild:  ret={d['rebuild']['ret']} outbufs={d['rebuild']['outbufs']}")
    if result.get("original_disasm"):
        lines.append("  original, first instructions (ground truth):")
        for ins in result["original_disasm"][:12]:
            lines.append(f"    0x{ins['addr']:x}  {ins['bytes']:<20} {ins['text']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Prove a rebuilt function matches the original by emulation.")
    ap.add_argument("--original", required=True, help="Path to the original compiled binary (ELF/PE).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--rebuild", help="Path to the rebuilt binary you compiled.")
    g.add_argument("--rebuild-src", help="C source of the rebuild; compiled in the sandbox.")
    ap.add_argument("--function", required=True, help="Function name (or 0x-address) in both binaries.")
    ap.add_argument("--function-original", help="Name in the original, if it differs.")
    ap.add_argument("--function-rebuild", help="Name in the rebuild, if it differs.")
    ap.add_argument("--abi", default="sysv", choices=["sysv", "win64"], help="Calling convention (default sysv).")
    ap.add_argument("--arg", action="append", default=[], metavar="TYPE",
                    help="An argument type, in order: int:W, uint:W, or outbuf:N (repeatable).")
    ap.add_argument("--ret", default="int:4", help="Return type: int:W, uint:W or void (default int:4).")
    ap.add_argument("--seed", action="append", default=[], metavar="CSV",
                    help="An explicit input tuple, comma-separated ints (repeatable).")
    ap.add_argument("--no-boundaries", action="store_true", help="Skip the per-argument boundary inputs.")
    ap.add_argument("--fuzz", type=int, default=2000, help="How many seeded random inputs (default 2000).")
    ap.add_argument("--fuzz-seed", type=int, default=1234, help="Seed for the fuzz inputs (default 1234).")
    ap.add_argument("--max", type=int, default=5000, help="Cap on total corpus size (default 5000).")
    ap.add_argument("--max-insns", type=int, default=200000, help="Instruction cap per call (default 200000).")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", help="Write the full result as JSON here.")
    args = ap.parse_args()

    try:
        arg_specs = [_parse_typed(a) for a in args.arg]
        ret_spec = _parse_typed(args.ret)
        corpus = {
            "seeds": [[int(x) for x in s.split(",")] for s in args.seed],
            "boundaries": not args.no_boundaries,
            "fuzz": {"count": args.fuzz, "seed": args.fuzz_seed},
            "max": args.max,
        }
        rebuild_path = args.rebuild
        tmp_rebuild = None
        if args.rebuild_src:
            data, _ = compile_source(args.rebuild_src, args.abi, args.function, timeout=args.timeout)
            fd, tmp_rebuild = tempfile.mkstemp(prefix="re-oracle-rebuild-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            rebuild_path = tmp_rebuild
        try:
            result = run_oracle(args.original, rebuild_path, args.function, arg_specs, ret_spec, abi=args.abi,
                                corpus=corpus, timeout=args.timeout, max_insns=args.max_insns,
                                function_original=args.function_original, function_rebuild=args.function_rebuild)
        finally:
            if tmp_rebuild and os.path.exists(tmp_rebuild):
                os.unlink(tmp_rebuild)
    except (OracleError, sandbox.SandboxError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    print(_format(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        print(f"Wrote {args.out}")
    sys.exit(0 if result["match"] else 1)


if __name__ == "__main__":
    main()
