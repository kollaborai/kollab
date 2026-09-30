"""Contact requests cross only the trusted local Hub RPC boundary."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from plugins.hub.contact_requests import (
    ContactDecision,
    PendingContactRequest,
    PrivateMessage,
)
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_owner import RELAY_METHODS
from plugins.hub.relay_state import RelayError, RelayStateStore

_SENDER = SigningKey.generate().verify_key.encode().hex()


class _Directory:
    def agents(self, _workspace):
        return [SimpleNamespace(agent_id="local-agent")]


class _Commands:
    def __init__(self, client=None):
        self.submissions = []
        self.decisions = []
        self.resolved_routes = []
        self.client = client
        self.decision_error = None

    async def resolve_contact_route(self, domain, route):
        self.resolved_routes.append((domain, route))
        return "d" * 64

    async def submit_contact_request(self, domain, recipient_key, introduction, device_name=""):
        self.submissions.append((domain, recipient_key, introduction, device_name))
        return "a" * 32

    async def pending_contact_requests(self, _domain):
        return [
            PendingContactRequest(
                "b" * 32,
                _SENDER,
                1_800_000_000,
                PrivateMessage("private introduction"),
                "ana-laptop",
            )
        ]

    async def decide_contact_request(self, _domain, receipt_id, decision):
        if self.decision_error is not None:
            raise self.decision_error
        self.decisions.append((receipt_id, decision))
        return ContactDecision(
            receipt_id, "accepted" if decision == "accept" else "rejected"
        )

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
    bridge.commands = _Commands(RelayClient(tmp_path, state_dir=bridge.owner.state_dir))
    return plugin, bridge


def _decision(decision, device_name="ana-laptop"):
    return {
        "agent_id": "local-agent",
        "domain": "relay.example",
        "request_id": "b" * 32,
        "decision": decision,
        "sender_key": _SENDER,
        "device_name": device_name,
    }


def _disk(tmp_path):
    return RelayStateStore(tmp_path, tmp_path / "private-state").state


@pytest.mark.asyncio
async def test_contact_methods_are_allowlisted_local_and_receipt_scoped(tmp_path):
    plugin, bridge = _bridge(tmp_path)

    assert {
        "relay.contact_submit",
        "relay.contact_pending",
        "relay.contact_decide",
    } <= RELAY_METHODS

    submitted = await bridge._rpc_contact_submit(
        {
            "agent_id": "local-agent",
            "domain": "relay.example",
            "route": "e" * 16,
            "introduction": "private introduction",
            "device_name": "mac-kollab",
        }
    )
    assert submitted == {"status": "queued", "receipt_id": "a" * 32}
    assert bridge.commands.resolved_routes == [("relay.example", "e" * 16)]
    assert bridge.commands.submissions == [
        ("relay.example", "d" * 64, "private introduction", "mac-kollab")
    ]

    pending = await bridge._rpc_contact_pending(
        {"agent_id": "local-agent", "domain": "relay.example"}
    )
    assert pending == {
        "requests": [
            {
                "receipt_id": "b" * 32,
                "sender_key": _SENDER,
                "expires_at": 1_800_000_000,
                "introduction": "private introduction",
                "device_name": "ana-laptop",
            }
        ]
    }

    decided = await bridge._rpc_contact_decide(_decision("accept"))
    assert decided == {"status": "accepted", "receipt_id": "b" * 32}
    assert bridge.commands.decisions == [("b" * 32, "accept")]
    # accepted = named, approved, trust agents; a knock never gets open trust
    disk = _disk(tmp_path)
    assert disk.peer_devices == {_SENDER: "ana-laptop"}
    assert disk.approvals == [_SENDER]
    assert disk.peer_trust == {_SENDER: "agents"}
    plugin._on_message_received.assert_not_awaited()
    await bridge.close()


@pytest.mark.asyncio
async def test_contact_decide_reject_does_not_approve_or_name_the_sender(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    decided = await bridge._rpc_contact_decide(_decision("reject"))

    assert decided == {"status": "rejected", "receipt_id": "b" * 32}
    disk = _disk(tmp_path)
    assert disk.approvals == [] and disk.peer_devices == {} and disk.peer_trust == {}
    await bridge.close()


@pytest.mark.asyncio
async def test_accepting_a_second_knock_keeps_the_first_peer_named(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()

    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))
    second = _decision("accept", "bob-desktop") | {"sender_key": other}
    await bridge._rpc_contact_decide(second)

    disk = _disk(tmp_path)
    assert disk.peer_devices == {_SENDER: "ana-laptop", other: "bob-desktop"}
    assert disk.peer_trust == {_SENDER: "agents", other: "agents"}
    await bridge.close()


@pytest.mark.asyncio
async def test_accepting_a_knock_with_a_taken_name_fails_and_leaves_it_pending(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()
    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))

    clash = _decision("accept", "ana-laptop") | {"sender_key": other}
    decided = await bridge._rpc_contact_decide(clash)

    assert decided == {"error": "name_taken"}
    assert bridge.commands.decisions == [("b" * 32, "accept")]  # only the first
    disk = _disk(tmp_path)
    assert disk.approvals == [_SENDER] and disk.peer_devices == {_SENDER: "ana-laptop"}
    await bridge.close()


@pytest.mark.asyncio
async def test_a_failed_relay_decision_undoes_the_accept(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge.commands.decision_error = RelayError("relay is down")

    decided = await bridge._rpc_contact_decide(_decision("accept"))

    assert decided == {"error": "transport"}
    disk = _disk(tmp_path)
    assert disk.approvals == [] and disk.peer_devices == {} and disk.peer_trust == {}
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
async def test_a_failed_relay_decision_keeps_an_approval_that_existed_before(tmp_path):
    """Only what this accept added is undone: a stranger accepted earlier keeps its approval."""
    _plugin, bridge = _bridge(tmp_path)
    bridge.commands.client.approve(_SENDER)
    bridge.set_peer_link(_SENDER)  # a member that joined by code is refused instead
    bridge.commands.decision_error = RelayError("relay is down")

    decided = await bridge._rpc_contact_decide(_decision("accept"))

    assert decided == {"error": "transport"}
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
    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))
    before = _state_of(tmp_path)

    decided = await bridge._rpc_contact_decide(_decision("accept", "ana-desktop"))

    assert decided == {"error": "already_named"}
    assert bridge.commands.decisions == [("b" * 32, "accept")]  # the relay was not asked again
    assert _state_of(tmp_path) == before
    assert bridge._peer_name(_SENDER) == "ana-laptop"
    with pytest.raises(RelayError, match="already on your network as ana-laptop"):
        bridge.bind_peer_device(_SENDER, "ana-desktop")
    await bridge.close()


@pytest.mark.asyncio
async def test_repeating_the_same_name_is_idempotent(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))

    again = await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))

    assert again == {"status": "accepted", "receipt_id": "b" * 32}
    assert _disk(tmp_path).peer_devices == {_SENDER: "ana-laptop"}
    await bridge.close()


@pytest.mark.asyncio
async def test_a_failed_accept_restores_approvals_names_and_trust_exactly(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    other = SigningKey.generate().verify_key.encode().hex()
    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop") | {"sender_key": other})
    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))  # taken: refused
    await bridge._rpc_contact_decide(_decision("accept", "bob-desktop"))
    before = _state_of(tmp_path)
    assert before[0] == [other, _SENDER]

    bridge.commands.decision_error = RelayError("relay is down")
    decided = await bridge._rpc_contact_decide(_decision("accept", "bob-desktop"))

    assert decided == {"error": "transport"}
    assert _state_of(tmp_path) == before  # approvals, peer_devices and peer_trust, untouched
    await bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", ["bind", "trust", "approve"])
async def test_every_failing_step_of_an_accept_leaves_a_known_peer_as_it_was(tmp_path, fail_at):
    _plugin, bridge = _bridge(tmp_path)
    await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))
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
    bridge.set_device_name("mac-kollab")
    peer = SigningKey.generate().verify_key.encode().hex()
    bridge._cache[("session", peer, "peer-session")] = (
        time.monotonic(),
        [{"name": "ops", "device": "alzan-prod-home", "handle": "ops@alzan-prod-home"}],
    )

    own = await bridge._rpc_contact_decide(_decision("accept", "mac-kollab"))
    roster = await bridge._rpc_contact_decide(_decision("accept", "alzan-prod-home"))

    assert own == {"error": "name_taken"} and roster == {"error": "name_taken"}
    assert _state_of(tmp_path) == ([], {}, {})
    assert bridge.commands.decisions == []  # the relay never recorded either accept
    with pytest.raises(RelayError, match="already on this network"):
        bridge.set_device_name("alzan-prod-home")
    await bridge.close()


@pytest.mark.asyncio
async def test_a_peers_own_roster_rows_do_not_block_its_own_name(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge._cache[("session", _SENDER, "peer-session")] = (
        time.monotonic(),
        [{"name": "ops", "device": "ana-laptop", "handle": "ops@ana-laptop"}],
    )

    decided = await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))

    assert decided == {"status": "accepted", "receipt_id": "b" * 32}
    await bridge.close()


@pytest.mark.asyncio
async def test_a_full_approval_table_is_reported_as_capacity_and_leaves_no_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.hub.relay_client.MAX_APPROVALS", 0)
    _plugin, bridge = _bridge(tmp_path)

    with pytest.raises(RelayError) as refused:
        bridge.commands.client.approve(_SENDER)
    decided = await bridge._rpc_contact_decide(_decision("accept"))

    assert refused.value.code == "capacity"
    assert decided == {"error": "capacity"}
    assert _state_of(tmp_path) == ([], {}, {})
    await bridge.close()


@pytest.mark.asyncio
async def test_accepting_a_knock_allows_no_agent_until_the_human_says_so(tmp_path):
    _plugin, bridge = _bridge(tmp_path)

    await bridge._rpc_contact_decide(_decision("accept"))

    room = bridge.commands.client.state.room
    assert bridge.store.grants(room) == []  # the allow list stays empty
    assert bridge.effective_trust(_SENDER) == "agents"
    await bridge.close()


@pytest.mark.asyncio
async def test_remote_model_turn_cannot_submit_review_or_decide_contact(tmp_path):
    plugin = SimpleNamespace(
        _identity=SimpleNamespace(agent_id="local-agent", identity="operator")
    )
    bridge = RelayAgentBridge(
        plugin,
        tmp_path,
        state_dir=tmp_path / "private-state",
        directory=_Directory(),
    )
    bridge.commands = _Commands()
    token = bridge._turn.set("remote-model-task")
    try:
        calls = (
            bridge._rpc_contact_submit(
                {
                    "agent_id": "local-agent",
                    "domain": "relay.example",
                    "route": "e" * 16,
                    "introduction": "private introduction",
                    "device_name": "mac-kollab",
                }
            ),
            bridge._rpc_contact_pending(
                {"agent_id": "local-agent", "domain": "relay.example"}
            ),
            bridge._rpc_contact_decide(
                {
                    "agent_id": "local-agent",
                    "domain": "relay.example",
                    "request_id": "b" * 32,
                    "decision": "accept",
                    "sender_key": _SENDER,
                    "device_name": "ana-laptop",
                }
            ),
        )
        for call in calls:
            with pytest.raises(RelayError, match="remote model turns"):
                await call
    finally:
        bridge._turn.reset(token)
    assert bridge.commands.submissions == []
    assert bridge.commands.decisions == []
    await bridge.close()


@pytest.mark.asyncio
async def test_contact_rpc_rejects_surrogate_text_and_untyped_decision(tmp_path):
    plugin = SimpleNamespace(
        _identity=SimpleNamespace(agent_id="local-agent", identity="operator")
    )
    bridge = RelayAgentBridge(
        plugin,
        tmp_path,
        state_dir=tmp_path / "private-state",
        directory=_Directory(),
    )
    bridge.commands = _Commands()

    with pytest.raises(RelayError, match="invalid local contact request"):
        await bridge._rpc_contact_submit(
            {
                "agent_id": "local-agent",
                "domain": "relay.example",
                "route": "e" * 16,
                "introduction": "\ud800",
                "device_name": "mac-kollab",
            }
        )
    with pytest.raises(RelayError, match="invalid local contact decision"):
        await bridge._rpc_contact_decide(
            {
                "agent_id": "local-agent",
                "domain": "relay.example",
                "request_id": "b" * 32,
                "decision": [],
                "sender_key": _SENDER,
                "device_name": "ana-laptop",
            }
        )
    assert bridge.commands.submissions == []
    assert bridge.commands.decisions == []
    await bridge.close()


@pytest.mark.asyncio
async def test_contact_rpc_rejects_a_route_that_is_not_16_hex(tmp_path):
    plugin = SimpleNamespace(
        _identity=SimpleNamespace(agent_id="local-agent", identity="operator")
    )
    bridge = RelayAgentBridge(
        plugin,
        tmp_path,
        state_dir=tmp_path / "private-state",
        directory=_Directory(),
    )
    bridge.commands = _Commands()

    with pytest.raises(RelayError, match="invalid local contact request"):
        await bridge._rpc_contact_submit(
            {
                "agent_id": "local-agent",
                "domain": "relay.example",
                "route": "not-hex",
                "introduction": "hello",
                "device_name": "mac-kollab",
            }
        )
    assert bridge.commands.resolved_routes == []
    await bridge.close()


@pytest.mark.asyncio
async def test_a_knock_from_a_device_already_on_the_network_by_code_is_refused(tmp_path):
    _plugin, bridge = _bridge(tmp_path)
    bridge.bind_peer_device(_SENDER, "ana-laptop")  # joined with a code: named,
    bridge.commands.client.approve(_SENDER)  # approved, never linked
    before = _state_of(tmp_path)

    decided = await bridge._rpc_contact_decide(_decision("accept", "ana-laptop"))

    assert decided == {"error": "already_named"}
    assert bridge.commands.decisions == []  # the relay was not asked
    assert _state_of(tmp_path) == before and _disk(tmp_path).links == []
    with pytest.raises(RelayError, match="already on your network as ana-laptop"):
        bridge._bind_knock_peer(_SENDER, "ana-laptop")
    await bridge.close()
