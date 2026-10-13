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
from kollabor_agent.runtime import AgentRuntime
from kollabor_ai.profile_manager import ProfileManager
from kollabor_config import config_utils
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


@pytest.mark.asyncio
async def test_real_terminal_deferred_acp_hub_lifecycle():
    """Real constructor + local Hub + SDK fake; no Ed25519, real adapter, or CLI detach."""
    with tempfile.TemporaryDirectory(prefix="kacp-", dir="/tmp") as task_dir:
        root = Path(task_dir)
        global_root = root / "global"
        home = root / "home"
        project = root / "project"
        for path in (global_root, home, project):
            path.mkdir()
        profile = {"provider": "acp", "command": [sys.executable, "-c", AGENT], "workspace": str(project)}
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

                async def completion():
                    while len([item for item in received if item.reply_to == message.id]) < 2:
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(completion(), timeout=6)
                matching = [item for item in received if item.reply_to == message.id]
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
                    and result["visible_answer"] == "OWNED SDK ANSWER"
                    and result["outcome"] == "end_turn"
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
