import asyncio
import sys

import pytest
from acp.schema import PermissionOption, ToolCallStart

from kollabor.llm import acp_bridge as bridge_mod
from kollabor.llm.acp_bridge import ACPError, ACPPermissionBridge, ACPProfile, ACPRunner

STUBBORN_ADAPTER = """
import asyncio
import os
import acp
from acp.schema import AgentMessageChunk, InitializeResponse, NewSessionResponse, PromptResponse, TextContentBlock

class FakeAgent:
    def __init__(self):
        self._conn = None
        self._run_file = os.environ["ACP_RUN_FILE"]
        try:
            self._run = int(open(self._run_file).read()) + 1
        except (FileNotFoundError, ValueError):
            self._run = 1
        with open(self._run_file, "w") as f:
            f.write(str(self._run))
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId=f"s{self._run}")
    async def prompt(self, session_id, prompt, **kwargs):
        if self._run == 1:
            async def late_update():
                await asyncio.sleep(0.2)
                await self._conn.session_update(session_id, AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=TextContentBlock(type="text", text="late-a "),
                ))
            asyncio.create_task(late_update())
            await asyncio.Future()
        await self._conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk",
            content=TextContentBlock(type="text", text="recovered "),
        ))
        return PromptResponse(stopReason="end_turn")
    async def cancel(self, session_id, **kwargs):
        return None
    def on_connect(self, conn):
        self._conn = conn

asyncio.run(acp.run_agent(FakeAgent()))
"""


COMPLIANT_ADAPTER = """
import asyncio
import acp
from acp.schema import AgentMessageChunk, InitializeResponse, NewSessionResponse, PromptResponse, TextContentBlock

class FakeAgent:
    def __init__(self):
        self._conn = None
        self._cancel = asyncio.Event()
    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId="preserved")
    async def prompt(self, session_id, prompt, **kwargs):
        await self._cancel.wait()
        await self._conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk",
            content=TextContentBlock(type="text", text="drained "),
        ))
        return PromptResponse(stopReason="cancelled")
    async def cancel(self, session_id, **kwargs):
        self._cancel.set()
    def on_connect(self, conn):
        self._conn = conn

asyncio.run(acp.run_agent(FakeAgent()))
"""

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
    assert result2.generation == 3
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
async def test_compliant_cancel_preserves_process_session_and_single_consumer(tmp_path):
    profile = ACPProfile("compliant", (sys.executable, "-c", COMPLIANT_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    process = runner._process
    worker = runner._worker
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(runner.prompt("second"))
    await runner.cancel()
    with pytest.raises(ACPError):
        await first
    result = await second
    assert result.text == "drained "
    assert result.session_id == "preserved"
    assert result.generation == 2
    assert runner._process is process
    assert runner.session_id == "preserved"
    assert runner._worker is worker
    assert not runner._worker.done()
    await runner.close()


@pytest.mark.asyncio
async def test_ignored_cancel_kills_old_process_and_restarts_before_queue(tmp_path):
    run_file = tmp_path / "runs"
    profile = ACPProfile(
        "stubborn",
        (sys.executable, "-c", STUBBORN_ADAPTER),
        cwd=str(tmp_path),
        env={"ACP_RUN_FILE": str(run_file)},
    )
    runner = ACPRunner(profile)
    runner.cancel_timeout = 0.1
    await runner.start()
    old_process = runner._process
    first = asyncio.create_task(runner.prompt("first"))
    await asyncio.sleep(0.03)
    second = asyncio.create_task(runner.prompt("second"))
    await runner.cancel()
    with pytest.raises(ACPError):
        await first
    result = await asyncio.wait_for(second, 2)
    assert result.text == "recovered "
    assert runner._process is not old_process
    assert old_process.returncode is not None
    assert runner._worker is not None and not runner._worker.done()
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

@pytest.mark.asyncio
async def test_admission_accepts_exactly_ten_waiting_turns(tmp_path):
    profile = ACPProfile("fake", (sys.executable, "-c", FAKE_ADAPTER), cwd=str(tmp_path))
    runner = ACPRunner(profile)
    await runner.start()
    runner._queue_release.clear()
    admissions = [
        runner.admit(f"m{i}", request_id=f"r{i}", source="state_rpc")
        for i in range(10)
    ]
    assert [item.request_id for item in admissions] == [f"r{i}" for i in range(10)]
    with pytest.raises(ACPError, match="full"):
        runner.admit("overflow", request_id="overflow", source="state_rpc")
    await runner.close()


@pytest.mark.asyncio
async def test_restart_failure_cleans_connection_process_and_stderr(tmp_path, monkeypatch):
    runner = ACPRunner(ACPProfile("fake", (sys.executable,), cwd=str(tmp_path)))

    class Connection:
        async def close(self):
            raise RuntimeError("close failed")

    class Process:
        pid = 4242
        returncode = None

        async def wait(self):
            self.returncode = 0

    process = Process()
    runner._connection = Connection()
    runner._process = process
    runner._stderr_task = asyncio.create_task(asyncio.sleep(60))
    monkeypatch.setattr(bridge_mod.os, "killpg", lambda *_args: None)

    async def fail_start():
        raise RuntimeError("initialize failed")

    runner._start_process = fail_start
    with pytest.raises(RuntimeError, match="initialize failed"):
        await runner._restart_process()

    assert runner._connection is None
    assert runner._process is None
    assert runner._stderr_task is None
    assert process.returncode == 0


def test_final_answer_uses_last_message_id_and_preserves_stream():
    updates = [
        {"kind": "text", "text": "working", "message_id": "commentary"},
        {"kind": "tool_call", "payload": {}},
        {"kind": "text", "text": "answer ", "message_id": "final"},
        {"kind": "text", "text": "now", "message_id": "final"},
    ]
    assert ACPRunner._select_answer(updates) == "answer now"
    assert updates[0]["message_id"] == "commentary"
    assert updates[-1]["message_id"] == "final"


def test_final_answer_without_ids_prefers_text_after_last_tool():
    updates = [
        {"kind": "text", "text": "before "},
        {"kind": "tool_call", "payload": {"x": 1}},
        {"kind": "thought", "payload": {"hidden": True}},
        {"kind": "text", "text": "after"},
    ]
    assert ACPRunner._select_answer(updates) == "after"


def test_final_answer_without_ids_falls_back_before_tool_and_ignores_output():
    updates = [
        {"kind": "text", "text": "answer"},
        {"kind": "tool_output", "payload": {"secret": "ignored"}},
        {"kind": "thought", "payload": {"secret": "ignored"}},
    ]
    assert ACPRunner._select_answer(updates) == "answer"


def test_final_answer_without_ids_uses_last_segment_before_new_tool():
    updates = [
        {"kind": "text", "text": "Planning. "},
        {"kind": "tool_call", "payload": {"toolCallId": "t1"}},
        {"kind": "text", "text": "Last answer. "},
        {"kind": "tool_call", "payload": {"toolCallId": "t2"}},
    ]
    assert ACPRunner._select_answer(updates) == "Last answer. "


def test_tool_progress_does_not_create_new_message_boundary():
    updates = [
        {"kind": "text", "text": "Planning. "},
        {"kind": "tool_call", "payload": {"toolCallId": "t1"}},
        {"kind": "text", "text": "Final answer. "},
        {"kind": "tool_call_update", "payload": {"toolCallId": "t1"}},
    ]
    assert ACPRunner._select_answer(updates) == "Final answer. "


@pytest.mark.asyncio
async def test_profile_resolution_uses_cache_and_strips_provider_keys(tmp_path, monkeypatch):
    adapter = tmp_path / "adapter"
    adapter.write_text("#!/bin/sh\n")
    adapter.chmod(0o755)
    config = tmp_path / "config"
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(config))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-pass")
    monkeypatch.setenv("CUSTOM_ENV", "kept")
    profile = ACPProfile("custom", (str(adapter),), cwd=str(tmp_path), pass_env=("OPENAI_API_KEY",))
    command, env, _ = profile.resolve()
    assert command[0] == str(adapter)
    assert env["OPENAI_API_KEY"] == "must-not-pass"
    assert env["CUSTOM_ENV"] == "kept"
    cache_path = config / "acp" / "resolved.json"
    assert cache_path.exists()
    assert __import__("json").loads(cache_path.read_text())["custom"]["path"] == str(adapter)
    adapter.unlink()
    with pytest.raises(ACPError, match="adapter executable not found"):
        profile.resolve()


def test_builtin_profile_and_missing_adapter_guidance(tmp_path, monkeypatch):
    profile = ACPProfile.builtin("claude-code", cwd=str(tmp_path))
    assert profile.command == ("claude-agent-acp",)
    assert "claude-agent-acp@0.89.0" in profile.install_guidance
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "config"))
    with pytest.raises(ACPError, match="Install the adapter"):
        profile.resolve()
