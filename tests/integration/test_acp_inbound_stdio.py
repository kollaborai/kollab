"""Actual CLI stdio and daemons, using an owned local API and SDK harness fixture."""

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import psutil
import pytest
from acp.schema import InitializeResponse, SessionNotification
from aiohttp import web

ROOT = Path(__file__).resolve().parents[2]

SDK_AGENT = r'''
import asyncio, os
from pathlib import Path
import acp
from acp.exceptions import RequestError
from acp.schema import (
    AgentCapabilities, AgentMessageChunk, CloseSessionResponse,
    InitializeResponse, LoadSessionResponse, NewSessionResponse,
    PermissionOption, PromptResponse, ResumeSessionResponse,
    TextContentBlock, ToolCallProgress, ToolCallStart, ToolCallUpdate,
)
Path("owned-adapter-pid.txt").write_text(str(os.getpid()))
with Path("owned-adapter-pids.txt").open("a") as stream:
    stream.write(str(os.getpid()) + "\n")

class Agent:
    def __init__(self):
        self.cancelled = asyncio.Event()

    def on_connect(self, connection):
        self.connection = connection

    async def initialize(self, protocol_version, **kwargs):
        if os.environ.get("OWNED_ACP_SCENARIO") == "startup-auth":
            raise RequestError.auth_required({"reason": "OWNED login required"})
        return InitializeResponse(protocolVersion=1, agentCapabilities=AgentCapabilities(
            loadSession=True, sessionCapabilities={"resume": {}, "close": {}}))

    def record(self, method):
        with Path("owned-session-methods.txt").open("a") as stream:
            stream.write(method + "\n")

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        self.record("new")
        return NewSessionResponse(sessionId="owned-sdk-session")

    async def resume_session(self, cwd, session_id, mcp_servers=None, **kwargs):
        self.record("resume")
        return ResumeSessionResponse()

    async def load_session(self, cwd, session_id, mcp_servers=None, **kwargs):
        self.record("load")
        return LoadSessionResponse()

    async def close_session(self, session_id, **kwargs):
        self.record("close")
        return CloseSessionResponse()

    async def prompt(self, session_id, prompt, **kwargs):
        if os.environ.get("OWNED_ACP_SCENARIO") == "prompt-auth":
            raise RequestError.auth_required({"reason": "OWNED login expired"})
        await self.connection.session_update(session_id, ToolCallStart(
            sessionUpdate="tool_call", toolCallId="owned-vendor-edit",
            title="Owned vendor edit", kind="edit", status="pending",
            rawInput={"path": "vendor.txt", "content": "after"}))
        result = await self.connection.request_permission(session_id=session_id,
            tool_call=ToolCallUpdate(toolCallId="owned-vendor-edit", title="Owned vendor edit", kind="edit"),
            options=[PermissionOption(optionId="vendor-once", name="Allow once", kind="allow_once"),
                     PermissionOption(optionId="vendor-deny", name="Deny once", kind="reject_once"),
                     PermissionOption(optionId="vendor-always", name="Always", kind="allow_always")])
        selected = getattr(result.outcome, "optionId", None)
        if selected is None:
            return PromptResponse(stopReason="cancelled")
        permitted = selected in {"vendor-once", "vendor-always"}
        if permitted and os.environ.get("OWNED_ACP_SCENARIO") == "wait-cancel":
            await self.connection.session_update(session_id, ToolCallProgress(
                sessionUpdate="tool_call_update", toolCallId="owned-vendor-edit",
                title="Owned vendor waiting", status="in_progress", rawOutput=0,
                content=[{"type": "content", "content": {"type": "text", "text": "waiting"}},
                         {"type": "diff", "path": "vendor.txt", "oldText": "before", "newText": "after"}]))
            await self.connection.session_update(session_id, AgentMessageChunk(
                sessionUpdate="agent_message_chunk", content=TextContentBlock(type="text", text="OWNED SDK PARTIAL")))
            await self.cancelled.wait()
            return PromptResponse(stopReason="cancelled")
        if permitted:
            Path("vendor.txt").write_text("after")
        await self.connection.session_update(session_id, ToolCallProgress(
            sessionUpdate="tool_call_update", toolCallId="owned-vendor-edit",
            title="Owned vendor edited", status="completed" if permitted else "failed",
            content=[{"type": "content", "content": {"type": "text", "text": "edited" if permitted else "denied"}},
                     {"type": "diff", "path": "vendor.txt", "oldText": "before", "newText": "after"}],
            rawOutput={"message": "edited" if permitted else "denied"}))
        await self.connection.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk", content=TextContentBlock(type="text",
                text="OWNED SDK ANSWER" if permitted else "OWNED SDK DENIED")))
        return PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kwargs):
        Path("owned-observed-sdk-cancel.txt").write_text("cancelled")
        self.cancelled.set()
        return None

asyncio.run(acp.run_agent(Agent(), use_unstable_protocol=True))
'''

PRINT_BOOTSTRAP = '''
import os
from kollabor.acp_server import KollabACPAgent
from kollabor_cli_main import cli_main
original = KollabACPAgent.initialize
async def noisy_initialize(self, *args, **kwargs):
    print("OWNED stray Python print", flush=True)
    os.write(1, b"OWNED stray descriptor write\\n")
    return await original(self, *args, **kwargs)
KollabACPAgent.initialize = noisy_initialize
cli_main()
'''


class ProtocolFailure(Exception):
    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


class StdioClient:
    def __init__(self, process, permission_choice=None):
        self.process = process
        self.pending = {}
        self.trace = []
        self.sent = []
        self.updates = []
        self.permissions = []
        self.bad_lines = []
        self.sequence = 0
        self.permission_choice = permission_choice
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(process.stderr.read())

    async def _write(self, data):
        self.sent.append(data)
        self.process.stdin.write((json.dumps(data) + "\n").encode())
        await self.process.stdin.drain()

    async def _read(self):
        while line := await self.process.stdout.readline():
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self.bad_lines.append(line)
                continue
            self.trace.append(message)
            if message.get("method") == "session/request_permission":
                self.permissions.append(message["params"])
                choice = self.permission_choice or next(option["optionId"] for option in message["params"]["options"]
                                                       if option["kind"] == "allow_once")
                await self._write({"jsonrpc": "2.0", "id": message["id"], "result": {
                    "outcome": {"outcome": "selected", "optionId": choice}}})
            elif message.get("method") == "session/update":
                parsed = SessionNotification.model_validate(message["params"])
                self.updates.append(parsed.model_dump(by_alias=True, exclude_none=True))
            elif message.get("id") in self.pending:
                future = self.pending.pop(message["id"])
                if future.done():
                    continue
                if "error" in message:
                    future.set_exception(ProtocolFailure(message["error"]))
                else:
                    future.set_result(message.get("result"))

    async def rpc(self, method, params, timeout=45):
        self.sequence += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.sequence] = future
        await self._write({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            shapes = [(item.get("id"), item.get("method"),
                       (item.get("params") or {}).get("update", {}).get("sessionUpdate")) for item in self.trace]
            raise AssertionError(f"{method} timed out; protocol trace shapes: {shapes}") from exc

    async def close(self):
        if self.process.stdin and not self.process.stdin.is_closing():
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 15)
        except asyncio.TimeoutError:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), 5)
        await self.reader
        await self.stderr


@contextlib.asynccontextmanager
async def client(workspace, home, profile=None, attach=None, permission_choice=None, startup_noise=False):
    # Deliberately exclude the caller's credentials, config roots and profile overrides.
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "PYTHONPATH": str(ROOT), "KOLLAB_NO_KEYRING": "1", "TERM": "dumb"}
    argv = [sys.executable, "-u", str(ROOT / "kollabor_cli_main.py"), "acp"]
    if startup_noise:
        argv = [sys.executable, "-u", "-c", PRINT_BOOTSTRAP, "acp"]
    if profile:
        argv += ["--llm", profile]
    if attach:
        argv += ["--attach", attach]
    process = await asyncio.create_subprocess_exec(*argv, cwd=workspace, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=50 * 1024 * 1024)
    connection = StdioClient(process, permission_choice)
    try:
        yield connection
    finally:
        try:
            await connection.close()
        finally:
            evidence = {"sent": connection.sent, "received": connection.trace,
                        "stdout_non_protocol_lines": len(connection.bad_lines), "exit_code": process.returncode}
            (workspace.parent / f"stdio-{process.pid}.json").write_text(json.dumps(evidence, indent=2))


@contextlib.asynccontextmanager
async def local_provider(final_release=None, tool_command="printf 'owned output' > owned.txt",
                         tool_name="terminal", tool_input=None):
    calls = []

    async def models(request):
        return web.json_response({"object": "list", "data": [{"id": "owned-native", "object": "model"}]})

    async def completion(request):
        body = await request.json()
        calls.append(body)
        if any(message.get("role") == "tool" for message in body.get("messages", [])):
            if final_release is not None:
                await final_release.wait()
            message = {"role": "assistant", "content": "OWNED NATIVE ANSWER"}
            reason = "stop"
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "owned-write", "type": "function", "function": {
                    "name": tool_name, "arguments": json.dumps(tool_input if tool_input is not None else {"command": tool_command})}}]}
            reason = "tool_calls"
        result = {"id": "owned-response", "object": "chat.completion", "created": 1, "model": "owned-native",
                  "choices": [{"index": 0, "finish_reason": reason, "message": message}],
                  "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}
        if body.get("stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            delta = dict(message)
            if "tool_calls" in delta:
                delta["tool_calls"] = [{"index": index, **tool} for index, tool in enumerate(delta["tool_calls"])]
            for chunk in [{**result, "object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": delta, "finish_reason": None}]},
                    {**result, "object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": {}, "finish_reason": reason}]}]:
                await response.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return web.json_response(result)

    app = web.Application()
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", completion)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    try:
        yield endpoint, calls
    finally:
        if final_release is not None:
            final_release.set()
        await runner.cleanup()


def configure(home, endpoint):
    directory = home / ".kollab"
    directory.mkdir(parents=True)
    profile = {"provider": "openai", "model": "owned-native", "base_url": endpoint,
               "api_key": "owned-placeholder", "streaming": False, "supports_tools": True}
    config = {"kollabor": {"llm": {"default_profile": "owned-native", "active_profile": "owned-native",
                                   "profiles": {"owned-native": profile}}},
              "plugins": {"hub": {"enabled": True}, "mcp": {"enabled": False}}}
    (directory / "config.json").write_text(json.dumps(config))


def configure_acp(home, workspace, scenario="normal"):
    directory = home / ".kollab"
    directory.mkdir(parents=True)
    adapter = workspace / "owned_sdk_agent.py"
    adapter.write_text(SDK_AGENT)
    command = [sys.executable, str(adapter)]
    if scenario == "missing-executable":
        command = [str(workspace / "absent-owned-adapter")]
    profile = {"provider": "acp", "command": command, "workspace": str(workspace),
               "env": {"OWNED_ACP_SCENARIO": scenario}}
    config = {"kollabor": {"llm": {"default_profile": "owned-acp", "active_profile": "owned-acp",
                                   "profiles": {"owned-acp": profile}}},
              "plugins": {"hub": {"enabled": True}, "mcp": {"enabled": False}}}
    (directory / "config.json").write_text(json.dumps(config))


def owned_daemons(home):
    result = []
    for path in home.glob(".kollab/**/presence/*.json"):
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            continue
        pid = int(record.get("pid") or 0)
        if process_alive(pid):
            result.append(record)
    return result


def process_alive(pid):
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def wait_no_daemons(home):
    async def wait():
        while owned_daemons(home):
            await asyncio.sleep(0.05)
    await asyncio.wait_for(wait(), 15)


@contextlib.asynccontextmanager
async def daemon_rpc(record, evidence_path):
    from kollabor_rpc import RpcClient, open_unix_connection_with_large_buffer

    reader, writer = await open_unix_connection_with_large_buffer(record["socket_path"])
    writer.write((json.dumps({"action": "attach", "mode": "interactive",
                             "client_id": "owned-native-input-proof"}) + "\n").encode())
    await writer.drain()
    ack = json.loads(await asyncio.wait_for(reader.readline(), 5))
    assert ack["type"] == "attach_ack"
    rpc = RpcClient(writer)
    events = []

    async def read():
        while line := await reader.readline():
            message = json.loads(line)
            if message.get("action") == "rpc_reply":
                rpc.on_reply(message)
            else:
                events.append(message)

    task = asyncio.create_task(read())
    try:
        yield rpc, events
    finally:
        evidence_path.write_text(json.dumps(events, indent=2))
        rpc.close()
        writer.close()
        await writer.wait_closed()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_stdio_mixed_initialize_and_unknown_method_have_v1_shape(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    home.mkdir()
    workspace.mkdir()
    async with client(workspace, home) as connection:
        initialized = await connection.rpc("initialize", {"protocolVersion": 2,
            "clientCapabilities": {}, "clientInfo": {"name": "owned-mixed-client", "version": "1"},
            "capabilities": {"session": {"resume": True}}, "supportedProtocolVersions": [1, 2]})
        assert InitializeResponse.model_validate(initialized).protocolVersion == 1
        assert "agentCapabilities" in initialized and "capabilities" not in initialized
        with pytest.raises(ProtocolFailure) as failure:
            await connection.rpc("not/a/method", {})
        assert failure.value.error["code"] == -32601
    assert connection.process.returncode == 0
    assert connection.bad_lines == []
    assert owned_daemons(home) == []


@pytest.mark.asyncio
async def test_real_stdio_invalid_session_and_workspace_never_spawn_daemon(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    async with client(workspace, home) as connection:
        with pytest.raises(ProtocolFailure) as failure:
            await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []})
        assert failure.value.error["code"] == -32602
        await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        for folder in ["relative-folder", str(workspace / "missing-folder")]:
            with pytest.raises(ProtocolFailure) as failure:
                await connection.rpc("session/new", {"cwd": folder, "mcpServers": []})
            assert failure.value.error["code"] == -32602
        with pytest.raises(ProtocolFailure) as failure:
            await connection.rpc("session/prompt", {"sessionId": "not-owned", "prompt": [{"type": "text", "text": "refuse"}]})
        assert failure.value.error["code"] == -32602
    assert owned_daemons(home) == []
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
async def test_real_owned_native_prompt_permission_tools_restore_and_eof(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    async with local_provider() as (endpoint, calls):
        configure(home, endpoint)
        async with client(workspace, home, "owned-native") as connection:
            await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            opened = await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []})
            session_id = opened["sessionId"]
            assert len(owned_daemons(home)) == 1
            daemon_pid = owned_daemons(home)[0]["pid"]
            result = await connection.rpc("session/prompt", {"sessionId": session_id,
                "prompt": [{"type": "text", "text": "Write owned.txt and report the result."}]})
            assert result["stopReason"] == "end_turn"
            assert (workspace / "owned.txt").read_text() == "owned output"
            assert connection.permissions
            assert any(update["update"]["sessionUpdate"] == "tool_call" for update in connection.updates)
            assert any(update["update"]["sessionUpdate"] == "tool_call_update" for update in connection.updates)
            assert any(update["update"].get("content", {}).get("text") == "OWNED NATIVE ANSWER"
                       for update in connection.updates if update["update"]["sessionUpdate"] == "agent_message_chunk")
            await connection.rpc("session/close", {"sessionId": session_id})
            await wait_no_daemons(home)
            assert not process_alive(daemon_pid)
            before = len(connection.updates)
            await connection.rpc("session/resume", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
            resumed_pid = owned_daemons(home)[0]["pid"]
            assert len(connection.updates) == before
            await connection.rpc("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
            assert len(connection.updates) > before
            start = next(update["update"] for update in connection.updates[before:] if update["update"]["sessionUpdate"] == "tool_call")
            result = next(update["update"] for update in connection.updates[before:] if update["update"]["sessionUpdate"] == "tool_call_update")
            assert start["title"] == "terminal" and start["kind"] == "execute"
            assert start["rawInput"] == {"command": "printf 'owned output' > owned.txt"}
            assert result["toolCallId"] == start["toolCallId"] == "owned-write" and result["status"] == "completed"
            await connection.rpc("session/close", {"sessionId": session_id})
            await wait_no_daemons(home)
            assert not process_alive(resumed_pid)
        await wait_no_daemons(home)
        async with client(workspace, home, "owned-native") as restored:
            await restored.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            await restored.rpc("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
            restored_pid = owned_daemons(home)[0]["pid"]
            restored_tools = [entry["update"] for entry in restored.updates
                              if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
            assert [entry["sessionUpdate"] for entry in restored_tools] == ["tool_call", "tool_call_update"]
            assert restored_tools[0]["rawInput"] == {"command": "printf 'owned output' > owned.txt"}
            assert restored_tools[1]["status"] == "completed"
            assert any(entry["update"].get("content", {}).get("text") == "OWNED NATIVE ANSWER" for entry in restored.updates
                       if entry["update"]["sessionUpdate"] == "agent_message_chunk")
            before = len(restored.updates)
            response = await restored.rpc("session/prompt", {"sessionId": session_id,
                "prompt": [{"type": "text", "text": "Append another owned native tool turn."}]})
            assert response["stopReason"] == "end_turn"
            assert [entry["update"]["sessionUpdate"] for entry in restored.updates[before:]
                    if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}] == ["tool_call", "tool_call_update"]
        await wait_no_daemons(home)
        assert not process_alive(restored_pid)
        async with client(workspace, home, "owned-native") as continued:
            await continued.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            await continued.rpc("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
            continued_pid = owned_daemons(home)[0]["pid"]
            assert sum(entry["update"]["sessionUpdate"] == "tool_call" for entry in continued.updates) == 2
            assert sum(entry["update"]["sessionUpdate"] == "tool_call_update" for entry in continued.updates) == 2
            assert sum(entry["update"].get("content", {}).get("text") == "OWNED NATIVE ANSWER" for entry in continued.updates
                       if entry["update"]["sessionUpdate"] == "agent_message_chunk") == 2
        await wait_no_daemons(home)
        assert not process_alive(continued_pid) and len(calls) == 4
    assert connection.process.returncode == 0
    assert restored.process.returncode == 0 and continued.process.returncode == 0
    assert connection.bad_lines == [] and restored.bad_lines == [] and continued.bad_lines == []


@pytest.mark.asyncio
async def test_real_native_file_edit_diff_is_retained_after_restore(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "owned.txt"
    target.write_text("before")
    arguments = {"file": "owned.txt", "find": "before", "replace": "after"}
    async with local_provider(tool_name="file_edit", tool_input=arguments) as (endpoint, _):
        configure(home, endpoint)
        async with client(workspace, home, "owned-native") as connection:
            await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            session_id = (await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
            response = await connection.rpc("session/prompt", {"sessionId": session_id,
                "prompt": [{"type": "text", "text": "Make the owned file edit."}]})
            assert response["stopReason"] == "end_turn" and target.read_text() == "after"
            cards = [entry["update"] for entry in connection.updates
                     if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
            expected = {"type": "diff", "path": str(target), "oldText": "before", "newText": "after"}
            assert cards[0]["kind"] == "edit"
            assert expected in cards[1]["content"]
            await connection.rpc("session/close", {"sessionId": session_id})
            await wait_no_daemons(home)
            before = len(connection.updates)
            await connection.rpc("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
            restored_cards = [entry["update"] for entry in connection.updates[before:]
                              if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
            assert restored_cards[0]["kind"] == "edit" and restored_cards[0]["rawInput"] == arguments
            assert expected in restored_cards[1]["content"] and restored_cards[1]["status"] == "completed"
        await wait_no_daemons(home)
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
async def test_attached_queued_cancel_and_eof_preserve_the_other_client_turn(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    release = asyncio.Event()
    async with local_provider(release) as (endpoint, calls):
        configure(home, endpoint)
        async with client(workspace, home, "owned-native") as owner:
            await owner.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            opened = await owner.rpc("session/new", {"cwd": str(workspace), "mcpServers": []})
            own_id = opened["sessionId"]
            own_prompt = asyncio.create_task(owner.rpc("session/prompt", {"sessionId": own_id,
                "prompt": [{"type": "text", "text": "Write the owned file and report."}]}))
            async def second_request():
                while len(calls) < 2:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(second_request(), 10)
            daemon_pid = owned_daemons(home)[0]["pid"]
            async with client(workspace, home, attach=f"acp-{own_id[:8]}") as attached:
                initialized = await attached.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                assert initialized["agentCapabilities"]["loadSession"] is False
                attached_id = (await attached.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
                with pytest.raises(ProtocolFailure) as failure:
                    await attached.rpc("session/set_mode", {"sessionId": attached_id, "modeId": "trust_all"})
                assert failure.value.error["code"] == -32602
                queued = asyncio.create_task(attached.rpc("session/prompt", {"sessionId": attached_id,
                    "prompt": [{"type": "text", "text": "This queued request must not run."}]}))
                await asyncio.sleep(0.05)
                await attached._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": attached_id}})
                result = await asyncio.wait_for(queued, 5)
                assert result["stopReason"] == "cancelled"
                assert not own_prompt.done()
                assert psutil.pid_exists(daemon_pid)
            assert psutil.pid_exists(daemon_pid)
            release.set()
            assert (await own_prompt)["stopReason"] == "end_turn"
            assert (workspace / "owned.txt").read_text() == "owned output"
        await wait_no_daemons(home)
    assert owner.bad_lines == [] and attached.bad_lines == []


@pytest.mark.asyncio
async def test_queued_acp_cancel_preserves_native_state_rpc_owner(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    release = asyncio.Event()
    async with local_provider(release) as (endpoint, calls):
        configure(home, endpoint)
        async with client(workspace, home, "owned-native") as owner:
            await owner.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            own_id = (await owner.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
            await owner.rpc("session/set_mode", {"sessionId": own_id, "modeId": "trust_all"})
            record = owned_daemons(home)[0]
            async with daemon_rpc(record, tmp_path / "native-rpc-events.json") as (rpc, events):
                admission = await rpc.call("state.send_message", {"message": "Run the owned native turn.",
                    "request_id": "owned-native-input"})
                assert admission["accepted"]

                async def native_waiting():
                    while len(calls) < 2:
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(native_waiting(), 10)
                async with client(workspace, home, attach=f"acp-{own_id[:8]}") as attached:
                    await attached.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                    attached_id = (await attached.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
                    queued = asyncio.create_task(attached.rpc("session/prompt", {"sessionId": attached_id,
                        "prompt": [{"type": "text", "text": "This queued ACP input must not run."}]}))
                    await asyncio.sleep(0.05)
                    await attached._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": attached_id}})
                    assert (await asyncio.wait_for(queued, 5))["stopReason"] == "cancelled"
                    assert (await rpc.call("state.get_processing_state"))["is_processing"] is True
                    assert len(calls) == 2 and process_alive(record["pid"])
                assert process_alive(record["pid"])
                release.set()

                async def native_finished():
                    while not any(event.get("type") == "turn_complete" and not event.get("queued")
                                  for event in events):
                        await asyncio.sleep(0.01)
                await asyncio.wait_for(native_finished(), 10)
                native_completions = [event for event in events if event.get("type") == "turn_complete"
                                      and not event.get("queued")]
                assert len(native_completions) == 1
                queued_completions = [event for event in events if event.get("type") == "turn_complete" and event.get("queued")]
                assert len(queued_completions) == 1 and queued_completions[0]["input_source"] == "acp_client"
                assert len(calls) == 2 and (workspace / "owned.txt").read_text() == "owned output"
        await wait_no_daemons(home)
    assert owner.bad_lines == [] and attached.bad_lines == []


@pytest.mark.asyncio
async def test_attached_native_permission_never_offers_always_and_eof_preserves_owner(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    async with local_provider() as (endpoint, _):
        configure(home, endpoint)
        async with client(workspace, home, "owned-native") as owner:
            await owner.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            own_id = (await owner.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
            daemon_pid = owned_daemons(home)[0]["pid"]
            sibling = tmp_path / "sibling"
            sibling.mkdir()
            async with client(sibling, home, attach=f"acp-{own_id[:8]}") as wrong_folder:
                await wrong_folder.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                with pytest.raises(ProtocolFailure) as failure:
                    await wrong_folder.rpc("session/new", {"cwd": str(sibling), "mcpServers": []})
                assert failure.value.error["code"] == -32602
            assert process_alive(daemon_pid) and wrong_folder.bad_lines == []
            async with client(workspace, home, attach=f"acp-{own_id[:8]}") as attached:
                await attached.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                attached_id = (await attached.rpc("session/new", {"cwd": str(workspace), "mcpServers": [
                    {"name": "owned-ignored-server", "command": "never-owned-command", "args": [], "env": []}]}))["sessionId"]
                with pytest.raises(ProtocolFailure) as failure:
                    await attached.rpc("session/new", {"cwd": str(workspace), "mcpServers": []})
                assert failure.value.error["code"] == -32602
                result = await attached.rpc("session/prompt", {"sessionId": attached_id,
                    "prompt": [{"type": "text", "text": "Run the owned terminal command."}]})
                assert result["stopReason"] == "end_turn"
                assert attached.permissions
                assert {option["kind"] for prompt in attached.permissions for option in prompt["options"]} == {
                    "allow_once", "reject_once"}
            assert psutil.pid_exists(daemon_pid)
        await wait_no_daemons(home)
    assert owner.bad_lines == [] and attached.bad_lines == []
    assert "owned-ignored-server" in attached.stderr.result().decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["cancel", "eof"])
async def test_active_native_cancel_stops_owned_tool_children(tmp_path, termination):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    command = "touch owned-started; sleep 30; printf forbidden > forbidden-after-cancel.txt"
    async with local_provider(tool_command=command) as (endpoint, _):
        configure(home, endpoint)
        async with client(workspace, home, "owned-native") as connection:
            await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            session_id = (await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
            prompt = asyncio.create_task(connection.rpc("session/prompt", {"sessionId": session_id,
                "prompt": [{"type": "text", "text": "Run the owned blocking tool."}]}))
            async def started():
                while not (workspace / "owned-started").exists():
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(started(), 10)
            daemon_pid = owned_daemons(home)[0]["pid"]
            tool_processes = []
            for proc in psutil.Process(daemon_pid).children(recursive=True):
                with contextlib.suppress(psutil.NoSuchProcess):
                    if "owned-started" in " ".join(proc.cmdline()):
                        tool_processes.append(proc)
            assert tool_processes
            tool_pids = {proc.pid for proc in tool_processes}
            for proc in tool_processes:
                tool_pids.update(child.pid for child in proc.children(recursive=True))
            if termination == "cancel":
                await connection._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
                assert (await asyncio.wait_for(prompt, 5))["stopReason"] == "cancelled"
            else:
                await connection.close()
                prompt.cancel()
                await asyncio.gather(prompt, return_exceptions=True)
            async def stopped():
                while any(process_alive(pid) for pid in tool_pids):
                    await asyncio.sleep(0.05)
            await asyncio.wait_for(stopped(), 10)
            assert not (workspace / "forbidden-after-cancel.txt").exists()
        await wait_no_daemons(home)
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
async def test_real_stdio_keeps_python_print_and_fd_write_off_protocol(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    async with client(workspace, tmp_path / "home", startup_noise=True) as connection:
        result = await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        assert result["protocolVersion"] == 1
    stderr = connection.stderr.result().decode()
    assert "OWNED stray Python print" in stderr and "OWNED stray descriptor write" in stderr
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
async def test_real_sdk_harness_opaque_permission_diff_cards_and_restore(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    configure_acp(home, workspace)
    async with client(workspace, home, "owned-acp") as connection:
        await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        session_id = (await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
        adapter_pid = int((workspace / "owned-adapter-pid.txt").read_text())
        response = await connection.rpc("session/prompt", {"sessionId": session_id,
            "prompt": [{"type": "text", "text": "Make the owned SDK edit."}]})
        assert response["stopReason"] == "end_turn" and (workspace / "vendor.txt").read_text() == "after"
        assert connection.permissions[0]["options"][0]["optionId"] == "vendor-once"
        cards = [entry["update"] for entry in connection.updates
                 if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
        assert [card["toolCallId"] for card in cards] == ["owned-vendor-edit", "owned-vendor-edit"]
        assert cards[0]["title"] == "Owned vendor edit" and cards[0]["kind"] == "edit"
        assert cards[0]["rawInput"] == {"path": "vendor.txt", "content": "after"}
        assert cards[1]["title"] == "Owned vendor edited" and cards[1]["status"] == "completed"
        assert cards[1]["rawOutput"] == {"message": "edited"}
        assert cards[1]["content"][1] == {"type": "diff", "path": "vendor.txt", "oldText": "before", "newText": "after"}
        await connection.rpc("session/close", {"sessionId": session_id})
        await wait_no_daemons(home)
        assert not process_alive(adapter_pid)
        before = len(connection.updates)
        await connection.rpc("session/resume", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
        resumed_pid = int((workspace / "owned-adapter-pid.txt").read_text())
        assert len(connection.updates) == before
        await connection.rpc("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
        restored = [entry["update"] for entry in connection.updates[before:]
                    if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
        assert restored == cards
    await wait_no_daemons(home)
    assert not process_alive(resumed_pid)
    assert "resume" in (workspace / "owned-session-methods.txt").read_text().splitlines()
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
async def test_real_sdk_active_cancel_partial_history_survives_close_and_reopen(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    configure_acp(home, workspace, "wait-cancel")
    async with client(workspace, home, "owned-acp") as connection:
        await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        session_id = (await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
        daemon_pid = owned_daemons(home)[0]["pid"]
        adapter_pid = int((workspace / "owned-adapter-pid.txt").read_text())
        prompt = asyncio.create_task(connection.rpc("session/prompt", {"sessionId": session_id,
            "prompt": [{"type": "text", "text": "Start the owned SDK tool and wait for cancellation."}]}))

        async def partial_visible():
            while not any(entry["update"].get("content", {}).get("text") == "OWNED SDK PARTIAL"
                          for entry in connection.updates if entry["update"]["sessionUpdate"] == "agent_message_chunk"):
                await asyncio.sleep(0.01)
        await asyncio.wait_for(partial_visible(), 10)
        assert not prompt.done()
        live_cards = [entry["update"] for entry in connection.updates
                      if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
        assert [card["status"] for card in live_cards] == ["pending", "in_progress"]
        assert live_cards[1]["rawOutput"] == 0
        await connection._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": session_id}})
        assert (await asyncio.wait_for(prompt, 15))["stopReason"] == "cancelled"
        assert (workspace / "owned-observed-sdk-cancel.txt").read_text() == "cancelled"
        assert not (workspace / "vendor.txt").exists()
        await connection.rpc("session/close", {"sessionId": session_id})
        await wait_no_daemons(home)
        assert not process_alive(daemon_pid) and not process_alive(adapter_pid)
        assert all(not process_alive(int(pid)) for pid in (workspace / "owned-adapter-pids.txt").read_text().splitlines())

    async with client(workspace, home, "owned-acp") as restored:
        await restored.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        await restored.rpc("session/load", {"sessionId": session_id, "cwd": str(workspace), "mcpServers": []})
        restored_cards = [entry["update"] for entry in restored.updates
                          if entry["update"]["sessionUpdate"] in {"tool_call", "tool_call_update"}]
        assert restored_cards == live_cards
        assert [entry["update"]["content"]["text"] for entry in restored.updates
                if entry["update"]["sessionUpdate"] == "agent_message_chunk"] == ["OWNED SDK PARTIAL"]
        record = owned_daemons(home)[0]
        async with daemon_rpc(record, tmp_path / "stopped-rpc-events.json") as (rpc, _):
            snapshot = await rpc.call("state.get_conversation")
        stopped = [message for message in snapshot["messages"] if message["role"] == "assistant"
                   and (message.get("metadata") or {}).get("stop_reason") == "cancelled"]
        assert len(stopped) == 1 and stopped[0]["content"] == "OWNED SDK PARTIAL"
        metadata = stopped[0]["metadata"]
        assert metadata["provider"] == "acp"
        saved_cards = [SessionNotification.model_validate({"sessionId": session_id, "update": update["payload"]})
                       .update.model_dump(by_alias=True, exclude_none=True) for update in metadata["acp_updates"]
                       if update.get("kind") in {"tool_call", "tool_call_update"}]
        assert saved_cards == live_cards
        assert metadata["tool_calls"][0]["metadata"]["acp"]["status"] == "in_progress"
        (tmp_path / "stopped-parent.json").write_text(json.dumps(metadata, indent=2))
    await wait_no_daemons(home)
    assert not process_alive(record["pid"])
    assert all(not process_alive(int(pid)) for pid in (workspace / "owned-adapter-pids.txt").read_text().splitlines())
    assert connection.bad_lines == [] and restored.bad_lines == []
    assert connection.process.returncode == 0 and restored.process.returncode == 0


@pytest.mark.asyncio
async def test_real_sdk_attached_harness_permission_filters_always_and_detaches(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    configure_acp(home, workspace)
    async with client(workspace, home, "owned-acp") as owner:
        await owner.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        own_id = (await owner.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
        daemon_pid = owned_daemons(home)[0]["pid"]
        adapter_pid = int((workspace / "owned-adapter-pid.txt").read_text())
        async with client(workspace, home, attach=f"acp-{own_id[:8]}") as attached:
            await attached.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            attached_id = (await attached.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
            result = await attached.rpc("session/prompt", {"sessionId": attached_id,
                "prompt": [{"type": "text", "text": "Make this attached SDK edit."}]})
            assert result["stopReason"] == "end_turn"
            assert {option["optionId"] for request in attached.permissions for option in request["options"]} == {
                "vendor-once", "vendor-deny"}
        assert process_alive(daemon_pid) and process_alive(adapter_pid)
        assert (workspace / "vendor.txt").read_text() == "after"
    await wait_no_daemons(home)
    assert not process_alive(adapter_pid)
    assert owner.bad_lines == [] and attached.bad_lines == []


@pytest.mark.asyncio
@pytest.mark.parametrize("choice, stop_reason", [("vendor-deny", "end_turn"), ("once", "cancelled")])
async def test_real_sdk_harness_denied_or_nonoffered_choice_never_writes(tmp_path, choice, stop_reason):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    configure_acp(home, workspace)
    async with client(workspace, home, "owned-acp", permission_choice=choice) as connection:
        await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        session_id = (await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
        response = await connection.rpc("session/prompt", {"sessionId": session_id,
            "prompt": [{"type": "text", "text": "This edit must be refused."}]}, timeout=10)
        assert response["stopReason"] == stop_reason
        assert not (workspace / "vendor.txt").exists()
        assert connection.permissions
    await wait_no_daemons(home)
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario, guidance", [("startup-auth", "authentication required"),
                                                 ("missing-executable", "Install the ACP adapter")])
async def test_real_stdio_startup_error_has_guidance_and_no_owned_daemon(tmp_path, scenario, guidance):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    configure_acp(home, workspace, scenario)
    async with client(workspace, home, "owned-acp") as connection:
        await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        with pytest.raises(ProtocolFailure) as failure:
            await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}, timeout=15)
        assert failure.value.error["code"] == -32603
        assert guidance.lower() in json.dumps(failure.value.error).lower()
        await wait_no_daemons(home)
        if scenario == "startup-auth":
            assert not process_alive(int((workspace / "owned-adapter-pid.txt").read_text()))
        else:
            assert not (workspace / "owned-adapter-pid.txt").exists()
    assert connection.bad_lines == [] and connection.process.returncode == 0


@pytest.mark.asyncio
async def test_real_sdk_harness_expired_login_fails_prompt_without_output(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    configure_acp(home, workspace, "prompt-auth")
    async with client(workspace, home, "owned-acp") as connection:
        await connection.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        session_id = (await connection.rpc("session/new", {"cwd": str(workspace), "mcpServers": []}))["sessionId"]
        with pytest.raises(ProtocolFailure) as failure:
            await connection.rpc("session/prompt", {"sessionId": session_id,
                "prompt": [{"type": "text", "text": "The login has expired."}]}, timeout=15)
        assert failure.value.error["code"] == -32603
        assert "authentication required" in json.dumps(failure.value.error).lower()
        assert not (workspace / "vendor.txt").exists()
    await wait_no_daemons(home)
    assert connection.bad_lines == [] and connection.process.returncode == 0
