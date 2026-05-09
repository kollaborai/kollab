#!/usr/bin/env python3
"""Validate bundled skills meet the Agent Skills contract (agentskills.io).

Exits non-zero if any bundle under bundles/skills/ fails to parse as a Skill.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    packages = REPO_ROOT / "packages"
    sys.path.insert(0, str(packages / "kollabor-agent" / "src"))

    from kollabor_agent.agent_manager import Skill  # noqa: E402

    skills_root = REPO_ROOT / "bundles" / "skills"
    if not skills_root.is_dir():
        print(f"Missing skills directory: {skills_root}", file=sys.stderr)
        return 2

    errors: list[str] = []
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        md = entry / "SKILL.md"
        if not md.is_file():
            errors.append(f"Expected SKILL.md missing: {entry}")
            continue
        loaded = Skill.from_directory(entry, source="bundled")
        if loaded is None:
            errors.append(f"Invalid skill (does not load): {entry}")

    if errors:
        print(f"Bundled Agent Skills validation failed ({len(errors)}):", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(f"OK: {skills_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
