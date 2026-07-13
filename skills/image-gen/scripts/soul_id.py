#!/usr/bin/env python3
"""Manage Higgsfield Soul character references (custom-trained identities).

Train a character once from 5-20 images, then reuse that identity for consistent
stills and video across a whole production. Thin wrapper over `higgsfield soul-id`.
All output is JSON so the caller (bot/skill) can parse it.

    python soul_id.py create --name Alice --model soul-2 --image a1.jpg --image a2.jpg ... (5-20)
    python soul_id.py list
    python soul_id.py get <soul_id>
    python soul_id.py wait <soul_id>
"""

import argparse
import json
import subprocess
import sys


def _run(cmd: list[str], timeout: int = 60) -> dict:
    """Run a higgsfield soul-id command and return a JSON-friendly result dict."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr.strip() or r.stdout.strip())
            if "authenticate" in err.lower() or "token" in err.lower():
                err = "Higgsfield not authenticated. Run: higgsfield auth login"
            return {"success": False, "error": err[:500]}
        out = r.stdout.strip()
        if not out:
            return {"success": True}
        try:
            return {"success": True, "data": json.loads(out)}
        except json.JSONDecodeError:
            return {"success": True, "raw": out}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"timed out ({timeout}s)"}
    except Exception as e:  # noqa: BLE001 - surface any failure as JSON to the caller
        return {"success": False, "error": str(e)}


def cmd_create(args) -> dict:
    imgs = args.image or []
    if not (5 <= len(imgs) <= 20):
        return {"success": False, "error": f"Soul training needs 5-20 images (got {len(imgs)})"}
    cmd = ["higgsfield", "soul-id", "create", "--name", args.name, "--json"]
    cmd.append("--soul-cinematic" if args.model == "soul-cinematic" else "--soul-2")
    for img in imgs:
        cmd.extend(["--image", img])
    return _run(cmd, timeout=180)


def cmd_list(args) -> dict:
    return _run(["higgsfield", "soul-id", "list", "--json"])


def cmd_get(args) -> dict:
    return _run(["higgsfield", "soul-id", "get", args.soul_id, "--json"])


def cmd_wait(args) -> dict:
    return _run(["higgsfield", "soul-id", "wait", args.soul_id, "--json"], timeout=args.timeout)


def main():
    p = argparse.ArgumentParser(description="Train and manage Higgsfield Soul character references")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="Train a new Soul reference from 5-20 images")
    c.add_argument("--name", required=True, help="Character name")
    c.add_argument("--model", choices=["soul-2", "soul-cinematic"], default="soul-2",
                   help="Soul model to train (default soul-2)")
    c.add_argument("--image", action="append", help="Image path or upload id (repeatable, 5-20)")
    c.set_defaults(func=cmd_create)

    lst = sub.add_parser("list", help="List Soul references")
    lst.set_defaults(func=cmd_list)

    g = sub.add_parser("get", help="Show one Soul reference")
    g.add_argument("soul_id")
    g.set_defaults(func=cmd_get)

    w = sub.add_parser("wait", help="Poll Soul training until it finishes")
    w.add_argument("soul_id")
    w.add_argument("--timeout", type=int, default=1800, help="Max seconds to wait (default 1800)")
    w.set_defaults(func=cmd_wait)

    args = p.parse_args()
    result = args.func(args)
    print(json.dumps(result, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
