"""Contact requests cross only the trusted local Hub RPC boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.hub.contact_requests import (
    ContactDecision,
    PendingContactRequest,
    PrivateMessage,
)
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_owner import RELAY_METHODS
from plugins.hub.relay_state import RelayError


class _Directory:
    def agents(self, _workspace):
        return [SimpleNamespace(agent_id="local-agent")]


class _Commands:
    def __init__(self):
        self.submissions = []
        self.decisions = []

    async def submit_contact_request(self, domain, recipient_key, introduction):
        self.submissions.append((domain, recipient_key, introduction))
        return "a" * 32

    async def pending_contact_requests(self, _domain):
        return [
            PendingContactRequest(
                "b" * 32,
                "c" * 64,
                1_800_000_000,
                PrivateMessage("private introduction"),
            )
        ]

    async def decide_contact_request(self, _domain, receipt_id, decision):
        self.decisions.append((receipt_id, decision))
        return ContactDecision(
            receipt_id, "accepted" if decision == "accept" else "rejected"
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_contact_methods_are_allowlisted_local_and_receipt_scoped(tmp_path):
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
    bridge.commands = _Commands()

    assert {
        "relay.contact_submit",
        "relay.contact_pending",
        "relay.contact_decide",
    } <= RELAY_METHODS

    submitted = await bridge._rpc_contact_submit(
        {
            "agent_id": "local-agent",
            "domain": "relay.example",
            "recipient_key": "d" * 64,
            "introduction": "private introduction",
        }
    )
    assert submitted == {"status": "queued", "receipt_id": "a" * 32}
    assert bridge.commands.submissions == [
        ("relay.example", "d" * 64, "private introduction")
    ]

    pending = await bridge._rpc_contact_pending(
        {"agent_id": "local-agent", "domain": "relay.example"}
    )
    assert pending == {
        "requests": [
            {
                "receipt_id": "b" * 32,
                "sender_key": "c" * 64,
                "expires_at": 1_800_000_000,
                "introduction": "private introduction",
            }
        ]
    }

    decided = await bridge._rpc_contact_decide(
        {
            "agent_id": "local-agent",
            "domain": "relay.example",
            "request_id": "b" * 32,
            "decision": "accept",
        }
    )
    assert decided == {"status": "accepted", "receipt_id": "b" * 32}
    assert bridge.commands.decisions == [("b" * 32, "accept")]
    plugin._on_message_received.assert_not_awaited()
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
                    "recipient_key": "d" * 64,
                    "introduction": "private introduction",
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
                "recipient_key": "d" * 64,
                "introduction": "\ud800",
            }
        )
    with pytest.raises(RelayError, match="invalid local contact decision"):
        await bridge._rpc_contact_decide(
            {
                "agent_id": "local-agent",
                "domain": "relay.example",
                "request_id": "b" * 32,
                "decision": [],
            }
        )
    assert bridge.commands.submissions == []
    assert bridge.commands.decisions == []
    await bridge.close()
