"""Local Hub slash-command path for explicit enrollment review."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from plugins.hub.enrollment_client import (
    EnrollmentIssuer,
    _ActiveEnrollmentOffer,
    _device_key_fingerprint,
    _LiveEnrollmentRequest,
    _ProvisioningPlan,
)
from plugins.hub.enrollment_codes import EnrollmentEnvelopeKey
from plugins.hub.enrollment_delegations import EnrollmentDelegationStore
from plugins.hub.plugin import CODE_IN_COMMAND, HubPlugin
from plugins.hub.provisioning import ProfilePreferences
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_commands import RelayCommands
from plugins.hub.relay_state import RelayError

AGENT_ID = "owner-agent-01"
ISSUER = "ed25519:" + "a" * 64
NETWORKS = ("origin:" + "b" * 64, "room:" + "c" * 64)
PROFILE = "profile:" + "d" * 64
CATEGORIES = ("conversation:send", "provider:openai:api_key")
ORIGIN = "https://kollabor.ai"
WORKSPACE_ID = "f" * 32


class LocalDirectory:
    def agents(self, _workspace=None):
        return [SimpleNamespace(agent_id=AGENT_ID)]


def _local_hub(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = SimpleNamespace(
        agent_id=AGENT_ID,
        identity="owner",
        socket_path=str(tmp_path / "owner.sock"),
    )
    hub = HubPlugin.__new__(HubPlugin)
    hub._identity = identity
    hub._rpc_server = object()
    hub._cli_args = SimpleNamespace(attach=False)
    bridge = RelayAgentBridge(
        hub,
        workspace,
        state_dir=tmp_path / "relay-state",
        directory=LocalDirectory(),
    )
    bridge.commands = RelayCommands(
        workspace,
        state_dir=bridge.owner.state_dir,
        agent_bridge=bridge,
    )
    hub._relay_agent = bridge
    client = bridge.commands.client
    client.state.origin = ORIGIN
    client._store.save()
    client._session_id = "e" * 32
    client._state = "online"
    return hub, bridge


def test_connect_status_includes_redacted_destination_recovery_summary(tmp_path):
    _hub, bridge = _local_hub(tmp_path)
    offer_id = "0123456789abcdef0123456789abcdef"
    bridge._enrollment_issuer = SimpleNamespace(
        destination_recovery_status=lambda: {
            "pending": 3,
            "active": 1,
            "issuer_pending": 2,
            "destination_pending": 1,
            "last_error_code": "conflict",
            "retry_in_seconds": 300,
        }
    )

    status = bridge.commands.format_status()

    assert (
        "device enrollment recovery: 3 pending (2 issuer, 1 destination), 1 running; "
        "last issue: conflict; retry in 300s"
    ) in status
    assert offer_id not in status


def _add_pending(bridge, issuer, *, round_id, now):
    client = bridge.commands.client
    destination = SigningKey.generate()
    destination_key = destination.verify_key.encode().hex()
    offer_id = ("f" if round_id[0] != "f" else "1") * 32
    human_action_id = "human-action-01"
    expires_at = now + 300
    store = EnrollmentDelegationStore(bridge.owner.state_dir / "enrollment-delegations.json")
    if store.get(human_action_id) is None:
        store.create(
            human_action_id=human_action_id,
            authorized_agent_id=AGENT_ID,
            authorized_session_id=client._session_id,
            issuer=ISSUER,
            network_ids=NETWORKS,
            configuration_profile=PROFILE,
            credential_categories=CATEGORIES,
            max_new_devices=2,
            expires_at=expires_at,
            now=now,
        )
    envelope_key = EnrollmentEnvelopeKey(offer_id, b"x" * 32)
    fingerprint = _device_key_fingerprint(destination_key)
    store.record_pending(
        human_action_id,
        enrollment_id=round_id,
        device_key_fingerprint=fingerprint,
        agent_id=AGENT_ID,
        session_id=client._session_id,
        issuer=ISSUER,
        network_ids=NETWORKS,
        configuration_profile=PROFILE,
        credential_categories=CATEGORIES,
        workspace_id=WORKSPACE_ID,
        expires_at=expires_at,
        now=now,
    )
    provisioning_plan = _ProvisioningPlan(
        source_profile_name="default",
        destination_profile_name="kollab-new-device",
        profile_preferences=ProfilePreferences(
            name="default", provider="openai", model="gpt-4.1"
        ),
        credential_category="provider:openai:api_key",
        display="default (openai/gpt-4.1) to kollab-new-device",
    )
    offer = _ActiveEnrollmentOffer(
        offer_id=offer_id,
        expires_at=expires_at,
        human_action_id=human_action_id,
        session_id=client._session_id,
        issuer_key=client.public_key,
        room_capability=client.state.room,
        origin=ORIGIN,
        issuer_principal_id=ISSUER,
        network_ids=NETWORKS,
        profile=PROFILE,
        envelope_key=envelope_key,
        owner_signing_key=SigningKey.generate(),
        discovery=SimpleNamespace(manifest={"coordinator": {"public_key": "0" * 64}}),
        ca="",
        private_cidrs=(),
        provisioning_plan=provisioning_plan,
        credential_categories=CATEGORIES,
    )
    live = _LiveEnrollmentRequest(
        offer=offer,
        destination_key=destination_key,
        round_id=round_id,
        workspace_id=WORKSPACE_ID,
        challenge_token="challenge-secret",
        proof_token="proof-secret",
        decision_event=asyncio.Event(),
    )
    issuer._live_requests[round_id] = live
    return store, live, fingerprint


@pytest.mark.asyncio
async def test_local_hub_command_lists_redacted_requests_and_accepts_or_rejects(
    tmp_path,
):
    hub, bridge = _local_hub(tmp_path)
    issuer = EnrollmentIssuer(bridge)
    bridge._enrollment_issuer = issuer
    now = int(time.time())
    accepted_id = "1" * 32
    rejected_id = "2" * 32
    store, accepted_live, fingerprint = _add_pending(bridge, issuer, round_id=accepted_id, now=now)
    _add_pending(bridge, issuer, round_id=rejected_id, now=now)

    listed = await hub._handle_connect_command("requests")

    assert accepted_id in listed
    assert rejected_id in listed
    assert "sha256:" + fingerprint[:16] in listed
    assert fingerprint not in listed
    assert "challenge-secret" not in listed
    assert "proof-secret" not in listed
    assert "token" not in listed.lower()
    assert "decision: available" in listed
    assert f"destination workspace: {WORKSPACE_ID}" in listed
    assert "provider credentials will be copied to this device" in listed
    assert "network revocation does not revoke them at the provider" in listed
    assert "default (openai/gpt-4.1) to kollab-new-device" in listed

    accepted = await hub._handle_connect_command(f"accept {accepted_id}")
    assert accepted == f"enrollment accepted; receipt: {accepted_id}"
    assert await hub._handle_connect_command(f"accept {accepted_id}") == f"enrollment accepted; receipt: {accepted_id}"
    assert accepted_live.decision == "approved"
    assert accepted_live.decision_event.is_set()
    assert store.get("human-action-01").consumed_new_devices == 1
    assert (
        store.get_enrollment_request(
            accepted_id,
            agent_id=AGENT_ID,
            session_id=bridge.commands.client._session_id,
        ).status
        == "approved"
    )

    rejected = await hub._handle_connect_command(f"reject {rejected_id}")
    assert rejected == f"enrollment rejected; receipt: {rejected_id}"
    assert store.get("human-action-01").consumed_new_devices == 1
    assert (
        store.get_enrollment_request(
            rejected_id,
            agent_id=AGENT_ID,
            session_id=bridge.commands.client._session_id,
        ).status
        == "rejected"
    )


@pytest.mark.asyncio
async def test_remote_turn_and_wrong_local_agent_cannot_decide_enrollment(tmp_path):
    hub, bridge = _local_hub(tmp_path)
    issuer = EnrollmentIssuer(bridge)
    bridge._enrollment_issuer = issuer
    now = int(time.time())
    round_id = "3" * 32
    store, _, _ = _add_pending(bridge, issuer, round_id=round_id, now=now)

    remote_token = bridge._turn.set("remote-task-id")
    try:
        remote_result = await hub._handle_connect_command(f"accept {round_id}")
    finally:
        bridge._turn.reset(remote_token)
    assert "enrollment approved" not in remote_result
    assert (
        store.get_enrollment_request(round_id, agent_id=AGENT_ID, session_id=bridge.commands.client._session_id).status
        == "pending"
    )

    with pytest.raises(RelayError, match="only the active local issuer agent"):
        await bridge.decide_enrollment_request(round_id, decision="accept", source_agent="different-local-agent")
    assert store.get("human-action-01").consumed_new_devices == 0


@pytest.mark.asyncio
async def test_relay_session_change_reports_restart_instead_of_unauthorized(tmp_path):
    hub, bridge = _local_hub(tmp_path)
    issuer = EnrollmentIssuer(bridge)
    bridge._enrollment_issuer = issuer
    now = int(time.time())
    round_id = "4" * 32
    store, _, _ = _add_pending(bridge, issuer, round_id=round_id, now=now)
    issued_session = bridge.commands.client._session_id

    # A relay restart drops its ephemeral state and the client reconnects
    # under a new session; the pending request can no longer be decided.
    bridge.commands.client._session_id = "f" * 32
    with pytest.raises(RelayError, match="relay connection restarted.*create a new code"):
        await bridge.decide_enrollment_request(round_id, decision="accept", source_agent=AGENT_ID)

    record = store.get_enrollment_request(round_id, agent_id=AGENT_ID, session_id=issued_session)
    assert record.status == "pending"
    assert store.get("human-action-01").consumed_new_devices == 0


@pytest.mark.asyncio
async def test_code_text_is_rejected_before_owner_command_dispatch(tmp_path):
    hub, bridge = _local_hub(tmp_path)
    code_text = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"

    result = await hub._handle_connect_command(f"accept {code_text}")

    assert result == CODE_IN_COMMAND
    assert bridge._enrollment_issuer is None
    assert code_text not in result


@pytest.mark.asyncio
async def test_attach_mode_forwards_only_receipt_commands_to_the_owner_daemon():
    state = SimpleNamespace(hub_connect=AsyncMock(side_effect=lambda value: value))
    hub = HubPlugin.__new__(HubPlugin)
    hub._cli_args = SimpleNamespace(attach=True)
    hub.event_bus = SimpleNamespace(get_service=lambda _name: state)

    results = []
    for command in (
        "requests",
        "accept " + "4" * 32,
        "reject " + "5" * 32,
    ):
        results.append(await hub._handle_connect_command(command))

    assert results == ["requests", "accept " + "4" * 32, "reject " + "5" * 32]
    assert [call.args[0] for call in state.hub_connect.await_args_list] == results

    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"
    rejected_code = await hub._handle_connect_command(f"accept {code}")

    assert rejected_code == CODE_IN_COMMAND
    assert state.hub_connect.await_count == 3
    assert code not in repr(state.hub_connect.await_args_list)


@pytest.mark.asyncio
async def test_connect_palette_lists_subcommands_and_each_reaches_the_owner_daemon():
    registered = []
    hub = HubPlugin.__new__(HubPlugin)
    hub.name = "hub"
    hub.command_registry = SimpleNamespace(register_command=registered.append)
    hub._register_commands()
    connect = next(cmd for cmd in registered if cmd.name == "connect")
    names = [sub.name for sub in connect.subcommands]
    assert {"offer", "requests", "send", "answer", "networks", "help"} <= set(names)

    state = SimpleNamespace(hub_connect=AsyncMock(side_effect=lambda value: value))
    hub._cli_args = SimpleNamespace(attach=True)
    hub.event_bus = SimpleNamespace(get_service=lambda _name: state)
    local = {"enroll", "offer", "contact", "contacts", "help"}
    for name in names:
        if name in local:
            continue
        command = f"{name} {'a' * 32} two words"
        assert await hub._handle_connect_command(command) == command

    help_text = await hub._handle_connect_command("help")
    assert all(f"/connect {name}" in help_text for name in names)
    assert state.hub_connect.await_count == len(names) - len(local)


@pytest.mark.asyncio
async def test_codes_and_typos_in_connect_commands_never_reach_the_daemon():
    state = SimpleNamespace(hub_connect=AsyncMock(side_effect=lambda value: value))
    hub = HubPlugin.__new__(HubPlugin)
    hub._cli_args = SimpleNamespace(attach=True)
    hub.event_bus = SimpleNamespace(get_service=lambda _name: state)
    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"

    assert await hub._handle_connect_command(f"approve {code}") == CODE_IN_COMMAND
    assert await hub._handle_connect_command("pair") == (
        "connect: unknown subcommand 'pair'. Run /connect help for the list."
    )
    assert await hub._handle_connect_command("auth") == (
        "connect: unknown subcommand 'auth'. Did you mean /connect authorize? "
        "Run /connect help for the list."
    )
    state.hub_connect.assert_not_awaited()

    for target in ("kollabor.ai", "localhost:8443", "https://example.test"):
        assert await hub._handle_connect_command(target) == target


def test_log_lines_never_carry_enrollment_codes():
    import logging

    from kollabor.logging.setup import CompactFormatter

    code = "K1-0123456789abcdef0123456789abcdef-ABCD-EFGH-JKMN-PQRS-TVWX"
    message = "Executing highlighted menu command: %s"
    record = logging.LogRecord("t", logging.INFO, __file__, 1, message, (f"/connect approve {code}",), None)
    line = CompactFormatter("%(message)s").format(record)

    assert code not in line
    assert line == "Executing highlighted menu command: /connect approve K1-[redacted]"


@pytest.mark.asyncio
async def test_connect_in_a_window_without_its_daemon_says_so():
    hub = HubPlugin.__new__(HubPlugin)
    hub._cli_args = SimpleNamespace(attach=True)
    hub.event_bus = SimpleNamespace(get_service=lambda _name: None)

    assert await hub._handle_connect_command("status") == (
        "connect: this window is not connected to its agent daemon; restart kollab"
    )
