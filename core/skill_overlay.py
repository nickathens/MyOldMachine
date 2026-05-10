"""Per-user skill customization layer.

Three primitives, three escalating tiers:

  1. Override (visibility) — hide a skill or strip its description
     {"skillOverrides": {"vpn": "off", "rss": "name-only"}}

  2. Note (sticky hint) — short freeform text appended to a skill's summary
     {"notes": {"image-gen": "always avoid cartoon style"}}

  3. Fork (full copy) — copy the entire skill directory into the user's
     overlay. The user can then edit SKILL.md, scripts/, anything.
     The shared skill is untouched. Other users are untouched.

Resolution rule (mirrors Claude Code's scope precedence and Linux OverlayFS):
  * Walk shared skills.
  * For any skill that exists in the user's overlay dir, the overlay
    version replaces the shared one wholesale (no merge).
  * Apply skillOverrides: drop "off", abbreviate "name-only".
  * Append notes to remaining skills' summaries.

Storage layout per user:

  data/users/userN/
    skills/
      <forked-name>/                  # full skill dir copy
        SKILL.md
        .forked_from_hash             # SHA256 of shared SKILL.md at fork time
        scripts/...
    skill_settings.json               # overrides + notes

The fork directory and settings file live in the user's data dir and
are scoped per Telegram user at the application level.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from core.skill_loader import Skill, SkillManager
from core.self_install import (
    check_resource_requirements,
    get_skill_resource_info,
)

logger = logging.getLogger(__name__)


# --- Constants ---

SETTINGS_FILE = "skill_settings.json"
OVERLAY_SUBDIR = "skills"
HASH_FILE = ".forked_from_hash"

VALID_OVERRIDE_STATES = ("on", "off", "name-only")
DEFAULT_OVERRIDE_STATE = "on"

# Skill names: lowercase letters, digits, hyphens, underscores. 1..40 chars.
# Must start with a letter or digit (not a hyphen, underscore, or dot).
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

NOTE_MAX_LEN = 500
SETTINGS_FILE_MAX_BYTES = 32 * 1024  # 32 KB ceiling per-user settings file


# --- Validation ---

def validate_skill_name(name: Any) -> Optional[str]:
    """Return error string if invalid, None if OK."""
    if not isinstance(name, str):
        return "skill name must be a string"
    if not _SKILL_NAME_RE.match(name):
        return (
            f"invalid skill name {name!r}: must be lowercase letters, digits, "
            f"hyphens, or underscores (1-40 chars, starting with a letter or digit)"
        )
    return None


def validate_override_state(state: Any) -> Optional[str]:
    if not isinstance(state, str) or state not in VALID_OVERRIDE_STATES:
        return f"invalid override state: must be one of {VALID_OVERRIDE_STATES}"
    return None


def validate_note(text: Any) -> Optional[str]:
    if not isinstance(text, str):
        return "note must be a string"
    if len(text) > NOTE_MAX_LEN:
        return f"note too long: max {NOTE_MAX_LEN} chars (got {len(text)})"
    return None


# --- Settings I/O ---

def _settings_path(user_dir: Path) -> Path:
    return user_dir / SETTINGS_FILE


def _empty_settings() -> dict:
    return {"skillOverrides": {}, "notes": {}}


def load_settings(user_dir: Path) -> dict:
    """Load skill_settings.json. Returns empty defaults on missing/corrupt."""
    path = _settings_path(user_dir)
    if not path.exists():
        return _empty_settings()
    try:
        raw = path.read_text(encoding="utf-8")
        if len(raw) > SETTINGS_FILE_MAX_BYTES:
            logger.warning("skill_settings.json oversized; treating as empty")
            return _empty_settings()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return _empty_settings()
        result = _empty_settings()
        if isinstance(data.get("skillOverrides"), dict):
            result["skillOverrides"] = data["skillOverrides"]
        if isinstance(data.get("notes"), dict):
            result["notes"] = data["notes"]
        return result
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to load {path}: {e}")
        return _empty_settings()


def save_settings(user_dir: Path, settings: dict) -> None:
    """Atomically write skill_settings.json. Creates user_dir if needed."""
    user_dir.mkdir(parents=True, exist_ok=True)
    path = _settings_path(user_dir)
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(settings, indent=2, sort_keys=True, ensure_ascii=False)
    if len(payload.encode("utf-8")) > SETTINGS_FILE_MAX_BYTES:
        raise ValueError(
            f"settings exceed {SETTINGS_FILE_MAX_BYTES} bytes; refusing write"
        )
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# --- Overlay path helpers ---

def _overlay_dir(user_dir: Path) -> Path:
    return user_dir / OVERLAY_SUBDIR


def _safe_overlay_skill_dir(user_dir: Path, name: str) -> Optional[Path]:
    """Return the overlay path for a skill, or None if name escapes the overlay.

    Defense in depth on top of validate_skill_name: even if the regex were
    bypassed, Path.resolve() must remain inside the overlay subdir.
    """
    overlay = _overlay_dir(user_dir).resolve()
    candidate = (overlay / name).resolve()
    try:
        candidate.relative_to(overlay)
    except ValueError:
        return None
    return candidate


# --- Fork / Unfork ---

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def fork_skill(shared_skills_dir: Path, user_dir: Path, name: str) -> tuple[bool, str]:
    """Copy a shared skill directory into the user's overlay.

    Returns (ok, message).
    """
    err = validate_skill_name(name)
    if err:
        return False, err

    src = shared_skills_dir / name
    if not src.is_dir() or not (src / "SKILL.md").exists():
        return False, f"shared skill {name!r} not found"

    overlay_dir = _overlay_dir(user_dir)
    overlay_dir.mkdir(parents=True, exist_ok=True)
    dst = _safe_overlay_skill_dir(user_dir, name)
    if dst is None:
        return False, f"refusing to fork {name!r}: path escapes overlay"

    if dst.exists():
        return False, f"{name!r} is already forked; unfork first to refresh"

    try:
        shutil.copytree(src, dst, symlinks=False)
    except OSError as e:
        return False, f"copy failed: {e}"

    try:
        h = _hash_file(src / "SKILL.md")
        (dst / HASH_FILE).write_text(h + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning(f"failed to write hash file for forked {name}: {e}")

    return True, f"forked {name!r} into your private overlay"


def unfork_skill(user_dir: Path, name: str) -> tuple[bool, str]:
    """Remove the user's overlay copy of a skill."""
    err = validate_skill_name(name)
    if err:
        return False, err
    dst = _safe_overlay_skill_dir(user_dir, name)
    if dst is None:
        return False, f"refusing to unfork {name!r}: path escapes overlay"
    if not dst.exists():
        return False, f"no fork found for {name!r}"
    try:
        shutil.rmtree(dst)
    except OSError as e:
        return False, f"remove failed: {e}"
    return True, f"unforked {name!r}: reverted to shared version"


# --- Override / Note operations ---

def _skill_exists(shared_skills_dir: Path, user_dir: Path, name: str) -> bool:
    """A skill exists if it's in shared OR in the user's overlay."""
    if (shared_skills_dir / name / "SKILL.md").exists():
        return True
    overlay = _safe_overlay_skill_dir(user_dir, name)
    if overlay is not None and (overlay / "SKILL.md").exists():
        return True
    return False


def set_override(shared_skills_dir: Path, user_dir: Path, name: str,
                 state: str) -> tuple[bool, str]:
    """Set the visibility override for a skill.

    state="on" clears the entry (default state).
    state="off" hides the skill from this user's context.
    state="name-only" shows the name without the description.
    """
    err = validate_skill_name(name)
    if err:
        return False, err
    err = validate_override_state(state)
    if err:
        return False, err
    if not _skill_exists(shared_skills_dir, user_dir, name):
        return False, f"skill {name!r} not found"

    settings = load_settings(user_dir)
    overrides = settings.setdefault("skillOverrides", {})
    if state == DEFAULT_OVERRIDE_STATE:
        overrides.pop(name, None)
    else:
        overrides[name] = state
    save_settings(user_dir, settings)
    return True, f"set {name!r} override to {state!r}"


def set_note(shared_skills_dir: Path, user_dir: Path, name: str,
             text: str) -> tuple[bool, str]:
    """Pin a short note that gets appended to this skill's summary in context."""
    err = validate_skill_name(name)
    if err:
        return False, err
    err = validate_note(text)
    if err:
        return False, err
    if not _skill_exists(shared_skills_dir, user_dir, name):
        return False, f"skill {name!r} not found"

    settings = load_settings(user_dir)
    settings.setdefault("notes", {})[name] = text
    save_settings(user_dir, settings)
    return True, f"set note on {name!r}"


def clear_note(user_dir: Path, name: str) -> tuple[bool, str]:
    err = validate_skill_name(name)
    if err:
        return False, err
    settings = load_settings(user_dir)
    notes = settings.setdefault("notes", {})
    if name not in notes:
        return False, f"no note set for {name!r}"
    notes.pop(name, None)
    save_settings(user_dir, settings)
    return True, f"cleared note on {name!r}"


# --- Resolution: shared + overlay + settings → list[Skill] ---

def _load_overlay_skills(user_dir: Path) -> dict[str, Skill]:
    """Walk user_dir/skills/* and return Skill instances keyed by name."""
    overlay = _overlay_dir(user_dir)
    if not overlay.is_dir():
        return {}
    out: dict[str, Skill] = {}
    for path in overlay.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        if not (path / "SKILL.md").exists():
            continue
        try:
            skill = Skill(path.name, path)
            out[skill.name] = skill
        except Exception as e:
            logger.warning(f"failed to load overlay skill {path}: {e}")
    return out


def resolve_user_skills(shared_manager: SkillManager,
                        user_dir: Path) -> list[Skill]:
    """Return the user's effective skill list: shared with overlay shadowing.

    Does not yet apply visibility overrides — caller does that during context
    building, because a skill that's "off" should still be reachable for
    `set_override("on")` to find it.
    """
    merged: dict[str, Skill] = dict(shared_manager.skills)
    overlay = _load_overlay_skills(user_dir)
    for name, skill in overlay.items():
        merged[name] = skill
    return [merged[k] for k in sorted(merged.keys())]


def build_user_context(shared_manager: SkillManager, user_dir: Path,
                       exclude: Optional[list[str]] = None,
                       ram_gb: float = 0,
                       disk_free_gb: float = 0) -> str:
    """Build the user's per-conversation skill context section.

    Mirrors SkillManager.build_context() shape but applies the overlay,
    visibility overrides, and notes for this specific user.
    """
    skills = resolve_user_skills(shared_manager, user_dir)
    settings = load_settings(user_dir)
    overrides = settings.get("skillOverrides", {}) or {}
    notes = settings.get("notes", {}) or {}
    excluded = set(exclude or [])

    visible: list[tuple[Skill, str]] = []  # (skill, mode)
    for skill in skills:
        if not skill.enabled:
            continue
        if skill.name in excluded:
            continue
        state = overrides.get(skill.name, DEFAULT_OVERRIDE_STATE)
        if state not in VALID_OVERRIDE_STATES:
            state = DEFAULT_OVERRIDE_STATE
        if state == "off":
            continue
        visible.append((skill, state))

    if not visible:
        return ""

    skills_dir = shared_manager.skills_dir
    parts = [
        "## Available Skills (use Read tool on SKILL.md for full instructions):",
        f"Skills directory: {skills_dir}",
        f"To use a skill, read its full instructions: Read {skills_dir}/<skill-name>/SKILL.md",
        "",
    ]

    has_resource_warnings = False
    overlay_dir = _overlay_dir(user_dir)
    for skill, state in visible:
        # Resource-aware annotation, mirroring SkillManager.build_context().
        warning = check_resource_requirements(
            skill.path, ram_gb, disk_free_gb
        ) if (ram_gb or disk_free_gb) else None

        resource_warning = ""
        if warning:
            has_resource_warnings = True
            info = get_skill_resource_info(skill.path) or {}
            tag = f"[{info.get('weight', 'heavy').upper()}]"
            note = info.get("install_note", "")
            warn_parts = []
            min_ram = info.get("min_ram_gb", 0)
            min_disk = info.get("min_disk_gb", 0)
            if min_ram and ram_gb and ram_gb < min_ram:
                warn_parts.append(f"needs {min_ram}GB RAM, machine has {ram_gb}GB")
            if min_disk and disk_free_gb and disk_free_gb < min_disk:
                warn_parts.append(f"needs {min_disk}GB disk, only {disk_free_gb}GB free")
            resource_warning = f"{tag} WARN: {'; '.join(warn_parts)}"
            if note:
                resource_warning += f" ({note})"
            resource_warning += " — ask user before installing"
        else:
            info = get_skill_resource_info(skill.path)
            if info:
                tag = f"[{info['weight'].upper()}]"
                note = info.get("install_note", "")
                resource_warning = f"{tag} {note}" if note else tag

        # Mark forked skills so the LLM knows this is the user's own version.
        forked_marker = ""
        forked_path = overlay_dir / skill.name
        if forked_path == skill.path and forked_path.exists():
            forked_marker = "[FORKED] "

        if state == "name-only":
            line = f"- **{skill.name}**: {forked_marker}"
            if resource_warning:
                line += f" | {resource_warning}"
        else:
            line = skill.to_summary(resource_warning)
            if forked_marker:
                line = line.replace(
                    f"- **{skill.name}**: ",
                    f"- **{skill.name}**: {forked_marker}",
                    1,
                )

        user_note = notes.get(skill.name)
        if isinstance(user_note, str):
            # Collapse all whitespace (incl. newlines, tabs) so a malicious or
            # accidentally-multiline note can't inject fake skill listings.
            collapsed = " ".join(user_note.split())
            if collapsed:
                line += f" | Your note: {collapsed[:NOTE_MAX_LEN]}"
        parts.append(line)

    if has_resource_warnings:
        parts.insert(4, "**NOTE:** Some skills are marked with resource warnings. "
                     "Do NOT auto-install their dependencies without asking the user first.\n")

    parts.append("")
    return "\n".join(parts)
