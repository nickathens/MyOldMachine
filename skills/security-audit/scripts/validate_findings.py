#!/usr/bin/env python3
"""
Validate findings.json against report-schema.json.

Usage: python validate_findings.py <path-to-findings.json>

Python port of validate-findings.cjs from cloudflare/security-audit-skill (MIT).
The validation rules live in report-schema.json -- the single source of truth.
This script reads that schema at runtime and interprets the subset of JSON
Schema it uses: type (object|array|string|integer), properties, required,
additionalProperties:false, enum, const, items, minItems, and oneOf.

Some constraints can't be expressed in that subset (a confirmed trace must
start at an "entrypoint", end at a "sink", and only use "propagation" for
intermediate steps). They're applied as an explicit, clearly-labelled
semantic layer after schema validation.

Zero third-party dependencies (stdlib only). Exits 0 on success, 1 on failure.
"""
import json
import os
import sys


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
    point at the intended oneOf branch (e.g. discriminate confirmed vs rejected
    by "verdict")."""
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
    elif t == "string":
        if type_of(value) != "string":
            errors.append(f"{p}: expected string, got {type_of(value)}")
    # default: no type constraint at this node


def collect(value, schema, p):
    errors = []
    validate(value, schema, p, errors)
    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_findings.py <path-to-findings.json>", file=sys.stderr)
        sys.exit(1)
    file = sys.argv[1]

    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report-schema.json")
    try:
        with open(schema_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        item_schema = doc.get("output_schema")
        if not item_schema:
            raise ValueError('report-schema.json is missing top-level "output_schema"')
    except Exception as e:  # noqa: BLE001 - surface any load/parse error to the user
        print(f"Failed to load schema from {schema_path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(file, "r", encoding="utf-8") as fh:
            findings = json.load(fh)
    except Exception as e:  # noqa: BLE001 - surface any load/parse error to the user
        print(f"Failed to parse JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(findings, list):
        print("findings.json must be an array", file=sys.stderr)
        sys.exit(1)

    error_count = 0
    for i, f in enumerate(findings):
        title = (f.get("title") or f.get("reason")) if isinstance(f, dict) else None
        label = f"[{i}] {title or '(untitled)'}"
        print(f"Checking {label}")

        errs = collect(f, item_schema, f"[{i}]")

        # Semantic layer -- constraints the schema subset can't express:
        # a confirmed trace must be one entrypoint, zero or more propagation
        # steps, then one sink.
        if isinstance(f, dict) and f.get("verdict") == "confirmed":
            trace = f.get("trace")
            if isinstance(trace, list) and len(trace) > 0:
                if isinstance(trace[0], dict) and trace[0].get("kind") != "entrypoint":
                    errs.append(
                        f'[{i}].trace[0].kind must be "entrypoint", got {json.dumps(trace[0].get("kind"))}'
                    )
                last = len(trace) - 1
                if isinstance(trace[last], dict) and trace[last].get("kind") != "sink":
                    errs.append(
                        f'[{i}].trace[{last}].kind must be "sink", got {json.dumps(trace[last].get("kind"))}'
                    )
                for j in range(1, last):
                    if isinstance(trace[j], dict) and trace[j].get("kind") != "propagation":
                        errs.append(
                            f'[{i}].trace[{j}].kind must be "propagation", got {json.dumps(trace[j].get("kind"))}'
                        )

        for msg in errs:
            print(f"  ERROR: {msg}", file=sys.stderr)
        error_count += len(errs)

    print()
    if error_count == 0:
        print(f"PASS: {len(findings)} findings valid")
    else:
        print(f"FAIL: {error_count} error(s) across {len(findings)} findings", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
