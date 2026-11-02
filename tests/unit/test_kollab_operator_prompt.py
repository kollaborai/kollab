"""Exercise the operator through the real agent, prompt, and compaction paths.

The catalog is developed on a separate branch. Until integrated, the fixture
supplies a stand-in; when bundled, these tests use the actual catalog instead.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import kollabor_config.config_utils as config_utils
from kollabor.cli import parse_arguments
from kollabor.llm.session_manager import SessionManager
from kollabor_agent.agent_manager import AgentManager, skill_markdown_body
from kollabor_ai import SystemPromptBuilder
from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "bundles" / "skills" / "kollab-internal-tools" / "SKILL.md"
COORDINATOR = ROOT / "bundles" / "skills" / "repository-coordinator" / "SKILL.md"


class Config:
    def get(self, key, default=None):
        return {
            "kollabor.llm.system_prompt.stable_prefix": True,
            "kollabor.llm.system_prompt.include_project_structure": False,
            "kollabor.llm.system_prompt.attachment_files": ["HANDOFF.md"],
            "kollabor.tool_registry.use_registry": False,
        }.get(key, default)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KOLLAB_HUB_DISABLED", "1")
    monkeypatch.setenv("KOLLAB_HUB_SOLO", "1")
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    for name in (
        "KOLLAB_PROJECT_ROOT",
        "KOLLAB_PROMPT_LIBRARIES_DIR",
        "KOLLAB_PROMPT_ROLES_DIR",
        "KOLLAB_SYSTEM_PROMPT",
        "KOLLAB_SYSTEM_PROMPT_FILE",
    ):
        monkeypatch.delenv(name, raising=False)

    if CATALOG.is_file():
        catalog = CATALOG
    else:
        catalog = home / ".kollab" / "skills" / "kollab-internal-tools" / "SKILL.md"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(
            "---\nname: kollab-internal-tools\ndescription: Tool guide fixture\n"
            "---\n\nFixture tool guide: inspect live availability and permissions.\n",
            encoding="utf-8",
        )

    # The real volatile rail loads the session-context template. Use an inert
    # project-local template so this test never probes host services or processes.
    def project(name, handoff, policy):
        folder = tmp_path / name
        session = folder / ".kollab" / "agents" / "_base" / "sections"
        session.mkdir(parents=True)
        (session / "01-session-context.md").write_text("Isolated session fixture", encoding="utf-8")
        (folder / "AGENTS.md").write_text(policy, encoding="utf-8")
        (folder / "HANDOFF.md").write_text(handoff, encoding="utf-8")
        return folder

    return SimpleNamespace(
        project=project,
        catalog=catalog,
        catalog_body=skill_markdown_body(catalog.read_text(encoding="utf-8")).strip(),
    )


def activate(project, monkeypatch, *, defaults=True):
    monkeypatch.chdir(project)
    manager = AgentManager(project_path=project)
    assert manager.set_active_agent("kollab-operator", load_defaults=defaults)
    agent = manager.get_active_agent()
    assert agent.source == "bundled"
    assert agent.directory.resolve() == ROOT / "bundles" / "agents" / "kollab-operator"
    builder = SystemPromptBuilder(config=Config(), agent_manager=manager)
    return manager, builder


def initialize(builder):
    history = []
    logger = SimpleNamespace(log_user_message=AsyncMock(return_value="initial"))
    session = SessionManager(
        conversation_logger=logger,
        conversation_manager=SimpleNamespace(bind_session_stats=lambda stats: None),
        config=Config(),
        event_bus=None,
        api_service=None,
        prompt_builder=builder,
    )
    assert asyncio.run(session.initialize_conversation(history, history.append)) == "initial"
    return history


def test_required_catalog_is_shipped():
    # The profile must never pass its shipping gate with a missing default skill.
    assert CATALOG.is_file(), "kollab-operator requires the bundled internal-tools catalog"


def test_required_coordinator_is_shipped():
    assert COORDINATOR.is_file(), "kollab-operator requires the bundled repository-coordinator skill"


def test_default_skill_body_enters_the_real_system_message(machine, monkeypatch):
    project = machine.project("one", "Handoff A", "Policy A")
    inactive, inactive_builder = activate(project, monkeypatch, defaults=False)
    assert "kollab-internal-tools" not in inactive.get_active_agent().active_skills
    assert machine.catalog_body not in inactive_builder.build()

    manager, builder = activate(project, monkeypatch)
    agent = manager.get_active_agent()
    assert "kollab-internal-tools" in agent.active_skills
    assert agent.skills["kollab-internal-tools"].file_path.resolve() == machine.catalog.resolve()
    history = initialize(builder)
    assert history[0].metadata["system_initialization"] is True
    assert machine.catalog_body in history[0].content
    assert "## Skill: kollabor-harness" not in history[0].content


def test_real_coordinator_is_available_on_demand_and_local_skills_still_resolve(machine, monkeypatch):
    project = machine.project("neutral", "Plan a catalog update.", "Use the local tracker.")
    local_skill = project / ".kollab" / "skills" / "catalog-work" / "SKILL.md"
    local_skill.parent.mkdir(parents=True)
    local_skill.write_text(
        "---\nname: catalog-work\ndescription: Local catalog procedures\n---\n\nLocal-only catalog procedure.\n",
        encoding="utf-8",
    )
    manager, builder = activate(project, monkeypatch)
    agent = manager.get_active_agent()
    assert COORDINATOR.is_file()
    coordinator = agent.skills["repository-coordinator"]
    assert coordinator.source == "bundled"
    assert coordinator.file_path.resolve() == COORDINATOR
    assert "repository-coordinator" not in agent.active_skills
    assert agent.skills["catalog-work"].source == "local"
    # Restrict unrelated bundled guidance without hiding user-installed skills.
    assert "oss-maintainers" in manager.skill_library.list_names()
    assert "oss-maintainers" not in agent.skills

    coordinator_body = skill_markdown_body(COORDINATOR.read_text(encoding="utf-8")).strip()
    initial = builder.build()
    assert str(COORDINATOR) in initial
    assert str(local_skill) in initial
    assert coordinator_body not in initial
    assert "Local-only catalog procedure." not in initial

    assert manager.load_skill("repository-coordinator")
    assert manager.load_skill("catalog-work")
    loaded = builder.build()
    assert coordinator_body in loaded
    assert "Local-only catalog procedure." in loaded
    assert machine.catalog_body in loaded


def test_seeded_operator_uses_ordinary_files_and_preserves_global_customization(machine, monkeypatch):
    project = machine.project("seeded", "Handoff A", "Policy A")
    monkeypatch.chdir(project)
    original = AgentManager(project_path=project)
    assert original.set_active_agent("kollab-operator")
    assert original.get_active_agent().source == "bundled"

    attempts = []

    def deny_symlink(*args, **kwargs):
        attempts.append((args, kwargs))
        raise PermissionError("symlinks unavailable")

    monkeypatch.setattr(os, "symlink", deny_symlink)
    monkeypatch.setattr(Path, "symlink_to", deny_symlink)
    config_utils.initialize_system_prompt()

    seeded = Path.home() / ".kollab" / "agents" / "kollab-operator"
    for name in ("agent.json", "system_prompt.md"):
        source = ROOT / "bundles" / "agents" / "kollab-operator" / name
        target = seeded / name
        assert target.is_file() and not target.is_symlink()
        assert target.read_bytes() == source.read_bytes()

    global_manager = AgentManager(project_path=project)
    assert global_manager.set_active_agent("kollab-operator")
    assert global_manager.get_active_agent().source == "global"
    assert machine.catalog_body in SystemPromptBuilder(config=Config(), agent_manager=global_manager).build()

    customized = (seeded / "system_prompt.md").read_text(encoding="utf-8") + "\nUser note.\n"
    (seeded / "system_prompt.md").write_text(customized, encoding="utf-8")
    global_skill = Path.home() / ".kollab" / "skills" / "kollab-internal-tools" / "SKILL.md"
    global_skill.parent.mkdir(parents=True, exist_ok=True)
    custom_skill = (
        "---\nname: kollab-internal-tools\ndescription: Customized tool guide\n---\n\nUser-customized tool guidance.\n"
    )
    global_skill.write_text(custom_skill, encoding="utf-8")
    config_utils.initialize_system_prompt()
    assert (seeded / "system_prompt.md").read_text(encoding="utf-8") == customized
    assert global_skill.read_text(encoding="utf-8") == custom_skill
    assert not global_skill.is_symlink()
    resumed_manager = AgentManager(project_path=project)
    assert resumed_manager.set_active_agent("kollab-operator")
    assert resumed_manager.get_active_agent().source == "global"
    assert resumed_manager.get_active_agent().skills["kollab-internal-tools"].source == "global"
    resumed = SystemPromptBuilder(config=Config(), agent_manager=resumed_manager).build()
    assert "User note." in resumed
    assert "User-customized tool guidance." in resumed
    assert attempts == []


def test_operator_and_active_guide_survive_compaction(machine, monkeypatch):
    project = machine.project("one", "Handoff A", "Policy A")
    _, builder = activate(project, monkeypatch)
    system = initialize(builder)[0]
    original = system.content
    assert "# Kollab operator" in original
    assert machine.catalog_body in original

    plugin = ContextCompactionPlugin("test", None, None, Config())
    with patch.object(plugin, "_get_hard_cap_threshold", return_value=0):
        compacted = plugin._build_compacted_history(
            system_msg=system,
            summary_text="A summary that omits the former conversation.",
            to_keep=[ConversationMessage(role="user", content="Continue the task.")],
        )
    assert compacted[0].role == "system"
    assert compacted[0].content == original
    assert machine.catalog_body in compacted[0].content


def test_hub_protocol_does_not_replace_operator_or_active_guide(machine, monkeypatch):
    project = machine.project("one", "Handoff A", "Policy A")
    monkeypatch.delenv("KOLLAB_HUB_DISABLED")
    monkeypatch.delenv("KOLLAB_HUB_SOLO")
    monkeypatch.delenv("KOLLAB_NO_HUB", raising=False)
    _, builder = activate(project, monkeypatch)
    prompt = initialize(builder)[0].content

    # Protocol inclusion is not proof that a live Hub is connected.
    assert "## hub collaboration" in prompt
    assert "# Kollab operator" in prompt
    assert machine.catalog_body in prompt


def test_fresh_instance_receives_its_handoff_without_an_old_chat(machine, monkeypatch):
    first = machine.project(
        "orchard",
        "Directive: repair irrigation. Integrator: river. Task: OR-14. "
        "Ref: review/or-14. Boundary: do not deploy. Evidence: checks/14. "
        "Next: inspect the valve test.",
        "Project policy: use the orchard handoff; integration belongs to river.",
    )
    second = machine.project(
        "observatory",
        "Directive: catalog stars. Owner: sky. Task: OBS-9. "
        "Boundary: read-only. Evidence: notes/9. Next: inspect the catalog.",
        "Project policy: observatory work is read-only.",
    )
    _, first_builder = activate(first, monkeypatch)
    first_system = initialize(first_builder)[0].content
    first_context = dict(first_builder.build_volatile_context())

    # A new manager and a new history get the second project's records, not the
    # first project's chat or its handoff. The attachment is explicitly configured.
    _, second_builder = activate(second, monkeypatch)
    second_system = initialize(second_builder)[0].content
    second_context = dict(second_builder.build_volatile_context())

    assert first_system == second_system
    assert "OR-14" in first_context["context:attachment:HANDOFF.md"]
    assert "integration belongs to river" in first_context["context:agents_md"]
    assert "OBS-9" in second_context["context:attachment:HANDOFF.md"]
    assert "observatory work is read-only" in second_context["context:agents_md"]
    assert "OR-14" not in second_system + "\n".join(second_context.values())
    assert "OBS-9" not in first_system + "\n".join(first_context.values())


def test_bundle_provider_and_hub_identity_remain_separate(machine, monkeypatch):
    project = machine.project("one", "Handoff A", "Policy A")
    args, other = [
        parse_arguments(
            plugin_classes=[],
            argv=["--agent", "kollab-operator", "--llm", provider, "--as", identity],
        )
        for provider, identity in (("provider-a", "river"), ("provider-b", "sky"))
    ]
    manager, _ = activate(project, monkeypatch)
    assert (args.agent, args.llm, args.as_identity) == (
        manager.active_agent_name,
        "provider-a",
        "river",
    )
    assert (other.agent, other.llm, other.as_identity) == (
        manager.active_agent_name,
        "provider-b",
        "sky",
    )
    assert manager.get_preferred_profile() is None
    assert not manager.get_active_agent().identity
