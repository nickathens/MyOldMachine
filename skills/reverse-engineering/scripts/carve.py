#!/usr/bin/env python3
"""
Tier 3: find what is hidden inside a blob (a firmware image, a game asset pack,
a container file) and, under the gate, pull the pieces out.

  python carve.py blob.bin                      # pure-Python signature scan (always safe)
  python carve.py blob.bin --binwalk            # binwalk's signature scan, in the sandbox
  python carve.py blob.bin --binwalk --entropy  # + an entropy profile (packed/encrypted hints)
  python carve.py blob.bin --extract OUTDIR --purpose format_understanding --note "..."

Two scanners, one gate:

- The default scan is a bounded, pure-Python reader (mmap, no copy into memory),
  so it is safe to run on anything, including a file you do not trust. It reports
  every known magic it finds and the byte offset, and nothing more: the presence
  of a signature is GROUND TRUTH; what the embedded thing IS remains a hypothesis
  until it is read.
- `--binwalk` runs binwalk inside sandbox.py's re-carve image (no network,
  read-only root, non-root, memory capped), for its far larger signature set.

Extraction (`--extract`) is the gate. It writes real files out of the blob, so
it refuses unless you name a lawful `--purpose` (the same set the dossier
allows, read from dossier-schema.json) and a one-sentence `--note`. Extraction
NEVER serves pulling copyrighted assets out to ship or redistribute them; that
is refused here the same way the dossier refuses it. The gate cannot judge your
honesty, only make you state the basis out loud, exactly like the dossier.
"""
import argparse
import json
import mmap
import os
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402

MAX_HITS_PER_SIG = 256
MAX_HITS_TOTAL = 8192
MAX_CANDIDATES_PER_SIG = 100_000   # validator rejections count too: 50 MB of 'MZ' once took 18 s for 0 hits
MAX_EXTRACT_BYTES = 2 * 1024 ** 3  # what one extraction may write; a 1 MB zip once wrote 1 GB in 9 s


def _pe_validator(mm, off, size):
    """An 'MZ' is only a PE if e_lfanew points at a 'PE\\0\\0' signature. Without
    this, the two-byte 'MZ' matches plain text and data constantly."""
    if off + 0x40 > size:
        return None
    lfanew = int.from_bytes(mm[off + 0x3C:off + 0x40], "little")
    p = off + lfanew
    if 0 < lfanew < size and p + 4 <= size and mm[p:p + 4] == b"PE\x00\x00":
        return "DOS/PE image"
    return None


def _jpeg_validator(mm, off, size):
    """FF D8 FF is three bytes, so blind it turns up once per 16 MB of random or
    compressed data. A real JPEG goes on with a marker and a segment whose length
    lands on the next marker's FF."""
    if off + 6 > size or not (0xC0 <= mm[off + 3] <= 0xFE):
        return None
    end = off + 4 + int.from_bytes(mm[off + 4:off + 6], "big")
    return "JPEG image" if end < size and mm[end] == 0xFF else None


def _gzip_validator(mm, off, size):
    """1F 8B 08 is three bytes too: the header goes on with flags whose top three bits
    are clear, an extra-flags byte of 0, 2 or 4, and a known OS byte (RFC 1952)."""
    if off + 10 > size or mm[off + 3] & 0xE0 or mm[off + 8] not in (0, 2, 4):
        return None
    return "gzip" if mm[off + 9] <= 13 or mm[off + 9] == 255 else None


def _bzip2_validator(mm, off, size):
    """'BZh', a block size digit 1 to 9, then the block magic (pi, '1AY&SY'). The old
    single magic 'BZh91' found only files packed at block size 9."""
    if off + 10 <= size and mm[off + 3] in b"123456789" and mm[off + 4:off + 10] == b"1AY&SY":
        return "bzip2"
    return None


# (magic bytes, label, validator). Oriented at the formats a firmware image or a
# game asset pack is made of. A hit is ground truth that the bytes are there;
# naming the embedded object is a hypothesis until it is read. A magic of four or
# more distinctive bytes stands alone (once per 4 GB of random data); a shorter or
# low-entropy one (MZ, JPEG, gzip, bzip2's 'BZh', TrueType) is validated by the
# structure after it or pinned to a fixed offset (the Tier 1 lesson: a short
# pattern read blind is noise; 3-byte JPEG and gzip magics fired 8 times in 64 MB
# of random bytes before they were validated).
SIGNATURES = [
    (b"\x7fELF", "ELF binary", None),
    (b"MZ", "DOS/PE image", _pe_validator),
    (b"\xfe\xed\xfa\xce", "Mach-O (32)", None), (b"\xfe\xed\xfa\xcf", "Mach-O (64)", None),
    (b"\xca\xfe\xba\xbe", "Mach-O fat / Java class", None),
    (b"PK\x03\x04", "ZIP (or zip-based: jar, apk, docx, asset pack)", None),
    (b"PK\x05\x06", "ZIP end-of-central-directory (empty/partial zip)", None),
    (b"\x1f\x8b\x08", "gzip", _gzip_validator),
    (b"BZh", "bzip2", _bzip2_validator),
    (b"\xfd7zXZ\x00", "xz", None),
    (b"7z\xbc\xaf\x27\x1c", "7-zip", None),
    (b"\x28\xb5\x2f\xfd", "zstandard", None),
    (b"\x04\x22\x4d\x18", "lz4 frame", None),
    (b"Rar!\x1a\x07", "RAR", None),
    (b"\x89PNG\r\n\x1a\n", "PNG image", None),
    (b"\xff\xd8\xff", "JPEG image", _jpeg_validator),
    (b"GIF87a", "GIF image", None), (b"GIF89a", "GIF image", None),
    (b"RIFF", "RIFF (WAV/AVI/WebP: subtype at +8)", None),
    (b"OggS", "Ogg stream", None),
    (b"fLaC", "FLAC audio", None),
    (b"%PDF-", "PDF document", None),
    (b"SQLite format 3\x00", "SQLite database", None),
    (b"DDS |", "DDS texture", None),
    (b"\xabKTX 11\xbb\r\n\x1a\n", "KTX texture", None),
    (b"\xabKTX 20\xbb\r\n\x1a\n", "KTX2 texture", None),
    (b"glTF", "glTF binary model", None),
    (b"UnityFS", "Unity asset bundle", None),
    (b"OTTO", "OpenType (CFF) font", None),
    (b"wOF2", "WOFF2 font", None), (b"wOFF", "WOFF font", None),
    (b"\xed\xab\xee\xdb", "RPM package", None),
    (b"hsqs", "SquashFS (LE)", None), (b"sqsh", "SquashFS (BE)", None),
    (b"-rom1fs-", "romfs", None),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE compound (legacy MS Office)", None),
]
# Magic only meaningful pinned to a fixed offset, where searching blind would be noise.
AT_OFFSET = [(257, b"ustar", "tar archive"), (0, b"\x00\x01\x00\x00\x00", "TrueType font")]


def scan(path):
    """Every known signature in the file, with its byte offset. mmap-backed, so a
    multi-gigabyte blob is paged by the OS and never copied into the bot's memory.
    A signature that stopped early (its hit cap, or its candidate budget when a
    validator keeps saying no) is named in `capped` and sets `truncated`, so a
    cut list never reads as a complete one."""
    size = os.path.getsize(path)
    hits, capped = [], []
    if size == 0:
        return {"size": 0, "hits": [], "truncated": False, "capped": []}
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for magic, label, validator in SIGNATURES:
                start, found, seen = 0, 0, 0
                while True:
                    i = mm.find(magic, start)
                    if i == -1:
                        break
                    if found >= MAX_HITS_PER_SIG or seen >= MAX_CANDIDATES_PER_SIG or len(hits) >= MAX_HITS_TOTAL:
                        capped.append(label)       # one more is there, and it was not looked at
                        break
                    start, seen = i + 1, seen + 1
                    detail = label
                    if validator is not None:
                        detail = validator(mm, i, size)
                        if detail is None:
                            continue
                    elif magic == b"RIFF" and i + 12 <= size:
                        detail = f"RIFF/{mm[i + 8:i + 12].decode('latin-1').strip()}"
                    hits.append({"offset": i, "magic": magic.hex(), "type": detail})
                    found += 1
            for off, magic, label in AT_OFFSET:
                if off + len(magic) <= size and mm[off:off + len(magic)] == magic:
                    hits.append({"offset": off, "magic": magic.hex(), "type": label})
        finally:
            mm.close()
    hits.sort(key=lambda h: h["offset"])
    return {"size": size, "hits": hits, "truncated": bool(capped), "capped": sorted(set(capped))}


def binwalk_scan(path, entropy=False, timeout=300):
    flags = ["-E"] if entropy else []
    with tempfile.TemporaryDirectory(prefix="re-carve-") as work:
        in_dir = os.path.join(work, "in")
        os.makedirs(in_dir)
        shutil.copyfile(path, os.path.join(in_dir, "target"))
        image = sandbox.ensure_image(sandbox.CARVE_DOCKERFILE)
        res = sandbox.run(["binwalk", *flags, "/in/target"], image=image, ro=[f"{in_dir}:/in"],
                          memory="2g", timeout=timeout)
    if res["timed_out"]:
        raise CarveError(f"binwalk timed out after {timeout} s")
    if res["returncode"] != 0 and not res["stdout"].strip():
        raise CarveError("binwalk failed: " + res["stderr"][-300:])
    return res["stdout"]


def lawful_purposes():
    """The allowed purpose set, read from the dossier schema so there is one source."""
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossier-schema.json")
    with open(schema_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return (doc["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"])


REFUSED = ("Extraction never serves pulling copyrighted assets (models, textures, audio, fonts) out of a "
           "pack to ship or redistribute them, defeating copy protection, or anything whose honest basis "
           "is 'to avoid paying for it'. Those are refused, the same as in the dossier.")


def _drop_escaping_links(outdir):
    """Remove every link under outdir whose target lies outside it; returns their paths.
    binwalk already points such links at /dev/null (kept: they read as empty), but a
    compromised extractor could leave one to a host file, which a later read would follow."""
    root = os.path.realpath(outdir)
    dropped = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if (os.path.islink(p) and os.readlink(p) != "/dev/null"
                    and not os.path.realpath(p).startswith(root + os.sep)):
                os.unlink(p)
                dropped.append(p)
    return dropped


def binwalk_extract(path, outdir, purpose, note, recursive=False, timeout=600, max_bytes=None):
    """GATED. Extract embedded files with binwalk into outdir, which must be new or empty
    (it is mounted writable into the sandbox). Refuses without a lawful purpose and a note,
    and stops once the extraction has written max_bytes (default MAX_EXTRACT_BYTES), so an
    archive bomb cannot fill the disk. Returns (stdout, [extracted paths])."""
    purposes = lawful_purposes()
    if purpose not in purposes:
        raise CarveError(f"--purpose must be one of {', '.join(purposes)}. {REFUSED}")
    if not note or not note.strip():
        raise CarveError(f"--note must name the concrete basis in one sentence. {REFUSED}")
    if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        raise CarveError(f"--extract needs a new or empty folder; {outdir} is not one (it would be mounted "
                         f"writable into the sandbox, and what it holds would be listed as extracted)")
    cap = MAX_EXTRACT_BYTES if max_bytes is None else max_bytes
    os.makedirs(outdir, exist_ok=True)
    flags = ["-e", "-C", "/out"] + (["-M"] if recursive else [])
    with tempfile.TemporaryDirectory(prefix="re-carve-") as work:
        in_dir = os.path.join(work, "in")
        os.makedirs(in_dir)
        shutil.copyfile(path, os.path.join(in_dir, "target"))
        image = sandbox.ensure_image(sandbox.CARVE_DOCKERFILE)
        res = sandbox.run(["binwalk", *flags, "/in/target"], image=image,
                          ro=[f"{in_dir}:/in"], rw=[f"{outdir}:/out"], memory="2g", timeout=timeout,
                          with_passwd=True, max_write=cap)
    dropped = _drop_escaping_links(outdir)
    if dropped:
        print(f"Removed {len(dropped)} link(s) that pointed outside {outdir}", file=sys.stderr)
    if res["timed_out"]:
        raise CarveError(f"binwalk timed out after {timeout} s")
    if res.get("over_cap"):
        raise CarveError(f"extraction stopped: it passed the {cap >> 20} MB cap or 200,000 files (an archive "
                         f"bomb, or raise --max-mb); "
                         f"what it wrote before the stop is in {outdir}")
    extracted, special = [], []
    for root, _dirs, files in os.walk(outdir):
        for f in files:
            p = os.path.join(root, f)
            # A tar can carry pipes and the extractor makes them; reading one blocks for ever.
            (extracted if os.path.islink(p) or stat.S_ISREG(os.lstat(p).st_mode) else special).append(p)
    if special:
        print(f"Left out of the list, never read them: {len(special)} pipe(s) or other special file(s) in "
              f"{outdir}: {', '.join(sorted(special)[:20])}", file=sys.stderr)
    return res["stdout"], sorted(extracted)


class CarveError(RuntimeError):
    pass


def main():
    ap = argparse.ArgumentParser(description="Find (and under the gate, extract) files embedded in a blob.")
    ap.add_argument("file", help="Path to the blob.")
    ap.add_argument("--binwalk", action="store_true", help="Use binwalk's signature scan (in the sandbox).")
    ap.add_argument("--entropy", action="store_true", help="With --binwalk: add an entropy profile.")
    ap.add_argument("--extract", metavar="OUTDIR", help="GATED: extract embedded files to OUTDIR.")
    ap.add_argument("--recursive", action="store_true", help="With --extract: recurse into extracted files.")
    ap.add_argument("--purpose", help="Lawful basis for extraction (required by --extract).")
    ap.add_argument("--note", help="One concrete sentence naming the basis (required by --extract).")
    ap.add_argument("--max-mb", type=int, default=MAX_EXTRACT_BYTES >> 20,
                    help="With --extract: stop once this many MB are written (default %(default)s).")
    ap.add_argument("--out", help="Write the scan result as JSON here (pure-Python scan only).")
    ap.add_argument("--limit", type=int, default=80, help="Rows in the console summary.")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.extract:
            out, files = binwalk_extract(args.file, args.extract, args.purpose, args.note, recursive=args.recursive,
                                         max_bytes=args.max_mb << 20)
            print(out)
            print(f"\nExtracted {len(files)} file(s) to {os.path.abspath(args.extract)}:")
            for f in files[:args.limit]:
                print(f"  {f}")
        elif args.binwalk:
            print(binwalk_scan(args.file, entropy=args.entropy))
        else:
            rep = scan(args.file)
            print(f"{rep['size']} bytes, {len(rep['hits'])} signature hit(s)"
                  + (f" (cut short, more not looked at: {', '.join(rep['capped'])})" if rep["truncated"] else "")
                  + ":")
            for h in rep["hits"][:args.limit]:
                print(f"  @{h['offset']:<12} {h['type']}")
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(rep, fh, indent=1)
                print(f"Wrote {args.out}")
    except CarveError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except sandbox.SandboxError as e:
        print(f"sandbox: {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
