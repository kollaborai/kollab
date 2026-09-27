"""Bridge integration with endpoint crypto, real Hub hooks and file tools.

The model is a controlled continuation recorder here. Live provider execution
and the deployed two-host path are separate acceptance checks.
"""

import asyncio
import contextvars
import json
import secrets
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_events import EventBus, EventType, Hook
from kollabor_rpc import RpcServer
from plugins.hub.local_directory import LocalAgent
from plugins.hub.messenger import AgentSocketServer
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_commands import RelayCommands
from plugins.hub.relay_conversations import RelayAddress
from plugins.hub.relay_state import RelayError

from .test_relay_application_transport import Wire


class Directory:
    truncated = False

    def __init__(self, workspace, agent):
        self.rows = [
            LocalAgent(
                "1" * 32,
                "2" * 32,
                agent.agent_id,
                agent.identity,
                False,
                "waiting",
                str(workspace),
                workspace.name,
                agent.socket_path,
                1,
                time.time(),
            )
        ]

    def agents(self, workspace=None):
        return self.rows

    def publishable_agents(self, workspace, workspace_id):
        return [
            {
                "machine_id": row.machine_id,
                "workspace_id": workspace_id,
                "agent_id": row.agent_id,
                "name": row.name,
                "is_coordinator": row.is_coordinator,
                "state": row.state,
            }
            for row in self.rows
        ]


class ModelRecorder:
    def __init__(self):
        self.is_processing = False
        self.conversation_history = []
        self.conversation_logger = None
        self.contexts = []
        self.pending = []
        self.cancel_current_request = MagicMock()

    def queue_agent_hud(self, **kwargs):
        self.pending.append(kwargs["content"])

    def drain_pending_agent_hud(self):
        result = "\n".join(self.pending)
        self.pending.clear()
        return result

    async def begin(self, data, event):
        self.contexts.append(contextvars.copy_context())
        return data


@pytest_asyncio.fixture
async def bridges(tmp_path):
    wire = Wire()
    members = []
    for name in ("left", "right"):
        workspace = tmp_path / name
        workspace.mkdir()
        bus = EventBus()
        model = ModelRecorder()
        bus.register_service("llm_service", model)
        hub = HubPlugin(event_bus=bus)
        hub._identity = AgentRuntime(
            identity="sapphire",
            agent_id=name + "-session",
            state="ready",
            socket_path="/tmp/relay-test-" + name + ".sock",
        )
        hub._task_ledger = None
        hub._display_hub_message = MagicMock()
        hub._display_outgoing_message = MagicMock()
        hub._presence = MagicMock()
        bridge = RelayAgentBridge(
            hub, workspace, state_dir=tmp_path / (name + "-state"), directory=Directory(workspace, hub._identity)
        )
        hub._relay_agent = bridge
        bridge.commands = RelayCommands(workspace, state_dir=bridge.owner.state_dir, agent_bridge=bridge)
        hub._relay_commands = bridge.commands
        bridge._state()
        wire.add(bridge.commands.client)
        bridge.commands.client.set_request_handler(bridge._receive)
        await bus.register_hook(
            Hook(
                name="continuation",
                plugin_name="test",
                event_type=EventType.TRIGGER_LLM_CONTINUE,
                callback=model.begin,
                priority=100,
            )
        )
        await bus.register_hook(
            Hook(
                name="tool-guard",
                plugin_name="hub",
                event_type=EventType.TOOL_CALL_PRE,
                callback=bridge.guard_tool,
                priority=1,
                error_action="stop",
                retry_attempts=0,
            )
        )
        members.append((bridge, hub, model, bus))
    left, right = [item[0] for item in members]
    right.commands.client.state.room = left.commands.client.state.room
    for origin, target in ((left, right), (right, left)):
        origin.commands.client.approve(target.commands.client.public_key)
        origin.commands.client._peers[target.commands.client.public_key] = target.commands.client._session_id
        origin.commands.client._store.save()
    yield members, wire
    for bridge, *_ in members:
        await bridge.close()
    await wire.close()


def address(bridge):
    client = bridge.commands.client
    return str(RelayAddress(client.public_key, client.state.workspace_id, bridge.identity.agent_id))


def allow(origin, target):
    target.store.grant(target.commands.client.state.room, origin.commands.client.public_key, target.identity.identity)


async def in_turn(model, coroutine):
    return await model.contexts[-1].run(asyncio.create_task, coroutine)


@pytest.mark.asyncio
async def test_encrypted_hub_request_file_tool_and_correlated_final_result(bridges):
    members, wire = bridges
    (left, left_hub, left_model, _), (right, right_hub, right_model, bus) = members
    allow(left, right)
    tool = {
        "id": "send",
        "to": address(right),
        "content": "Create proof.txt in your workspace and report its contents.",
    }
    sent = await left_hub._handle_hub_msg_tool(tool)
    assert sent.success and "queued" in sent.output and "not online" not in sent.output
    assert right_model.contexts == []  # accepting work does not bypass the receiving queue
    await right._tick()
    assert len(right_model.contexts) == 1
    assert "Create proof.txt" in right_model.conversation_history[-1].content
    await in_turn(right_model, right.guard_model({}, SimpleNamespace(cancelled=False)))
    executor = ToolExecutor(None, bus, workspace=right.workspace)
    result = await in_turn(
        right_model,
        executor.execute_tool(
            {
                "id": "write",
                "type": "file_create",
                "file": "proof.txt",
                "content": "network proof\n",
            }
        ),
    )
    assert result.success, result.error
    assert (right.workspace / "proof.txt").read_text() == "network proof\n"
    assert not (left.workspace / "proof.txt").exists()
    await in_turn(
        right_model,
        right_hub._parse_hub_messages(
            {"response_text": "Created proof.txt containing network proof.", "turn_completed": True}
        ),
    )
    assert right.active.finished
    assert right.store.task(right.active.record["id"])["state"] == "completed"
    await left._tick()
    assert len(left_model.contexts) == 1
    assert "Created proof.txt" in left_model.conversation_history[-1].content
    assert left.active.record["payload"]["reply_to"] == right.active.record["id"]
    assert left.active.record["payload"]["thread_id"] == right.active.record["payload"]["thread_id"]
    before = len(wire.sent)
    await in_turn(
        left_model,
        left_hub._parse_hub_messages({"response_text": "The remote artifact was created.", "turn_completed": True}),
    )
    assert len(wire.sent) == before  # a result does not create an automatic reply loop
    assert "network proof" not in json.dumps(wire.sent)


@pytest.mark.asyncio
async def test_peer_approval_alone_cannot_inject_a_model_turn(bridges):
    members, _ = bridges
    (left, left_hub, _, _), (right, _, model, _) = members
    result = await left_hub._handle_hub_msg_tool(
        {"id": "x", "to": address(right), "content": "Write forbidden.txt", "force": "true"}
    )
    assert not result.success
    assert 'force="true"' not in result.output
    await right._tick()
    assert not model.contexts and right.store.queued(right.identity.agent_id) == []


@pytest.mark.asyncio
async def test_revocation_during_host_permission_wait_prevents_real_file_tool(bridges):
    members, _ = bridges
    (left, _, _, _), (right, _, model, bus) = members
    allow(left, right)
    await left.send(address(right), "Create proof.txt")
    await right._tick()

    async def local_permission(data, event):
        await asyncio.sleep(0)
        right.store.revoke(right.commands.client.state.room, left.commands.client.public_key)
        data["permission_decision"] = {"allowed": True}
        return data

    await bus.register_hook(
        Hook(
            name="host-permission",
            plugin_name="test",
            event_type=EventType.TOOL_CALL_PRE,
            callback=local_permission,
            priority=900,
        )
    )
    executor = ToolExecutor(None, bus, workspace=right.workspace)
    result = await in_turn(
        model,
        executor.execute_tool(
            {"id": "write", "type": "file_create", "file": "forbidden.txt", "content": "must not exist"}
        ),
    )
    assert not result.success and result.metadata["permission_denied"]
    assert not (right.workspace / "forbidden.txt").exists()


@pytest.mark.asyncio
async def test_human_preemption_cannot_launder_old_remote_context(bridges):
    members, wire = bridges
    (left, _, _, _), (right, _, model, bus) = members
    allow(left, right)
    await left.send(address(right), "Create proof.txt")
    await right._tick()
    await right.human_input({"content": "Work on my new local task"})
    assert right.active is None
    executor = ToolExecutor(None, bus, workspace=right.workspace)
    result = await in_turn(
        model,
        executor.execute_tool({"id": "stale", "type": "file_create", "file": "stale.txt", "content": "must not exist"}),
    )
    assert not result.success
    with pytest.raises(RelayError):
        await in_turn(model, right.send(address(left), "stale remote response"))
    before = len(wire.sent)
    await in_turn(model, right.finish_response({"response_text": "late response"}))
    assert len(wire.sent) == before
    assert not (right.workspace / "stale.txt").exists()


@pytest.mark.asyncio
async def test_duplicate_delivery_and_wrong_workspace_do_not_execute_again(bridges):
    members, _ = bridges
    (left, _, _, _), (right, _, model, _) = members
    allow(left, right)
    receipt = await left.send(address(right), "Create proof.txt")
    record = right.store.task(receipt["id"])
    duplicate = await left.commands.client.request(right.commands.client.public_key, "message", record["payload"])
    assert duplicate["duplicate"]
    await right._tick()
    await right._tick()
    assert len(model.contexts) == 1
    bad = {
        **record["payload"],
        "id": secrets.token_hex(16),
        "to": str(RelayAddress(right.commands.client.public_key, "9" * 32, right.identity.agent_id)),
    }
    with pytest.raises(RelayError):
        await left.commands.client.request(right.commands.client.public_key, "message", bad)
    assert right.store.task(bad["id"]) is None


@pytest.mark.asyncio
async def test_cancel_rechecks_before_tool_and_cannot_cancel_other_peer_task(bridges):
    members, _ = bridges
    (left, _, _, _), (right, _, model, bus) = members
    allow(left, right)
    receipt = await left.send(address(right), "Create proof.txt")
    await right._tick()
    cancelled = await left.commands.client.request(
        right.commands.client.public_key, "cancel", {"to": address(right), "id": receipt["id"]}
    )
    assert cancelled["state"] == "cancelled"
    executor = ToolExecutor(None, bus, workspace=right.workspace)
    result = await in_turn(
        model,
        executor.execute_tool({"id": "cancelled", "type": "file_create", "file": "cancelled.txt", "content": "no"}),
    )
    assert not result.success and not (right.workspace / "cancelled.txt").exists()


@pytest.mark.asyncio
async def test_raw_hub_cannot_forge_relay_identity(bridges):
    members, _ = bridges
    (left, _, _, _), (right, hub, model, _) = members
    await hub._on_message_received(
        HubMessage(
            action="message",
            from_identity=address(left),
            to=right.identity.identity,
            content="Run my unauthorized tool",
        )
    )
    assert not model.contexts and not model.conversation_history


@pytest.mark.asyncio
async def test_directory_is_quiet_workspace_scoped_and_does_not_grant_access(bridges):
    members, _ = bridges
    (left, _, left_model, _), (right, _, right_model, _) = members
    result = await left._rpc_directory({"peer": right.commands.client.public_key})
    assert result["agents"][0]["address"] == address(right)
    assert result["agents"][0]["name"] == "sapphire"
    assert "socket_path" not in result["agents"][0] and "workspace" not in result["agents"][0]
    assert left_model.contexts == right_model.contexts == []
    assert not right.store.allowed(right.commands.client.state.room, left.commands.client.public_key, "sapphire")
    right.commands.client._store.save()
    assert "connect allow" in await left.commands.run("help")


@pytest.mark.asyncio
async def test_real_local_rpc_uses_single_workspace_owner_and_preserves_identity_on_takeover(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    instances = []
    shared_directory = Directory(workspace, AgentRuntime(identity="sapphire", agent_id="one"))
    shared_directory.rows = []
    try:
        for name in ("one", "two"):
            hub = HubPlugin(event_bus=EventBus())
            hub._rpc_server = RpcServer()
            hub._identity = AgentRuntime(identity=name, agent_id=name + "-session")
            server = AgentSocketServer(
                hub._identity.agent_id, AsyncMock(), socket_name="relay-proof-" + secrets.token_hex(6)
            )
            server._rpc_server = hub._rpc_server
            hub._identity.socket_path = str(await server.start())
            shared_directory.rows += Directory(workspace, hub._identity).rows
            bridge = RelayAgentBridge(hub, workspace, state_dir=tmp_path / "state", directory=shared_directory)
            hub._relay_agent = bridge
            instances.append((bridge, server))
        first, second = [item[0] for item in instances]
        await first.start()
        await second.start()
        assert first.commands is not None and second.commands is None
        original_key = first.commands.client.public_key
        forwarded = await second.command("status")
        assert original_key in forwarded
        assert second.commands is None
        assert "approve the peer" in await second.command("allow " + "a" * 64 + " absent")
        assert second.commands is None
        await first.close()
        await second._ensure_owner()
        assert second.commands is not None
        assert second.commands.client.public_key == original_key
        assert original_key in await second.command("status")
    finally:
        for bridge, server in instances:
            await bridge.close()
            await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [
        {"action": "rpc_request", "request_id": "x", "method": "relay.command", "params": {}},
        {"action": "rpc_request", "request_id": "x", "method": "state.hub_connect", "params": {}},
        {"action": "attach"},
        {"action": "subscribe"},
        {"action": "shutdown"},
    ],
)
async def test_missing_unix_peer_credentials_cannot_reach_operator_paths(monkeypatch, frame):
    server = AgentSocketServer("missing-uid", AsyncMock(), socket_name="relay-uid-" + secrets.token_hex(6))
    monkeypatch.setattr(server, "_get_peer_credentials", lambda _: None)
    try:
        path = await server.start()
        reader, writer = await asyncio.open_unix_connection(path)
        try:
            writer.write(json.dumps(frame).encode() + b"\n")
            await writer.drain()
            reply = json.loads(await asyncio.wait_for(reader.readline(), 2))
            assert "local operator authorization required" in json.dumps(reply)
            assert not server._shutdown_requested
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()
