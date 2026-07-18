#!/usr/bin/env python3
"""Install the CooCoo menu scripts into DaVinci Resolve's Scripts menu.

Copies everything in this skill's menu_scripts/ into the user-level Resolve
scripts folder, so the items appear under Workspace menu > Scripts inside
the app (works on the free edition). Re-run after editing a menu script.
"""
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "menu_scripts"
TARGET = (Path.home() / "Library/Application Support/Blackmagic Design"
          / "DaVinci Resolve/Fusion/Scripts/Utility")


def main():
    scripts = sorted(SOURCE.glob("*.py"))
    if not scripts:
        sys.exit(f"No menu scripts found in {SOURCE}")
    TARGET.mkdir(parents=True, exist_ok=True)
    for script in scripts:
        shutil.copy2(script, TARGET / script.name)
        print(f"Installed: Workspace > Scripts > {script.stem}")
    print(f"({TARGET})")
    print("If Resolve is open, the menu picks them up immediately.")


if __name__ == "__main__":
    main()
