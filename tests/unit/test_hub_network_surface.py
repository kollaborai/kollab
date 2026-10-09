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
from unittest.mock import MagicMock

import pytest

from kollabor_events.data_models import ConversationMessage
from plugins.hub.device_names import contact_route_hex, key_label, short_fingerprint
from plugins.hub.models import HubMessage, MessageScope
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_commands import RelayCommands

REMOTE_ROWS = [
    {
        "name": "infra",
        "device": "home-server",
        "handle": "infra@home-server",
        "state": "idle",
        "address": "relay:" + "a" * 64 + ":" + "b" * 32 + ":infra-1",
        "online": True,
        "is_coordinator": False,
        "workspace_id": "b" * 32,
    },
    {
        "name": "ops",
        "device": "home-server",
        "handle": "ops@home-server",
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


@pytest.mark.asyncio
async def test_status_shows_network_and_remote_rows_without_keys(tmp_path):
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=lambda: REMOTE_ROWS,
        plugin=SimpleNamespace(_presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")),
        _enrollment_issuer=None,
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    status = await commands.format_status()

    assert "network kollabor.ai  trust: open" in status
    assert "this device laptop-kollab" in status
    assert "infra@home-server - idle" in status
    assert "ops@home-server" not in status
    assert "  home-server (offline)" in status
    assert "koordinator (this device)" in status
    assert "join code run /connect code" in status
    # No keys, workspace ids, or relay: addresses by default.
    assert commands.client.public_key not in status
    assert "relay:" not in status
    assert "workspace id" not in status


@pytest.mark.asyncio
async def test_status_shows_a_16_hex_contact_route_and_a_short_join_fingerprint(tmp_path):
    fingerprint = "abcd" + "0" * 56 + "ef01"
    row = SimpleNamespace(
        enrollment_id="a" * 32,
        device_name="ana-laptop",
        device_key_fingerprint=fingerprint,
    )
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=lambda: [],
        plugin=SimpleNamespace(_presence=None, _identity=None),
        _enrollment_issuer=None,
        identity=SimpleNamespace(agent_id="k1"),
        pending_enrollment_requests=lambda *, source_agent: (
            [row] if source_agent == "k1" else []
        ),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    status = await commands.format_status()

    route = contact_route_hex(commands.client.public_key)
    assert len(route) == 16
    assert f"contact route kollabor.ai/c/{route}" in status
    assert "ana-laptop wants to join   device ID abcd\u2026ef01" in status
    assert short_fingerprint(fingerprint) == "abcd\u2026ef01"
    assert fingerprint not in status
    assert "a" * 32 not in status


async def _async_remote_agents():
    return REMOTE_ROWS


@pytest.mark.asyncio
async def test_connect_snapshot_names_requests_roster_and_offline_devices(tmp_path):
    fingerprint = "abcd" + "0" * 56 + "ef01"
    row = SimpleNamespace(
        enrollment_id="a" * 32,
        device_name="ana-laptop",
        device_key_fingerprint=fingerprint,
        credential_categories=("conversation:send", "provider:openai:api_key"),
    )
    offline_key = "b" * 64
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        network_name=lambda: "marco-home",
        remote_agents=_async_remote_agents,
        identity=SimpleNamespace(agent_id="k1"),
        plugin=SimpleNamespace(
            _presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")
        ),
        pending_enrollment_requests=lambda *, source_agent: [row],
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={offline_key: "laptop-kollab"})
        ),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    commands.client.state.approvals = [offline_key]

    snapshot = await commands.connect_snapshot()

    assert (snapshot.network, snapshot.domain, snapshot.trust) == (
        "marco-home",
        "kollabor.ai",
        "open",
    )
    assert snapshot.device == "laptop-kollab"
    assert snapshot.relay_online is True
    assert snapshot.local_agents == ("koordinator",)
    assert snapshot.remote_agents == ("infra@home-server",)
    assert "laptop-kollab" in snapshot.offline_devices
    [request] = snapshot.requests
    assert request.device == "ana-laptop"
    assert request.fingerprint == short_fingerprint(fingerprint)
    assert request.categories == ("conversation:send", "provider:openai:api_key")
    assert snapshot.knocks == 0
    assert fingerprint not in repr(snapshot)


@pytest.mark.asyncio
async def test_connect_snapshot_counts_ringing_and_missed_knocks_kept_on_this_device(tmp_path):
    from plugins.hub.knocks import Ringing

    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=_async_remote_agents,
        plugin=SimpleNamespace(_presence=None, _identity=None),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    commands.knocks.ringing["a" * 32] = Ringing("a" * 32, "d" * 64, "ana", "hi", {}, 9e9)
    commands.knocks.store.missed.append(
        {"id": "b" * 32, "key": "e" * 64, "device": "bob", "text": "hi", "at": 1}
    )

    assert (await commands.connect_snapshot()).knocks == 2
    # The directory keeps no knock, so a relay that is down changes nothing here.
    commands.client._state = "reconnecting"
    snapshot = await commands.connect_snapshot()
    assert snapshot.relay_online is False and snapshot.knocks == 2


@pytest.mark.asyncio
async def test_connect_snapshot_names_the_primary_once_its_config_arrived(tmp_path, monkeypatch):
    from nacl.signing import SigningKey

    from kollabor_config.managed_config import ManagedConfig, write_managed_config
    from plugins.hub.relay_commands import ConnectSnapshot

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "home-server",
        remote_agents=_async_remote_agents,
        plugin=SimpleNamespace(_presence=None, _identity=None),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    primary = SigningKey.generate().verify_key.encode().hex()
    commands.client.state.inviter = primary
    commands.client._store.save()

    assert (await commands.connect_snapshot()).config_from == ""  # nothing has arrived

    write_managed_config(ManagedConfig(primary_key=primary, primary_name="laptop-kollab"))
    snapshot = await commands.connect_snapshot()
    assert snapshot.config_from == "laptop-kollab"
    assert ConnectSnapshot.from_wire(snapshot.to_wire()).config_from == "laptop-kollab"

    write_managed_config(ManagedConfig(primary_key="f" * 64, primary_name="someone-else"))
    assert (await commands.connect_snapshot()).config_from == ""  # not this device's primary


@pytest.mark.asyncio
async def test_status_lists_offline_devices_for_approved_keys_with_no_online_agents(tmp_path):
    offline_key = "b" * 64
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=lambda: [],
        plugin=SimpleNamespace(_presence=None, _identity=None),
        _enrollment_issuer=None,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={offline_key: "laptop-kollab"})
        ),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    commands.client.state.approvals = [offline_key]

    status = await commands.format_status()

    assert "  laptop-kollab (offline)" in status


@pytest.mark.asyncio
async def test_status_offline_device_without_a_recorded_name_shows_a_hash_label_not_the_key(tmp_path):
    offline_key = "c" * 64
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=lambda: [],
        plugin=SimpleNamespace(_presence=None, _identity=None),
        _enrollment_issuer=None,
        _state=lambda: SimpleNamespace(state=SimpleNamespace(peer_devices={})),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    commands.client.state.approvals = [offline_key]

    status = await commands.format_status()

    assert f"  {key_label(offline_key)} (offline)" in status
    assert offline_key[:8] not in status


@pytest.mark.asyncio
async def test_status_shows_trust_suffix_when_peer_override_differs_from_network(tmp_path):
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=lambda: REMOTE_ROWS,
        plugin=SimpleNamespace(
            _presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")
        ),
        _enrollment_issuer=None,
        effective_trust=lambda key: "agents" if key == "a" * 64 else "open",
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    status = await commands.format_status()

    assert "infra@home-server - idle  trust agents" in status


# --------------------------------------------------------------------- #
# /connect name, /connect trust
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_connect_name_validates_and_calls_set_device_name(tmp_path):
    calls = []
    bridge = SimpleNamespace(set_device_name=lambda name: calls.append(name) or name)
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    result = await commands._run("name laptop-kollab", source_agent=None)

    assert result == "this device is now laptop-kollab"
    assert calls == ["laptop-kollab"]


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

    assert result == (
        "trust for kollabor.ai is now open\n"
        "every device on this network can now open your agents and approve their tools"
    )
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

    result = await commands._run("allow home-server ops", source_agent="me")

    assert result == "ok"
    assert seen["head"] == "allow"
    assert seen["rest"] == "a" * 64 + " ops"


@pytest.mark.asyncio
async def test_connect_revoke_rejects_an_unknown_device(tmp_path):
    commands = _relay_commands(tmp_path, agent_bridge=SimpleNamespace(remote_agents=lambda: []))

    result = await commands._run("revoke some-unknown-device", source_agent=None)

    assert result == (
        "connect: no known device matches 'some-unknown-device'; "
        "/connect status lists them"
    )


@pytest.mark.asyncio
async def test_resolve_peer_key_finds_an_offline_recorded_device_name(tmp_path):
    offline_key = "a" * 64
    bridge = SimpleNamespace(
        remote_agents=lambda: [],
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={offline_key: "laptop-kollab"})
        ),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    assert await commands._resolve_peer_key("laptop-kollab") == offline_key


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
        to="infra@home-server",
        content="check the tunnel",
        scope=MessageScope.DIRECT.value,
    )

    rejections = await hub._route_message(msg)

    assert rejections == []
    assert sent["address"] == "relay:resolved:infra@home-server"
    assert sent["content"] == "check the tunnel"
    # The message's own thread rides along so an answer can name it.
    assert sent["kwargs"] == {"kind": "message", "thread_id": msg.thread_id, "reply_to": ""}
    assert msg.metadata["network"] == {"to": "relay:resolved:infra@home-server"}


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
        ("relay:resolved:infra@home-server", "shipped phase B", {"kind": "message"})
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


@pytest.mark.asyncio
async def test_format_status_lists_remote_agents_with_device_online_state():
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

    await hub._refresh_remote_agent_rows()
    status = hub._format_status()

    assert "infra@home-server - idle (device online)" in status
    assert "ops@home-server - working (device offline)" in status


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

    # remote_agents() rows are always online: True in production (see
    # RelayAgentBridge.remote_agents); a fully offline device instead has no
    # row at all and shows up only through its approved key + bound name.
    online_rows = [{**row, "online": True} for row in REMOTE_ROWS]
    offline_peer_key = "f" * 64
    hub._relay_agent = SimpleNamespace(
        remote_agents=lambda: online_rows,
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        harness_context=_async_empty_list,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={offline_peer_key: "laptop-kollab"})
        ),
        commands=SimpleNamespace(
            client=SimpleNamespace(
                state=SimpleNamespace(approvals=[offline_peer_key]),
                status=lambda: {"state": "online"},
                peers=lambda: [],
            )
        ),
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
    assert "this device: laptop-kollab" in content
    assert "infra@home-server - idle" in content
    assert "ops@home-server - working: rotating logs" in content
    assert "offline devices: laptop-kollab" in content
    assert "remote agents use the same tag with their full name:" in content
    assert '<hub_msg to="infra@home-server">your message</hub_msg>' in content


async def _async_empty_list(*_args, **_kwargs):
    return []


# --------------------------------------------------------------------- #
# The real bridge's remote_agents()/resolve_handle() are coroutines. The
# first build called them without await and every agent@device message
# failed live; these tests use async fakes so that can't come back.
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_status_awaits_an_async_bridge(tmp_path):
    async def remote_agents():
        return REMOTE_ROWS

    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "laptop-kollab",
        remote_agents=remote_agents,
        plugin=SimpleNamespace(_presence=None, _identity=SimpleNamespace(identity="koordinator", agent_id="k1")),
        _enrollment_issuer=None,
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    status = await commands.format_status()

    assert "  koordinator (this device)" in status
    assert "  infra@home-server - idle" in status
    assert status.index("koordinator (this device)") < status.index("infra@home-server")
    assert "offline devices" not in status or "home-server" in status


@pytest.mark.asyncio
async def test_route_message_awaits_resolve_handle():
    hub = HubPlugin.__new__(HubPlugin)
    sent = []

    async def resolve_handle(handle):
        assert handle == "infra@home-server"
        return REMOTE_ROWS[0]["address"]

    async def send(address, content, kind="message", thread_id="", reply_to=""):
        sent.append((address, content, kind))

    hub._relay_agent = SimpleNamespace(resolve_handle=resolve_handle, send=send, trust_level=lambda: "open")
    hub._trace_delivery = lambda *a, **k: None
    message = HubMessage(from_identity="lapis", to="infra@home-server", content="check the tunnel")

    rejections = await hub._route_message(message)

    assert rejections == []
    assert sent == [(REMOTE_ROWS[0]["address"], "check the tunnel", "message")]
    assert message.metadata["network"] == {"to": REMOTE_ROWS[0]["address"]}


@pytest.mark.asyncio
async def test_refresh_remote_agent_rows_awaits_and_snapshots():
    hub = HubPlugin.__new__(HubPlugin)

    async def remote_agents():
        return REMOTE_ROWS

    hub._relay_agent = SimpleNamespace(remote_agents=remote_agents)

    assert hub._remote_agent_rows() == []
    rows = await hub._refresh_remote_agent_rows()
    assert [r["handle"] for r in rows] == ["infra@home-server", "ops@home-server"]
    assert hub._remote_agent_rows() == rows


def _outgoing_box(target, rows):
    """The text the outgoing box draws for a send to `target`."""
    shown = []
    renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(
            display_message_sequence=lambda items: shown.append(items[0][1])
        )
    )
    bus = MagicMock()
    bus.get_service.return_value = renderer
    plugin = HubPlugin(event_bus=bus)
    plugin._identity = SimpleNamespace(identity="sapphire")
    plugin._remote_rows_snapshot = rows
    plugin._display_outgoing_message(target, "check the tunnel")
    return shown[0]


def test_an_outgoing_box_never_draws_a_relay_address():
    address = "relay:" + "a" * 64 + ":" + "9" * 32 + ":infra-1"
    row = {"name": "infra", "device": "home-server", "address": address}

    assert _outgoing_box(address, [row]).startswith(
        "sapphire -> infra@home-server\n"
    )
    assert _outgoing_box(address, []).startswith("sapphire -> a remote agent\n")
    assert _outgoing_box("lapis", [row]).startswith("sapphire -> lapis\n")
