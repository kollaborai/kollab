"""Slice B (hub surface) of the agent network: docs/specs/agent-network-simple-flow.md.

Covers the redesigned /connect status text, the name/trust commands, hub_msg
routing through an agent@device handle, and the roster/hub-context lines that
merge remote agents into the local picture. Slice A owns the real relay
bridge (plugins/hub/relay_agent.py); everything here mocks its documented
interface (device_name, set_device_name, trust_level, set_trust_level,
remote_agents, resolve_handle) rather than importing it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kollabor_events.data_models import ConversationMessage
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_commands import RelayCommands

REMOTE_ROWS = [
    {
        "name": "infra",
        "device": "alzan-prod-home",
        "handle": "infra@alzan-prod-home",
        "state": "idle",
        "address": "relay:" + "a" * 64 + ":" + "b" * 32 + ":infra-1",
        "online": True,
        "is_coordinator": False,
        "workspace_id": "b" * 32,
    },
    {
        "name": "ops",
        "device": "alzan-prod-home",
        "handle": "ops@alzan-prod-home",
        "state": "working",
        "task": "rotating logs",
        "address": "relay:" + "c" * 64 + ":" + "d" * 32 + ":ops-1",
        "online": False,
        "is_coordinator": False,
        "workspace_id": "d" * 32,
    },
]


def _relay_commands(tmp_path, *, agent_bridge):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commands = RelayCommands(
        workspace, state_dir=tmp_path / "relay-state", agent_bridge=agent_bridge
    )
    commands.client.state.origin = "https://kollabor.ai"
    commands.client._store.save()
    commands.client._session_id = "e" * 32
    commands.client._state = "online"
    return commands


# --------------------------------------------------------------------- #
# /connect status
# --------------------------------------------------------------------- #


def test_status_shows_network_and_remote_rows_without_keys(tmp_path):
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "mac-kollab",
        remote_agents=lambda: REMOTE_ROWS,
        plugin=SimpleNamespace(_presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")),
        _enrollment_issuer=None,
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    status = commands.format_status()

    assert "network kollabor.ai via kollabor.ai  trust: open" in status
    assert "this device mac-kollab" in status
    assert "infra@alzan-prod-home - idle" in status
    assert "ops@alzan-prod-home" not in status
    assert "offline devices: alzan-prod-home" in status
    assert "koordinator (this device)" in status
    assert "join code: run /connect code" in status
    # No keys, workspace ids, or relay: addresses by default.
    assert commands.client.public_key not in status
    assert "relay:" not in status
    assert "workspace id" not in status


def test_status_keys_appends_the_technical_block(tmp_path):
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "mac-kollab",
        remote_agents=lambda: [],
        plugin=SimpleNamespace(_presence=None, _identity=None),
        _enrollment_issuer=None,
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    status = commands.format_status(show_keys=True)

    assert commands.client.public_key in status
    assert "workspace id:" in status
    assert "Private room" in status


@pytest.mark.asyncio
async def test_connect_status_keys_reaches_format_status_with_show_keys(tmp_path):
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "",
        remote_agents=lambda: [],
        plugin=SimpleNamespace(_presence=None, _identity=None),
        _enrollment_issuer=None,
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    plain = await commands._run("status", source_agent=None)
    keyed = await commands._run("status keys", source_agent=None)

    assert commands.client.public_key not in plain
    assert commands.client.public_key in keyed


# --------------------------------------------------------------------- #
# /connect name, /connect trust
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_connect_name_validates_and_calls_set_device_name(tmp_path):
    calls = []
    bridge = SimpleNamespace(set_device_name=lambda name: calls.append(name) or name)
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    result = await commands._run("name mac-kollab", source_agent=None)

    assert result == "this device is now mac-kollab"
    assert calls == ["mac-kollab"]


@pytest.mark.asyncio
async def test_connect_name_rejects_an_invalid_name(tmp_path):
    commands = _relay_commands(tmp_path, agent_bridge=SimpleNamespace())

    result = await commands._run("name Not A Device", source_agent=None)

    assert result.startswith("connect: device name:")


@pytest.mark.asyncio
async def test_connect_trust_open_calls_set_trust_level(tmp_path):
    calls = []
    bridge = SimpleNamespace(set_trust_level=lambda level: calls.append(level) or level)
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    result = await commands._run("trust open", source_agent=None)

    assert result == "trust for kollabor.ai is now open"
    assert calls == ["open"]


@pytest.mark.asyncio
async def test_connect_trust_manual_warns_about_authorize(tmp_path):
    bridge = SimpleNamespace(set_trust_level=lambda level: level)
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    result = await commands._run("trust manual", source_agent=None)

    assert "trust for kollabor.ai is now manual" in result
    assert "messages now need /connect authorize or /connect send" in result


@pytest.mark.asyncio
async def test_connect_trust_rejects_an_unknown_level(tmp_path):
    commands = _relay_commands(tmp_path, agent_bridge=SimpleNamespace())

    result = await commands._run("trust superadmin", source_agent=None)

    assert result == "connect: trust: open, agents or manual"


# --------------------------------------------------------------------- #
# /connect allow / deny / revoke resolve a device name to its peer key
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_connect_allow_resolves_a_device_name_to_its_peer_key(tmp_path):
    seen = {}

    async def application_command(head, rest, source_agent=None):
        seen["head"] = head
        seen["rest"] = rest
        return "ok"

    bridge = SimpleNamespace(
        remote_agents=lambda: REMOTE_ROWS, application_command=application_command
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    result = await commands._run("allow alzan-prod-home ops", source_agent="me")

    assert result == "ok"
    assert seen["head"] == "allow"
    assert seen["rest"] == "a" * 64 + " ops"


@pytest.mark.asyncio
async def test_connect_revoke_rejects_an_unknown_device(tmp_path):
    commands = _relay_commands(tmp_path, agent_bridge=SimpleNamespace(remote_agents=lambda: []))

    result = await commands._run("revoke some-unknown-device", source_agent=None)

    assert result == "connect: no known device matches 'some-unknown-device'; use its 64-hex peer key"


# --------------------------------------------------------------------- #
# hub_msg to agent@device routes through resolve_handle + send
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_route_message_resolves_a_handle_and_sends_with_no_grant():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="lapis", agent_id="lapis-1")
    hub._vault = None
    hub._task_ledger = None
    sent = {}

    async def fake_send(address, content, **kwargs):
        sent["address"] = address
        sent["content"] = content
        sent["kwargs"] = kwargs
        return {"id": "x", "state": "delivered"}

    hub._relay_agent = SimpleNamespace(
        resolve_handle=lambda handle: f"relay:resolved:{handle}",
        send=fake_send,
    )

    msg = HubMessage(
        action="message",
        from_agent="lapis-1",
        from_identity="lapis",
        to="infra@alzan-prod-home",
        content="check the tunnel",
        scope=MessageScope.DIRECT.value,
    )

    rejections = await hub._route_message(msg)

    assert rejections == []
    assert sent["address"] == "relay:resolved:infra@alzan-prod-home"
    assert sent["content"] == "check the tunnel"
    assert sent["kwargs"] == {"kind": "message"}
    assert msg.metadata["network"] == {"to": "relay:resolved:infra@alzan-prod-home"}


@pytest.mark.asyncio
async def test_route_message_reports_a_relay_error_from_resolve_handle():
    from plugins.hub.relay_state import RelayError

    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="lapis", agent_id="lapis-1")
    hub._vault = None
    hub._task_ledger = None

    def raise_unknown(handle):
        raise RelayError("no such device on this network")

    async def unreachable_send(*args, **kwargs):
        raise AssertionError("send must not be called when resolve_handle fails")

    hub._relay_agent = SimpleNamespace(resolve_handle=raise_unknown, send=unreachable_send)

    msg = HubMessage(
        action="message",
        from_agent="lapis-1",
        from_identity="lapis",
        to="ghost@nowhere",
        content="hello?",
        scope=MessageScope.DIRECT.value,
    )

    rejections = await hub._route_message(msg)

    assert rejections == [("ghost@nowhere", "no such device on this network")]


# --------------------------------------------------------------------- #
# hub_broadcast scope="network" reaches online remote agents under open trust
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_broadcast_scope_network_reaches_online_remote_agents_under_open_trust():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="lapis", agent_id="lapis-1")
    hub._presence = SimpleNamespace(get_cached_agents=lambda: [])
    hub._route_message = _async_empty_list  # local routing is exercised elsewhere

    sent = []

    async def fake_send(address, content, **kwargs):
        sent.append((address, content, kwargs))
        return {"id": "x", "state": "delivered"}

    hub._relay_agent = SimpleNamespace(
        remote_agents=lambda: REMOTE_ROWS,
        trust_level=lambda: "open",
        resolve_handle=lambda handle: f"relay:resolved:{handle}",
        send=fake_send,
    )

    result = await hub._handle_broadcast_command("shipped phase B", scope="network")

    # Only the online row (infra) is reached; the offline row (ops) is not.
    assert sent == [
        ("relay:resolved:infra@alzan-prod-home", "shipped phase B", {"kind": "message"})
    ]
    assert "1 network agent(s)" in result


@pytest.mark.asyncio
async def test_broadcast_without_network_scope_stays_local():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="lapis", agent_id="lapis-1")
    hub._presence = SimpleNamespace(get_cached_agents=lambda: [])
    hub._route_message = _async_empty_list

    called = []
    hub._relay_agent = SimpleNamespace(
        remote_agents=lambda: REMOTE_ROWS,
        trust_level=lambda: "open",
        resolve_handle=lambda handle: called.append(handle),
        send=None,
    )

    result = await hub._handle_broadcast_command("shipped phase B")

    assert called == []
    assert "network agent(s)" not in result


# --------------------------------------------------------------------- #
# hub_status / hub_agents gain remote rows
# --------------------------------------------------------------------- #


def test_format_status_lists_remote_agents_with_device_online_state():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(
        identity="koordinator",
        agent_id="k1",
        is_coordinator=True,
        state="idle",
        current_task="",
        project="",
    )
    hub._presence = SimpleNamespace(get_cached_agents=lambda: [])
    hub._work_queue = None
    hub._task_ledger = None
    hub._change_feed = None
    hub._relay_agent = SimpleNamespace(remote_agents=lambda: REMOTE_ROWS)

    status = hub._format_status()

    assert "infra@alzan-prod-home - idle (device online)" in status
    assert "ops@alzan-prod-home - working (device offline)" in status


# --------------------------------------------------------------------- #
# Hub context: network line, this device, remote rows, the two section 7
# lines about agent@device.
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_roster_context_includes_network_line_and_remote_rows():
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = SimpleNamespace(identity="lapis", is_coordinator=False)
    hub._roster = []
    hub.config = None
    hub._task_ledger = None
    hub._change_feed = None
    hub._relay_agent = SimpleNamespace(
        remote_agents=lambda: REMOTE_ROWS,
        trust_level=lambda: "open",
        device_name=lambda: "mac-kollab",
        harness_context=_async_empty_list,
    )
    hub._relay_commands = SimpleNamespace(
        client=SimpleNamespace(state=SimpleNamespace(origin="https://kollabor.ai"))
    )

    llm_service = SimpleNamespace(
        conversation_history=[ConversationMessage(role="system", content="base prompt")]
    )
    hub.event_bus = SimpleNamespace(
        get_service=lambda name: llm_service if name == "llm_service" else None
    )

    await hub._inject_roster_context({})

    content = llm_service.conversation_history[0].content
    assert "network: kollabor.ai via kollabor.ai (trust: open)" in content
    assert "this device: mac-kollab" in content
    assert "infra@alzan-prod-home - idle" in content
    assert "ops@alzan-prod-home - working: rotating logs" in content
    assert "offline devices: alzan-prod-home" in content
    assert "remote agents use the same tag with their full name:" in content
    assert '<hub_msg to="infra@alzan-prod-home">your message</hub_msg>' in content


async def _async_empty_list(*_args, **_kwargs):
    return []
