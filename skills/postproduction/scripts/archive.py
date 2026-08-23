#!/usr/bin/env python3
"""Versions and archive: keep the undo history, and delete only what you can restore.

Three separate faults live here, all of them expensive, all of them met on real
jobs, and each has a command.

  stage    ARCHIVE BEFORE YOU OVERWRITE. Every previous attempt is the undo
           history. Re-encoding straight over a delivery destroys the version
           somebody is comparing against, and a regenerated file only counts if
           it hashes to what the notes recorded.
  unlink   BREAK THE HARD LINKS FIRST. A new version set up with cp -al shares
           inodes with the delivered one, so any tool opening a file with mode
           'w' truncates the approved build THROUGH THE LINK. Auditing writers
           one at a time does not work: the one that did the damage was a tool
           that was only being CALLED.
  sweep    THREE GATES BEFORE ANY DELETION. Verify the survivors against their
           record; hash the condemned on the way out; prove each restore path
           exists. In that order, because if what you are keeping is not what
           you think it is, what you are deleting is not redundant, it is the
           last copy.
  links    What in this tree is a hard link or a symlink into somewhere else.
           A version folder can look self contained and be built entirely out
           of the previous version's plates.

Usage:
  python archive.py stage out/ --label v9
  python archive.py unlink film_v2/work
  python archive.py links film_v9/
  python archive.py sweep --keep-ledger live/SHA256.json --condemn old/*.mov
  python archive.py sweep --keep-ledger live/SHA256.json --condemn old/*.mov --execute
  (add --json for structured output)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import _common as C  # noqa: E402
import prove as PROVE  # noqa: E402


def stage(target, label=None, archive_root=None):
    """Copy a deliverable folder into a dated archive BEFORE anything overwrites it."""
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise NotADirectoryError(f"{target} is not a directory.")
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    label = label or os.path.basename(target.rstrip("/"))
    root = archive_root or os.path.join(os.path.dirname(target), "archive")
    dest = os.path.join(root, f"{label}_{stamp}")
    if os.path.exists(dest):
        raise FileExistsError(f"{dest} already exists. Refusing to write into an "
                              "existing archive: that is the fault this command "
                              "exists to prevent.")
    os.makedirs(root, exist_ok=True)
    shutil.copytree(target, dest, symlinks=True)
    rows = []
    for dirpath, _dirs, files in os.walk(dest):
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            if os.path.islink(p):
                continue
            rows.append({"path": p, "sha256": C.sha256_file(p),
                         "size": os.path.getsize(p)})
    ledger = os.path.join(dest, "SHA256.json")
    PROVE.write_ledger(rows, ledger)
    return {"source": target, "archive": dest, "files": len(rows),
            "ledger": ledger,
            "note": "Hashes recorded now. Without them a later regeneration is a "
                    "reconstruction, not the same file, and saying so is the "
                    "difference between a claim and a proof."}


def unlink(target):
    """Break hard links in a tree by replacing every multiply linked file.

    Do this to the whole folder before the first write. Do not audit writers.
    """
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise NotADirectoryError(f"{target} is not a directory.")
    broken, bytes_copied = [], 0
    for dirpath, _dirs, files in os.walk(target):
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.islink(p):
                continue
            st = os.stat(p, follow_symlinks=False)
            if st.st_nlink <= 1:
                continue
            tmp = p + ".unlink.tmp"
            shutil.copy2(p, tmp)
            os.replace(tmp, p)
            broken.append(os.path.relpath(p, target))
            bytes_copied += st.st_size
    return {"tree": target, "broken": len(broken), "files": broken[:200],
            "bytes_copied": bytes_copied,
            "verdict": (f"{len(broken)} file(s) no longer share an inode with "
                        "anything else" if broken else
                        "nothing in this tree was hard linked"),
            "note": "Read only trees such as plates and src can stay linked; it "
                    "is the working folder that gets written."}


def links(target):
    """Hard links and symlinks in a tree, and where they land."""
    target = os.path.abspath(target)
    hard, sym, outside = [], [], []
    for dirpath, _dirs, files in os.walk(target):
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.islink(p):
                real = os.path.realpath(p)
                sym.append({"link": os.path.relpath(p, target), "target": real})
                if not real.startswith(target + os.sep):
                    outside.append(real)
                continue
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            if st.st_nlink > 1:
                hard.append({"path": os.path.relpath(p, target),
                             "links": st.st_nlink, "size": st.st_size})
    roots = sorted({_common_root(target, p) for p in outside})
    return {"tree": target, "hard_linked": len(hard), "symlinks": len(sym),
            "symlinks_leaving_the_tree": len(outside),
            "external_roots": roots[:20],
            "hard_examples": hard[:20], "symlink_examples": sym[:20],
            "verdict": ("nothing in this tree depends on anywhere else"
                        if not hard and not outside else
                        f"{len(hard)} hard linked file(s) and {len(outside)} "
                        "symlink(s) landing outside the tree. Deleting the "
                        "folders those point into deletes part of THIS version."),
            "note": "Resolve symlinks before believing a dependency graph. A scan "
                    "that filters relative links reports 'depends on nothing' on "
                    "a tree that is mostly somebody else's plates."}


def _common_root(base, path):
    parts = path.split(os.sep)
    return os.sep.join(parts[:min(len(parts), base.count(os.sep) + 2)])


def sweep(keep_ledger, condemned, restore_map=None, execute=False,
          ledger_out=None):
    """The three gates. Dry run by default: nothing is deleted without --execute."""
    report = {"keep_ledger": os.path.abspath(keep_ledger) if keep_ledger else None,
              "condemned_requested": [os.path.abspath(p) for p in condemned],
              "executed": False, "gates": []}

    # Gate one: the survivors.
    if not keep_ledger:
        report["gates"].append({
            "gate": "survivors verified", "pass": False,
            "detail": "No ledger of what is being KEPT was given. Without it "
                      "there is no way to know the survivors are what they claim "
                      "to be, and a deletion cannot be called redundant."})
    else:
        verified = PROVE.verify_ledger(keep_ledger)
        report["survivors"] = verified
        report["gates"].append({
            "gate": "survivors verified", "pass": verified["failures"] == 0,
            "detail": verified["verdict"]})

    # Gate two: hash the condemned before they go.
    condemned_rows = []
    for p in report["condemned_requested"]:
        if not os.path.exists(p):
            condemned_rows.append({"path": p, "state": "already gone"})
            continue
        condemned_rows.append({"path": p, "sha256": C.sha256_file(p),
                               "size": os.path.getsize(p), "state": "on disk"})
    report["condemned"] = condemned_rows
    real = [r for r in condemned_rows if r["state"] == "on disk"]
    report["gates"].append({
        "gate": "condemned hashed", "pass": bool(real),
        "detail": f"{len(real)} file(s) hashed on the way out"
                  if real else "nothing to hash: none of these are on disk"})
    if ledger_out and real:
        PROVE.write_ledger(real, ledger_out)
        report["condemned_ledger"] = os.path.abspath(ledger_out)

    # Gate three: a restore path that exists.
    restore_map = restore_map or {}
    restore_rows = []
    for r in real:
        route = restore_map.get(r["path"]) or restore_map.get(
            os.path.basename(r["path"]))
        restore_rows.append({
            "path": r["path"], "restore_from": route,
            "exists": bool(route) and os.path.exists(route),
        })
    report["restore"] = restore_rows
    missing_route = [r for r in restore_rows if not r["exists"]]
    report["gates"].append({
        "gate": "restore path proved", "pass": not missing_route,
        "detail": ("every condemned file has a restore path that exists"
                   if not missing_route else
                   f"{len(missing_route)} file(s) have no proved restore path. "
                   "Give one with --restore-from, or accept in writing that "
                   "these are gone for good.")})

    # The trap: a file referenced by somebody else's dependency record.
    referenced = _referenced_elsewhere([r["path"] for r in real])
    report["referenced_elsewhere"] = referenced
    report["gates"].append({
        "gate": "not a live dependency", "pass": not referenced,
        "detail": ("no other ledger in the surrounding tree names these files"
                   if not referenced else
                   "these files are named as dependencies by another version's "
                   "records. A version number in a path is a LABEL, not a fact "
                   "about what is live.")})

    all_pass = all(g["pass"] for g in report["gates"])
    report["pass"] = all_pass
    if execute and all_pass:
        deleted = []
        for r in real:
            os.remove(r["path"])
            deleted.append(r["path"])
        report["deleted"] = deleted
        report["executed"] = True
        report["freed_bytes"] = sum(r["size"] for r in real)
    report["verdict"] = (
        ("deleted " + str(len(report.get("deleted", []))) + " file(s)")
        if report["executed"] else
        ("all gates pass. Re-run with --execute to delete." if all_pass else
         "STOP. At least one gate did not pass, and nothing was deleted."))
    return report


def _referenced_elsewhere(paths, depth=3):
    """Is any condemned file named as a dependency in another ledger nearby?

    Liveness lives in the dependency records, not in the folder name. One real
    master sitting in a folder named for an old version was the only graded copy
    on disk and was found only by reading a NEWER version's own hash file.
    """
    if not paths:
        return []
    roots = set()
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        for _ in range(depth):
            roots.add(d)
            d = os.path.dirname(d)
    names = {os.path.basename(p): p for p in paths}
    hits = []
    seen_files = set()
    for root in sorted(roots):
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if not (f.lower().startswith("sha") or f.lower().endswith(
                        (".sha256", ".sha256.txt", "_sha256.json"))
                        or f in ("SHA256.json", "SHA256.txt")):
                    continue
                fp = os.path.join(dirpath, f)
                if fp in seen_files:
                    continue
                seen_files.add(fp)
                try:
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                for name, full in names.items():
                    if name in text and os.path.dirname(fp) != os.path.dirname(full):
                        hits.append({"condemned": full, "named_in": fp})
    return hits


def main(argv=None):
    ap = C.parser_for(__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("stage", help="Archive a folder before overwriting it")
    st.add_argument("target")
    st.add_argument("--label")
    st.add_argument("--archive-root")
    C.add_json(st)

    un = sub.add_parser("unlink", help="Break hard links in a tree")
    un.add_argument("target")
    C.add_json(un)

    li = sub.add_parser("links", help="What this tree depends on")
    li.add_argument("target")
    C.add_json(li)

    sw = sub.add_parser("sweep", help="Delete only what can be restored")
    sw.add_argument("--keep-ledger")
    sw.add_argument("--condemn", nargs="+", required=True)
    sw.add_argument("--restore-from", action="append", default=[],
                    help="NAME=PATH, repeatable")
    sw.add_argument("--ledger-out", default=None,
                    help="Where to write the ledger of the condemned")
    sw.add_argument("--execute", action="store_true")
    C.add_json(sw)

    args = ap.parse_args(argv)

    if args.cmd == "stage":
        res = stage(args.target, args.label, args.archive_root)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['files']} file(s) copied to {r['archive']}"),
            print(f"  ledger {r['ledger']}"), print(f"  {r['note']}")))
    if args.cmd == "unlink":
        res = unlink(args.target)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['verdict']} ({r['bytes_copied'] / 1e9:.2f} GB copied)"),
            print(f"  {r['note']}")))
    if args.cmd == "links":
        res = links(args.target)
        return C.emit(res, args.json, lambda r: (
            print(f"  {r['hard_linked']} hard linked, {r['symlinks']} symlinks, "
                  f"{r['symlinks_leaving_the_tree']} leaving the tree"),
            [print(f"    external root: {p}") for p in r["external_roots"]],
            print(f"\n  {r['verdict']}"), print(f"  {r['note']}")))
    if args.cmd == "sweep":
        restore = {}
        for pair in args.restore_from:
            if "=" not in pair:
                raise ValueError("--restore-from wants NAME=PATH")
            k, v = pair.split("=", 1)
            restore[k] = v
        res = sweep(args.keep_ledger, args.condemn, restore, args.execute,
                    args.ledger_out)
        return C.emit(res, args.json, _print_sweep)
    return 0


def _print_sweep(r):
    for g in r["gates"]:
        print(f"  [{'pass' if g['pass'] else 'FAIL'}] {g['gate']}: {g['detail']}")
    for hit in r.get("referenced_elsewhere", []):
        print(f"    {os.path.basename(hit['condemned'])} is named in "
              f"{hit['named_in']}")
    print(f"\n  {r['verdict']}")
    if r.get("condemned_ledger"):
        print(f"  ledger of the condemned: {r['condemned_ledger']}")


if __name__ == "__main__":
    sys.exit(C.main_guard(main))
