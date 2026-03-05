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

    def to_summary(self) -> str:
        scripts = self.get_scripts_dir()
        scripts_note = f" | Scripts: {scripts}" if scripts else ""
        return f"- **{self.name}**: {self.description}{scripts_note}"


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

    def build_context(self, exclude: list[str] | None = None) -> str:
        """Build lazy context: skill names + descriptions only."""
        enabled = self.get_enabled_skills(exclude)
        if not enabled:
            return ""
        parts = [
            "## Available Skills",
            f"Skills directory: {self.skills_dir}",
            "",
        ]
        for skill in sorted(enabled, key=lambda s: s.name):
            parts.append(skill.to_summary())
        parts.append("")
        return "\n".join(parts)
