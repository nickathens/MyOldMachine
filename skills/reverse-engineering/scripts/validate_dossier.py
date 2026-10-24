#!/usr/bin/env python3
"""
Validate a reverse-engineering dossier against dossier-schema.json.

Usage: python validate_dossier.py <path-to-dossier.json> [--evidence DIR]

This is the verification spine made executable. A dossier is ONE object per
target; every claim carries a `kind` that fixes how much it is allowed to
assert. The schema (dossier-schema.json, the single source of truth) is read at
runtime and the subset of JSON Schema it uses is interpreted here: type
(object|array|string|integer|boolean), properties, required,
additionalProperties:false, enum, const, items, minItems, oneOf.

The schema-subset interpreter is ported from the security-audit skill's
validate_findings.py (itself from cloudflare/security-audit-skill, MIT). Four
constraints the subset cannot express are applied as an explicit, labelled
semantic layer after schema validation:

  1. claim `id`s are unique within the dossier.
  2. every id in a hypothesis `supports` array refers to a claim that exists.
  3. a behavioural claim with status "confirmed" MUST carry dynamic evidence --
     static reading alone can never confirm a runtime behaviour.
  4. a ground_truth literal is not empty: an empty quote matches anywhere.

With --evidence DIR the locator discipline is checked too: every ground_truth
locator of the form "<file> byte <N>" or "<file> @ char <N>" must name a file
in DIR, and that file must hold the claim's literal at exactly that place. A
citation that does not match the artifact fails, the same as a schema error;
so does one whose file is missing from DIR, and a locator that names a file in
DIR in any other form (it could not be re-read), and one naming a code or record
file (.js, .css, .html, .wasm, .map, .json) that is not in DIR, even as a
subfolder path: a name typed from memory. A libscan literal lists its
markers joined by " | ": the first is re-read at the offset, and every other
one must be in some file of DIR (only the first was once checked, so a marker
typed wrong passed).

Zero third-party dependencies (stdlib only). Exits 0 on success, 1 on failure.
"""
import argparse
import json
import os
import re
import sys

DYNAMIC_METHODS = ("dynamic", "sandbox", "runtime")
FILE_NAME = re.compile(r"\.(?:m?js|cjs|css|html?|wasm|map|json)$", re.I)
LOCATOR = re.compile(r"^(?P<file>[^\s@]+)\s+(?:byte\s+(?P<byte>\d+)|@\s*char\s+(?P<char>\d+))")


def type_of(v):
    # bool is a subclass of int in Python, so it must be tested first, matching
    # JS where typeof true === "boolean" (never "number").
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, list):
        return "array"
    if v is None:
        return "null"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, dict):
        return "object"
    return "unknown"


def find_discriminator(schema):
    """Return the first property defined with a `const`, so error messages can
    point at the intended oneOf branch (here, discriminate claims by `kind`)."""
    props = schema.get("properties")
    if not props:
        return None
    for key, sub in props.items():
        if isinstance(sub, dict) and "const" in sub:
            return {"key": key, "value": sub["const"]}
    return None


def validate(value, schema, p, errors):
    if "oneOf" in schema:
        # Prefer the branch whose const discriminator matches, so the caller
        # sees detailed errors for the branch they clearly intended.
        for branch in schema["oneOf"]:
            disc = find_discriminator(branch)
            if disc and isinstance(value, dict) and value.get(disc["key"]) == disc["value"]:
                validate(value, branch, p, errors)
                return
        # No discriminator matched. If every branch is discriminated by the same
        # key, report the bad discriminator value clearly.
        discs = [d for d in (find_discriminator(b) for b in schema["oneOf"]) if d]
        if len(discs) == len(schema["oneOf"]) and isinstance(value, dict):
            key = discs[0]["key"]
            allowed = ", ".join(json.dumps(d["value"]) for d in discs)
            errors.append(
                f'{p}: "{key}" must be one of {allowed}, got {json.dumps(value.get(key))}'
            )
            return
        passing = [b for b in schema["oneOf"] if len(collect(value, b, p)) == 0]
        if len(passing) != 1:
            errors.append(f"{p}: does not match exactly one of the allowed schemas")
        return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{p}: must equal {json.dumps(schema['const'])}, got {json.dumps(value)}")

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(json.dumps(v) for v in schema["enum"])
        errors.append(f"{p}: invalid value {json.dumps(value)} (expected one of {allowed})")

    t = schema.get("type")
    if t == "object":
        if type_of(value) != "object":
            errors.append(f"{p}: expected object, got {type_of(value)}")
            return
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f'{p}: missing required field "{req}"')
        props = schema.get("properties")
        for key in value.keys():
            if props and key in props:
                validate(value[key], props[key], f"{p}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f'{p}: unexpected field "{key}"')
    elif t == "array":
        if type_of(value) != "array":
            errors.append(f"{p}: expected array, got {type_of(value)}")
            return
        min_items = schema.get("minItems")
        if isinstance(min_items, (int, float)) and not isinstance(min_items, bool) and len(value) < min_items:
            errors.append(f"{p}: must have at least {min_items} item(s), got {len(value)}")
        items = schema.get("items")
        if items:
            for i, el in enumerate(value):
                validate(el, items, f"{p}[{i}]", errors)
    elif t == "integer":
        # Match JS Number.isInteger: reject booleans, accept integral floats.
        if type_of(value) != "number" or not float(value).is_integer():
            errors.append(f"{p}: expected integer, got {type_of(value)}")
    elif t == "boolean":
        if type_of(value) != "boolean":
            errors.append(f"{p}: expected boolean, got {type_of(value)}")
    elif t == "string":
        if type_of(value) != "string":
            errors.append(f"{p}: expected string, got {type_of(value)}")
    # default: no type constraint at this node


def collect(value, schema, p):
    errors = []
    validate(value, schema, p, errors)
    return errors


def semantic_layer(dossier, errors):
    """Constraints the schema subset cannot express."""
    claims = dossier.get("claims")
    if not isinstance(claims, list):
        return  # structural error already reported by the schema pass

    # 1. Unique ids.
    seen = {}
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if not isinstance(cid, str):
            continue
        if cid in seen:
            errors.append(
                f'claims[{i}].id "{cid}" is a duplicate of claims[{seen[cid]}].id'
            )
        else:
            seen[cid] = i

    # 2. supports references resolve.
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or c.get("kind") != "hypothesis":
            continue
        supports = c.get("supports")
        if isinstance(supports, list):
            for ref in supports:
                if isinstance(ref, str) and ref not in seen:
                    errors.append(
                        f'claims[{i}].supports references "{ref}", which is not the id of any claim'
                    )

    # 3. A behavioural claim can only be confirmed with dynamic evidence.
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or c.get("kind") != "behavioural":
            continue
        if c.get("status") != "confirmed":
            continue
        ev = c.get("evidence")
        if not isinstance(ev, dict):
            errors.append(
                f'claims[{i}]: behavioural claim is "confirmed" but carries no dynamic evidence '
                f"(static reading alone cannot confirm a runtime behaviour)"
            )
            continue
        # The schema already restricts evidence.method to the dynamic set when
        # evidence is present; this re-states it as a semantic guarantee so the
        # rule is legible at the point it matters.
        if ev.get("method") not in DYNAMIC_METHODS:
            errors.append(
                f'claims[{i}]: behavioural claim is "confirmed" but evidence.method '
                f"{json.dumps(ev.get('method'))} is not a dynamic observation "
                f"(one of {', '.join(DYNAMIC_METHODS)})"
            )

    # 4. Ground truth quotes something.
    for i, c in enumerate(claims):
        if not isinstance(c, dict) or c.get("kind") != "ground_truth":
            continue
        ev = c.get("evidence")
        lit = ev.get("literal") if isinstance(ev, dict) else None
        if isinstance(lit, str) and not lit.strip():
            errors.append(f"claims[{i}]: ground_truth literal is empty (an empty quote matches anywhere)")


def evidence_layer(dossier, evidence_dir, errors):
    """Re-read every byte/char locator against the file it names. Returns
    (checked, not_found); a mismatch, a cited file missing from the folder, and
    a locator naming a file there in another form all go into errors."""
    files = {n for n in os.listdir(evidence_dir) if os.path.isfile(os.path.join(evidence_dir, n))} \
        if os.path.isdir(evidence_dir) else set()
    checked, not_found, texts = 0, [], None
    for i, c in enumerate(dossier.get("claims") or []):
        if not isinstance(c, dict) or c.get("kind") != "ground_truth":
            continue
        ev = c.get("evidence") or {}
        loc = str(ev.get("locator", ""))
        m = LOCATOR.match(loc)
        if not m:
            tokens = re.split(r"[\s:@,()]+", loc)
            named = [t for t in tokens if os.path.basename(t) in files]
            if named:
                errors.append(f'claims[{i}] ({c.get("id")}): locator {loc!r} names {named[0]} but not as '
                              f'"<file> byte <N>" or "<file> @ char <N>", so it cannot be re-read')
            # A request URL or path (/api/x.json) is a network citation, not a file of the folder;
            # "x.js:120" and "x.js@5" name x.js.
            names = [re.split(r"[:@]", t)[0].rstrip(".;") for t in re.split(r"[\s,()]+", loc)
                     if t and "//" not in t and not t.startswith("/")]
            absent = [n for n in names if FILE_NAME.search(n) and not os.path.isfile(os.path.join(evidence_dir, n))]
            if absent:
                not_found.append(os.path.basename(absent[0]))
                errors.append(f'claims[{i}] ({c.get("id")}): locator {loc!r} names {absent[0]}, which is not in '
                              f'the evidence folder')
            continue
        name = os.path.basename(m.group("file"))
        path = os.path.join(evidence_dir, name)
        if not os.path.isfile(path):
            not_found.append(name)
            errors.append(f'claims[{i}] ({c.get("id")}): {name} is not in the evidence folder, so locator '
                          f'{loc!r} cannot be re-read')
            continue
        lit = str(ev.get("literal", ""))
        with open(path, "rb") as fh:
            data = fh.read()
        if m.group("byte") is not None:
            off, want = int(m.group("byte")), lit.encode("utf-8")
            ok = bool(want) and data[off:off + len(want)] == want
        else:
            off, text = int(m.group("char")), data.decode("utf-8", errors="replace")
            # libscan cites the first marker of a list by offset; the literal lists them all, joined by " | ".
            want, *more = lit.split(" | ")
            ok = bool(want) and text[off:off + len(want)] == want
            # The rest carry no offset ("+N more"), so each must at least be in some file of the folder.
            if texts is None:
                texts = []
                for n in sorted(files):
                    with open(os.path.join(evidence_dir, n), "rb") as fh:
                        texts.append(fh.read().decode("utf-8", errors="replace"))
            nowhere = [mk for mk in more if not mk or not any(mk in t for t in texts)]
            if nowhere:
                errors.append(f'claims[{i}] ({c.get("id")}): literal lists {nowhere} but no file in the evidence '
                              f"folder holds it (the citation does not match the artifact)")
        checked += 1
        if not ok:
            errors.append(f'claims[{i}] ({c.get("id")}): locator {ev.get("locator")!r} does not hold the literal '
                          f"in {name} (the citation does not match the artifact)")
    return checked, sorted(set(not_found))


def main():
    ap = argparse.ArgumentParser(description="Validate a reverse-engineering dossier (schema, semantics, locators).")
    ap.add_argument("dossier", help="Path to the dossier JSON.")
    ap.add_argument("--evidence", metavar="DIR",
                    help="Folder of the downloaded files; re-read every '<file> byte N' / '<file> @ char N' locator.")
    args = ap.parse_args()
    file, evidence_dir = args.dossier, args.evidence

    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dossier-schema.json")
    try:
        with open(schema_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        root_schema = doc.get("dossier_schema")
        if not root_schema:
            raise ValueError('dossier-schema.json is missing top-level "dossier_schema"')
    except Exception as e:  # noqa: BLE001 - surface any load/parse error to the user
        print(f"Failed to load schema from {schema_path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(file, "r", encoding="utf-8") as fh:
            dossier = json.load(fh)
    except Exception as e:  # noqa: BLE001 - surface any load/parse error to the user
        print(f"Failed to parse JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(dossier, dict):
        print("A dossier must be a single JSON object (not an array)", file=sys.stderr)
        sys.exit(1)

    target = dossier.get("target")
    claims = dossier.get("claims")
    n_claims = len(claims) if isinstance(claims, list) else 0
    print(f"Checking dossier: {target or '(no target)'} ({n_claims} claim(s))")
    sys.stdout.flush()

    errors = collect(dossier, root_schema, "$")
    semantic_layer(dossier, errors)
    if evidence_dir:
        checked, missing = evidence_layer(dossier, evidence_dir, errors)
        print(f"Locators re-read against {evidence_dir}: {checked} checked"
              + (f"; files not there, so not checked (an error): {', '.join(missing)}" if missing else ""))

    for msg in errors:
        print(f"  ERROR: {msg}", file=sys.stderr)

    print()
    if not errors:
        print(f"PASS: dossier valid ({n_claims} claims)")
    else:
        print(f"FAIL: {len(errors)} error(s)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
