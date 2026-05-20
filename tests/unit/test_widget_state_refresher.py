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
        return SessionStats(
            messages=2,
            input_tokens=10,
            output_tokens=5,
            total_input_tokens=100,
            total_output_tokens=50,
        )

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
    assert ctx.remote_state["total_input_tokens"] == 100
    assert ctx.remote_state["total_output_tokens"] == 50


@pytest.mark.asyncio
async def test_refresh_adds_agent_and_active_skills_from_state_service():
    ctx = SimpleNamespace(remote_state={})
    refresher = WidgetStateRefresher(ctx, FakeStateService())

    await refresher.refresh_once()

    assert ctx.remote_state["agent"] == "coder"
    assert ctx.remote_state["skills"] == "tdd"


@pytest.mark.asyncio
async def test_refresh_requests_render_after_remote_state_update():
    ctx = SimpleNamespace(remote_state={})
    render_requests = []
    refresher = WidgetStateRefresher(
        ctx,
        FakeStateService(),
        request_render=lambda: render_requests.append("render"),
    )

    await refresher.refresh_once()

    assert render_requests == ["render"]


@pytest.mark.asyncio
async def test_refresh_does_not_request_render_for_timestamp_only_update():
    ctx = SimpleNamespace(remote_state={})
    render_requests = []
    refresher = WidgetStateRefresher(
        ctx,
        FakeStateService(),
        request_render=lambda: render_requests.append("render"),
    )

    await refresher.refresh_once()
    await refresher.refresh_once()

    assert render_requests == ["render"]


@pytest.mark.asyncio
async def test_refresh_embeds_widget_state_freshness_metadata():
    ctx = SimpleNamespace(remote_state={}, runtime_mode="attach")
    refresher = WidgetStateRefresher(ctx, FakeStateService())

    await refresher.refresh_once()

    assert ctx.remote_state["_source"] == "state_service"
    assert ctx.remote_state["_updated_at"] > 0
    assert ctx.remote_state["_stale"] is False
    assert ctx.remote_state["_degraded"] is False
    assert ctx.remote_state["runtime_mode"] == "attach"


@pytest.mark.asyncio
async def test_refresh_preserves_existing_fields_when_snapshot_is_partial():
    class PartialStateService(FakeStateService):
        async def get_session_stats(self):
            raise RuntimeError("stats unavailable")

    ctx = SimpleNamespace(
        remote_state={
            "messages": 5,
            "cache_read_tokens": 46800,
            "legacy_only": "keep-me",
        }
    )
    refresher = WidgetStateRefresher(ctx, PartialStateService())

    await refresher.refresh_once()

    assert ctx.remote_state["messages"] == 5
    assert ctx.remote_state["cache_read_tokens"] == 46800
    assert ctx.remote_state["legacy_only"] == "keep-me"
    assert ctx.remote_state["profile_name"] == "openai-oauth"
    assert ctx.remote_state["_degraded"] is True
