#!/usr/bin/env python3
"""
Read an Electron app archive (.asar): the file tree an app like a desktop client
bundles its JavaScript into, with each file's offset, size and integrity hash.
Under the gate, pull the files out so the web tools (fingerprint, libscan,
unminify) can run on the app's real code.

  python asar.py app.asar                      # the file tree, offsets and sizes
  python asar.py app.asar --json out.json       # the full listing as JSON
  python asar.py app.asar --verify              # re-hash each file against its SHA-256
  python asar.py app.asar --extract OUT --purpose interoperability --note "..."

Pure Python, bounded and memory-safe like bin_triage and godot_pck, so it is
safe on a file you do not trust; it is ground truth about the archive's header
and nothing more. Many desktop apps (the kind built on Electron) are a web app
packed into one of these; unpacking it is what lets the Tier 1 web tools read
the app's code.

Format (an asar is a Pickle header then concatenated file data): u32 = 4, then
headerSize (u32), then the header pickle (u32 payload size, u32 JSON length, the
JSON, padded). File data begins at 8 + headerSize; each entry's "offset" is a
string relative to there. A file marked "unpacked" lives outside the archive
(in app.asar.unpacked), and a "link" entry is a symlink; both are reported, not
read. Confirmed against @electron/asar output.

Extraction (`--extract`) is the gate carve.py uses: a lawful `--purpose` and a
one-sentence `--note`, a size cap, no write outside the output folder. It serves
understanding and interoperability, never shipping another app's code or assets
as your own.
"""
import argparse
import hashlib
import json
import mmap
import os
import sys

COPY_CHUNK = 4 << 20
MAX_FILES = 2_000_000
MAX_DEPTH = 256
MAX_HEADER = 256 << 20       # a sane ceiling for the JSON header, so a bad size cannot allocate wildly


class AsarError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.d = data
        self.end = len(data)

    def u32(self, off):
        if off < 0 or off + 4 > self.end:
            raise AsarError(f"read at {off} overruns the file (size {self.end})")
        return int.from_bytes(self.d[off:off + 4], "little")


def parse(data):
    """Header + the whole file tree, flattened. Ground truth only."""
    r = Reader(data)
    if r.u32(0) != 4:
        raise AsarError("not an asar archive: the first word is not the pickle framing (4)")
    header_size = r.u32(4)
    json_len = r.u32(12)
    if header_size <= 0 or header_size > MAX_HEADER or 16 + json_len > r.end or json_len > MAX_HEADER:
        raise AsarError(f"asar header size {header_size}/{json_len} is out of range for a {r.end}-byte file")
    try:
        header = json.loads(bytes(data[16:16 + json_len]).decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise AsarError(f"asar header is not valid JSON: {e}")
    if not isinstance(header, dict) or "files" not in header:
        raise AsarError("asar header has no 'files' tree")
    data_start = 8 + header_size
    rep = {"size": r.end, "header_size": header_size, "data_start": data_start,
           "files": [], "truncated": False, "errors": []}
    _walk(header.get("files"), "", data_start, r.end, rep)   # .get: the guard above already rejected a missing tree
    return rep


def _walk(node, prefix, data_start, total, rep):
    """Iterative walk of the files tree (a depth cap guards against a crafted deep
    tree); appends one flat entry per file."""
    stack = [(prefix, node, 1)]
    while stack:
        pfx, files, depth = stack.pop()
        if not isinstance(files, dict):
            rep["errors"].append(f"{pfx or '/'}: 'files' is not an object")
            continue
        if depth > MAX_DEPTH:
            rep["errors"].append(f"{pfx}: tree deeper than {MAX_DEPTH}; not followed")
            rep["truncated"] = True
            continue
        for name, meta in files.items():
            if len(rep["files"]) >= MAX_FILES:
                rep["truncated"] = True
                return
            path = f"{pfx}/{name}" if pfx else name
            if not isinstance(meta, dict):
                rep["errors"].append(f"{path}: entry is not an object")
                continue
            if "files" in meta:                       # a directory
                stack.append((path, meta["files"], depth + 1))
                continue
            if "link" in meta:                        # a symlink entry: a target, not data
                rep["files"].append({"path": path, "link": meta["link"], "size": 0, "offset": None,
                                     "unpacked": False, "executable": bool(meta.get("executable")),
                                     "integrity": None, "in_bounds": True})
                continue
            unpacked = bool(meta.get("unpacked"))
            try:
                size = int(meta.get("size", 0))
                offset = None if unpacked else data_start + int(meta["offset"])
            except (KeyError, ValueError, TypeError):
                rep["errors"].append(f"{path}: bad size/offset")
                continue
            integ = (meta.get("integrity") or {}).get("hash")
            rep["files"].append({
                "path": path, "size": size, "offset": offset, "unpacked": unpacked,
                "executable": bool(meta.get("executable")), "link": None, "integrity": integ,
                "in_bounds": unpacked or (offset is not None and 0 <= offset and offset + size <= total),
            })


def verify_sha256(data, entry):
    """Re-hash a file's bytes and compare to the archive's integrity hash. Returns
    None when there is nothing to check (unpacked, a link, no hash, out of bounds)."""
    if entry["unpacked"] or entry["link"] or not entry["integrity"] or not entry["in_bounds"]:
        return None
    h = hashlib.sha256()
    pos, remaining = entry["offset"], entry["size"]
    while remaining:
        n = min(COPY_CHUNK, remaining)
        h.update(data[pos:pos + n])
        pos += n
        remaining -= n
    return h.hexdigest() == entry["integrity"]


def _safe_rel(path):
    p = path.replace("\\", "/").lstrip("/")
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise AsarError(f"unsafe path {path!r} in the archive; not extracted")
    return os.path.join(*parts)


def extract(data, outdir, purpose, note, max_bytes=None):
    """GATED. Write the in-bounds, in-archive files out to outdir (new or empty).
    Unpacked files (data outside the archive) and symlink entries are skipped and
    named. Returns (written paths, [(path, reason) skipped])."""
    purposes = lawful_purposes()
    if purpose not in purposes:
        raise AsarError(f"--purpose must be one of {', '.join(purposes)}. {REFUSED}")
    if not note or not note.strip():
        raise AsarError(f"--note must name the concrete basis in one sentence. {REFUSED}")
    if os.path.lexists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        raise AsarError(f"--extract needs a new or empty folder; {outdir} is not one")
    cap = (2 * 1024 ** 3) if max_bytes is None else max_bytes
    rep = parse(data)
    os.makedirs(outdir, exist_ok=True)
    root = os.path.realpath(outdir)
    written, skipped, total = [], [], 0
    for f in rep["files"]:
        if f["link"]:
            skipped.append((f["path"], f"symlink to {f['link']}"))
            continue
        if f["unpacked"]:
            skipped.append((f["path"], "unpacked (data is outside the archive)"))
            continue
        if f["offset"] is None:                   # backstop: no data in the archive (unpacked/link)
            skipped.append((f["path"], "no data in the archive"))
            continue
        if not f["in_bounds"]:
            skipped.append((f["path"], "offset/size outside the file"))
            continue
        if total + f["size"] > cap:
            skipped.append((f["path"], f"would pass the {cap >> 20} MB cap"))
            continue
        try:
            rel = _safe_rel(f["path"])
        except AsarError as e:
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
    schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossier-schema.json")
    with open(schema, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]


REFUSED = ("Extraction serves understanding an app's own code and interoperability, never shipping "
           "another app's code or assets as your own or defeating a licence. Those are refused, the "
           "same as in the dossier.")


def main():
    ap = argparse.ArgumentParser(description="Read an Electron .asar archive.")
    ap.add_argument("file", help="Path to the .asar.")
    ap.add_argument("--json", metavar="OUT", help="Write the full listing as JSON here.")
    ap.add_argument("--extract", metavar="OUTDIR", help="GATED: extract the files to OUTDIR.")
    ap.add_argument("--purpose", help="Lawful basis for extraction (required by --extract).")
    ap.add_argument("--note", help="One concrete sentence naming the basis (required by --extract).")
    ap.add_argument("--max-mb", type=int, default=2048, help="With --extract: stop once this many MB are written.")
    ap.add_argument("--verify", action="store_true", help="Re-hash each file against its SHA-256 integrity hash.")
    ap.add_argument("--limit", type=int, default=80, help="Rows in the console listing.")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"No such file: {args.file}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(args.file, "rb") as fh:
            if os.fstat(fh.fileno()).st_size < 16:
                print("File is too small to be an asar.", file=sys.stderr)
                sys.exit(2)
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                if args.extract:
                    written, skipped = extract(mm, args.extract, args.purpose, args.note, max_bytes=args.max_mb << 20)
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
    except AsarError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print(f"Read failed: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def _print(rep, args, data):
    print(f"Electron asar, {rep['size']} bytes, header {rep['header_size']} B, data at {rep['data_start']}")
    files = rep["files"]
    print(f"  {len(files)} file(s)" + (" (cut short)" if rep["truncated"] else "") + ":")
    for f in files[:args.limit]:
        tags = [t for t in ("unpacked" if f["unpacked"] else "", "exec" if f["executable"] else "",
                            f"link->{f['link']}" if f["link"] else "",
                            "" if f["in_bounds"] else "OUT-OF-BOUNDS") if t]
        extra = f"  [{', '.join(tags)}]" if tags else ""
        line = f"    {f['path']}  {f['size']}B" + (f" @{f['offset']}" if f["offset"] is not None else "") + extra
        if args.verify:
            ok = verify_sha256(data, f)
            line += "  sha256:" + ("ok" if ok else "MISMATCH" if ok is False else "n/a")
        print(line)
    for e in rep["errors"]:
        print(f"  error: {e}")


if __name__ == "__main__":
    main()
