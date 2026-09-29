"""Contact requests cross only the trusted local Hub RPC boundary."""

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
