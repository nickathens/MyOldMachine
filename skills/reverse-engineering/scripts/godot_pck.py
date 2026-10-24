#!/usr/bin/env python3
# Semicolon-chained binary-parse steps are this skill's house style (the donor
# repo exempts E701/E702/E741 under skills/; MOM lints skills/ globally, so the
# exemption is pinned per file here).
# ruff: noqa: E702
"""
Read a Godot engine pack (.pck, or one appended to an exported game's
executable) without running anything: the pack format and engine version, the
flags, and every file in the directory with its offset, size, md5 and flags.
Under the gate, pull the stored files out.

  python godot_pck.py game.pck                     # header + file table
  python godot_pck.py game.pck --json out.json      # the full listing as JSON
  python godot_pck.py game.x86_64                    # a pack appended to an executable
  python godot_pck.py game.pck --extract OUT --purpose own_work --note "..."

This is to a Godot pack what bin_triage.py is to an ELF: a bounded, pure-Python
reader in memory-safe code, safe to run on a file you do not trust. It produces
GROUND TRUTH (what the directory literally records) and nothing more. What a
packed `.gdc` script or a binary `.scn` scene *means* is a later step: raw bytes
come out here; turning compiled GDScript back into source is GDRE Tools' job
(see the games section of the skill), run in the sandbox.

Format (confirmed against godotengine/godot core/io/file_access_pack.*, the
version each release writes read from its own source, and every version proven
on a pack exported here by that editor):
  format 1  Godot 3.x     magic "GDPC" (u32 0x43504447), version, engine
                          major/minor/patch, 16 reserved u32, then the directory.
                          Each entry: a u32 path length (padded to 4), the path
                          ("res://..."), a u64 offset that is an ABSOLUTE position
                          in the file (Godot 3 adds the executable's length when it
                          embeds), a u64 size and a 16-byte md5; no flags at all.
  format 2  Godot 4.0-4.4 as 1, plus pack_flags and a u64 file-data base before the
                          16 reserved u32, and a u32 flags after each entry's md5;
                          offsets are relative to the base.
  format 3  Godot 4.5-4.6 as 2, but a u64 directory offset instead of the reserved
  format 4  Godot 4.7     words: the directory sits at the end, after the data.
  An encrypted directory (pack_flags bit 0) or an encrypted file (entry flags bit
  0) cannot be read without the project's key; both are reported, never guessed.

Extraction (`--extract`) is the gate, the same one carve.py uses: it writes real
files out, so it refuses without a lawful `--purpose` (the dossier's set) and a
one-sentence `--note`, stops once it has written `--max-mb`, and never writes
outside the output folder. It never serves ripping another game's assets to ship
or redistribute them; that is refused here the same way the dossier refuses it.
"""
import argparse
import hashlib
import json
import mmap
import os
import sys

COPY_CHUNK = 4 << 20       # bytes per read when copying/hashing a file body, so one slice of a
                           # near-cap file never pulls gigabytes into the bot's shared cgroup

MAGIC = b"GDPC"
V1, V2, V3, V4 = 1, 2, 3, 4
# Pack (directory) flags.
PACK_DIR_ENCRYPTED = 1 << 0
PACK_REL_FILEBASE = 1 << 1
PACK_SPARSE_BUNDLE = 1 << 2
# Per-file flags.
PACK_FILE_ENCRYPTED = 1 << 0
PACK_FILE_REMOVAL = 1 << 1
PACK_FILE_DELTA = 1 << 2

MAX_FILES = 2_000_000       # a hostile file_count cannot make an unbounded list
MAX_PATH = 8192             # one entry's path length, padded, is never longer than this
TRAILER = 12                # an embedded pack ends with u64 size + u32 magic
MAX_EXTRACT_BYTES = 2 * 1024 ** 3


class PckError(ValueError):
    pass


class Reader:
    """Absolute, bounds-checked little-endian reads over a bytes/mmap buffer.
    Raises PckError on any overrun, so a malformed field cannot walk off the end."""

    def __init__(self, data):
        self.d = data
        self.end = len(data)

    def _chk(self, off, n):
        if off < 0 or n < 0 or off + n > self.end:
            raise PckError(f"read of {n} byte(s) at {off} overruns the file (size {self.end})")

    def u32(self, off):
        self._chk(off, 4)
        return int.from_bytes(self.d[off:off + 4], "little")

    def u64(self, off):
        self._chk(off, 8)
        return int.from_bytes(self.d[off:off + 8], "little")

    def raw(self, off, n):
        self._chk(off, n)
        return bytes(self.d[off:off + n])


def locate_pack(r):
    """Where the pack starts: 0 for a standalone .pck, or the embedded offset when
    a pack is appended to an exported game's executable (Godot writes a trailer of
    a u64 size then the magic at the very end; pck_start = len - size - 12)."""
    if r.end >= 4 and r.raw(0, 4) == MAGIC:
        return 0, "standalone"
    if r.end >= TRAILER and r.raw(r.end - 4, 4) == MAGIC:
        ds = r.u64(r.end - TRAILER)
        start = r.end - ds - TRAILER
        if 0 <= start <= r.end - 4 and r.raw(start, 4) == MAGIC:
            return start, "embedded in executable"
    raise PckError("no Godot pack here: the magic 'GDPC' is neither at the start nor in an "
                   "end-of-file trailer (not a .pck, or a pack embedded in an unusual way)")


def parse(data):
    """Header and full directory. Pure ground truth; nothing about a file's meaning."""
    r = Reader(data)
    start, where = locate_pack(r)
    off = start + 4
    version = r.u32(off); off += 4
    if version not in (V1, V2, V3, V4):
        raise PckError(f"unsupported pack format version {version} (this reader handles 1 to 4: Godot 3 to 4.7)")
    major, minor, patch = r.u32(off), r.u32(off + 4), r.u32(off + 8); off += 12
    if version == V1:
        # Godot 3: no flags and no file base; offsets are absolute positions in the file.
        pack_flags, file_base = 0, 0
    else:
        pack_flags = r.u32(off); off += 4
        # v3/v4 always store file_base relative to the pack start; v2 only when the flag is set.
        file_base = r.u64(off); off += 8
        rel_base = version >= V3 or (pack_flags & PACK_REL_FILEBASE)
        file_base = file_base + start if rel_base else file_base

    dir_off = None
    if version >= V3:
        dir_off = r.u64(off) + start; off += 8
        # v4 salt (32 bytes) only when a sparse bundle and an encrypted directory meet.
        if version >= V4 and (pack_flags & PACK_SPARSE_BUNDLE) and (pack_flags & PACK_DIR_ENCRYPTED):
            off += 32
    else:
        off += 16 * 4          # v1/v2: 16 reserved u32, then the directory follows inline
        dir_off = off

    rep = {
        "pack_start": start, "located": where, "format_version": version,
        "engine_version": f"{major}.{minor}.{patch}", "pack_flags": pack_flags,
        "dir_encrypted": bool(pack_flags & PACK_DIR_ENCRYPTED),
        "sparse_bundle": bool(pack_flags & PACK_SPARSE_BUNDLE),
        "file_base": file_base, "directory_offset": dir_off, "size": r.end,
        "files": [], "truncated": False, "errors": [],
    }
    if rep["dir_encrypted"]:
        # The directory bytes are wrapped in Godot's FileAccessEncrypted; without the
        # project's script_encryption_key the file list cannot be read, only reported.
        rep["errors"].append("directory is encrypted (pack_flags bit 0); the file list needs the project's "
                             "encryption key and is not readable here")
        return rep
    _read_directory(r, dir_off, file_base, rep, entry_flags=version >= V2)
    return rep


def _read_directory(r, dir_off, file_base, rep, entry_flags=True):
    p = dir_off
    count = r.u32(p); p += 4
    if count > MAX_FILES:
        rep["errors"].append(f"directory claims {count} files; only the first {MAX_FILES} are read")
        rep["truncated"] = True
        count = MAX_FILES
    for i in range(count):
        try:
            sl = r.u32(p); p += 4
            if sl > MAX_PATH:
                rep["errors"].append(f"entry {i}: path length {sl} over the {MAX_PATH} cap; stopping")
                rep["truncated"] = True
                break
            path = r.raw(p, sl).rstrip(b"\x00").decode("utf-8", errors="replace"); p += sl
            ofs = r.u64(p); size = r.u64(p + 8); p += 16
            md5 = r.raw(p, 16); p += 16
            flags = 0
            if entry_flags:                       # format 1 entries end at the md5
                flags = r.u32(p); p += 4
        except PckError as e:
            rep["errors"].append(f"entry {i}: {e}")
            rep["truncated"] = True
            break
        abs_off = file_base + ofs
        rep["files"].append({
            "path": path, "offset": abs_off, "size": size, "md5": md5.hex(),
            "encrypted": bool(flags & PACK_FILE_ENCRYPTED),
            "removal": bool(flags & PACK_FILE_REMOVAL),
            "delta": bool(flags & PACK_FILE_DELTA),
            "in_bounds": 0 <= abs_off and abs_off + size <= r.end,
        })
    return rep


def _safe_rel(path):
    """A pack path ('res://a/b.gd', or 'a/b.gd') turned into a relative path that
    cannot escape the output folder: the scheme is stripped, the path is forced
    relative, and '..' or an empty result is refused."""
    p = path.split("://", 1)[1] if "://" in path else path
    p = p.replace("\\", "/").lstrip("/")
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise PckError(f"unsafe path {path!r} in the pack; not extracted")
    return os.path.join(*parts)


def extract(data, outdir, purpose, note, max_bytes=None):
    """GATED. Write the stored (unencrypted, in-bounds) files out to outdir, which
    must be new or empty. Stops once max_bytes are written. Returns
    (written paths, [(path, reason) skipped]). Encrypted files are skipped (their
    bytes are meaningless without the key); an out-of-bounds entry is skipped."""
    purposes = lawful_purposes()
    if purpose not in purposes:
        raise PckError(f"--purpose must be one of {', '.join(purposes)}. {REFUSED}")
    if not note or not note.strip():
        raise PckError(f"--note must name the concrete basis in one sentence. {REFUSED}")
    if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        raise PckError(f"--extract needs a new or empty folder; {outdir} is not one")
    cap = MAX_EXTRACT_BYTES if max_bytes is None else max_bytes
    rep = parse(data)
    if rep["dir_encrypted"]:
        raise PckError("the directory is encrypted; nothing can be extracted without the project's key")
    os.makedirs(outdir, exist_ok=True)
    root = os.path.realpath(outdir)
    written, skipped, total = [], [], 0
    for f in rep["files"]:
        if f["encrypted"]:
            skipped.append((f["path"], "encrypted"))
            continue
        if f["removal"]:
            skipped.append((f["path"], "removal marker, no data"))
            continue
        if not f["in_bounds"]:
            skipped.append((f["path"], "offset/size outside the file"))
            continue
        if total + f["size"] > cap:
            skipped.append((f["path"], f"would pass the {cap >> 20} MB cap"))
            continue
        try:
            rel = _safe_rel(f["path"])
        except PckError as e:
            skipped.append((f["path"], str(e)))
            continue
        dst = os.path.join(root, rel)
        if os.path.realpath(dst) != dst or not dst.startswith(root + os.sep):
            skipped.append((f["path"], "resolves outside the output folder"))
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        pos, remaining = f["offset"], f["size"]
        with open(dst, "wb") as fh:
            while remaining:
                n = min(COPY_CHUNK, remaining)
                fh.write(data[pos:pos + n])
                pos += n
                remaining -= n
        total += f["size"]
        written.append(dst)
    return written, skipped


def lawful_purposes():
    """The allowed purpose set, read from the dossier schema so there is one source."""
    schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossier-schema.json")
    with open(schema, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]


REFUSED = ("Extraction never serves ripping another game's assets (models, textures, audio, scripts) out of "
           "its pack to ship or redistribute them, or opening a pack locked with a key that is not yours. "
           "Those are refused, the same as in the dossier.")


def verify_md5(data, f):
    """Re-hash a file's bytes and compare to the directory's md5 (ground-truth check)."""
    if not f["in_bounds"] or f["encrypted"]:
        return None
    h = hashlib.md5()
    pos, remaining = f["offset"], f["size"]
    while remaining:
        n = min(COPY_CHUNK, remaining)
        h.update(data[pos:pos + n])
        pos += n
        remaining -= n
    return h.hexdigest() == f["md5"]


def main():
    ap = argparse.ArgumentParser(description="Read a Godot engine pack (.pck or one embedded in an executable).")
    ap.add_argument("file", help="Path to the .pck or the exported game executable.")
    ap.add_argument("--json", metavar="OUT", help="Write the full listing as JSON here.")
    ap.add_argument("--extract", metavar="OUTDIR", help="GATED: extract the stored files to OUTDIR.")
    ap.add_argument("--purpose", help="Lawful basis for extraction (required by --extract).")
    ap.add_argument("--note", help="One concrete sentence naming the basis (required by --extract).")
    ap.add_argument("--max-mb", type=int, default=MAX_EXTRACT_BYTES >> 20,
                    help="With --extract: stop once this many MB are written (default %(default)s).")
    ap.add_argument("--verify-md5", action="store_true", help="Re-hash each file and check it against the directory.")
    ap.add_argument("--limit", type=int, default=60, help="Rows in the console listing.")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.file, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size < 4:
                print("File is too small to be a pack.", file=sys.stderr)
                sys.exit(2)
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                if args.extract:
                    written, skipped = extract(mm, args.extract, args.purpose, args.note,
                                               max_bytes=args.max_mb << 20)
                    print(f"Extracted {len(written)} file(s) to {os.path.abspath(args.extract)}:")
                    for p in written[:args.limit]:
                        print(f"  {p}")
                    if skipped:
                        print(f"\nSkipped {len(skipped)}:")
                        for path, why in skipped[:args.limit]:
                            print(f"  {path}: {why}")
                    sys.exit(0)
                rep = parse(mm)
                _print(rep, args, mm)
                if args.json:
                    with open(args.json, "w", encoding="utf-8") as out:
                        json.dump(rep, out, indent=1)
                    print(f"Wrote {args.json}")
            finally:
                mm.close()
    except PckError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"Read failed: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def _print(rep, args, data):
    print(f"Godot pack, format v{rep['format_version']}, engine {rep['engine_version']} ({rep['located']})")
    print(f"  pack starts at {rep['pack_start']}, file base {rep['file_base']}, directory at "
          f"{rep['directory_offset']}, {rep['size']} bytes")
    if rep["dir_encrypted"]:
        print("  directory is ENCRYPTED; the file list needs the project's key")
    if rep["sparse_bundle"]:
        print("  sparse bundle (files live outside this pack)")
    files = rep["files"]
    print(f"  {len(files)} file(s)" + (" (list cut short)" if rep["truncated"] else "") + ":")
    for f in files[:args.limit]:
        tags = [t for t in ("encrypted" if f["encrypted"] else "", "removal" if f["removal"] else "",
                            "delta" if f["delta"] else "", "" if f["in_bounds"] else "OUT-OF-BOUNDS") if t]
        extra = f"  [{', '.join(tags)}]" if tags else ""
        line = f"    {f['path']}  @{f['offset']} {f['size']}B{extra}"
        if args.verify_md5:
            ok = verify_md5(data, f)
            line += "  md5:" + ("ok" if ok else "MISMATCH" if ok is False else "n/a")
        print(line)
    for e in rep["errors"]:
        print(f"  error: {e}")


if __name__ == "__main__":
    main()
