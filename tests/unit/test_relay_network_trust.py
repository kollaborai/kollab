"""Trust-level behaviour for the agent network (docs/specs/agent-network-simple-flow.md).

Reuses the two-bridge fixture and helpers from test_relay_agent_bridge.py. That
fixture pins both sides to manual trust (it is the pre-existing Codex-model
suite); each test here flips trust explicitly with set_trust() to exercise
open/agents instead.
"""

import pytest

from plugins.hub.device_names import default_device_name, format_handle, key_label
from plugins.hub.relay_state import RelayError
from tests.unit.test_relay_agent_bridge import Directory, address, allow


def set_trust(bridge, level):
    store = bridge.commands.client._store
    store.state.trust = level
    store.save()


async def warm_directory(bridge):
    """remote_agents()/resolve_handle() read the cache only (cached=True);
    warm it once first, the way the periodic _refresh_directory() loop does
    in a running app (this fixture never starts that loop)."""
    await bridge._owner_call("relay.directory", {"peer": "", "cached": False})


@pytest.mark.asyncio
async def test_open_trust_send_needs_no_human_grant(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    set_trust(left, "open")
    set_trust(right, "open")

    sent = await left.send(address(right), "status check please")

    assert sent["state"] == "queued"
    assert sent["duplicate"] is False


@pytest.mark.asyncio
async def test_manual_trust_still_requires_the_human_grant(bridges):
    """Regression guard: the bridges() fixture pins manual trust by default."""
    members, _ = bridges
    (left, *_), (right, *_) = members

    with pytest.raises(RelayError, match="human communication grant"):
        await left.send(address(right), "ping")


@pytest.mark.asyncio
async def test_open_trust_inbound_is_a_plain_hub_turn_with_handle_identity(bridges):
    members, _ = bridges
    (left, _, _, _), (right, _right_hub, right_model, _) = members
    set_trust(left, "open")
    set_trust(right, "open")

    sent = await left.send(address(right), "check the tunnel")
    assert sent["state"] == "queued"

    await right._tick()

    # No task envelope: no active task, and the queued record reaches a
    # terminal "delivered" outcome instead of "running"/"completed".
    assert right.active is None
    assert right.store.task(sent["id"])["state"] == "delivered"
    assert len(right_model.contexts) == 1
    handle = format_handle("sapphire", default_device_name(left.workspace))
    assert handle in right_model.conversation_history[-1].content


@pytest.mark.asyncio
async def test_agents_trust_still_requires_the_receiving_grant(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    set_trust(left, "agents")
    set_trust(right, "agents")
    # No allow(): the receiver has not listed this agent as reachable.

    receipt = await left.send(address(right), "ping")

    assert receipt["state"] == "rejected"
    assert receipt["reason"] == "not_authorized"
    assert right.store.queued(right.identity.agent_id) == []


@pytest.mark.asyncio
async def test_agents_trust_delivers_once_allowed(bridges):
    members, _ = bridges
    (left, *_), (right, _, right_model, _) = members
    set_trust(left, "agents")
    set_trust(right, "agents")
    allow(left, right)

    sent = await left.send(address(right), "ping")
    assert sent["state"] == "queued"

    await right._tick()

    assert right.active is None
    assert right.store.task(sent["id"])["state"] == "delivered"
    assert len(right_model.contexts) == 1


@pytest.mark.asyncio
async def test_an_allowed_manual_receiver_answers_an_open_senders_request_on_its_thread(
    bridges,
):
    """Open sender, manual receiver (live Story 7 r4/r5): the request is an
    ordinary turn there, and its answer goes back on the request's thread."""
    members, _ = bridges
    (left, _, left_model, _), (right, _, right_model, _) = members
    set_trust(left, "open")
    set_trust(right, "manual")
    allow(left, right)  # the receiver's human: /connect allow <left> <agent>

    sent = await left.send(address(right), "what does uname -n print?")
    assert sent["state"] == "queued"
    await right._tick()

    # The open sender waits for no task result: no task envelope here either.
    assert right.active is None
    request = right.store.task(sent["id"])
    assert request["state"] == "delivered"
    assert len(right_model.contexts) == 1

    thread = request["payload"]["thread_id"]
    answer = await right.send(address(left), "it prints worker-1", thread_id=thread)
    assert answer["state"] not in {"failed", "rejected", "revoked"}
    await left._tick()
    assert "it prints worker-1" in left_model.conversation_history[-1].content

    # Only that thread goes without a human grant ...
    with pytest.raises(RelayError, match="human communication grant"):
        await right.send(address(left), "unrelated", thread_id="f" * 32)
    # ... and only while the receiving grant stands (/connect deny).
    right.store.revoke(right.commands.client.state.room, left.commands.client.public_key)
    with pytest.raises(RelayError, match="human communication grant"):
        await right.send(address(left), "one more", thread_id=thread)


@pytest.mark.asyncio
async def test_harness_context_drops_manual_only_lines_under_open_trust(bridges):
    members, _ = bridges
    (left, *_), _ = members
    set_trust(left, "open")

    lines = await left.harness_context()

    assert lines == [
        "Discovery and peer approval do not grant tool access. Receiving workspace permissions always apply.",
    ]


@pytest.mark.asyncio
async def test_harness_context_keeps_manual_lines_under_manual_trust(bridges):
    members, _ = bridges
    (left, *_), _ = members

    lines = await left.harness_context()

    assert any(
        "Network conversations require human direction" in line for line in lines
    )
    assert any("human communication grant is required" not in line for line in lines)
    assert any("do not poll with hub_status" in line for line in lines)


@pytest.mark.asyncio
async def test_resolve_handle_found_and_unknown(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    await warm_directory(left)

    resolved = await left.resolve_handle(
        f"sapphire@{default_device_name(right.workspace)}"
    )
    assert resolved == address(right)

    with pytest.raises(RelayError, match="unknown agent@device"):
        await left.resolve_handle("nobody@nowhere")

    with pytest.raises(RelayError, match="unknown agent@device"):
        await left.resolve_handle("not-a-handle")


@pytest.mark.asyncio
async def test_resolve_handle_ambiguous(bridges):
    members, _ = bridges
    (left, *_), _ = members

    async def two_rows_same_handle(cached=True):
        return [
            {
                "name": "sapphire",
                "device": "dup",
                "handle": "sapphire@dup",
                "state": "idle",
                "address": "relay:" + "a" * 64 + ":" + "b" * 32 + ":agent-one",
                "online": True,
                "is_coordinator": False,
                "workspace_id": "b" * 32,
            },
            {
                "name": "sapphire",
                "device": "dup",
                "handle": "sapphire@dup",
                "state": "idle",
                "address": "relay:" + "c" * 64 + ":" + "d" * 32 + ":agent-two",
                "online": True,
                "is_coordinator": False,
                "workspace_id": "d" * 32,
            },
        ]

    left.remote_agents = two_rows_same_handle

    with pytest.raises(RelayError, match="ambiguous agent@device"):
        await left.resolve_handle("sapphire@dup")


@pytest.mark.asyncio
async def test_effective_trust_per_peer_override_and_clear(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    set_trust(left, "open")
    set_trust(right, "open")
    left_key = left.commands.client.public_key

    assert right.effective_trust(left_key) == "open"

    right.set_peer_trust(left_key, "agents")
    assert right.effective_trust(left_key) == "agents"

    right.clear_peer_trust(left_key)
    assert right.effective_trust(left_key) == "open"


@pytest.mark.asyncio
async def test_effective_trust_manual_network_wins_over_peer_override(bridges):
    """bridges() pins manual trust by default; a peer override cannot loosen it."""
    members, _ = bridges
    (left, *_), (right, *_) = members
    left_key = left.commands.client.public_key
    right.set_peer_trust(left_key, "agents")

    assert right.effective_trust(left_key) == "manual"


@pytest.mark.asyncio
async def test_set_peer_trust_rejects_manual_and_open(bridges):
    """A peer can only be pinned to agents; a stranger never gets open, and
    manual is a whole-network setting (docs/specs/agent-network-simple-flow.md
    §3-4)."""
    members, _ = bridges
    (left, *_), (right, *_) = members
    left_key = left.commands.client.public_key

    with pytest.raises(RelayError, match="only be set to agents"):
        right.set_peer_trust(left_key, "manual")

    with pytest.raises(RelayError, match="only be set to agents"):
        right.set_peer_trust(left_key, "open")


@pytest.mark.asyncio
async def test_receive_requires_grant_for_a_peer_with_an_agents_override_under_open_network(
    bridges,
):
    members, _ = bridges
    (left, *_), (right, *_) = members
    set_trust(left, "open")
    set_trust(right, "open")
    right.set_peer_trust(left.commands.client.public_key, "agents")

    receipt = await left.send(address(right), "ping")

    assert receipt["state"] == "rejected"
    assert receipt["reason"] == "not_authorized"


@pytest.mark.asyncio
async def test_recorded_device_name_wins_over_self_report_in_delivery(bridges):
    members, _ = bridges
    (left, *_), (right, _, right_model, _) = members
    set_trust(left, "open")
    set_trust(right, "open")
    right.bind_peer_device(left.commands.client.public_key, "recorded-name")

    sent = await left.send(address(right), "check the tunnel")
    assert sent["state"] == "queued"

    await right._tick()

    handle = format_handle("sapphire", "recorded-name")
    assert handle in right_model.conversation_history[-1].content


@pytest.mark.asyncio
async def test_bind_peer_device_rejects_a_collision_and_is_idempotent(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    left_key = left.commands.client.public_key

    right.bind_peer_device(left_key, "laptop-kollab")
    # Re-accepting the same key with the same name is fine.
    right.bind_peer_device(left_key, "laptop-kollab")
    assert right._state().state.peer_devices[left_key] == "laptop-kollab"

    with pytest.raises(RelayError, match="already on this network"):
        right.set_device_name("laptop-kollab")


@pytest.mark.asyncio
async def test_rpc_directory_prefers_recorded_device_name_over_self_report(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    right_key = right.commands.client.public_key
    left.bind_peer_device(right_key, "recorded-name")

    await warm_directory(left)
    rows = await left.remote_agents()

    assert rows[0]["device"] == "recorded-name"


@pytest.mark.asyncio
async def test_remote_agents_row_shape_and_device_fallback_for_older_peers(bridges):
    members, _ = bridges
    (left, *_), (right, *_) = members
    await warm_directory(left)

    rows = await left.remote_agents()
    assert len(rows) == 1
    row = rows[0]
    device = default_device_name(right.workspace)
    assert row == {
        "name": "sapphire",
        "device": device,
        "handle": format_handle("sapphire", device),
        "state": "waiting",
        "address": address(right),
        "online": True,
        "is_coordinator": False,
        "workspace_id": right.commands.client.state.workspace_id,
    }

    # An older peer sends no "device" field at all; the caller falls back to
    # a stable per-peer stand-in (key_label) rather than failing closed.
    original = Directory.publishable_agents

    def without_device(self, workspace, workspace_id, device_name=None):
        return [
            {k: v for k, v in row.items() if k != "device"}
            for row in original(self, workspace, workspace_id, device_name)
        ]

    right.directory.publishable_agents = without_device.__get__(right.directory)
    left._cache = {}
    await warm_directory(left)

    rows = await left.remote_agents()
    assert len(rows) == 1
    assert rows[0]["device"] == key_label(right.commands.client.public_key)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["open", "agents"])
async def test_revoked_peer_queued_message_never_reaches_the_model(bridges, level):
    """A device revoked through membership keeps nothing queued deliverable.

    A member-side revocation (accept_membership -> client.revoke) drops the
    approval without touching the conversation store, so the delivery loop
    itself must refuse a queued record whose peer is no longer approved
    (docs section 4: revoking a device drops it on every member).
    """
    members, _ = bridges
    (left, *_), (right, _, right_model, _) = members
    set_trust(left, level)
    set_trust(right, level)
    if level == "agents":
        allow(left, right)

    sent = await left.send(address(right), "check the tunnel")
    assert sent["state"] == "queued"

    # Membership-level revocation: approvals drop, the store keeps the task.
    right.commands.client.revoke(left.commands.client.public_key)
    assert right.store.task(sent["id"])["state"] == "queued"

    await right._tick()

    assert right_model.contexts == []
    assert right.store.task(sent["id"])["state"] == "cancelled"
