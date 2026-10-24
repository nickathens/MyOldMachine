#!/usr/bin/env python3
"""
Render a reverse-engineering dossier (JSON) into a Markdown report.

Usage:
  python dossier.py <dossier.json> [--out report.md]

The JSON is the machine-checkable contract (validate it first with
validate_dossier.py); this script is the human-readable view. It groups claims
by epistemic weight so a reader can never mistake a guess for a fact:

  * Ground truth  -- what tools literally showed (each with its citation).
  * Hypotheses    -- interpretation, still provisional (each with its basis).
  * Behaviour     -- what the target does at runtime, split into Confirmed
                     (observed in a dynamic/sandbox run) and Unconfirmed
                     (labelled guesses that still need dynamic proof).

Reads nothing but the file given. Stdlib only.
"""
import argparse
import json
import re
import sys


def esc(s):
    """Keep literal evidence readable inside a Markdown table cell."""
    return str(s).replace("|", "\\|").replace("\n", " ")


def code(s, table=False):
    """A literal as an inline code span that survives its own backticks (JavaScript template
    strings): the fence is one longer than the longest run inside. Only a table cell needs its
    pipes escaped; elsewhere a backslash inside code would show as written."""
    s = str(s).replace("\n", " ")
    if table:
        s = s.replace("|", "\\|")
    fence = "`" * (max((len(r) for r in re.findall(r"`+", s)), default=0) + 1)
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def render(dossier):
    out = []
    target = dossier.get("target", "(no target)")
    out.append(f"# RE dossier: {target}")
    out.append("")

    tier = dossier.get("tier", "?")
    date = dossier.get("analyst_date", "?")
    out.append(f"- **Tier:** {tier}")
    out.append(f"- **Date:** {date}")

    lb = dossier.get("lawful_basis") or {}
    if lb:
        out.append(f"- **Lawful basis:** {lb.get('purpose', '?')}. {lb.get('note', '')}")
    out.append("")

    question = dossier.get("question")
    if question:
        out.append("## Question")
        out.append("")
        out.append(question)
        out.append("")

    claims = dossier.get("claims") or []
    ground = [c for c in claims if isinstance(c, dict) and c.get("kind") == "ground_truth"]
    hyps = [c for c in claims if isinstance(c, dict) and c.get("kind") == "hypothesis"]
    behav = [c for c in claims if isinstance(c, dict) and c.get("kind") == "behavioural"]
    other = [c for c in claims if isinstance(c, dict) and c.get("kind") not in
             ("ground_truth", "hypothesis", "behavioural")]

    out.append("## Ground truth")
    out.append("")
    out.append("What the tools literally showed. Not interpreted.")
    out.append("")
    if ground:
        out.append("| id | Statement | Tool | Locator | Literal |")
        out.append("|---|---|---|---|---|")
        for c in ground:
            ev = c.get("evidence") or {}
            out.append(
                f"| {code(c.get('id', ''), table=True)} | {esc(c.get('statement', ''))} "
                f"| {esc(ev.get('tool', ''))} | {esc(ev.get('locator', ''))} "
                f"| {code(ev.get('literal', ''), table=True)} |"
            )
    else:
        out.append("_None recorded._")
    out.append("")

    out.append("## Hypotheses")
    out.append("")
    out.append("What the ground truth is believed to mean. Provisional until proven.")
    out.append("")
    if hyps:
        for c in hyps:
            sup = c.get("supports") or []
            sup_s = ", ".join(code(s) for s in sup) if sup else "(none cited)"
            out.append(f"- **{code(c.get('id', ''))}**: {esc(c.get('statement', ''))}")
            out.append(f"  - *Basis:* {esc(c.get('basis', ''))}")
            out.append(f"  - *Rests on:* {sup_s}")
    else:
        out.append("_None recorded._")
    out.append("")

    out.append("## Behaviour")
    out.append("")
    confirmed = [c for c in behav if c.get("status") == "confirmed"]
    unconfirmed = [c for c in behav if c.get("status") != "confirmed"]

    out.append("### Confirmed (observed in a dynamic / sandbox run)")
    out.append("")
    if confirmed:
        for c in confirmed:
            ev = c.get("evidence") or {}
            out.append(f"- {esc(c.get('statement', ''))}")
            out.append(
                f"  - *Observed:* {esc(ev.get('method', ''))} · "
                f"{esc(ev.get('locator', ''))} · {code(ev.get('literal', ''))}"
            )
    else:
        out.append("_None._")
    out.append("")

    out.append("### Unconfirmed (labelled guesses, still need dynamic proof)")
    out.append("")
    if unconfirmed:
        for c in unconfirmed:
            out.append(f"- {esc(c.get('statement', ''))}")
            ev = c.get("evidence") or {}
            if ev:
                out.append(
                    f"  - *Partial:* {esc(ev.get('method', ''))} · "
                    f"{esc(ev.get('locator', ''))} · {code(ev.get('literal', ''))}"
                )
    else:
        out.append("_None._")
    out.append("")

    if other:
        out.append("## Unclassified claims (schema violation, fix before trusting)")
        out.append("")
        for c in other:
            out.append(f"- kind={code(c.get('kind'))} id={code(c.get('id'))}: {esc(c.get('statement', ''))}")
        out.append("")

    out.append("---")
    out.append(
        f"_{len(ground)} ground truth, {len(hyps)} hypothesis, "
        f"{len(confirmed)} confirmed behaviour, {len(unconfirmed)} unconfirmed behaviour claim(s). "
        f"Ground truth cites a tool and the literal output; a behavioural claim is 'confirmed' only "
        f"with a dynamic observation. Everything else is a labelled guess._"
    )
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Render an RE dossier JSON as Markdown.")
    ap.add_argument("dossier", help="Path to the dossier JSON file.")
    ap.add_argument("--out", help="Write Markdown here instead of stdout.")
    args = ap.parse_args()

    try:
        with open(args.dossier, "r", encoding="utf-8") as fh:
            dossier = json.load(fh)
    except Exception as e:  # noqa: BLE001 - surface any load/parse error to the user
        print(f"Failed to read dossier: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(dossier, dict):
        print("A dossier must be a single JSON object.", file=sys.stderr)
        sys.exit(1)

    md = render(dossier)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"Wrote {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
