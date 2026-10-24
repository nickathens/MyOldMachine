#!/usr/bin/env python3
"""
Turn a Godot game's compiled scripts and binary scenes back into readable text
with GDRE Tools (gdsdecomp), run in the offline re-gdre sandbox.

  python gdre.py game.pck --list                          # GDRE's own reading of the file list
  python gdre.py game.pck --member res://main.gdc         # one compiled script back to GDScript
  python gdre.py main.gdc --engine 4.7.2                  # a loose .gdc (engine version: godot_pck.py)
  python gdre.py game.pck --member res://.godot/exported/.../main.scn   # one binary scene as text
  python gdre.py game.pck --recover OUT --purpose own_work --note "..."  # GATED: the whole project
  python gdre.py game.pck --recover OUT --scripts-only --purpose ... --note "..."

A pack is a .pck or an exported game with its pack appended (GDRE finds it by
content, not by name). Reading one script or one scene to stdout is ungated;
writing a recovered project to disk is gated like the other extractors (a
lawful `--purpose` and a one-sentence `--note`, a new or empty folder, a size
cap). Everything GDRE does happens in the sandbox (no network, read-only,
non-root, capped); what it writes is read back only through sandbox.open_output
and copy_output_tree.

What comes back, proven on packs made here by Godot 3.6.3, 4.4.1, 4.6.2 and
4.7.2 (proof/gdre/): every statement on its source line, names, control flow
and values; comments are gone (they are not in a compiled script) and literals
are written in GDRE's notation (0xFF comes back as 255). One measured loss: in
Godot 4 a literal `-0.0` comes back as `0.0`. A decompiled script is a reading of
the game's code, ground truth only for what it says; for a C# Godot game read
the managed assembly with csharp.py instead. An encrypted pack is not opened:
this tool takes no key.

GDRE's own exit code is not a verdict: a decompile that fails still exits 0 and
writes nothing, so success here means the output file exists.
"""
import argparse
import mmap
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import godot_pck  # noqa: E402
import sandbox  # noqa: E402

GDRE = "/opt/gdre/gdre_tools.x86_64"
# An engine version as GDRE's --bytecode takes it: Godot 1 to 4. GDRE maps it to the
# newest bytecode table at or below it (4.7.2 reads with 4.5.0-stable's); a version it
# does not know, 9.9.9 say, crashed it.
ENGINE_OK = re.compile(r"^[1-4]\.\d{1,2}(\.\d{1,2})?\Z")
SCRIPT_EXT = (".gdc",)
RESOURCE_EXT = (".scn", ".res")
MAX_TEXT = 16 << 20              # a decompiled script or text resource read back, at most
MAX_MEMBER = 64 << 20            # one pack member copied out to decompile
MAX_LISTING = 32 << 20           # GDRE's file listing comes back on stdout
MAX_EXTRACT_BYTES = 2 * 1024 ** 3
TABLE_LINE = re.compile(r"Decompiling from bytecode version (\w+) \(([^)]+)\)")
REPORT_LINE = re.compile(r"^([A-Z][A-Za-z -]+?):\s+(\d+)\s*$")


class GdreError(RuntimeError):
    pass


def _engine_version(engine):
    if not isinstance(engine, str) or not ENGINE_OK.match(engine):
        raise GdreError(f"engine version {engine!r} is not a Godot version GDRE knows (1.x to 4.x, like 4.7.2); "
                        "read it from the pack with godot_pck.py")
    return engine


def _gdre(args, ro, rw=(), memory="2g", timeout=120, max_write=64 << 20, max_output=sandbox.MAX_OUTPUT):
    image = sandbox.ensure_image(sandbox.GDRE_DOCKERFILE, timeout=1200)
    res = sandbox.run([GDRE, "--headless"] + args, image=image, ro=ro, rw=rw, memory=memory, timeout=timeout,
                      max_write=max_write, max_output=max_output)
    if res["timed_out"]:
        raise GdreError(f"GDRE timed out after {timeout} s")
    if res.get("over_cap"):
        raise GdreError(f"GDRE passed the {max_write >> 20} MB write cap and was stopped")
    if res["returncode"] >= 128:
        raise GdreError(f"GDRE crashed (exit {res['returncode']}, signal {res['returncode'] - 128}):\n"
                        + _why(res))
    return res


def _why(res, limit=6):
    """GDRE's own error lines, without the font noise a headless run always prints."""
    lines = [ln.strip() for ln in (res["stdout"] + "\n" + res["stderr"]).replace("\r", "\n").splitlines()]
    keep = [ln for ln in lines if ("ERROR" in ln or ln.startswith("Error")) and "fontconfig" not in ln]
    keep = list(dict.fromkeys(keep))[:limit]          # GDRE prints most errors twice (stdout and stderr)
    return "\n".join(keep) or (res["stderr"] or res["stdout"])[-800:]


def _read_text(out_dir, rel):
    with sandbox.open_output(out_dir, rel) as fh:
        data = fh.read(MAX_TEXT + 1)
    if len(data) > MAX_TEXT:
        raise GdreError(f"GDRE's output {rel} is over {MAX_TEXT >> 20} MB; not read")
    return data.decode("utf-8", errors="replace")


def _target(path):
    """Mount spec for a pack file: the file alone, read-only, under a fixed name, so
    nothing beside it is visible in the sandbox (the extension is kept if it is plain)."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise GdreError(f"no such file: {path}")
    ext = os.path.splitext(path)[1]
    name = "target" + (ext if re.fullmatch(r"\.[A-Za-z0-9_]{1,16}", ext) else "")
    return f"{path}:/in/{name}", f"/in/{name}"


def kind_of(name):
    """'script' for a compiled GDScript, 'resource' for a binary scene or resource."""
    low = name.lower()
    if low.endswith(SCRIPT_EXT):
        return "script"
    if low.endswith(RESOURCE_EXT):
        return "resource"
    if low.endswith(".gde"):
        raise GdreError(f"{name}: an encrypted script (.gde); this tool takes no key")
    raise GdreError(f"{name}: not a compiled script (.gdc) or a binary scene/resource (.scn, .res); "
                    "a .gd, .tscn or .tres is already text (extract it with godot_pck.py), and a pack "
                    "takes --list, --member or --recover")


def decompile(gdc, engine, timeout=120):
    """One compiled GDScript back to source. Returns (text, the bytecode table GDRE used)."""
    engine = _engine_version(engine)
    if not os.path.isfile(gdc):
        raise GdreError(f"no such file: {gdc}")
    with tempfile.TemporaryDirectory(prefix="re-gdre-") as work:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        shutil.copyfile(gdc, os.path.join(in_dir, "script.gdc"))
        res = _gdre(["--decompile=/in/script.gdc", f"--bytecode={engine}", "--output=/out"],
                    ro=[f"{in_dir}:/in"], rw=[f"{out_dir}:/out"], timeout=timeout)
        if not os.path.lexists(os.path.join(out_dir, "script.gd")):      # GDRE exits 0 on failure
            raise GdreError(f"GDRE could not decompile it with the {engine} table:\n" + _why(res))
        text = _read_text(out_dir, "script.gd")
    m = TABLE_LINE.search(res["stdout"])
    return text, (f"{m.group(2)} ({m.group(1)})" if m else "unknown")


def to_text(resource, timeout=120):
    """One binary scene (.scn) or resource (.res) as Godot's text form. A scene comes
    back in its raw stored form (a PackedScene's _bundled data); --recover writes the
    editor's [node] form instead."""
    kind_of(resource)
    if not os.path.isfile(resource):
        raise GdreError(f"no such file: {resource}")
    ext = os.path.splitext(resource)[1].lower()
    with tempfile.TemporaryDirectory(prefix="re-gdre-") as work:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        shutil.copyfile(resource, os.path.join(in_dir, "resource" + ext))
        res = _gdre([f"--bin-to-txt=/in/resource{ext}", "--output=/out"],
                    ro=[f"{in_dir}:/in"], rw=[f"{out_dir}:/out"], timeout=timeout)
        made = sorted(os.listdir(out_dir))
        if len(made) != 1:
            raise GdreError("GDRE wrote no text form" + (f" ({len(made)} files)" if made else "") + ":\n" + _why(res))
        return _read_text(out_dir, made[0])


def member_bytes(pack, member):
    """A member's stored bytes and the pack's engine version, read by godot_pck (pure
    Python), so a script inside a pack decompiles with the version its pack names."""
    want = member.split("://", 1)[1] if "://" in member else member
    if not os.path.isfile(pack):
        raise GdreError(f"no such file: {pack}")
    with open(pack, "rb") as fh:
        if os.fstat(fh.fileno()).st_size < 4:
            raise GdreError(f"{pack} is too small to be a pack")
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            try:
                rep = godot_pck.parse(mm)
            except godot_pck.PckError as e:
                raise GdreError(str(e)) from None
            if rep["dir_encrypted"]:
                raise GdreError("the pack's directory is encrypted; this tool takes no key")
            hits = [f for f in rep["files"] if (f["path"].split("://", 1)[1] if "://" in f["path"] else f["path"]) == want]
            if not hits:
                near = [f["path"] for f in rep["files"] if f["path"].endswith(os.path.basename(want))][:5]
                raise GdreError(f"{member} is not in the pack" + (f" (did you mean {', '.join(near)}?)" if near else "")
                                + (" (its directory was cut short: " + "; ".join(rep["errors"][:2]) + ")"
                                   if rep["truncated"] else ""))
            if len(hits) > 1:                # never pick one silently: they may differ
                raise GdreError(f"{member} appears {len(hits)} times in the directory; read each with godot_pck.py")
            f = hits[0]
            if f["encrypted"]:
                raise GdreError(f"{member} is encrypted in the pack; this tool takes no key")
            if not f["in_bounds"]:
                raise GdreError(f"{member}'s offset and size point outside the file")
            if f["size"] > MAX_MEMBER:
                raise GdreError(f"{member} is {f['size']} bytes, over the {MAX_MEMBER >> 20} MB cap for one member")
            return bytes(mm[f["offset"]:f["offset"] + f["size"]]), rep["engine_version"]


def from_member(pack, member, engine=None, timeout=120):
    """Decompile a script, or turn a binary resource into text, straight out of a pack.
    A script returns (text, table); a resource returns (text, None)."""
    kind = kind_of(member)
    data, pack_engine = member_bytes(pack, member)
    with tempfile.TemporaryDirectory(prefix="re-gdre-member-") as work:
        path = os.path.join(work, "member" + os.path.splitext(member)[1].lower())
        with open(path, "wb") as fh:
            fh.write(data)
        if kind == "script":
            return decompile(path, engine or pack_engine, timeout=timeout)
        return to_text(path, timeout=timeout), None


def decompile_member(pack, member, engine=None, timeout=120):
    """One compiled script from inside a pack: (text, table)."""
    if kind_of(member) != "script":
        raise GdreError(f"{member} is not a compiled script (.gdc)")
    return from_member(pack, member, engine=engine, timeout=timeout)


def parse_listing(stdout):
    """The res:// paths of GDRE's --list-files output, checked against its own total."""
    lines = stdout.replace("\r", "\n").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "Contents:")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("Total files:"))
    except StopIteration:
        raise GdreError("GDRE printed no file listing (not a pack, or encrypted):\n" + stdout[-800:]) from None
    paths = [ln.strip() for ln in lines[start + 1:end] if ln.strip().startswith(("res://", "user://"))]
    try:
        total = int(lines[end].split(":", 1)[1])
    except ValueError:
        raise GdreError(f"GDRE's listing ends in an unreadable total: {lines[end][:80]!r}") from None
    if total != len(paths):
        raise GdreError(f"GDRE listed {len(paths)} files but reported a total of {total}")
    return paths


def list_files(pack, timeout=300):
    """GDRE's own reading of a pack's file list: a second reader beside godot_pck."""
    spec, inside = _target(pack)
    res = _gdre([f"--list-files={inside}"], ro=[spec], timeout=timeout, max_write=None, max_output=MAX_LISTING)
    if res["stdout_cut"]:
        raise GdreError(f"GDRE's listing is over {MAX_LISTING >> 20} MB; not read")
    return parse_listing(res["stdout"])


def parse_report(text):
    """The totals of GDRE's EXPORT REPORT ('Decompiled scripts': 1, ...)."""
    rep, inside = {}, False
    for ln in text.splitlines():
        if "EXPORT REPORT" in ln:
            inside = True
        elif inside and ln.startswith("----"):
            break
        elif inside:
            m = REPORT_LINE.match(ln.strip())
            if m:
                rep[m.group(1)] = int(m.group(2))
    return rep


def recover(pack, outdir, purpose, note, scripts_only=False, max_bytes=None, timeout=1800):
    """GATED. Rebuild the Godot project from a pack into outdir (new or empty):
    decompiled scripts, scenes and resources in the editor's text form, project.godot.
    Returns (written paths, [skipped non-regular files], GDRE's report totals)."""
    purposes = lawful_purposes()
    if purpose not in purposes:
        raise GdreError(f"--purpose must be one of {', '.join(purposes)}. {REFUSED}")
    if not note or not note.strip():
        raise GdreError(f"--note must name the concrete basis in one sentence. {REFUSED}")
    if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        raise GdreError(f"--recover needs a new or empty folder; {outdir} is not one")
    spec, inside = _target(pack)
    cap = MAX_EXTRACT_BYTES if max_bytes is None else max_bytes
    os.makedirs(outdir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="re-gdre-rec-") as work:
        res = _gdre([f"--recover={inside}", "--output=/out/project"] + (["--scripts-only"] if scripts_only else []),
                    ro=[spec], rw=[f"{work}:/out"], memory="4g", timeout=timeout, max_write=cap)
        if not os.path.isdir(os.path.join(work, "project")) or res["returncode"] != 0:
            raise GdreError(f"GDRE could not recover a project (exit {res['returncode']}):\n" + _why(res))
        skipped = sandbox.copy_output_tree(work, "project", outdir)
        try:                               # the report closes the log; a big game's log can run long
            with sandbox.open_output(work, os.path.join("project", "gdre_export.log")) as fh:
                fh.seek(max(0, os.fstat(fh.fileno()).st_size - (1 << 20)))
                report = parse_report(fh.read().decode("utf-8", errors="replace"))
        except sandbox.SandboxError:
            report = {}
    written = [os.path.join(dp, f) for dp, _, fs in os.walk(outdir) for f in fs]
    return written, skipped, report


def lawful_purposes():
    return godot_pck.lawful_purposes()


REFUSED = ("Recovering a project serves understanding, interoperability and your own lost work, never "
           "shipping another game's code or assets as your own or opening a pack locked with a key that is "
           "not yours. Those are refused, the same as in the dossier.")


def main():
    ap = argparse.ArgumentParser(description="Godot scripts and scenes back to text with GDRE Tools (in the sandbox).")
    ap.add_argument("file", help="A pack (.pck or an exported game), or a loose .gdc / .scn / .res file.")
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--list", action="store_true", help="GDRE's own listing of the pack's files.")
    what.add_argument("--member", metavar="RES_PATH", help="Read one member of the pack (a .gdc script or a .scn/.res).")
    what.add_argument("--recover", metavar="OUTDIR", help="GATED: rebuild the whole project into OUTDIR.")
    ap.add_argument("--engine", help="Engine version for a loose .gdc, or to override the pack's (like 4.7.2).")
    ap.add_argument("--scripts-only", action="store_true", help="With --recover: scripts and project.godot only.")
    ap.add_argument("--purpose", help="Lawful basis for --recover.")
    ap.add_argument("--note", help="One concrete sentence naming the basis for --recover.")
    ap.add_argument("--max-mb", type=int, default=MAX_EXTRACT_BYTES >> 20,
                    help="With --recover: stop once this many MB are written (default %(default)s).")
    ap.add_argument("--timeout", type=int, default=None, help="Seconds before GDRE is stopped.")
    args = ap.parse_args()
    t = {} if args.timeout is None else {"timeout": args.timeout}

    try:
        if args.recover:
            written, skipped, report = recover(args.file, args.recover, args.purpose, args.note,
                                               scripts_only=args.scripts_only, max_bytes=args.max_mb << 20, **t)
            print(f"Recovered {len(written)} file(s) into {os.path.abspath(args.recover)}")
            for k, v in report.items():
                print(f"  {k}: {v}")
            if skipped:
                print(f"  (left behind {len(skipped)} non-regular file(s): {', '.join(skipped[:10])})")
        elif args.list:
            paths = list_files(args.file, **t)
            print("\n".join(paths))
            print(f"{len(paths)} file(s), as GDRE reads them", file=sys.stderr)
        elif args.member:
            text, table = from_member(args.file, args.member, engine=args.engine, **t)
            if table:
                print(f"decompiled with GDRE's {table} bytecode table", file=sys.stderr)
            sys.stdout.write(text)
        elif kind_of(args.file) == "script":
            if not args.engine:
                ap.error("a loose .gdc needs --engine (the engine version, as godot_pck.py reads it from its pack)")
            text, table = decompile(args.file, args.engine, **t)
            print(f"decompiled with GDRE's {table} bytecode table", file=sys.stderr)
            sys.stdout.write(text)
        else:
            sys.stdout.write(to_text(args.file, **t))
    except (GdreError, sandbox.SandboxError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"Read failed: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
