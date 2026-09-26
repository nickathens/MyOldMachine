"""
Skill dependency manifests (deps.json): reading and resource gating.

Each skill's deps.json names what it needs (system packages, pip, npm) and how
heavy it is (weight, min_ram_gb, min_disk_gb). The skill loader reads the
resource half through check_resource_requirements and get_skill_resource_info
to hold back a skill this machine cannot run.

Nothing installs from here. An automatic installer, install_missing() with its
check_skill_deps(), lived in this module from March 2026 (376c572) and was
never called outside tests. What really installs a skill's dependencies is
the agent, acting on the error a skill's script prints (diagram.py: "mmdc not
found. Install it with ...") or on its SKILL.md. The installer was removed on
2026-09-26 so that a fix to how something installs lands where it runs, in the
script's own message, instead of in code that never runs. The module keeps
its name so imports stay put.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Package names from deps.json can end up in apt-get/brew/npm install commands
# that run as root. Skill manifests are checked in to source
# control and authored by humans, but a typo or malicious skill could embed
# a shell metacharacter and turn `apt-get install <pkg>` into arbitrary code
# execution. Enforce conservative allowlists at load time so the entire
# manifest is rejected (skill treated as having no deps) on any invalid value.
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
# pip allows a few extras: `>=`, `==`, `<`, `<=`, `>`, `~=`, `!=`, ` ` and brackets.
_PIP_PKG_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}"
    r"(\[[A-Za-z0-9,\-_]+\])?"
    r"(\s*(==|>=|<=|!=|~=|>|<)\s*[A-Za-z0-9._\-+]+)?$"
)
# npm: scoped packages like @scope/name plus optional @version. No spaces.
_NPM_PKG_RE = re.compile(
    r"^(@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}(@[A-Za-z0-9._\-+~^*<>=!|x ]+)?$"
)
_PKG_KEYS = ("apt", "dnf", "yum", "pacman", "zypper", "apk", "brew")


def _validate_deps(deps: dict, skill_name: str) -> bool:
    """Reject a deps manifest containing names that could escape the shell.

    Logs the first offender and returns False on failure; load_deps then
    treats the manifest as absent, so a name that could escape a shell is never
    carried as a package name. False positives just mean a skill author needs
    to rename a package or extend the allowlist.
    """
    if not isinstance(deps, dict):
        logger.error(f"deps.json for {skill_name} is not a JSON object")
        return False
    checks = deps.get("check", {}) or {}
    if not isinstance(checks, dict):
        logger.error(f"deps.json[{skill_name}].check must be an object")
        return False
    for name in checks.keys():
        if not isinstance(name, str) or not _PKG_NAME_RE.fullmatch(name):
            logger.error(f"deps.json[{skill_name}].check has invalid name: {name!r}")
            return False
    for key in _PKG_KEYS:
        pkgs = deps.get(key, []) or []
        if not isinstance(pkgs, list):
            logger.error(f"deps.json[{skill_name}][{key}] must be a list")
            return False
        for pkg in pkgs:
            if not isinstance(pkg, str) or not _PKG_NAME_RE.fullmatch(pkg):
                logger.error(f"deps.json[{skill_name}][{key}] has invalid pkg: {pkg!r}")
                return False
    for pkg in deps.get("pip", []) or []:
        if not isinstance(pkg, str) or not _PIP_PKG_RE.fullmatch(pkg):
            logger.error(f"deps.json[{skill_name}].pip has invalid spec: {pkg!r}")
            return False
    for pkg in deps.get("npm", []) or []:
        if not isinstance(pkg, str) or not _NPM_PKG_RE.fullmatch(pkg):
            logger.error(f"deps.json[{skill_name}].npm has invalid spec: {pkg!r}")
            return False
    return True


def load_deps(skill_path: Path) -> Optional[dict]:
    """Load and validate deps.json for a skill.

    Returns None if the file is missing, malformed, or contains any package
    name that fails the allowlist regex: a single typo that smuggles a shell
    metacharacter must never be carried as if it were a package name.
    """
    deps_file = skill_path / "deps.json"
    if not deps_file.exists():
        return None
    try:
        deps = json.loads(deps_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError, UnicodeDecodeError) as e:
        logger.error(f"Failed to load deps.json for {skill_path.name}: {e}")
        return None
    if not _validate_deps(deps, skill_path.name):
        return None
    return deps


def check_resource_requirements(skill_path: Path, ram_gb: float = 0, disk_free_gb: float = 0) -> Optional[str]:
    """Check if the machine has enough resources for a skill's dependencies.

    Args:
        skill_path: Path to the skill directory.
        ram_gb: Total system RAM in GB (from system_caps.json).
        disk_free_gb: Free disk space in GB (from system_caps.json).

    Returns:
        A warning string if resources are insufficient, None if OK.
    """
    deps = load_deps(skill_path)
    if not deps:
        return None

    min_ram = deps.get("min_ram_gb", 0)
    min_disk = deps.get("min_disk_gb", 0)
    weight = deps.get("weight", "light")
    install_note = deps.get("install_note", "")

    if not min_ram and not min_disk:
        return None

    issues = []

    if min_ram and ram_gb and ram_gb < min_ram:
        issues.append(f"needs {min_ram} GB RAM (this machine has {ram_gb} GB)")

    if min_disk and disk_free_gb and disk_free_gb < min_disk:
        issues.append(f"needs {min_disk} GB free disk (only {disk_free_gb} GB available)")

    if not issues:
        return None

    parts = [f"Skill '{skill_path.name}' [{weight}]"]
    if install_note:
        parts.append(install_note)
    parts.append("Resource warning: " + "; ".join(issues))
    return ". ".join(parts)


def get_skill_resource_info(skill_path: Path) -> Optional[dict]:
    """Get resource requirement metadata for a skill.

    Returns dict with weight, min_ram_gb, min_disk_gb, install_note
    or None if no resource requirements are specified.
    """
    deps = load_deps(skill_path)
    if not deps:
        return None

    weight = deps.get("weight")
    if not weight:
        return None

    return {
        "weight": weight,
        "min_ram_gb": deps.get("min_ram_gb", 0),
        "min_disk_gb": deps.get("min_disk_gb", 0),
        "install_note": deps.get("install_note", ""),
    }
