#!/usr/bin/env python3
"""
Turn a compiled .NET assembly back into readable C# with ILSpy's command-line
tool (ilspycmd), run in the offline re-dotnet sandbox. This is how a Unity game
built on Mono (its Assembly-CSharp.dll and the other managed DLLs) or any .NET
program is read.

  python csharp.py Assembly-CSharp.dll                     # the whole assembly as C#
  python csharp.py Assembly-CSharp.dll --type Game.Player   # one type
  python csharp.py Assembly-CSharp.dll --project OUT --purpose interoperability --note "..."
  python csharp.py global-metadata.dat --il2cpp              # recognise an IL2CPP build

Reading the decompiled C# (to stdout, or one type) is ungated; writing a whole
decompiled project to disk is gated like the other extractors (a lawful
`--purpose` and a one-sentence `--note`). The assembly is only ever read, inside
the sandbox (no network, read-only, non-root), so an untrusted one is safe.

Scope: ilspycmd reads MANAGED assemblies (the Mono backend, the common case). A
Unity game built with IL2CPP has no managed assemblies; its C# is compiled to
native code beside a `global-metadata.dat`. `--il2cpp` recognises that file and
reports its version, but reconstructing IL2CPP types needs Il2CppDumper, which
is not bundled here (its output is stubs, and it moves fast); say so in a
dossier rather than imply this tool reads IL2CPP.
"""
import argparse
import json
import mmap
import os
import re
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402

TYPE_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_.+`<>,\[\] ]*\Z")   # a .NET type name (generics allowed), no leading '-'
IL2CPP_MAGIC = 0xFAB11BAF
MAX_OUT = 16 << 20            # how much decompiled C# to read back from stdout


class CSharpError(RuntimeError):
    pass


def decompile(assembly, type_name=None, timeout=300):
    """Decompile a managed assembly to C# (the whole assembly, or one type) and
    return the text. Reads only; runs ilspycmd in the sandbox."""
    assembly = os.path.abspath(assembly)
    if not os.path.isfile(assembly):
        raise CSharpError(f"no such assembly: {assembly}")
    if type_name is not None and not TYPE_OK.match(type_name):
        raise CSharpError(f"{type_name!r} is not a plain .NET type name")
    image = sandbox.ensure_image(sandbox.DOTNET_DOCKERFILE, timeout=2400)
    with tempfile.TemporaryDirectory(prefix="re-cs-") as work:
        in_dir = os.path.join(work, "in")
        os.makedirs(in_dir)
        shutil.copyfile(assembly, os.path.join(in_dir, "asm.dll"))
        cmd = ["ilspycmd", "/in/asm.dll"] + (["-t", type_name] if type_name else [])
        res = sandbox.run(cmd, image=image, ro=[f"{in_dir}:/in"], memory="2g", timeout=timeout, max_output=MAX_OUT)
    if res["timed_out"]:
        raise CSharpError(f"ilspycmd timed out after {timeout} s")
    if res["returncode"] != 0 and not res["stdout"].strip():
        raise CSharpError("ilspycmd failed (is it a managed .NET assembly?):\n" + res["stderr"][-1000:])
    if res.get("stdout_cut"):
        raise CSharpError(f"decompiled C# exceeded {MAX_OUT >> 20} MB; decompile one --type, or use --project")
    return res["stdout"]


def decompile_project(assembly, outdir, purpose, note, timeout=600):
    """GATED. Write the whole assembly as a C# project tree into outdir (new or
    empty). Returns (written paths, [skipped])."""
    purposes = lawful_purposes()
    if purpose not in purposes:
        raise CSharpError(f"--purpose must be one of {', '.join(purposes)}. {REFUSED}")
    if not note or not note.strip():
        raise CSharpError(f"--note must name the concrete basis in one sentence. {REFUSED}")
    if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        raise CSharpError(f"--project needs a new or empty folder; {outdir} is not one")
    assembly = os.path.abspath(assembly)
    if not os.path.isfile(assembly):
        raise CSharpError(f"no such assembly: {assembly}")
    image = sandbox.ensure_image(sandbox.DOTNET_DOCKERFILE, timeout=2400)
    os.makedirs(outdir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="re-cs-") as work:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        shutil.copyfile(assembly, os.path.join(in_dir, "asm.dll"))
        # fsize_cap off: the .NET runtime takes SIGXFSZ under the memory cgroup when an
        # RLIMIT_FSIZE is set; the directory poll still caps total disk at max_write.
        res = sandbox.run(["ilspycmd", "/in/asm.dll", "-p", "-o", "/out/src"], image=image,
                          ro=[f"{in_dir}:/in"], rw=[f"{out_dir}:/out"], memory="2g", timeout=timeout,
                          max_write=512 << 20, fsize_cap=False)
        if res["timed_out"]:
            raise CSharpError(f"ilspycmd timed out after {timeout} s")
        if res.get("over_cap"):
            raise CSharpError("the decompiled project passed the 512 MB cap and was stopped")
        if not os.path.isdir(os.path.join(out_dir, "src")):
            raise CSharpError("ilspycmd wrote no project:\n" + (res["stderr"] or res["stdout"])[-1000:])
        skipped = sandbox.copy_output_tree(out_dir, "src", outdir)
    written = [os.path.join(dp, f) for dp, _, fs in os.walk(outdir) for f in fs]
    if res["returncode"] != 0 and not written:
        raise CSharpError("ilspycmd wrote nothing:\n" + res["stderr"][-1000:])
    return written, skipped


def il2cpp_info(data):
    """Recognise a Unity IL2CPP global-metadata.dat and report its version from a
    bytes-like buffer. Pure Python, bounded, safe on anything. Does not reconstruct
    types (that needs Il2CppDumper, not bundled)."""
    head = bytes(data[:8])
    if len(head) < 8:
        raise CSharpError("file is too small to be global-metadata.dat")
    magic, version = struct.unpack("<II", head)
    if magic != IL2CPP_MAGIC:
        raise CSharpError(f"not IL2CPP metadata (magic {magic:#010x}, expected {IL2CPP_MAGIC:#010x})")
    return {"il2cpp": True, "magic": f"{magic:#010x}", "version": version,
            "note": "IL2CPP build: C# is compiled to native code; reconstructing types needs Il2CppDumper "
                    "(not bundled here). ilspycmd reads managed (Mono) assemblies, not this."}


def lawful_purposes():
    schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossier-schema.json")
    with open(schema, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]


REFUSED = ("Writing a decompiled project out serves understanding and interoperability, never shipping "
           "another program's recovered source or assets as your own. That is refused, the same as in the dossier.")


def main():
    ap = argparse.ArgumentParser(description="Decompile a .NET/Unity managed assembly to C# (ilspycmd, in the sandbox).")
    ap.add_argument("file", help="Path to the .dll/.exe assembly, or global-metadata.dat with --il2cpp.")
    ap.add_argument("--type", metavar="NAME", help="Decompile just this type (namespace-qualified).")
    ap.add_argument("--project", metavar="OUTDIR", help="GATED: write the whole assembly as a C# project here.")
    ap.add_argument("--purpose", help="Lawful basis for --project.")
    ap.add_argument("--note", help="One concrete sentence naming the basis for --project.")
    ap.add_argument("--il2cpp", action="store_true", help="Recognise a Unity IL2CPP global-metadata.dat file.")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    try:
        if args.il2cpp:
            with open(args.file, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                info = il2cpp_info(mm)
            print(f"IL2CPP metadata, version {info['version']} (magic {info['magic']})")
            print(f"  {info['note']}")
            sys.exit(0)
        if args.project:
            written, skipped = decompile_project(args.file, args.project, args.purpose, args.note, timeout=args.timeout)
            print(f"Wrote {len(written)} file(s) to {os.path.abspath(args.project)}")
            if skipped:
                print(f"  (left behind {len(skipped)} non-regular file(s): {', '.join(skipped[:10])})")
            sys.exit(0)
        sys.stdout.write(decompile(args.file, type_name=args.type, timeout=args.timeout))
    except (CSharpError, sandbox.SandboxError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
