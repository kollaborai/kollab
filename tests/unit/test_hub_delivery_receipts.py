"""Delivery evidence a sender can read, and capture that shows what was sent.

Issue #609: a hub_msg reported `sent` with a thread id, but nothing said the
recipient had taken it; a recipient's `kollab --hub capture` showed none of the
messages sent to it; and a failed send could come back with a blank error.
These tests drive the real socket ack, the real router and the real capture
ring, with only the socket transport stubbed where it must be.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_tui.display_tap import DisplayTap
from plugins.hub.acp_tools import ACPHubTools
from plugins.hub.capture import render_transcript
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin

# ---------------------------------------------------------------------------
# Capture: messages displayed on a recipient reach its JSON transcript.
# ---------------------------------------------------------------------------


class _Coordinator:
    """Stands in for MessageDisplayCoordinator: rendered output reaches the tap
    the way `_output_rendered` publishes it."""

    def __init__(self, tap: DisplayTap):
        self._tap = tap

    def display_message_sequence(self, items):
        for _kind, content, _options in items:
            self._tap.publish({"type": "output", "rendered": content})


def _capture_plugin(tap: DisplayTap) -> HubPlugin:
    plugin = object.__new__(HubPlugin)
    plugin._display_tap = tap
    renderer = SimpleNamespace(message_coordinator=_Coordinator(tap))
    plugin.event_bus = SimpleNamespace(
        get_service=lambda name: renderer if name == "renderer" else None
    )
    return plugin


def test_hub_messages_reach_the_json_capture_beside_state_snapshots():
    """A heartbeat's state_snapshot is a semantic event. Before the fix, its
    presence made the JSON capture drop every rendered output event, which is
    where hub message boxes live, so the messages never reached a capture."""
    tap = DisplayTap(history_size=200)
    plugin = _capture_plugin(tap)
    tap.publish({"type": "state_snapshot", "hub_identity": "lapis"})

    plugin._render_hub_box("koordinator", "lapis", "build the parser report")
    plugin._render_hub_box("lapis", "koordinator", "report is ready")

    page = plugin._get_output_lines(limit=50, format="json", max_chars=10_000)
    transcript = render_transcript(page, identity="lapis")

    assert "build the parser report" in transcript
    assert "report is ready" in transcript


def test_outgoing_hub_messages_reach_the_json_capture():
    tap = DisplayTap(history_size=200)
    plugin = _capture_plugin(tap)
    plugin._identity = SimpleNamespace(identity="koordinator")
    tap.publish({"type": "state_snapshot", "hub_identity": "koordinator"})

    plugin._display_outgoing_message("lapis", "please run the migration")

    page = plugin._get_output_lines(limit=50, format="json", max_chars=10_000)
    transcript = render_transcript(page, identity="koordinator")

    assert "please run the migration" in transcript


# ---------------------------------------------------------------------------
# Socket ack: a delivery counts only when the recipient acks this message id.
# ---------------------------------------------------------------------------


def _message(to: str = "lapis", content: str = "hello") -> HubMessage:
    return HubMessage(
        action="message",
        id=uuid.uuid4().hex,
        from_agent="koordinator-id",
        from_identity="koordinator",
        to=to,
        content=content,
    )


async def _serve_reply(path: str, reply: str):
    async def handle(reader, writer):
        await reader.readline()
        writer.write((reply + "\n").encode())
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(handle, path=path)


@pytest.fixture
def short_socket_dir() -> Iterator[Path]:
    """Bind test sockets from a short /tmp directory.

    AF_UNIX paths cap at 104 bytes and pytest's tmp_path lives deep under
    ``/var/folders/...`` on macOS, past that limit; the same idiom as the
    short_socket_dir fixtures in the hub rpc and peer-credential tests.
    """
    short_dir = Path(tempfile.mkdtemp(prefix="hubrecv-", dir="/tmp"))
    yield short_dir
    shutil.rmtree(short_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_an_ack_for_another_message_does_not_confirm_this_one(short_socket_dir):
    sock = str(short_socket_dir / "lapis.sock")
    message = _message()
    server = await _serve_reply(sock, '{"type": "ack", "id": "' + "f" * 32 + '"}')
    try:
        confirmed = await AgentMessenger.send_to_agent(sock, message, timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()

    assert confirmed is False


@pytest.mark.asyncio
async def test_an_ack_naming_this_message_confirms_it(short_socket_dir):
    sock = str(short_socket_dir / "lapis.sock")
    message = _message()
    server = await _serve_reply(sock, '{"type": "ack", "id": "%s"}' % message.id)
    try:
        confirmed = await AgentMessenger.send_to_agent(sock, message, timeout=2.0)
    finally:
        server.close()
        await server.wait_closed()

    assert confirmed is True


# ---------------------------------------------------------------------------
# Sender: each local send reports how far it got, with thread and recipient.
# ---------------------------------------------------------------------------


def _local_plugin(recipients: list[AgentRuntime]) -> HubPlugin:
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="koordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._relay_agent = None
    plugin._vault = None
    plugin._task_ledger = None
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = list(recipients)
    plugin._presence.discover_agents_async = AsyncMock(return_value=list(recipients))
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    return plugin


def _lapis(socket_path: str = "/nonexistent/lapis.sock") -> AgentRuntime:
    return AgentRuntime(
        name="lapis",
        identity="lapis",
        agent_id="lapis-id",
        socket_path=socket_path,
    )


def _send_call(target: str = "lapis") -> dict:
    return {"id": "tool-1", "to": target, "message": "run the parser tests"}


@pytest.fixture
def no_disk(tmp_path):
    """Keep delivery traces and mailbox drops out of the real ~/.kollab."""
    with (
        patch.object(HubPlugin, "_delivery_trace", return_value=MagicMock()),
        patch.object(AgentMessenger, "send_to_file", AsyncMock(return_value=True)),
    ):
        yield tmp_path


@pytest.mark.asyncio
async def test_a_recipient_ack_is_reported_as_confirmed_delivery(no_disk):
    plugin = _local_plugin([_lapis()])
    with patch.object(AgentMessenger, "send_to_agent", AsyncMock(return_value=True)):
        result = await plugin._handle_hub_msg_tool(_send_call())

    assert result.success
    delivery = result.metadata["delivery"]
    assert delivery["state"] == "confirmed"
    assert delivery["recipients"] == ["lapis"]


@pytest.mark.asyncio
async def test_a_socket_that_fails_is_queued_not_confirmed(no_disk):
    plugin = _local_plugin([_lapis()])
    with patch.object(AgentMessenger, "send_to_agent", AsyncMock(return_value=False)):
        result = await plugin._handle_hub_msg_tool(_send_call())

    assert result.success
    assert result.metadata["delivery"]["state"] == "queued"
    assert result.metadata["delivery"]["recipients"] == []


@pytest.mark.asyncio
async def test_a_target_not_online_is_never_reported_as_confirmed(no_disk):
    plugin = _local_plugin([])
    with patch.object(AgentMessenger, "send_to_agent", AsyncMock(return_value=True)):
        result = await plugin._handle_hub_msg_tool(_send_call("nobody"))

    assert result.metadata["delivery"]["state"] != "confirmed"
    assert result.metadata["delivery"]["recipients"] == []


# ---------------------------------------------------------------------------
# ACP send result: `sent` only when the recipient confirmed.
# ---------------------------------------------------------------------------


def _acp_with(result: ToolExecutionResult) -> ACPHubTools:
    hub = SimpleNamespace(
        _identity=SimpleNamespace(
            identity="koordinator", agent_id="koordinator-id", tags={}
        ),
        _presence=None,
        _acp_roster=lambda: [{"identity": "lapis"}],
        _get_coordinator_identity=lambda: "",
        _handle_hub_msg_tool=AsyncMock(return_value=result),
        event_bus=SimpleNamespace(get_service=lambda name: None),
    )
    return ACPHubTools(hub)


def _delivery_result(state: str, recipients: list[str]) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_id="t",
        tool_type="hub_msg",
        success=True,
        output=f"{state} output",
        metadata={"delivery": {"state": state, "recipients": recipients}},
    )


@pytest.mark.asyncio
async def test_acp_says_sent_only_when_the_recipient_confirmed():
    tools = _acp_with(_delivery_result("confirmed", ["lapis"]))

    out = await tools.call("hub_msg", {"to": "lapis", "message": "hi"})

    assert out["status"] == "sent"
    assert out["delivery"] == "confirmed"
    assert out["thread"]


@pytest.mark.asyncio
async def test_acp_hub_spawn_forwards_explicit_identity():
    handler = AsyncMock(
        return_value=ToolExecutionResult(
            tool_id="spawn",
            tool_type="hub_spawn",
            success=True,
            output="created",
        )
    )
    hub = SimpleNamespace(_identity=SimpleNamespace(identity="jasper"), _handle_hub_spawn_tool=handler)
    tools = ACPHubTools(hub)

    result = await tools.call(
        "hub_spawn",
        {
            "name": "coder",
            "identity": "kollabor",
            "task": "review the work",
            "llm": "codex-cli",
        },
    )

    assert result["success"]
    call = handler.await_args.args[0]
    assert call["input"]["name"] == "coder"
    assert call["input"]["identity"] == "kollabor"
    assert call["input"]["llm"] == "codex-cli"


@pytest.mark.asyncio
async def test_acp_does_not_call_a_queued_message_sent():
    tools = _acp_with(_delivery_result("queued", []))

    out = await tools.call("hub_msg", {"to": "lapis", "message": "hi"})

    assert out["status"] == "queued"
    assert out["delivery"] == "queued"


# ---------------------------------------------------------------------------
# Blank error: a failure with an empty exception message still names its type.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_handler_exception_with_no_message_names_its_type():
    class _EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            return {}

    executor = ToolExecutor(mcp_integration=None, event_bus=_EventBus())

    async def handler(_tool_data):
        raise TimeoutError()

    executor.register_plugin_handler("hub_msg", handler)
    result = await executor.execute_tool(
        {"type": "hub_msg", "id": "call-1", "to": "lapis", "message": "hi"}
    )

    assert not result.success
    assert "TimeoutError" in result.error
