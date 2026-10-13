"""/skills never renames or deletes a skill that ships with Kollab (its folder is the install)."""

from types import SimpleNamespace

from kollabor.commands.system_commands.handlers.skills import SkillCommandHandler


def _skill(folder):
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n")
    return SimpleNamespace(skill_dir=folder)


def test_builtin_skills_are_never_deleted_or_renamed(tmp_path):
    bundled = tmp_path / "bundles" / "skills"
    skills = {"tdd": _skill(bundled / "tdd"), "notes": _skill(tmp_path / "home" / "skills" / "notes")}
    agent = SimpleNamespace(get_skill=skills.get)
    handler = SkillCommandHandler.__new__(SkillCommandHandler)
    handler.agent_manager = SimpleNamespace(skill_library=SimpleNamespace(bundled_dir=bundled))

    assert handler._delete_skill_file(agent, "tdd") is False
    assert handler._rename_skill_file(agent, "tdd", "tdd-2") is False
    assert (bundled / "tdd" / "SKILL.md").exists()

    assert handler._delete_skill_file(agent, "notes") is True
    assert not skills["notes"].skill_dir.exists()
