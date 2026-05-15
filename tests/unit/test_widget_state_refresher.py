"""Tests for WidgetStateRefresher remote_state ownership."""

from types import SimpleNamespace

import pytest

from kollabor.state.refresher import WidgetStateRefresher
from kollabor.state.snapshots import (
    AgentSnapshot,
    HubSnapshot,
    McpSnapshot,
    PermissionSnapshot,
    ProcessingSnapshot,
    ProfileSnapshot,
    SessionStats,
    SkillInfo,
    SkillListSnapshot,
    SystemInfoSnapshot,
)


class FakeStateService:
    async def get_session_stats(self):
        return SessionStats(messages=2, input_tokens=10, output_tokens=5)

    async def get_processing_state(self):
        return ProcessingSnapshot(is_processing=True, pending_tools_count=1)

    async def get_system_info(self):
        return SystemInfoSnapshot(cwd="/tmp/project", git_branch="main")

    async def get_hub_state(self):
        return HubSnapshot(my_identity="lapis", peer_count=3)

    async def get_mcp_state(self):
        return McpSnapshot(connected_servers=1, total_tools=4)

    async def get_active_profile(self):
        return ProfileSnapshot(name="openai-oauth", model="gpt-test")

    async def get_permission_state(self):
        return PermissionSnapshot(approval_mode="DEFAULT")

    async def get_active_agent(self):
        return AgentSnapshot(name="coder", active_skills=["tdd"], is_active=True)

    async def list_skills(self, agent_name: str = ""):
        return SkillListSnapshot(
            agent_name="coder",
            skills=[
                SkillInfo(name="tdd", active=True),
                SkillInfo(name="docs", active=False),
                SkillInfo(name="system_prompt", active=True),
            ],
        )


@pytest.mark.asyncio
async def test_refresh_merges_without_erasing_existing_remote_state_keys():
    ctx = SimpleNamespace(
        remote_state={
            "agent": "legacy-agent",
            "skills": "legacy-skill",
            "legacy_only": "keep-me",
        }
    )
    refresher = WidgetStateRefresher(ctx, FakeStateService())

    await refresher.refresh_once()

    assert ctx.remote_state["legacy_only"] == "keep-me"
    assert ctx.remote_state["profile_name"] == "openai-oauth"


@pytest.mark.asyncio
async def test_refresh_adds_agent_and_active_skills_from_state_service():
    ctx = SimpleNamespace(remote_state={})
    refresher = WidgetStateRefresher(ctx, FakeStateService())

    await refresher.refresh_once()

    assert ctx.remote_state["agent"] == "coder"
    assert ctx.remote_state["skills"] == "tdd"
