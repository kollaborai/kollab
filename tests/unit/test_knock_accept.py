"""Accepting a knock: what it binds, and that a failure anywhere binds nothing.

The knock service (plugins/hub/knocks.py) runs on a real client and a real
bridge; only the answer's trip through the directory is stood in for, so a
test can say whether the knocker heard it (`answered`) or not.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from plugins.hub.knocks import KnockService, Ringing
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_owner import RELAY_METHODS
from plugins.hub.relay_state import RelayError, RelayStateStore

_SENDER = SigningKey.generate().verify_key.encode().hex()


class _Directory:
    def agents(self, _workspace):
        return [SimpleNamespace(agent_id="local-agent", name="operator")]


class _Commands:
    """The owner's commands: a real client and knock service, no directory."""

    def __init__(self, bridge, client):
        self.client = client
        self.answers = []
        self.answer_result = "answered"
        self.knocks = KnockService(
            client,
            client.state_dir / "knocks.json",
            device_name=lambda: "laptop-kollab",
            bind_incoming=bridge._bind_knock_peer,
            bind_outgoing=bridge._bind_knocked_peer,
            links_changed=AsyncMock(),
        )

        async def send_answer(entry, answer):
            self.answers.append((entry.id, answer))
            return self.answer_result

        self.knocks._send_answer = send_answer

    def screen_polled(self) -> None:
        pass

    async def close(self):
        return None


def _bridge(tmp_path):
    plugin = SimpleNamespace(
        _identity=SimpleNamespace(agent_id="local-agent", identity="operator"),
        _on_message_received=AsyncMock(),
    )
    bridge = RelayAgentBridge(
        plugin,
        tmp_path,
        state_dir=tmp_path / "private-state",
        directory=_Directory(),
    )
    # A real client: it keeps its own long-lived copy of the state file, which
    # is what makes the binding survive (or not) an approve.
    bridge.commands = _Commands(bridge, RelayClient(tmp_path, state_dir=bridge.owner.state_dir))
    return plugin, bridge


async def _accept(bridge, device_name="ana-laptop", key=_SENDER, knock_id="b" * 32) -> str:
    """Ring a knock from `key` and accept it; the line the human reads."""
    bridge.commands.knocks.ringing[knock_id] = Ringing(
        knock_id, key, device_name, "private introduction", {}, time.time() + 300
    )
    result = await bridge._rpc_knocks(
        {"agent_id": "local-agent", "action": "accept", "args": {"id": knock_id}}
    )
    return result["text"]


TAKEN = (
    "connect: a device named {} is already on this network; "
    "it must pick another name (/connect name) and knock again"
)


def _disk(tmp_path):
    return RelayStateStore(tmp_path, tmp_path / "private-state").state


@pytest.mark.asyncio
async def test_knocks_cross_one_allowlisted_local_method(tmp_path):
    plugin, bridge = _bridge(tmp_path)

    assert "relay.knocks" in RELAY_METHODS
    assert not {m for m in RELAY_METHODS if m.startswith("relay.contact")}

    accepted = await _accept(bridge)
    assert accepted.startswith("accepted ana-laptop. /connect allow ana-laptop <agent>")
    assert bridge.commands.answers == [("b" * 32, "accept")]
    # accepted = named, approved, trust agents; a knock never gets open trust
    disk = _disk(tmp_path)
    assert disk.peer_devices == {_SENDER: "ana-laptop"}
    assert disk.approvals == [_SENDER]
    assert disk.peer_trust == {_SENDER: "agents"}
    plugin._on_message_received.assert_not_awaited()
    await bridge.close()


@pytest.mark.asyncio
async def test_rejecting_does_not_approve_or_name_the_sender(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge.commands.knocks.ringing["b" * 32] = Ringing(
        "b" * 32, _SENDER, "ana-laptop", "hi", {}, time.time() + 300
    )

    result = await bridge._rpc_knocks(
        {"agent_id": "local-agent", "action": "reject", "args": {"id": "b" * 32}}
    )

    assert result == {"text": "rejected ana-laptop"}
    assert bridge.commands.answers == []  # a reject is silence: it rings out at the knocker
    disk = _disk(tmp_path)
    assert disk.approvals == [] and disk.peer_devices == {} and disk.peer_trust == {}
    await bridge.close()


@pytest.mark.asyncio
async def test_accepting_a_second_knock_keeps_the_first_peer_named(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()

    await _accept(bridge, "ana-laptop")
    await _accept(bridge, "bob-desktop", other, "c" * 32)

    disk = _disk(tmp_path)
    assert disk.peer_devices == {_SENDER: "ana-laptop", other: "bob-desktop"}
    assert disk.peer_trust == {_SENDER: "agents", other: "agents"}
    await bridge.close()


@pytest.mark.asyncio
async def test_accepting_a_knock_with_a_taken_name_fails_and_keeps_it_ringing(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()
    await _accept(bridge, "ana-laptop")

    line = await _accept(bridge, "ana-laptop", other, "c" * 32)

    assert line == TAKEN.format("ana-laptop")
    assert bridge.commands.answers == [("b" * 32, "accept")]  # only the first
    assert "c" * 32 in bridge.commands.knocks.ringing
    disk = _disk(tmp_path)
    assert disk.approvals == [_SENDER] and disk.peer_devices == {_SENDER: "ana-laptop"}
    await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heard, line, still_ringing",
    [
        ("unavailable", "connect: ana-laptop hung up before the accept reached it", False),
        ("busy", "connect: the accept to ana-laptop could not be sent; try again", True),
    ],
)
async def test_an_accept_the_knocker_never_heard_is_undone(tmp_path, heard, line, still_ringing):
    _plugin, bridge = _bridge(tmp_path)
    bridge.commands.answer_result = heard

    assert await _accept(bridge) == line

    disk = _disk(tmp_path)
    assert disk.approvals == [] and disk.peer_devices == {} and disk.peer_trust == {}
    assert ("b" * 32 in bridge.commands.knocks.ringing) is still_ringing
    await bridge.close()


@pytest.mark.asyncio
async def test_a_failed_approval_rolls_back_the_binding_and_raises(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    def refuse(_key):
        raise RelayError("local peer approval capacity reached")

    bridge.commands.client.approve = refuse

    with pytest.raises(RelayError, match="capacity"):
        bridge._bind_knock_peer(_SENDER, "ana-laptop")

    assert _disk(tmp_path).peer_devices == {}
    await bridge.close()


@pytest.mark.asyncio
async def test_an_unheard_accept_keeps_an_approval_that_existed_before(tmp_path):
    """Only what this accept added is undone: a stranger accepted earlier keeps its approval."""
    _plugin, bridge = _bridge(tmp_path)
    bridge.commands.client.approve(_SENDER)
    bridge.set_peer_link(_SENDER)  # a member that joined by code is refused instead
    bridge.commands.answer_result = "unavailable"

    assert (await _accept(bridge)).endswith("hung up before the accept reached it")
    disk = _disk(tmp_path)
    assert disk.approvals == [_SENDER]
    assert disk.peer_devices == {} and disk.peer_trust == {}
    await bridge.close()


@pytest.mark.asyncio
async def test_trust_is_set_before_the_approval_so_a_failure_never_leaves_an_open_peer(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    def refuse(_key, _level):
        raise RelayError("state is read-only")

    bridge.set_peer_trust = refuse

    with pytest.raises(RelayError, match="read-only"):
        bridge._bind_knock_peer(_SENDER, "ana-laptop")

    disk = _disk(tmp_path)
    assert disk.approvals == []  # never approved on the network default (open)
    assert disk.peer_devices == {}
    await bridge.close()


@pytest.mark.asyncio
async def test_a_failed_approval_rolls_back_even_a_non_relay_error(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    def disk_full(_key):
        raise OSError("disk full")

    bridge.commands.client.approve = disk_full

    with pytest.raises(OSError, match="disk full"):
        bridge._bind_knock_peer(_SENDER, "ana-laptop")

    disk = _disk(tmp_path)
    assert disk.peer_devices == {} and disk.peer_trust == {} and disk.approvals == []
    await bridge.close()


def _state_of(tmp_path):
    disk = _disk(tmp_path)
    return list(disk.approvals), dict(disk.peer_devices), dict(disk.peer_trust)


@pytest.mark.asyncio
async def test_a_second_knock_from_a_bound_key_under_a_new_name_is_refused_not_renamed(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    await _accept(bridge, "ana-laptop")
    before = _state_of(tmp_path)

    line = await _accept(bridge, "ana-desktop", knock_id="c" * 32)

    assert line == "connect: this device is already on your network as ana-laptop"
    assert bridge.commands.answers == [("b" * 32, "accept")]  # nothing was answered again
    assert _state_of(tmp_path) == before
    assert bridge._peer_name(_SENDER) == "ana-laptop"
    with pytest.raises(RelayError, match="already on your network as ana-laptop"):
        bridge.bind_peer_device(_SENDER, "ana-desktop")
    await bridge.close()


@pytest.mark.asyncio
async def test_repeating_the_same_name_is_idempotent(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    await _accept(bridge, "ana-laptop")

    again = await _accept(bridge, "ana-laptop", knock_id="c" * 32)

    assert again.startswith("accepted ana-laptop")
    assert _disk(tmp_path).peer_devices == {_SENDER: "ana-laptop"}
    await bridge.close()


@pytest.mark.asyncio
async def test_a_failed_accept_restores_approvals_names_and_trust_exactly(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()
    await _accept(bridge, "ana-laptop", other, "c" * 32)
    await _accept(bridge, "ana-laptop", knock_id="d" * 32)  # taken: refused
    await _accept(bridge, "bob-desktop", knock_id="e" * 32)
    before = _state_of(tmp_path)
    assert before[0] == [other, _SENDER]

    bridge.commands.answer_result = "unavailable"
    line = await _accept(bridge, "bob-desktop", knock_id="f" * 32)

    assert line.endswith("hung up before the accept reached it")
    assert _state_of(tmp_path) == before  # approvals, peer_devices and peer_trust, untouched
    await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["bind", "trust", "approve"])
async def test_every_failing_step_of_an_accept_leaves_a_known_peer_as_it_was(tmp_path, fail_at):
    _plugin, bridge = _bridge(tmp_path)
    await _accept(bridge, "ana-laptop")
    before = _state_of(tmp_path)

    def boom(*_args):
        raise OSError("disk full")

    if fail_at == "bind":
        bridge.bind_peer_device = boom
    elif fail_at == "trust":
        bridge.set_peer_trust = boom
    else:
        bridge.commands.client.approve = boom

    with pytest.raises(OSError):
        bridge._bind_knock_peer(_SENDER, "ana-laptop")

    assert _state_of(tmp_path) == before
    await bridge.close()


@pytest.mark.asyncio
async def test_a_knock_cannot_take_a_name_from_this_device_or_a_device_in_the_roster(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge.set_device_name("laptop-kollab")
    peer = SigningKey.generate().verify_key.encode().hex()
    bridge._cache[("session", peer, "peer-session")] = (
        time.monotonic(),
        [{"name": "ops", "device": "home-server", "handle": "ops@home-server"}],
    )

    own = await _accept(bridge, "laptop-kollab")
    roster = await _accept(bridge, "home-server", knock_id="c" * 32)

    assert own == TAKEN.format("laptop-kollab") and roster == TAKEN.format("home-server")
    assert _state_of(tmp_path) == ([], {}, {})
    assert bridge.commands.answers == []  # neither accept was answered
    with pytest.raises(RelayError, match="already on this network"):
        bridge.set_device_name("home-server")
    await bridge.close()


@pytest.mark.asyncio
async def test_a_peers_own_roster_rows_do_not_block_its_own_name(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge._cache[("session", _SENDER, "peer-session")] = (
        time.monotonic(),
        [{"name": "ops", "device": "ana-laptop", "handle": "ops@ana-laptop"}],
    )

    assert (await _accept(bridge, "ana-laptop")).startswith("accepted ana-laptop")
    await bridge.close()


@pytest.mark.asyncio
async def test_a_full_approval_table_is_reported_as_capacity_and_leaves_no_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.hub.relay_client.MAX_APPROVALS", 0)
    _plugin, bridge = _bridge(tmp_path)

    with pytest.raises(RelayError) as refused:
        bridge.commands.client.approve(_SENDER)
    line = await _accept(bridge)

    assert refused.value.code == "capacity"
    assert line == "connect: local peer approval capacity reached"
    assert _state_of(tmp_path) == ([], {}, {})
    await bridge.close()


@pytest.mark.asyncio
async def test_accepting_a_knock_allows_no_agent_until_the_human_says_so(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    await _accept(bridge)

    room = bridge.commands.client.state.room
    assert bridge.store.grants(room) == []  # the allow list stays empty
    assert bridge.effective_trust(_SENDER) == "agents"
    await bridge.close()


@pytest.mark.asyncio
async def test_a_remote_model_turn_cannot_use_knocks(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    token = bridge._turn.set("remote-model-task")
    try:
        for action, args in (
            ("knock", {"domain": "relay.example", "route": "e" * 16, "text": "hi"}),
            ("list", {}),
            ("accept", {"id": "b" * 32}),
        ):
            with pytest.raises(RelayError, match="remote model turns"):
                await bridge._rpc_knocks({"agent_id": "local-agent", "action": action, "args": args})
            with pytest.raises(RelayError, match="remote model turns"):
                await bridge.knocks(action, args, source_agent="local-agent")
    finally:
        bridge._turn.reset(token)
    assert bridge.commands.answers == []
    await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"agent_id": "local-agent", "action": "launch", "args": {}},
        {"agent_id": "local-agent", "action": "list", "args": []},
        {"agent_id": "local-agent", "action": "list"},
        {"agent_id": "local-agent", "action": "knock", "args": {"text": "x" * 9000}},
    ],
)
async def test_the_knock_rpc_refuses_what_it_does_not_know(tmp_path, params):
    _plugin, bridge = _bridge(tmp_path)

    with pytest.raises(RelayError, match="invalid local knock request"):
        await bridge._rpc_knocks(params)
    await bridge.close()


@pytest.mark.asyncio
async def test_a_malformed_knock_action_reads_as_usage_not_a_crash(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    surrogate = {"domain": "relay.example", "route": "e" * 16, "text": "\ud800"}
    usage = 'connect: use /connect knock <route> "text"'
    for action, args, line in (
        ("knock", surrogate, usage),
        ("knock", {"domain": "relay.example"}, usage),
        ("knock", {"domain": "relay.example", "route": "not-hex", "text": "hi"}, usage),
        ("expect", {"route": "nope"}, "connect: that is not a contact route"),
    ):
        result = await bridge._rpc_knocks({"agent_id": "local-agent", "action": action, "args": args})
        assert result == {"text": line}
    await bridge.close()


@pytest.mark.asyncio
async def test_a_knock_from_a_device_already_on_the_network_by_code_is_refused(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge.bind_peer_device(_SENDER, "ana-laptop")  # joined with a code: named,
    bridge.commands.client.approve(_SENDER)  # approved, never linked
    before = _state_of(tmp_path)

    line = await _accept(bridge, "ana-laptop")

    assert line == "connect: this device is already on your network as ana-laptop"
    assert bridge.commands.answers == []  # nothing was answered
    assert _state_of(tmp_path) == before and _disk(tmp_path).links == []
    with pytest.raises(RelayError, match="already on your network as ana-laptop"):
        bridge._bind_knock_peer(_SENDER, "ana-laptop")
    await bridge.close()
