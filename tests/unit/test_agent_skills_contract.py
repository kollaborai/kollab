"""Strict Agent Skills directory contract (agentskills.io) for SKILL.md parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kollabor_agent.agent_manager import (
    Skill,
    skill_markdown_body,
    validate_skill_name,
)


class TestSkillNameValidation(unittest.TestCase):
    def test_valid_kebab_case(self) -> None:
        self.assertTrue(validate_skill_name("debugging"))
        self.assertTrue(validate_skill_name("code-review"))

    def test_rejects_invalid(self) -> None:
        self.assertFalse(validate_skill_name(""))
        self.assertFalse(validate_skill_name("Code-Review"))
        self.assertFalse(validate_skill_name("-bad"))
        self.assertFalse(validate_skill_name("bad--name"))


class TestSkillMarkdownBody(unittest.TestCase):
    def test_strips_frontmatter(self) -> None:
        raw = "---\nname: foo\ndescription: bar\n---\n\nhello\n"
        self.assertEqual(skill_markdown_body(raw), "hello\n")


class TestSkillFromFile(unittest.TestCase):
    def _write_skill(self, root: Path, dirname: str, body: str) -> Path:
        d = root / dirname
        d.mkdir(parents=True)
        fp = d / "SKILL.md"
        fp.write_text(body, encoding="utf-8")
        return fp

    def test_valid_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fp = self._write_skill(
                tmp_path,
                "my-skill",
                "---\nname: my-skill\ndescription: Does a thing when asked.\n---\n\nSteps.\n",
            )
            skill = Skill.from_file(fp)
            assert skill is not None
            self.assertEqual(skill.name, "my-skill")
            self.assertEqual(skill.description, "Does a thing when asked.")
            self.assertEqual(skill.content.strip(), "Steps.")

    def test_rejects_wrong_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "x"
            d.mkdir()
            wrong = d / "README.md"
            wrong.write_text(
                "---\nname: x\ndescription: hi\n---\n", encoding="utf-8"
            )
            self.assertIsNone(Skill.from_file(wrong))

    def test_requires_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write_skill(Path(tmp), "a", "no frontmatter")
            self.assertIsNone(Skill.from_file(fp))

    def test_name_must_match_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write_skill(
                Path(tmp),
                "dir-name",
                "---\nname: other\ndescription: x\n---\n\n",
            )
            self.assertIsNone(Skill.from_file(fp))

    def test_requires_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write_skill(
                Path(tmp),
                "x",
                "---\nname: x\n---\n\n",
            )
            self.assertIsNone(Skill.from_file(fp))

    def test_metadata_values_must_be_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write_skill(
                Path(tmp),
                "x",
                "---\nname: x\ndescription: d\nmetadata:\n  version: 1\n---\n\n",
            )
            self.assertIsNone(Skill.from_file(fp))

    def test_allowed_tools_must_be_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write_skill(
                Path(tmp),
                "x",
                "---\nname: x\ndescription: d\nallowed-tools: [Read]\n---\n\n",
            )
            self.assertIsNone(Skill.from_file(fp))

    def test_allowed_tools_space_separated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fp = self._write_skill(
                Path(tmp),
                "x",
                "---\nname: x\ndescription: d\nallowed-tools: Read Bash\n---\n\n",
            )
            skill = Skill.from_file(fp)
            assert skill is not None
            self.assertEqual(skill.allowed_tools, ["Read", "Bash"])


if __name__ == "__main__":
    unittest.main()
