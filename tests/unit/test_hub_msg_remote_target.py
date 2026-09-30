"""hub_msg to an agent@device reports what the network did, not local presence.

The live proof showed a delivered, answered message come back as
`warning: '<agent@device>' is not online. message broadcast but no matching
agent`, because the tool result asked local presence about a remote agent. The
model then told the human the peer was offline and resent. These tests drive the
real tool handler and the real router against a fake relay bridge.
"""

from __future__ import annotations

import re
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_agent import RelayAgentBridge
from plugins.hub.relay_state import RelayError

# Keys, receipt ids and relay: addresses never reach a human or the model.
LEAK = re.compile(r"[0-9a-f]{32,}|ed25519:|relay:|receipt", re.IGNORECASE)
PEER = "koordinator@alzan-prod-kollab-m1-server5"
UNKNOWN_WORDING = "unknown agent@device: run /connect status to see who is online"


def _plugin(
    *, on_roster=(), receipt=None, local=(), address=lambda handle: f"relay:{handle}"
) -> tuple[HubPlugin, list]:
    """A plugin whose network is `on_roster` (handles) and whose local mesh is `local`."""
    sent: list = []
    rows = []
    for handle in on_roster:
        name, device = handle.split("@")
        rows.append({"name": name, "device": device, "address": address(handle)})
    roster = SimpleNamespace(remote_agents=AsyncMock(return_value=rows))

    async def send(address, content, **kwargs):
        sent.append({"address": address, "content": content, **kwargs})
        return receipt or {"id": "a" * 32, "state": "queued", "duplicate": False}

    relay = SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: None),
        # The real producer of the unknown-agent wording, against this roster.
        resolve_handle=partial(RelayAgentBridge.resolve_handle, roster),
        send=send,
    )
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._relay_agent = relay
    plugin._vault = None
    plugin._task_ledger = None
    plugin._presence = MagicMock()
    plugin._presence.scan_all_presence.return_value = list(local)
    plugin._presence.discover_agents_async = AsyncMock(return_value=list(local))
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()
    return plugin, sent


def _call(target: str, content: str = "run uname -n") -> dict:
    return {"id": "tool-1", "to": target, "content": content}


@pytest.mark.asyncio
async def test_a_remote_agent_on_the_roster_gets_sent_and_no_warning():
    plugin, sent = _plugin(on_roster=[PEER])

    result = await plugin._handle_hub_msg_tool(_call(PEER))

    assert result.success
    assert result.output.startswith(f"sent to {PEER}")
    assert "not online" not in result.output and "warning" not in result.output
    assert len(sent) == 1 and sent[0]["address"] == f"relay:{PEER}"
    # Local presence has nothing to say about a remote agent.
    plugin._presence.scan_all_presence.assert_not_called()


@pytest.mark.asyncio
async def test_the_handle_is_matched_whatever_its_case():
    plugin, sent = _plugin(on_roster=[PEER])

    result = await plugin._handle_hub_msg_tool(_call(PEER.upper()))

    assert result.success and result.output.startswith(f"sent to {PEER}")


@pytest.mark.asyncio
async def test_a_remote_agent_not_on_the_roster_says_so_and_names_the_next_step():
    plugin, sent = _plugin(on_roster=[])

    result = await plugin._handle_hub_msg_tool(_call(PEER))

    assert not result.success
    assert UNKNOWN_WORDING in result.output
    assert "not online" not in result.output
    assert "force" not in result.output  # force means nothing for a remote agent
    assert sent == []


@pytest.mark.asyncio
async def test_a_failed_remote_send_can_be_retried_once_the_agent_is_on_the_roster():
    plugin, sent = _plugin(on_roster=[])
    failed = await plugin._handle_hub_msg_tool(_call(PEER))
    assert not failed.success

    # The device joins; the same words are not swallowed as a "duplicate".
    plugin._relay_agent.resolve_handle = partial(
        RelayAgentBridge.resolve_handle,
        SimpleNamespace(
            remote_agents=AsyncMock(
                return_value=[
                    {
                        "name": "koordinator",
                        "device": "alzan-prod-kollab-m1-server5",
                        "address": "relay:x",
                    }
                ]
            )
        ),
    )
    retried = await plugin._handle_hub_msg_tool(_call(PEER))

    assert retried.success and retried.output.startswith(f"sent to {PEER}")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_repeated_remote_send_is_not_resent_and_says_so():
    plugin, sent = _plugin(on_roster=[PEER])
    await plugin._handle_hub_msg_tool(_call(PEER))

    again = await plugin._handle_hub_msg_tool(_call(PEER))

    assert again.success
    assert "not sent again" in again.output
    assert len(sent) == 1


NEW_REQUEST_RESULT = (
    f"sent to {PEER}; its reply arrives by itself as a hub message. end your turn "
    "unless you have other local work, and do not check status, capture, or send again."
)


@pytest.mark.asyncio
async def test_a_new_request_result_tells_the_asker_the_reply_comes_by_itself():
    """The live run polled hub_status, tried hub_capture, then asked a second
    time, so the far side ran the task twice. The result is what the model
    reads before it decides; it must say to stop and wait."""
    plugin, sent = _plugin(on_roster=[PEER])

    result = await plugin._handle_hub_msg_tool(_call(PEER))

    assert result.success and result.output == NEW_REQUEST_RESULT
    assert len(sent) == 1
    assert LEAK.search(result.output) is None


@pytest.mark.asyncio
async def test_an_answer_on_a_received_request_thread_stays_plain_sent_to():
    from plugins.hub.models import HubMessage

    plugin, sent = _plugin(on_roster=[PEER])
    request = HubMessage(
        action="message",
        from_identity=PEER,
        to="koordinator",
        content="run uname",
        metadata={"network": {"kind": "relay"}},
    )
    plugin._open_network_turn(request, "wake")

    result = await plugin._handle_hub_msg_tool(_call(PEER, "alzan-prod"))

    assert result.success and result.output == f"sent to {PEER}"
    assert sent[0]["thread_id"] == request.thread_id
    assert sent[0]["reply_to"] == request.id


@pytest.mark.asyncio
async def test_a_follow_up_in_the_same_turn_is_still_an_answer_on_the_request_thread():
    """The request stays open until its turn ends (test_hub_network_turns.py
    covers the end): an interim message and the answer are both replies."""
    from plugins.hub.models import HubMessage

    plugin, sent = _plugin(on_roster=[PEER])
    plugin._open_network_turn(
        HubMessage(action="message", from_identity=PEER, to="koordinator", content="q"),
        "wake",
    )
    answer = await plugin._handle_hub_msg_tool(_call(PEER, "the answer"))
    followup = await plugin._handle_hub_msg_tool(_call(PEER, "one more thing"))

    assert answer.output == followup.output == f"sent to {PEER}"
    assert sent[0]["thread_id"] == sent[1]["thread_id"]
    assert sent[0]["reply_to"] == sent[1]["reply_to"] != ""


@pytest.mark.asyncio
async def test_the_wait_note_is_for_remote_requests_only():
    lapis = AgentRuntime(name="coder", identity="lapis", agent_id="lapis-id")
    plugin, _ = _plugin(local=[lapis])
    plugin._deliver_to_agent = AsyncMock(return_value=True)

    result = await plugin._handle_hub_msg_tool(_call("lapis"))

    assert result.output == "delivered to lapis"


@pytest.mark.asyncio
async def test_a_send_the_receiving_device_refused_is_not_reported_as_sent():
    plugin, sent = _plugin(
        on_roster=[PEER],
        receipt={
            "id": "a" * 32,
            "state": "rejected",
            "reason": "not_authorized",
            "duplicate": False,
        },
    )

    result = await plugin._handle_hub_msg_tool(_call(PEER))

    assert not result.success
    assert "peer has no conversation grant for this agent" in result.output
    assert "sent to" not in result.output


@pytest.mark.asyncio
async def test_no_network_bridge_yet_is_a_clear_refusal_not_a_local_lookup():
    plugin, sent = _plugin(on_roster=[PEER])
    plugin._relay_agent = None

    result = await plugin._handle_hub_msg_tool(_call(PEER))

    assert not result.success
    assert "/connect status" in result.output
    assert "not online" not in result.output


@pytest.mark.asyncio
async def test_a_local_target_that_is_not_online_keeps_its_warning():
    plugin, sent = _plugin(local=[])

    result = await plugin._handle_hub_msg_tool(_call("ghost"))

    assert result.success
    assert result.output.startswith("warning: 'ghost' is not online.")
    assert sent == []


@pytest.mark.asyncio
async def test_a_local_target_that_is_online_is_still_delivered():
    lapis = AgentRuntime(name="coder", identity="lapis", agent_id="lapis-id")
    plugin, sent = _plugin(local=[lapis])
    plugin._deliver_to_agent = AsyncMock(return_value=True)

    result = await plugin._handle_hub_msg_tool(_call("lapis"))

    assert result.success and result.output == "delivered to lapis"
    plugin._deliver_to_agent.assert_awaited_once()
    assert sent == []  # a local message never touches the network


# Sibling entry points that reach the same router ------------------------------


@pytest.mark.asyncio
async def test_slash_hub_msg_to_an_unknown_remote_agent_has_no_force_hint():
    plugin, sent = _plugin(on_roster=[])

    text = await plugin._handle_msg_command(f"{PEER} hello")

    assert UNKNOWN_WORDING in text
    assert "force" not in text


@pytest.mark.asyncio
async def test_slash_hub_msg_to_a_remote_agent_on_the_roster_reports_sent():
    plugin, sent = _plugin(on_roster=[PEER])

    assert await plugin._handle_msg_command(f"{PEER} hello") == f"sent to {PEER}"
    assert sent[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_at_mention_of_a_remote_agent_goes_to_the_network_not_the_pool_error():
    plugin, sent = _plugin(on_roster=[PEER])

    text = await plugin.send_user_message(PEER, "hello")

    assert text.startswith(f"sent to {PEER}")
    assert "not online" not in text and "runnable pool" not in text
    assert sent[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_wake_refuses_a_remote_agent_instead_of_calling_it_missing():
    plugin, sent = _plugin(on_roster=[PEER])

    text = await plugin._handle_wake_command(PEER)

    assert text == f"not allowed on a remote device; ask {PEER} to do it"
    assert "not found" not in text


@pytest.mark.asyncio
async def test_network_broadcast_counts_only_agents_that_took_the_message():
    plugin, sent = _plugin(on_roster=[PEER])
    plugin._relay_trust_level = lambda: "open"
    plugin._presence.get_cached_agents.return_value = []
    plugin._route_message = AsyncMock(return_value=[])
    plugin._refresh_remote_agent_rows = AsyncMock(
        return_value=[
            {"handle": PEER, "online": True},
            {"handle": "ghost@nowhere", "online": True},
        ]
    )

    text = await plugin._handle_broadcast_command("stand down", scope="network")

    assert text.endswith("; 1 network agent(s)")  # ghost is off the roster
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_the_unknown_agent_wording_comes_from_the_bridge_not_a_copy():
    with pytest.raises(RelayError, match=UNKNOWN_WORDING):
        await RelayAgentBridge.resolve_handle(
            SimpleNamespace(remote_agents=AsyncMock(return_value=[])), PEER
        )


@pytest.mark.asyncio
async def test_no_key_receipt_or_relay_address_in_any_remote_hub_msg_result():
    """Every result, error and published metadata under open trust is scanned."""

    def real_address(handle: str) -> str:
        return f"relay:{'a' * 64}:{'9' * 32}:{handle.split('@')[0]}-1"

    refused = {
        "id": "f" * 32,
        "state": "rejected",
        "reason": "not_authorized",
        "duplicate": False,
    }
    seen: list[str] = []
    for kwargs, call in (
        ({"on_roster": [PEER]}, _call(PEER)),
        ({"on_roster": [PEER]}, _call(PEER.upper())),
        ({"on_roster": []}, _call(PEER)),
        ({"on_roster": [PEER], "receipt": refused}, _call(PEER)),
        ({"on_roster": [PEER], "receipt": {**refused, "state": "failed"}}, _call(PEER)),
    ):
        plugin, _ = _plugin(address=real_address, **kwargs)
        result = await plugin._handle_hub_msg_tool(call)
        seen += [result.output, result.error or "", repr(result.metadata)]
        assert "network" not in result.metadata

    plugin, _ = _plugin(on_roster=[PEER], address=real_address)
    await plugin._handle_hub_msg_tool(_call(PEER))
    seen.append((await plugin._handle_hub_msg_tool(_call(PEER))).output)  # duplicate
    seen.append(await plugin._handle_msg_command(f"{PEER} other words"))
    seen.append(await plugin.send_user_message(PEER, "more words"))
    plugin._relay_agent = None
    seen.append((await plugin._handle_hub_msg_tool(_call(PEER, "no bridge"))).output)

    assert len([text for text in seen if text]) >= 12  # the scan saw real text
    assert [text for text in seen if LEAK.search(text)] == []
