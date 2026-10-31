import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.application import TerminalLLMChat
from kollabor.llm.permissions.attach_bridge import PERMISSION_RESPONSE_RPC_METHOD, AttachPermissionBridge
from kollabor_agent.runtime import AgentRuntime
from kollabor_ai.profile_manager import ProfileManager
from kollabor_config import config_utils
from kollabor_events.permissions_models import ApprovalMode, ConfirmationResponse
from plugins.hub import presence
from plugins.hub.messenger import AgentMessenger, AgentSocketServer, HubMessage
from plugins.hub.plugin import HubPlugin

AGENT = r"""
import asyncio,acp
from acp.schema import AgentMessageChunk,InitializeResponse,NewSessionResponse,PromptResponse,TextContentBlock
class Agent:
    def on_connect(self,conn):self.conn=conn
    async def initialize(self,protocol_version,**kwargs):return InitializeResponse(protocolVersion=1)
    async def new_session(self,cwd,mcp_servers=None,**kwargs):
        return NewSessionResponse(sessionId='owned-deferred-session')
    async def prompt(self,session_id,prompt,**kwargs):
        await self.conn.session_update(
            session_id,
            AgentMessageChunk(
                sessionUpdate='agent_message_chunk',
                content=TextContentBlock(type='text', text='OWNED SDK ANSWER'),
            ),
        )
        return PromptResponse(stopReason='end_turn')
    async def cancel(self,session_id,**kwargs):return None
asyncio.run(acp.run_agent(Agent()))
"""


PERMISSION_AGENT = r"""
import asyncio, acp
from acp.schema import (
    AgentMessageChunk,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    TextContentBlock,
    ToolCallStart,
    ToolCallUpdate,
    PermissionOption,
)


class Agent:
    def on_connect(self, conn):
        self.conn = conn

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=1)

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId="owned-deferred-session")

    async def prompt(self, session_id, prompt, **kwargs):
        await self.conn.session_update(
            session_id,
            ToolCallStart(
                sessionUpdate="tool_call",
                toolCallId="owned-permission",
                title="Owned echo tool",
                kind="execute",
                status="pending",
                rawInput={"command": ["echo", "owned"]},
            ),
        )
        answer = await self.conn.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(toolCallId="owned-permission"),
            options=[
                PermissionOption(optionId="owned-once", name="Allow once", kind="allow_once"),
                PermissionOption(optionId="owned-deny", name="Deny once", kind="reject_once"),
            ],
        )
        selected = getattr(answer.outcome, "optionId", None)
        if selected is None:
            return PromptResponse(stopReason="cancelled")
        text = "OWNED SDK ANSWER" if selected == "owned-once" else "OWNED SDK DENIED"
        await self.conn.session_update(
            session_id,
            AgentMessageChunk(sessionUpdate="agent_message_chunk", content=TextContentBlock(type="text", text=text)),
        )
        return PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kwargs):
        return None


asyncio.run(acp.run_agent(Agent()))
"""


class OwnedRpcServer:
    def __init__(self):
        self.handlers = {}

    def register(self, name, handler):
        self.handlers[name] = handler


class OwnedRpcClient:
    def __init__(self, server):
        self.server = server
        self.calls = []

    async def call(self, name, params, timeout=None):
        self.calls.append(params)
        return await self.server.handlers[name](params)


class OwnedPermissionView:
    def __init__(self):
        self.opened = asyncio.Event()
        self.future = None
        self.calls = 0
        self.closed = 0

    async def show_permission_prompt(self, details):
        self.calls += 1
        self.future = asyncio.get_running_loop().create_future()
        self.opened.set()
        try:
            return await self.future
        finally:
            self.closed += 1

    def cancel_permission_prompt(self):
        if self.future is not None and not self.future.done():
            self.future.set_result(ConfirmationResponse.CANCEL)

    def answer(self, response):
        self.future.set_result(response)


class OwnedDisplayTap:
    subscriber_count = 1

    def __init__(self, bridge, client, view):
        self.bridge = bridge
        self.client = client
        self.view = view
        self.events = []
        self.tasks = []

    def publish(self, event):
        if event.get("type") == "permission_request" and event.get("details", {}).get("source") != "acp":
            return
        self.events.append(event)
        if event.get("type") == "permission_request":
            self.tasks.append(
                asyncio.create_task(
                    self.bridge.handle_client_event(rpc_client=self.client, layout_manager=self.view, event=event)
                )
            )
        elif event.get("type") in ("permission_granted", "permission_denied"):
            self.bridge.handle_client_resolution(layout_manager=self.view, event=event)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["lifecycle", "local-first", "attach-first", "outer-cancel", "simultaneous"])
async def test_real_terminal_deferred_acp_hub_lifecycle(scenario):
    """Real constructor + local Hub + SDK fake; no Ed25519, real adapter, or CLI detach."""
    with tempfile.TemporaryDirectory(prefix="kacp-", dir="/tmp") as task_dir:
        root = Path(task_dir)
        global_root = root / "global"
        home = root / "home"
        project = root / "project"
        for path in (global_root, home, project):
            path.mkdir()
        profile = {
            "provider": "acp",
            "command": [sys.executable, "-c", AGENT if scenario == "lifecycle" else PERMISSION_AGENT],
            "workspace": str(project),
        }
        config = {
            "kollabor": {"llm": {"default_profile": "owned-acp", "profiles": {"owned-acp": profile}}},
            "plugins": {"hub": {"enabled": True, "project_scoped": True}, "mcp": {"enabled": False}},
        }
        (global_root / "config.json").write_text(json.dumps(config))
        instances = {}

        def factory(bus, renderer, configuration):
            hub = HubPlugin(event_bus=bus, renderer=renderer, config=configuration)
            instances["hub"] = hub
            return {"hub": hub}

        registry = SimpleNamespace(get_merged_config=lambda: {}, list_plugins=lambda: [], instantiate_plugins=factory)
        children = []
        child_names = {}
        original_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            children.append(process)
            child_names[process.pid] = Path(args[0]).name
            return process

        app = None
        requester_server = None
        requester_presence = None
        sdk_process = None
        hub_socket_path = None
        tap = None
        with (
            contextlib.chdir(project),
            patch.object(config_utils, "get_config_directory", return_value=global_root),
            patch.object(Path, "home", return_value=home),
            patch.object(presence, "SOCKET_DIR_ROOT", root / "sockets"),
            patch.object(ProfileManager, "_detect_oauth_provider"),
            patch.object(ProfileManager, "_detect_env_provider"),
            patch.dict(
                os.environ,
                {
                    "KOLLAB_CONFIG_DIR": str(global_root),
                    "KOLLAB_PROJECT_ROOT": str(project),
                    "KOLLAB_HUB_PROJECT_SCOPED": "1",
                    "KOLLAB_HUB_SOLO": "0",
                    "KOLLAB_PARENT_PID": "",
                },
            ),
            patch("kollabor.application.setup_from_config"),
            patch.object(HubPlugin, "_resume_relay", AsyncMock()),
            patch.object(asyncio, "create_subprocess_exec", spawn),
        ):
            try:
                app = TerminalLLMChat(
                    args=SimpleNamespace(simple=True, no_mcp=True, as_identity="owned-acp-proof", pipe=False),
                    profile_name="owned-acp",
                    plugin_registry=registry,
                )
                app.permission_manager.set_approval_mode(ApprovalMode.CONFIRM_ALL, persist=False)
                app.running = True
                app.render_loop = None
                for name in (
                    "_check_for_updates",
                    "_display_provider_warning",
                    "_check_first_run_wizard",
                    "_display_ready_message",
                ):
                    setattr(app, name, AsyncMock())
                app._process_cli_command = AsyncMock(return_value=None)
                await app.llm_service.register_hooks()
                await asyncio.wait_for(app._deferred_startup(), timeout=12)
                if scenario != "lifecycle":
                    rpc_server = OwnedRpcServer()
                    rpc_client = OwnedRpcClient(rpc_server)
                    remote_bridge = AttachPermissionBridge()
                    local_view = OwnedPermissionView()
                    remote_view = OwnedPermissionView()
                    tap = OwnedDisplayTap(remote_bridge, rpc_client, remote_view)
                    app.event_bus.register_service("display_tap", tap)
                    app.event_bus.register_service("rpc_server", rpc_server)
                    app.renderer.layout_manager.show_permission_prompt = local_view.show_permission_prompt
                    app.renderer.layout_manager.cancel_permission_prompt = local_view.cancel_permission_prompt
                owner = app.acp_orchestrator
                sdk_process = owner.runner._process
                hub = instances["hub"]
                hub_socket_path = Path(hub._socket_server.socket_path)
                row = {
                    "startup_complete": app._startup_complete,
                    "hub_socket_bound": hub._socket_server is not None and hub._socket_server._server is not None,
                    "controller_active": owner is not None and owner.active,
                    "same_hub_binding": owner is not None and owner.hub_plugin is hub,
                    "owned_child_count": len(children),
                    "session_id": owner.runner.session_id if owner and owner.runner else None,
                }
                print(json.dumps(row))
                assert all(
                    row[key]
                    for key in ("startup_complete", "hub_socket_bound", "controller_active", "same_hub_binding")
                ), row
                if scenario == "lifecycle":
                    # Exercise the daemon-side command with the real selected
                    # ACP profile and an active SDK process. The existing
                    # ordinary Hub turn below must still reach that process.
                    before = owner.runner._next_turn_id
                    goal = await app._local_state_service.goal_command(
                        "/goal examine the isolated test workspace"
                    )
                    print(json.dumps({"goal_command": goal}))
                    assert goal["success"] is False
                    assert "ACP" in goal["message"]
                    assert owner.runner._next_turn_id == before
                    service = app.event_bus.get_service("goal_service")
                    uid = app._local_state_service.get_conversation_uid()
                    assert service.store.get_active(uid) is None
                    shown = await app._local_state_service.goal_command("/goal show")
                    assert shown["success"] is True
                received = []

                async def receive(message):
                    received.append(message)

                requester = AgentRuntime(name="owned-requester", identity="owned-requester")
                requester_server = AgentSocketServer(requester.agent_id, receive, socket_name="owned-requester")
                requester.socket_path = await requester_server.start()
                requester_presence = presence.PresenceManager(requester)
                requester_presence.publish()
                message = HubMessage(
                    from_agent=requester.agent_id,
                    from_identity=requester.identity,
                    content="Return the owned fixture answer",
                    to=hub._identity.identity,
                    metadata={"relay_kind": "question"},
                    thread_id="owned-request-thread",
                )
                accepted = await AgentMessenger.send_to_agent(str(hub._socket_server.socket_path), message)

                if scenario != "lifecycle":
                    await asyncio.wait_for(
                        asyncio.gather(local_view.opened.wait(), remote_view.opened.wait()), timeout=2
                    )
                    if scenario == "local-first":
                        local_view.answer(ConfirmationResponse.APPROVE_SESSION)
                    elif scenario == "attach-first":
                        remote_view.answer("owned-once")
                    elif scenario == "outer-cancel":
                        await asyncio.wait_for(owner.cancel(), timeout=5)
                    else:
                        # Both answers arrive before the local responder gets another turn.
                        local_view.answer(ConfirmationResponse.APPROVE_SESSION)
                        reply = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
                            {"tool_id": "owned-permission", "control": "DENY"}
                        )
                        assert reply == {"ok": True}

                async def completion():
                    while not any(item.reply_to == message.id and item.action == "turn_end" for item in received):
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(completion(), timeout=6)
                if scenario != "lifecycle":
                    await asyncio.wait_for(asyncio.gather(*tap.tasks), timeout=2)
                    assert local_view.calls == remote_view.calls == 1
                    assert local_view.closed == remote_view.closed == 1
                    resolution = [
                        e
                        for e in tap.events
                        if e.get("type") in ("permission_granted", "permission_denied")
                        and e.get("tool_id") == "owned-permission"
                    ]
                    assert len(resolution) == 1, resolution
                    expected_type = (
                        "permission_denied" if scenario in ("outer-cancel", "simultaneous") else "permission_granted"
                    )
                    assert resolution[0]["type"] == expected_type, resolution
                    if scenario == "outer-cancel":
                        assert resolution[0].get("outcome") == "cancelled"
                    late = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
                        {"tool_id": "owned-permission", "option_id": "owned-once"}
                    )
                    assert late.get("ok") is False, late
                    assert app._attach_permission_bridge._pending == {}
                    assert app._attach_permission_bridge._pending_option_ids == {}
                    assert app._attach_permission_bridge._pending_details == {}
                    assert remote_bridge._queued == set()
                    assert remote_bridge._answered_elsewhere == set()
                    assert remote_bridge._showing is None
                    assert all(t.done() for t in tap.tasks)
                    assert len(app.permission_manager._session_approvals) == 0
                    assert len(app.permission_manager._acp_session_grants) == (1 if scenario == "local-first" else 0)
                notices = [item for item in received if item.reply_to == message.id and item.metadata.get("acp_waiting")]
                assert len(notices) == (0 if scenario == "lifecycle" else 1)
                assert all(item.action == "message" and item.thread_id == message.thread_id for item in notices)
                matching = [
                    item for item in received
                    if item.reply_to == message.id and not item.metadata.get("acp_waiting")
                ]
                result = {
                    "ordinary_socket_request_acked": accepted,
                    "reply_actions": [item.action for item in matching],
                    "same_thread": all(item.thread_id == message.thread_id for item in matching),
                    "reply_to_request": all(item.reply_to == message.id for item in matching),
                    "visible_answer": matching[0].content,
                    "outcome": matching[-1].metadata.get("turn_end", {}).get("outcome"),
                    "sdk_child_count": int(owner.runner._process is not None),
                }
                print(json.dumps(result))
                assert (
                    accepted
                    and result["reply_actions"] == ["message", "turn_end"]
                    and result["same_thread"]
                    and result["reply_to_request"]
                    and (
                        scenario == "outer-cancel"
                        or result["visible_answer"]
                        == ("OWNED SDK DENIED" if scenario == "simultaneous" else "OWNED SDK ANSWER")
                    )
                    and result["outcome"] == ("cancelled" if scenario == "outer-cancel" else "end_turn")
                ), result
            finally:
                cleanup_error = None
                if app is not None:
                    owner = getattr(app, "acp_orchestrator", None)
                    if owner is not None and owner.runner is not None:
                        original_close = owner.runner.close

                        async def close_then_cancel():
                            await original_close()
                            raise asyncio.CancelledError()

                        owner.runner.close = close_then_cancel
                    try:
                        await asyncio.wait_for(app.cleanup(), timeout=10)
                    except BaseException as exc:
                        cleanup_error = exc
                    assert cleanup_error is None, cleanup_error
                    assert hub_socket_path is None or not hub_socket_path.exists()
                if tap is not None:
                    for task in tap.tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tap.tasks, return_exceptions=True)
                if sdk_process is not None:
                    await asyncio.wait_for(sdk_process.wait(), timeout=5)
                    assert sdk_process.returncode is not None
                if requester_server is not None:
                    await requester_server.stop()
                if requester_presence is not None:
                    requester_presence.remove()
                for child in children:
                    if child.returncode is None:
                        try:
                            await asyncio.wait_for(child.wait(), timeout=1)
                        except asyncio.TimeoutError:
                            child.terminate()
                            await asyncio.wait_for(child.wait(), timeout=3)
                print(json.dumps({"remaining_owned_children": sum(p.returncode is None for p in children)}))
                assert all(p.returncode is not None for p in children)
