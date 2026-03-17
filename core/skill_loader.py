"""
Skill Loader for MyOldMachine.

Skills are modular capability packages. Each skill is a directory containing:
- SKILL.md   - Required. Description, usage examples, and instructions.
- scripts/   - Optional. Executable scripts the skill provides.
- config.json - Optional. Skill configuration and metadata.
- requirements.txt - Optional. Python dependencies for this skill.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from core.self_install import get_skill_resource_info, check_resource_requirements

logger = logging.getLogger(__name__)


class Skill:
    """Represents a loaded skill."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.enabled = True
        self.description = ""
        self.instructions = ""
        self.config = {}
        self.system_deps = []
        self._load()

    def _load(self):
        skill_md = self.path / "SKILL.md"
        if skill_md.exists():
            content = skill_md.read_text()
            lines = content.strip().split("\n")
            start = 1 if lines and lines[0].startswith("#") else 0
            desc_lines = []
            for line in lines[start:]:
                if line.strip() == "" and desc_lines:
                    break
                if line.strip():
                    desc_lines.append(line.strip())
            self.description = " ".join(desc_lines)[:200]
            self.instructions = content
        else:
            logger.warning(f"Skill {self.name} missing SKILL.md")

        config_file = self.path / "config.json"
        if config_file.exists():
            try:
                self.config = json.loads(config_file.read_text())
                self.enabled = self.config.get("enabled", True)
                self.system_deps = self.config.get("system_deps", [])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to load {self.name} config: {e}")

    def get_scripts_dir(self) -> Optional[Path]:
        scripts_dir = self.path / "scripts"
        return scripts_dir if scripts_dir.exists() else None

    def to_summary(self, resource_warning: str = "") -> str:
        scripts = self.get_scripts_dir()
        scripts_note = f" | Scripts: {scripts}" if scripts else ""
        warning_note = f" | {resource_warning}" if resource_warning else ""
        return f"- **{self.name}**: {self.description}{scripts_note}{warning_note}"


class SkillManager:
    """Manages loading and accessing skills."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, Skill] = {}
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.reload()

    def reload(self):
        self.skills.clear()
        if not self.skills_dir.exists():
            return
        for skill_path in self.skills_dir.iterdir():
            if skill_path.is_dir() and not skill_path.name.startswith("."):
                skill_md = skill_path / "SKILL.md"
                if skill_md.exists():
                    try:
                        skill = Skill(skill_path.name, skill_path)
                        self.skills[skill.name] = skill
                    except Exception as e:
                        logger.error(f"Failed to load skill {skill_path.name}: {e}")

    def get_skill(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def get_enabled_skills(self, exclude: list[str] | None = None) -> list[Skill]:
        excluded = set(exclude or [])
        return [s for s in self.skills.values() if s.enabled and s.name not in excluded]

    def build_context(self, exclude: list[str] | None = None,
                       ram_gb: float = 0, disk_free_gb: float = 0) -> str:
        """Build lazy context: skill names + descriptions + resource warnings.
        Full instructions are loaded on-demand when the LLM reads SKILL.md.

        Args:
            exclude: Skill names to exclude from the context.
            ram_gb: Total system RAM in GB (from system_caps.json).
            disk_free_gb: Free disk space in GB (from system_caps.json).
        """
        enabled = self.get_enabled_skills(exclude)
        if not enabled:
            return ""
        parts = [
            "## Available Skills (use Read tool on SKILL.md for full instructions):",
            f"Skills directory: {self.skills_dir}",
            f"To use a skill, read its full instructions: Read {self.skills_dir}/<skill-name>/SKILL.md",
            "",
        ]

        # Check which skills have resource concerns on this machine
        has_resource_warnings = False
        for skill in sorted(enabled, key=lambda s: s.name):
            warning = check_resource_requirements(
                skill.path, ram_gb, disk_free_gb
            ) if (ram_gb or disk_free_gb) else None

            if warning:
                has_resource_warnings = True
                # Build a concise inline warning
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
                parts.append(skill.to_summary(resource_warning))
            else:
                # Include weight tag for heavy/medium skills even if resources are OK
                info = get_skill_resource_info(skill.path)
                if info:
                    tag = f"[{info['weight'].upper()}]"
                    note = info.get("install_note", "")
                    resource_note = f"{tag} {note}" if note else tag
                    parts.append(skill.to_summary(resource_note))
                else:
                    parts.append(skill.to_summary())

        if has_resource_warnings:
            parts.insert(4, "**NOTE:** Some skills are marked with resource warnings. "
                         "Do NOT auto-install their dependencies without asking the user first.\n")

        parts.append("")
        return "\n".join(parts)
