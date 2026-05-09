"""Every shipped skill under bundles/skills must load under the Agent Skills contract."""

from __future__ import annotations

import unittest
from pathlib import Path

from kollabor_agent.agent_manager import Skill

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_SKILLS = REPO_ROOT / "bundles" / "skills"


class TestBundledSkillsShipped(unittest.TestCase):
    def test_all_bundled_skill_directories_validate(self) -> None:
        if not BUNDLED_SKILLS.is_dir():
            self.skipTest(f"No bundled skills at {BUNDLED_SKILLS}")

        failures: list[str] = []
        for entry in sorted(BUNDLED_SKILLS.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                failures.append(f"Missing SKILL.md: {entry.relative_to(REPO_ROOT)}")
                continue
            skill = Skill.from_directory(entry, source="bundled")
            if skill is None:
                failures.append(f"Loads as None: {entry.relative_to(REPO_ROOT)}")
                continue
            if skill.name != entry.name:
                failures.append(
                    f"name/dir mismatch {skill.name!r} vs {entry.name!r}: "
                    f"{entry.relative_to(REPO_ROOT)}"
                )

        self.assertEqual(
            failures,
            [],
            "Bundled skill validation failures:\n"
            + "\n".join(f"  - {f}" for f in failures),
        )


if __name__ == "__main__":
    unittest.main()
