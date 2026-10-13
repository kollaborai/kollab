import sys

import pytest
from acp.schema import PermissionOption, ToolCallStart

from kollabor.llm.acp_bridge import ACPError, ACPPermissionBridge, ACPProfile, ACPRunner

FAKE_ADAPTER = """
import asyncio
import acp
from acp.schema import (
    AgentMessageChunk, InitializeResponse, NewSessionResponse,
    PermissionOption, PromptResponse, TextContentBlock, ToolCallStart,
)

class FakeAgent:
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId='s1')
    async def prompt(self, session_id, prompt, **kwargs):
        await self._conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate='agent_message_chunk',
            content=TextContentBlock(type='text', text='hello '),
        ))
        tool = ToolCallStart(
            sessionUpdate='tool_call', toolCallId='t1', title='ignored', kind='execute', status='completed',
        )
        await self._conn.session_update(session_id, tool)
        return PromptResponse(stopReason='end_turn')
    async def cancel(self, session_id, **kwargs):
        return None
    def on_connect(self, conn):
        self._conn = conn

asyncio.run(acp.run_agent(FakeAgent()))
"""


@pytest.mark.asyncio
async def test_profile_resolution_strips_environment_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    command, env, cwd = profile.resolve()
    assert command[0] == sys.executable
    assert "OPENAI_API_KEY" not in env
    assert cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_runner_uses_sdk_agent_for_streams_answer_and_closes_process(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    result = await runner.prompt("hello")
    assert result.session_id == "s1"
    assert result.text == "hello "
    assert result.generation == 1
    assert result.end_frame["type"] == "turn_end"
    assert any(update["kind"] == "tool_call" for update in result.updates)
    await runner.close()
    assert runner._process is None


@pytest.mark.asyncio
async def test_permission_bridge_fails_closed_without_decider():
    bridge = ACPPermissionBridge()
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "allow", "kind": "allow_once"},),
    })()
    assert await bridge.request(request) is None


@pytest.mark.asyncio
async def test_permission_response_is_nested_and_option_bound(tmp_path):
    async def decide(request):
        return "allow"

    runner = ACPRunner(ACPProfile("fake", (sys.executable,), cwd=str(tmp_path)), ACPPermissionBridge(decide))
    runner.session_id = "s1"
    runner._active_generation = 1
    runner.permission_bridge.bind("s1", 1)
    response = await runner._request_permission(
        "s1",
        ToolCallStart(sessionUpdate="tool_call", toolCallId="rpc-1", title="read", kind="read"),
        [PermissionOption(optionId="allow", name="Allow once", kind="allow_once")],
    )
    assert response.outcome.outcome == "selected"
    assert response.outcome.optionId == "allow"


def test_missing_adapter_is_a_hard_gate(tmp_path):
    profile = ACPProfile("missing", ("definitely-not-an-acp-adapter",), cwd=str(tmp_path))
    with pytest.raises(ACPError):
        profile.resolve()
