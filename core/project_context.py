"""Bounded summaries of a project's own state record, for the system prompt.

Two rules the earlier inline version did not keep:

  * what the record says about right now (current_state, related_files) is the
    part that answers a question about a project, so it is carried, not dropped;
  * a project that does not fit the budget still names itself and its file,
    because a project the model cannot see cannot be asked about.

Reference paths are printed, never followed.
"""
import json

PROJECT_LIMIT = 1500
CONTEXT_LIMIT = 14000
OMITTED = "[Details omitted; read the state file.]"
COMPACT_HEADER = "Also live, not expanded here (read the state file for detail):"


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _shorten(entry, budget):
    """Shorten on a word boundary, so a cut line can never show half a path.

    Cutting mid-token turns "/work/nexus-v2/cues.json" into a reference that
    reads as usable and is not. Every token kept is therefore whole, and an
    entry with no word break inside the budget (a bare path, a lone key) is
    dropped rather than shown in part.
    """
    if len(entry) <= budget:
        return entry
    if budget < 24:
        return ""
    cut = entry.rfind(" ", 0, budget - 3)
    head = entry[:cut].rstrip() if cut > 0 else ""
    return head + "..." if head and not head.endswith(":") else ""


def _section(label, value, limit):
    """Render one field of the record, or "" when it holds nothing."""
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, dict):
        # Where the work stands now goes first, so a long note cannot push the
        # current phase or the latest delivery out of the budget.
        items = sorted(value.items(), key=lambda item: (
            0 if str(item[0]).startswith("latest") or item[0] in ("phase", "status")
            else 2 if item[0] == "notes" else 1))
        entries = [f"    {key}: {_text(item)}" for key, item in items]
    elif isinstance(value, list):
        entries = [f"    - {_text(item)}" for item in value]
    else:
        # A single value stays on its label's line, the way every other block
        # in this prompt renders one. Over the cap it is shortened, never
        # discarded whole.
        whole = f"  {label}: {_text(value)}"
        kept = _shorten(whole, limit - len(OMITTED) - 2)
        if kept == whole:
            return whole
        return f"{kept}\n  {OMITTED}" if kept else f"  {label}:\n  {OMITTED}"
    lines = [f"  {label}:"]
    used = len(lines[0]) + 1
    omitted = False
    for entry in entries:
        remaining = limit - used - len(OMITTED) - 2
        kept = _shorten(entry, min(800, remaining))
        if kept == entry:
            lines.append(entry)
            used += len(entry) + 1
            continue
        omitted = True
        if kept:
            lines.append(kept)
            used += len(kept) + 1
    if omitted:
        lines.append(f"  {OMITTED}")
    return "\n".join(lines)


def format_project_block(state, visibility: str, limit: int = PROJECT_LIMIT) -> str:
    """One project, current state first, bounded to `limit` characters."""
    if not isinstance(state, dict):
        return ""
    name = str(state.get("name", "Unknown"))[:160]
    sections = [f"\n**{name}** [{visibility}]"]
    fields = (
        ("Location", state.get("location", "unknown"), 400),
        ("Summary", state.get("summary"), 400),
        ("Current state", state.get("current_state"), 500),
        ("Related files (references only)", state.get("related_files"), 300),
        ("Next steps", state.get("next_steps")[:3]
         if isinstance(state.get("next_steps"), list) else state.get("next_steps"), 400),
        ("Blockers", state.get("blockers"), 300),
        ("Recent lessons", state.get("lessons", [])[-2:]
         if isinstance(state.get("lessons"), list) else state.get("lessons"), 300),
    )
    for label, value, field_limit in fields:
        section = _section(label, value, field_limit)
        if section:
            sections.append(section)
    text = "\n".join(sections)
    if len(text) <= limit:
        return text
    # Cut on a line boundary, for the same reason: half a line can name a path.
    cut = text.rfind("\n", 0, limit - len(OMITTED) - 3)
    return (text[:cut] if cut > 0 else "").rstrip() + f"\n  {OMITTED}"


def compact_project_line(state, visibility: str, source=None) -> str:
    """One named line for a project the budget cannot expand."""
    if not isinstance(state, dict):
        return ""
    name = str(state.get("name", "Unknown"))[:160]
    updated = state.get("updated") if isinstance(state.get("updated"), str) else ""
    location = str(state.get("location", "") or "")[:200]
    line = f"- {name} [{visibility}]"
    if updated:
        line += f", updated {updated}"
    if location:
        line += f", {location}"
    if source:
        line += f", {source}"
    return line


def summarize_projects(records, expand_max: int, limit: int = CONTEXT_LIMIT) -> str:
    """Expand the first `expand_max` that fit; name every project that does not.

    Room for one line per remaining project is reserved before anything is
    expanded, so the tail of the list cannot be pushed off the end by a long
    record earlier in it.
    """
    expanded, compact = [], []
    used = len(COMPACT_HEADER) + 2
    for index, (detail, line) in enumerate(records):
        reserve = sum(len(later) + 1 for _, later in records[index + 1:])
        if (detail and len(expanded) < expand_max
                and used + len(detail) + 1 + reserve <= limit):
            expanded.append(detail)
            used += len(detail) + 1
        elif line and used + len(line) + 1 <= limit:
            compact.append(line)
            used += len(line) + 1
    if compact:
        expanded.append("\n" + COMPACT_HEADER + "\n" + "\n".join(compact))
    return "\n".join(expanded)
