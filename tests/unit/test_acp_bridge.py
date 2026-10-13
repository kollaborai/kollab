import asyncio
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
    def __init__(self):
        self._sessions = 0
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        self._sessions += 1
        return NewSessionResponse(sessionId=f's{self._sessions}')
    async def prompt(self, session_id, prompt, **kwargs):
        import asyncio
        await asyncio.sleep(0.05)
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
    profile = ACPProfile.from_dict("mcp", {
        "command": [sys.executable],
        "mcp_servers": [{"name": "hub", "command": "kollab", "args": ["--mcp-hub"]}],
    })
    assert profile.sdk_mcp_servers()[0].name == "hub"


@pytest.mark.asyncio
async def test_queued_turn_after_cancel_rebases_generation(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    await runner.cancel()
    with pytest.raises(ACPError):
        await first
    result2 = await second
    assert result2.session_id == "s1"
    assert result2.generation == 2
    assert result2.text == "hello "
    await runner.close()


@pytest.mark.asyncio
async def test_queued_turn_keeps_first_generation_updates(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    result1, result2 = await asyncio.gather(first, second)
    assert result1.text == "hello "
    assert result2.text == "hello "
    await runner.close()


@pytest.mark.asyncio
async def test_permission_cancel_while_decider_never_returns():
    started = asyncio.Event()

    async def decide(request):
        started.set()
        await asyncio.Future()

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "allow", "kind": "allow_once"},),
    })()
    pending = asyncio.create_task(bridge.request(request))
    await started.wait()
    bridge.cancel()
    assert await asyncio.wait_for(pending, 1) is None


@pytest.mark.asyncio
async def test_permission_cancel_after_decider_await_fails_closed():
    started = asyncio.Event()
    release = asyncio.Event()

    async def decide(request):
        started.set()
        await release.wait()
        return "allow"

    bridge = ACPPermissionBridge(decide)
    bridge.bind("s1", 1)
    request = type("Request", (), {
        "session_id": "s1", "generation": 1,
        "options": ({"optionId": "allow", "kind": "allow_once"},),
    })()
    pending = asyncio.create_task(bridge.request(request))
    await started.wait()
    bridge.cancel()
    release.set()
    assert await pending is None


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
