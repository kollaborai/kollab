"""Review fixes on the network branch (issue #121): commands, roster and follower windows.

Constitution: docs/specs/agent-network-simple-flow.md. Names, never keys or
receipts, on anything a human reads; allow/deny mean nothing under open trust;
leaving a network lets the device join another; a second window in the
workspace gets the Connect screen too (read-only, see test_connect_second_window).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from plugins.hub.device_names import key_label
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_commands import NO_NETWORK, offline_device_names
from plugins.hub.relay_state import RelayStateStore
from tests.unit.test_hub_network_surface import _relay_commands

KEY = SigningKey.generate().verify_key.encode().hex()


def _row(name, fingerprint, receipt):
    return SimpleNamespace(
        enrollment_id=receipt,
        device_name=name,
        device_key_fingerprint=fingerprint,
        credential_categories=(),
    )


def _accepting_bridge(rows, decided):
    async def decide(receipt, *, decision, source_agent):
        decided.append((receipt, decision))
        return {"status": "accepted", "receipt_id": receipt}

    return SimpleNamespace(
        pending_enrollment_requests=lambda *, source_agent: rows,
        decide_enrollment_request=decide,
        network_name=lambda: "marco-home",
    )


# ------------------------------------------------------------- accept/reject


@pytest.mark.asyncio
async def test_accept_and_reject_name_the_device_and_never_print_a_receipt(tmp_path):
    decided = []
    rows = [_row("ana-laptop", "4d04" + "0" * 60, "a" * 32)]
    commands = _relay_commands(tmp_path, agent_bridge=_accepting_bridge(rows, decided))

    accepted = await commands._run("accept ana-laptop", source_agent="k1")
    rejected = await commands._run("reject ana-laptop", source_agent="k1")

    assert accepted == "accepted ana-laptop. it is now a trusted device on marco-home."
    assert rejected == "rejected ana-laptop."
    assert decided == [("a" * 32, "accept"), ("a" * 32, "reject")]
    assert "receipt" not in accepted + rejected
    assert "a" * 32 not in accepted + rejected


@pytest.mark.asyncio
async def test_two_requests_with_one_name_are_told_apart_by_fingerprint_not_receipt(tmp_path):
    decided = []
    rows = [
        _row("ana-laptop", "4d04" + "0" * 60, "a" * 32),
        _row("ana-laptop", "91c0" + "0" * 60, "b" * 32),
    ]
    commands = _relay_commands(tmp_path, agent_bridge=_accepting_bridge(rows, decided))

    ambiguous = await commands._run("accept ana-laptop", source_agent="k1")

    assert "more than one pending request is named 'ana-laptop'" in ambiguous
    assert "fingerprint" in ambiguous and "receipt" not in ambiguous
    assert decided == []

    accepted = await commands._run("accept ana-laptop 91c0", source_agent="k1")

    assert accepted.startswith("accepted ana-laptop.")
    assert decided == [("b" * 32, "accept")]


@pytest.mark.asyncio
async def test_accept_usage_names_the_device_not_a_receipt(tmp_path):
    commands = _relay_commands(tmp_path, agent_bridge=_accepting_bridge([], []))

    assert await commands._run("accept", source_agent="k1") == (
        "usage: /connect accept <device> [fingerprint]"
    )
    assert await commands._run("reject", source_agent="k1") == (
        "usage: /connect reject <device> [fingerprint]"
    )


@pytest.mark.asyncio
async def test_a_nameless_request_is_accepted_by_its_fingerprint_prefix(tmp_path):
    decided = []
    rows = [_row("", "4d04" + "0" * 60, "a" * 32)]
    commands = _relay_commands(tmp_path, agent_bridge=_accepting_bridge(rows, decided))

    status_lines = commands._pending_request_lines()
    accepted = await commands._run("accept 4d04", source_agent="k1")

    assert "unknown device wants to join" in status_lines[1]
    assert "/connect accept 4d04" in status_lines[1]
    assert "a" * 8 not in status_lines[1]
    assert accepted == "accepted that device. it is now a trusted device on marco-home."
    assert decided == [("a" * 32, "accept")]


# --------------------------------------------------------------- allow/deny


@pytest.mark.asyncio
async def test_allow_and_deny_usage_says_device_and_agent(tmp_path):
    bridge = SimpleNamespace(remote_agents=lambda: [], effective_trust=lambda key: "agents")
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    assert await commands._run("allow ana-laptop", source_agent="k1") == (
        "usage: /connect allow <device> <agent>"
    )
    assert await commands._run("allow", source_agent="k1") == (
        "usage: /connect allow <device> <agent>"
    )
    assert await commands._run("deny", source_agent="k1") == (
        "usage: /connect deny <device> [agent]"
    )


@pytest.mark.asyncio
async def test_allow_and_deny_say_so_instead_of_reporting_success_under_open_trust(tmp_path):
    async def application_command(*_args, **_kwargs):
        raise AssertionError("open trust never consults grants; nothing to change")

    bridge = SimpleNamespace(
        remote_agents=lambda: [],
        effective_trust=lambda key: "open",
        application_command=application_command,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={KEY: "ana-laptop"})
        ),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    allowed = await commands._run("allow ana-laptop ops", source_agent="k1")
    denied = await commands._run("deny ana-laptop", source_agent="k1")

    for text in (allowed, denied):
        assert text.startswith("connect: trust is open, so ")
        assert "no effect" in text
        assert "/connect trust agents" in text and "/connect revoke <device>" in text


@pytest.mark.asyncio
async def test_allow_under_agents_trust_prints_the_device_name_never_the_key(bridges):  # noqa: F811
    members, _ = bridges
    (left, *_), (right, *_) = members
    right_key = right.commands.client.public_key

    unnamed = await left.application_command("allow", f"{right_key} sapphire")
    left.bind_peer_device(right_key, "right-box")
    named = await left.application_command("allow", f"{right_key} sapphire")

    assert right_key not in unnamed + named
    assert f"conversation allowed: {key_label(right_key)} -> sapphire" in unnamed
    assert "conversation allowed: right-box -> sapphire" in named
    assert await left.application_command("allow", right_key) == (
        "usage: /connect allow <device> <agent>"
    )
    assert await left.application_command("deny", "") == (
        "usage: /connect deny <device> [agent]"
    )


# --------------------------------------------------------------------- leave


@pytest.mark.asyncio
async def test_leave_forgets_the_network_so_bare_connect_shows_none(tmp_path):
    bridge = SimpleNamespace(remote_agents=lambda: [], trust_level=lambda: "open")
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    commands.client.state.approvals = [KEY]
    commands.client._store.save()

    text = await commands._run("leave", source_agent=None)

    assert text.startswith("left the network")
    saved = RelayStateStore(commands.client.workspace, commands.client.state_dir).state
    assert saved.origin == "" and saved.enabled is False and saved.approvals == []
    assert (await commands.format_status()).splitlines()[0] == NO_NETWORK
    assert await commands._run("leave", source_agent=None) == (
        "connect: this device is not on a network"
    )


@pytest.mark.asyncio
async def test_leave_with_a_domain_only_leaves_that_network(tmp_path):
    bridge = SimpleNamespace(remote_agents=lambda: [], trust_level=lambda: "open")
    commands = _relay_commands(tmp_path, agent_bridge=bridge)

    refused = await commands._run("leave agents.webceive.com", source_agent=None)

    assert refused == "connect: this device is not on agents.webceive.com"
    assert commands.client.state.origin == "https://kollabor.ai"

    left = await commands._run("leave kollabor.ai", source_agent=None)

    assert left.startswith("left the network")
    assert commands.client.state.origin == ""


# ------------------------------------------------------------ status labels


@pytest.mark.asyncio
async def test_status_uses_the_screens_labels_and_folds_offline_devices_into_online(tmp_path):
    bridge = SimpleNamespace(
        trust_level=lambda: "open",
        device_name=lambda: "mac-kollab",
        network_name=lambda: "marco-home",
        remote_agents=lambda: [],
        plugin=SimpleNamespace(_presence=None, _identity=None),
        _enrollment_issuer=None,
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices={KEY: "laptop-kollab"}, peer_trust={})
        ),
    )
    commands = _relay_commands(tmp_path, agent_bridge=bridge)
    commands.client.state.approvals = [KEY]

    status = (await commands.format_status()).splitlines()

    assert status[0] == "network marco-home  via kollabor.ai  trust: open"
    assert status.index("online") < status.index("  laptop-kollab (offline)")
    assert not any(line.startswith(("offline devices", "network:")) for line in status)


# ---------------------------------------------------------- offline devices


def _client(state="online", online=(), approvals=()):
    return SimpleNamespace(
        status=lambda: {"state": state},
        peers=lambda: [{"key": key} for key in online],
        state=SimpleNamespace(approvals=list(approvals)),
    )


def _named(peer_devices, peer_trust=None):
    return SimpleNamespace(
        _state=lambda: SimpleNamespace(
            state=SimpleNamespace(peer_devices=peer_devices, peer_trust=peer_trust or {})
        )
    )


def test_nobody_is_offline_while_the_relay_itself_is_unreachable():
    client = _client(state="reconnecting", approvals=[KEY])

    assert offline_device_names(_named({KEY: "alzan-prod-home"}), [], client) == []


def test_a_peer_in_the_relay_roster_is_never_offline_even_before_its_directory_arrives():
    client = _client(online=[KEY], approvals=[KEY])

    assert offline_device_names(_named({KEY: "alzan-prod-home"}), [], client) == []


def test_an_accepted_stranger_lives_in_another_room_and_is_not_listed_offline():
    stranger = SigningKey.generate().verify_key.encode().hex()
    client = _client(approvals=[KEY, stranger])
    bridge = _named({KEY: "laptop-kollab", stranger: "ana-laptop"}, {stranger: "agents"})

    assert offline_device_names(bridge, [], client) == ["laptop-kollab"]


def test_an_unbound_offline_peer_shows_a_hash_label_never_its_key():
    client = _client(approvals=[KEY])

    [label] = offline_device_names(_named({}), [], client)

    assert label == key_label(KEY)
    assert label != KEY[:8]


@pytest.mark.asyncio
async def test_a_cached_directory_read_keeps_a_peer_a_few_seconds_past_the_ttl(bridges):  # noqa: F811
    members, _ = bridges
    (left, *_), (right, *_) = members
    await left._owner_call("relay.directory", {"peer": "", "cached": False})
    assert len(await left.remote_agents()) == 1

    left._cache = {
        key: (stamp - 60, rows) for key, (stamp, rows) in left._cache.items()
    }

    assert len(await left.remote_agents()) == 1  # cached read: stale is still known
    assert right.commands.client.public_key in {
        row["address"].split(":")[1] for row in await left.remote_agents()
    }


# ------------------------------------------------- knocks, follower windows


def _plugin(*, origin="", attach=False, owner=True):
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=attach)
    plugin._relay_commands = (
        SimpleNamespace(client=SimpleNamespace(state=SimpleNamespace(origin=origin)))
        if owner
        else None
    )
    plugin._relay_agent = SimpleNamespace(
        _state=lambda: SimpleNamespace(state=SimpleNamespace(origin=origin)),
        # A follower's lock probe finds the other window's record.
        owner=SimpleNamespace(owner=lambda: None if owner else {"pid": 4242}),
    )
    return plugin


@pytest.mark.asyncio
async def test_bare_knocks_reads_the_joined_directory_not_kollabor_ai():
    plugin = _plugin(origin="https://agents.webceive.com")
    plugin._open_contact_review_altview = AsyncMock(return_value="")

    await plugin._handle_connect_command("knocks")
    await plugin._handle_connect_command("knocks other.example")

    assert [call.args for call in plugin._open_contact_review_altview.await_args_list] == [
        ("agents.webceive.com",),
        ("other.example",),
    ]


@pytest.mark.asyncio
async def test_bare_knocks_with_no_network_falls_back_to_kollabor_ai():
    plugin = _plugin(origin="")
    plugin._open_contact_review_altview = AsyncMock(return_value="")

    await plugin._handle_connect_command("knocks")

    plugin._open_contact_review_altview.assert_awaited_once_with("kollabor.ai")


@pytest.mark.asyncio
async def test_a_follower_window_knows_the_workspaces_network_from_the_shared_state():
    follower = _plugin(origin="https://agents.webceive.com", owner=False)

    assert follower._relay_network_domain() == "agents.webceive.com"


@pytest.mark.asyncio
async def test_bare_connect_in_a_follower_window_opens_the_screen_not_the_status_text():
    follower = _plugin(origin="https://kollabor.ai", owner=False)
    status = "network marco-home  via kollabor.ai  trust: open\nthis device alzan-prod-home"
    follower._run_connect_command = AsyncMock(return_value=status)
    follower._open_connect_altview = AsyncMock(return_value="")
    follower._open_connect_screen = AsyncMock(return_value="")

    assert await follower._handle_connect_command("") == ""

    follower._run_connect_command.assert_awaited_once_with("status")
    follower._open_connect_altview.assert_not_awaited()
    follower._open_connect_screen.assert_awaited_once_with("kollabor.ai")


@pytest.mark.asyncio
async def test_bare_connect_in_a_follower_window_with_no_network_opens_the_screen_too():
    # The code form would offer a submit that only the owner can carry out.
    follower = _plugin(origin="", owner=False)
    follower._run_connect_command = AsyncMock(return_value=NO_NETWORK + "\ncontact route none")
    follower._open_connect_altview = AsyncMock(return_value="")
    follower._open_connect_screen = AsyncMock(return_value="")

    await follower._handle_connect_command("")

    follower._open_connect_altview.assert_not_awaited()
    follower._open_connect_screen.assert_awaited_once_with("")


@pytest.mark.asyncio
async def test_bare_connect_in_the_owner_window_still_opens_the_screen():
    owner = _plugin(origin="https://kollabor.ai", owner=True)
    owner._run_connect_command = AsyncMock()
    owner._open_connect_screen = AsyncMock(return_value="")

    await owner._handle_connect_command("")

    owner._run_connect_command.assert_not_awaited()
    owner._open_connect_screen.assert_awaited_once_with("kollabor.ai")


# ------------------------------------------------------ remote-target refusals


@pytest.mark.asyncio
async def test_capture_stop_and_spawn_refuse_a_remote_target_with_a_hint():
    hub = HubPlugin.__new__(HubPlugin)
    expected = "not allowed on a remote device; ask infra@alzan-prod-home to do it"

    assert await hub._handle_capture_command("infra@alzan-prod-home 20") == expected
    assert await hub._handle_stop_command("infra@alzan-prod-home") == expected
    assert await hub._handle_spawn_command("infra@alzan-prod-home check the tunnel") == expected
    assert (
        await hub._handle_spawn_command({"name": "infra@alzan-prod-home", "task": "x"})
        == expected
    )
