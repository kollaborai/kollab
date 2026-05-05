"""Unit tests for phase 4.5 LocalStateService additions.

Covers:
  - get_active_agent / list_agents / set_agent / clear_agent
  - list_skills / activate_skill / deactivate_skill
  - get_system_prompt / set_system_prompt

All tests use mock services so we don't need to boot a real llm_service.
The goal is to prove the wrapper logic (parameter validation, error
envelopes, snapshot shape, lock acquisition path) is correct -- the
real integration behavior is exercised by the tmux smoke test matrix
in step 10.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor.state import (
    AgentListSnapshot,
    AgentSnapshot,
    LocalStateService,
    SkillInfo,
    SkillListSnapshot,
    SystemPromptSnapshot,
)


def _run(coro):
    """Run an async coroutine to completion, returning the result."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_skill(
    name: str, description: str = "", content: str = "", source: str = "bundled"
) -> MagicMock:
    """Build a skill mock matching kollabor_agent.agent_manager.Skill shape."""
    skill = MagicMock()
    skill.name = name
    skill.description = description
    skill.content = content
    skill.source = source
    return skill


def _make_agent(
    *,
    name: str = "tech-dude",
    description: str = "test agent",
    profile: str = "",
    source: str = "bundled",
    default_skills: list[str] | None = None,
    active_skills: list[str] | None = None,
    skills: dict | None = None,
    system_prompt: str = "",
) -> MagicMock:
    """Build an AgentRuntime-shaped mock."""
    agent = MagicMock()
    agent.name = name
    agent.description = description
    agent.profile = profile
    agent.source = source
    agent.default_skills = default_skills or []
    agent.active_skills = active_skills or []
    agent.skills = skills or {}
    agent.system_prompt = system_prompt
    # load_skill / unload_skill return True by default
    agent.load_skill = MagicMock(return_value=True)
    agent.unload_skill = MagicMock(return_value=True)
    return agent


def _make_agent_manager(
    *,
    active: MagicMock | None = None,
    registry: dict[str, MagicMock] | None = None,
) -> MagicMock:
    """Build an AgentManager-shaped mock."""
    mgr = MagicMock()
    mgr.get_active_agent = MagicMock(return_value=active)
    mgr._active_agent_name = active.name if active else ""
    mgr.get_agent_names = MagicMock(return_value=list((registry or {}).keys()))
    mgr.get_agent = MagicMock(side_effect=lambda name: (registry or {}).get(name))
    mgr.list_agents = MagicMock(return_value=list((registry or {}).values()))

    def _set_active(name: str) -> bool:
        if not registry or name not in registry:
            return False
        mgr._active_agent_name = name
        mgr.get_active_agent.return_value = registry[name]
        return True

    mgr.set_active_agent = MagicMock(side_effect=_set_active)
    mgr.clear_active_agent = MagicMock(
        side_effect=lambda: setattr(mgr, "_active_agent_name", "")
    )
    return mgr


def _make_llm_service(*, with_inject: bool = True) -> MagicMock:
    """Build an llm_service mock with the canonical injection primitive."""
    llm = MagicMock()
    llm.conversation_history = []
    llm.rebuild_system_prompt = MagicMock(return_value=None)
    llm.system_prompt = ""
    if with_inject:
        llm.inject_system_message = MagicMock(return_value=None)
    else:
        # Simulate a build that doesn't have inject_system_message; the
        # fallback path should then append directly to conversation_history.
        if hasattr(llm, "inject_system_message"):
            del llm.inject_system_message
    return llm


# === Agents ===


class TestGetActiveAgent(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_agent_manager(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=None,
        )
        snap = await svc.get_active_agent()
        self.assertEqual(snap.name, "")
        self.assertFalse(snap.is_active)

    async def test_returns_snapshot_from_manager(self) -> None:
        agent = _make_agent(
            name="backend",
            description="backend engineer",
            profile="claude",
            default_skills=["tdd"],
            active_skills=["tdd", "code-review"],
            skills={
                "tdd": _make_skill("tdd"),
                "code-review": _make_skill("code-review"),
                "docs": _make_skill("docs"),
            },
        )
        mgr = _make_agent_manager(active=agent)
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        snap = await svc.get_active_agent()
        self.assertEqual(snap.name, "backend")
        self.assertEqual(snap.description, "backend engineer")
        self.assertEqual(snap.profile, "claude")
        self.assertTrue(snap.is_active)
        self.assertEqual(sorted(snap.available_skills), ["code-review", "docs", "tdd"])
        self.assertEqual(sorted(snap.active_skills), ["code-review", "tdd"])
        self.assertEqual(snap.default_skills, ["tdd"])

    async def test_empty_agent_manager_returns_empty_snapshot(self) -> None:
        mgr = _make_agent_manager(active=None)
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        snap = await svc.get_active_agent()
        self.assertEqual(snap.name, "")
        self.assertFalse(snap.is_active)


class TestListAgents(unittest.IsolatedAsyncioTestCase):
    async def test_list_agents_marks_active(self) -> None:
        alice = _make_agent(name="alice")
        bob = _make_agent(name="bob")
        mgr = _make_agent_manager(active=alice, registry={"alice": alice, "bob": bob})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        listing = await svc.list_agents()
        self.assertIsInstance(listing, AgentListSnapshot)
        self.assertEqual(listing.active, "alice")
        names = {a.name for a in listing.agents}
        self.assertEqual(names, {"alice", "bob"})
        actives = [a for a in listing.agents if a.is_active]
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives[0].name, "alice")

    async def test_list_agents_no_manager(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=None,
        )
        listing = await svc.list_agents()
        self.assertEqual(listing.active, "")
        self.assertEqual(listing.agents, [])


class TestSetAgent(unittest.IsolatedAsyncioTestCase):
    async def test_set_agent_success(self) -> None:
        alice = _make_agent(name="alice")
        bob = _make_agent(name="bob")
        mgr = _make_agent_manager(active=alice, registry={"alice": alice, "bob": bob})
        llm = _make_llm_service()
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=mgr
        )
        snap = await svc.set_agent("bob")
        self.assertEqual(snap.name, "bob")
        self.assertTrue(snap.is_active)
        mgr.set_active_agent.assert_called_with("bob")
        llm.rebuild_system_prompt.assert_called_once()

    async def test_set_agent_unknown_raises(self) -> None:
        alice = _make_agent(name="alice")
        mgr = _make_agent_manager(active=alice, registry={"alice": alice})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        with self.assertRaises(ValueError) as cm:
            await svc.set_agent("nonexistent")
        self.assertIn("agent not found", str(cm.exception))

    async def test_set_agent_empty_name_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=_make_agent_manager(),
        )
        with self.assertRaises(ValueError):
            await svc.set_agent("")
        with self.assertRaises(ValueError):
            await svc.set_agent("   ")

    async def test_set_agent_no_manager_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=None,
        )
        with self.assertRaises(ValueError):
            await svc.set_agent("alice")

    async def test_set_agent_handles_async_rebuild(self) -> None:
        """rebuild_system_prompt may be async. Verify we await it."""
        alice = _make_agent(name="alice")
        mgr = _make_agent_manager(active=alice, registry={"alice": alice})
        llm = _make_llm_service()
        rebuild_mock = AsyncMock()
        llm.rebuild_system_prompt = rebuild_mock
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=mgr
        )
        await svc.set_agent("alice")
        rebuild_mock.assert_awaited_once()


class TestClearAgent(unittest.IsolatedAsyncioTestCase):
    async def test_clear_agent_returns_previous_snapshot(self) -> None:
        alice = _make_agent(name="alice")
        mgr = _make_agent_manager(active=alice, registry={"alice": alice})
        llm = _make_llm_service()
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=mgr
        )
        snap = await svc.clear_agent()
        # Returned snapshot is the agent that was cleared, marked inactive.
        self.assertEqual(snap.name, "alice")
        self.assertFalse(snap.is_active)
        mgr.clear_active_agent.assert_called_once()
        llm.rebuild_system_prompt.assert_called_once()

    async def test_clear_agent_when_none_active(self) -> None:
        mgr = _make_agent_manager(active=None)
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        snap = await svc.clear_agent()
        self.assertEqual(snap.name, "")
        self.assertFalse(snap.is_active)


# === Skills ===


class TestListSkills(unittest.IsolatedAsyncioTestCase):
    async def test_list_skills_for_active_agent(self) -> None:
        agent = _make_agent(
            name="coder",
            active_skills=["tdd"],
            skills={
                "tdd": _make_skill("tdd", "Test-driven development", "body1"),
                "refactor": _make_skill("refactor", "Refactor code", "body2"),
            },
        )
        mgr = _make_agent_manager(active=agent, registry={"coder": agent})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        snap = await svc.list_skills()
        self.assertIsInstance(snap, SkillListSnapshot)
        self.assertEqual(snap.agent_name, "coder")
        names = {s.name for s in snap.skills}
        self.assertEqual(names, {"tdd", "refactor"})
        tdd = next(s for s in snap.skills if s.name == "tdd")
        self.assertTrue(tdd.active)
        self.assertEqual(tdd.description, "Test-driven development")
        self.assertEqual(tdd.content_length, len("body1"))
        refactor = next(s for s in snap.skills if s.name == "refactor")
        self.assertFalse(refactor.active)

    async def test_list_skills_for_named_agent(self) -> None:
        alice = _make_agent(
            name="alice",
            active_skills=[],
            skills={"tdd": _make_skill("tdd")},
        )
        bob = _make_agent(
            name="bob",
            skills={"docs": _make_skill("docs")},
        )
        mgr = _make_agent_manager(active=alice, registry={"alice": alice, "bob": bob})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        snap = await svc.list_skills("bob")
        self.assertEqual(snap.agent_name, "bob")
        self.assertEqual([s.name for s in snap.skills], ["docs"])

    async def test_list_skills_unknown_agent(self) -> None:
        mgr = _make_agent_manager(active=None, registry={})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        snap = await svc.list_skills("nonexistent")
        self.assertEqual(snap.agent_name, "nonexistent")
        self.assertEqual(snap.skills, [])


class TestActivateSkill(unittest.IsolatedAsyncioTestCase):
    async def test_activate_calls_injection_primitive(self) -> None:
        agent = _make_agent(
            name="coder",
            active_skills=[],
            skills={"tdd": _make_skill("tdd", "TDD skill", "tdd body content")},
        )
        mgr = _make_agent_manager(active=agent, registry={"coder": agent})
        llm = _make_llm_service()
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=mgr
        )
        snap = await svc.activate_skill("tdd")
        # The agent's load_skill was called
        agent.load_skill.assert_called_with("tdd")
        # inject_system_message was called with something containing the skill body
        self.assertTrue(llm.inject_system_message.called)
        call_args = llm.inject_system_message.call_args
        body = call_args[0][0]
        self.assertIn("tdd", body)
        self.assertIn("tdd body content", body)
        self.assertIn("loaded", body)
        # The returned snapshot has up-to-date state
        self.assertIsInstance(snap, SkillListSnapshot)

    async def test_activate_unknown_skill_raises(self) -> None:
        agent = _make_agent(
            name="coder",
            skills={"tdd": _make_skill("tdd")},
        )
        mgr = _make_agent_manager(active=agent, registry={"coder": agent})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        with self.assertRaises(ValueError) as cm:
            await svc.activate_skill("nonexistent")
        self.assertIn("skill not found", str(cm.exception))

    async def test_activate_without_active_agent_raises(self) -> None:
        mgr = _make_agent_manager(active=None)
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        with self.assertRaises(ValueError) as cm:
            await svc.activate_skill("tdd")
        self.assertIn("no active agent", str(cm.exception))

    async def test_activate_empty_name_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=_make_agent_manager(),
        )
        with self.assertRaises(ValueError):
            await svc.activate_skill("")
        with self.assertRaises(ValueError):
            await svc.activate_skill("   ")

    async def test_activate_fallback_appends_to_history(self) -> None:
        """When llm_service has no inject_system_message, activation
        should fall back to appending directly to conversation_history."""
        agent = _make_agent(
            name="coder",
            active_skills=[],
            skills={"tdd": _make_skill("tdd", "TDD", "body content")},
        )
        mgr = _make_agent_manager(active=agent, registry={"coder": agent})
        llm = _make_llm_service(with_inject=False)
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=mgr
        )
        # Should not raise even without the canonical primitive.
        await svc.activate_skill("tdd")
        # Verify the skill loaded on the agent and history got one entry
        agent.load_skill.assert_called_with("tdd")
        self.assertEqual(len(llm.conversation_history), 1)


class TestDeactivateSkill(unittest.IsolatedAsyncioTestCase):
    async def test_deactivate_success(self) -> None:
        agent = _make_agent(
            name="coder",
            active_skills=["tdd"],
            skills={"tdd": _make_skill("tdd")},
        )
        mgr = _make_agent_manager(active=agent, registry={"coder": agent})
        llm = _make_llm_service()
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=mgr
        )
        snap = await svc.deactivate_skill("tdd")
        agent.unload_skill.assert_called_with("tdd")
        # inject called with unloaded marker
        self.assertTrue(llm.inject_system_message.called)
        call_args = llm.inject_system_message.call_args
        body = call_args[0][0]
        self.assertIn("unloaded", body)
        self.assertIsInstance(snap, SkillListSnapshot)

    async def test_deactivate_not_active_raises(self) -> None:
        agent = _make_agent(
            name="coder",
            active_skills=[],
            skills={"tdd": _make_skill("tdd")},
        )
        mgr = _make_agent_manager(active=agent, registry={"coder": agent})
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=mgr,
        )
        with self.assertRaises(ValueError) as cm:
            await svc.deactivate_skill("tdd")
        self.assertIn("not active", str(cm.exception))

    async def test_deactivate_empty_name_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=_make_agent_manager(),
        )
        with self.assertRaises(ValueError):
            await svc.deactivate_skill("")


# === System prompt ===


class TestGetSystemPrompt(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_llm_service(self) -> None:
        svc = LocalStateService(
            llm_service=None, profile_manager=MagicMock(), agent_manager=None
        )
        snap = await svc.get_system_prompt()
        self.assertIsInstance(snap, SystemPromptSnapshot)
        self.assertEqual(snap.content, "")
        self.assertEqual(snap.size_chars, 0)

    async def test_reads_from_system_prompt_attribute(self) -> None:
        llm = _make_llm_service()
        llm.system_prompt = "You are a test assistant."
        # Make sure get_current_system_prompt is not present so we fall
        # through to the attribute reader.
        if hasattr(llm, "get_current_system_prompt"):
            del llm.get_current_system_prompt
        # Also remove _cli_system_prompt_file so source detection picks
        # "default" rather than "file"
        if hasattr(llm, "_cli_system_prompt_file"):
            del llm._cli_system_prompt_file
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=None
        )
        snap = await svc.get_system_prompt()
        self.assertEqual(snap.content, "You are a test assistant.")
        self.assertEqual(snap.size_chars, len("You are a test assistant."))


class TestSetSystemPrompt(unittest.IsolatedAsyncioTestCase):
    async def test_set_via_attribute_assign(self) -> None:
        # Use a bare class (not MagicMock) so hasattr only returns True
        # for attributes we explicitly define, forcing the set_system_prompt
        # code path to fall through to the attribute-assign branch.
        class _BareLlm:
            system_prompt = ""

            def rebuild_system_prompt(self) -> None:
                return None

        llm = _BareLlm()
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=None
        )
        snap = await svc.set_system_prompt(
            "New prompt content", source="file", path="/tmp/p.md"
        )
        self.assertEqual(snap.source, "file")
        self.assertEqual(snap.path, "/tmp/p.md")
        self.assertEqual(snap.content, "New prompt content")
        self.assertEqual(snap.size_chars, len("New prompt content"))
        # The attribute was actually set on the llm service
        self.assertEqual(llm.system_prompt, "New prompt content")

    async def test_set_via_set_system_prompt_content_method(self) -> None:
        """When llm_service has a set_system_prompt_content method,
        the state service should prefer that over raw attribute assignment."""
        calls: list[str] = []

        class _LlmWithMethod:
            def set_system_prompt_content(self, content: str) -> None:
                calls.append(content)

            def rebuild_system_prompt(self) -> None:
                return None

        llm = _LlmWithMethod()
        svc = LocalStateService(
            llm_service=llm, profile_manager=MagicMock(), agent_manager=None
        )
        snap = await svc.set_system_prompt("via method", source="inline")
        self.assertEqual(snap.content, "via method")
        self.assertEqual(calls, ["via method"])

    async def test_empty_content_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=None,
        )
        with self.assertRaises(ValueError):
            await svc.set_system_prompt("")
        with self.assertRaises(ValueError):
            await svc.set_system_prompt("   \n\n   ")

    async def test_oversize_content_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=None,
        )
        huge = "x" * (1_048_577)  # 1 MB + 1 byte
        with self.assertRaises(ValueError) as cm:
            await svc.set_system_prompt(huge)
        self.assertIn("too large", str(cm.exception))

    async def test_non_string_content_raises(self) -> None:
        svc = LocalStateService(
            llm_service=_make_llm_service(),
            profile_manager=MagicMock(),
            agent_manager=None,
        )
        with self.assertRaises(ValueError):
            await svc.set_system_prompt(12345)  # type: ignore[arg-type]

    async def test_no_llm_service_raises(self) -> None:
        svc = LocalStateService(
            llm_service=None, profile_manager=MagicMock(), agent_manager=None
        )
        with self.assertRaises(ValueError):
            await svc.set_system_prompt("prompt")


# === Snapshot round-trips (wire safety) ===


class TestSnapshotRoundTrips(unittest.TestCase):
    """Ensure the new snapshot DTOs survive to_dict / from_dict."""

    def test_agent_snapshot_round_trip(self) -> None:
        original = AgentSnapshot(
            name="coder",
            description="writes code",
            profile="claude",
            active_skills=["tdd"],
            available_skills=["tdd", "refactor"],
            default_skills=["tdd"],
            is_active=True,
            source="bundled",
        )
        data = original.to_dict()
        restored = AgentSnapshot.from_dict(data)
        self.assertEqual(restored, original)

    def test_agent_list_snapshot_round_trip(self) -> None:
        original = AgentListSnapshot(
            active="alice",
            agents=[
                AgentSnapshot(name="alice", is_active=True),
                AgentSnapshot(name="bob", is_active=False),
            ],
        )
        data = original.to_dict()
        restored = AgentListSnapshot.from_dict(data)
        self.assertEqual(restored.active, "alice")
        self.assertEqual(len(restored.agents), 2)
        self.assertEqual(restored.agents[0].name, "alice")

    def test_skill_info_round_trip(self) -> None:
        original = SkillInfo(
            name="tdd",
            description="Test-driven development",
            active=True,
            source="bundled",
            content_length=512,
        )
        data = original.to_dict()
        restored = SkillInfo.from_dict(data)
        self.assertEqual(restored, original)

    def test_skill_list_snapshot_round_trip(self) -> None:
        original = SkillListSnapshot(
            agent_name="coder",
            skills=[
                SkillInfo(name="tdd", active=True),
                SkillInfo(name="refactor", active=False),
            ],
        )
        data = original.to_dict()
        restored = SkillListSnapshot.from_dict(data)
        self.assertEqual(restored.agent_name, "coder")
        self.assertEqual(len(restored.skills), 2)

    def test_system_prompt_snapshot_round_trip(self) -> None:
        original = SystemPromptSnapshot(
            source="file",
            path="/tmp/prompt.md",
            content="Be helpful.",
            size_chars=len("Be helpful."),
            rendered_tags=["project_tree"],
        )
        data = original.to_dict()
        restored = SystemPromptSnapshot.from_dict(data)
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
