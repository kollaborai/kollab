"""A hub message the receiver refuses is answered with an error, never an ack (#581).

The sender counts a message as delivered only on an ack, so an ack for a
refused observation would make a lost message look delivered.
"""

import asyncio
import json
import time

import pytest

from plugins.hub import messenger as messenger_module
from plugins.hub.messenger import AgentSocketServer
from plugins.hub.models import HubMessage


class _Writer:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        return None

    def get_extra_info(self, name, default=None):
        return default

    def close(self):
        return None

    async def wait_closed(self):
        return None


async def _deliver(monkeypatch, tmp_path, on_message):
    monkeypatch.setattr(messenger_module, "get_socket_dir", lambda: tmp_path)
    messenger = AgentSocketServer(agent_id="receiver", on_message=on_message)
    messenger._get_peer_credentials = lambda writer: None
    message = HubMessage(
        action="message",
        from_agent="peer-id-1",
        from_identity="peer-1",
        to="*",
        content="PEER_OBSERVATION",
    )
    reader = asyncio.StreamReader()
    reader.feed_data(json.dumps(message.to_dict()).encode() + b"\n")
    reader.feed_eof()
    writer = _Writer()
    await messenger._handle_connection(reader, writer)
    return message, writer.written


@pytest.mark.asyncio
async def test_a_refused_message_gets_an_error_and_no_ack(monkeypatch, tmp_path):
    async def refuse(message):
        return False

    _, written = await _deliver(monkeypatch, tmp_path, refuse)

    assert written == [b'{"type":"error","msg":"message not accepted"}\n']


@pytest.mark.asyncio
async def test_an_admitted_message_is_still_acknowledged(monkeypatch, tmp_path):
    async def admit(message):
        return None

    message, written = await _deliver(monkeypatch, tmp_path, admit)

    assert written == [json.dumps({"type": "ack", "id": message.id}).encode() + b"\n"]


# --- Where a refused message goes next (#581 review round 1) -----------------

from collections import deque  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from plugins.hub.plugin import HubPlugin  # noqa: E402


@pytest.mark.asyncio
async def test_a_refused_message_replayed_from_the_mailbox_stays_queued(tmp_path):
    # The sender turns the refusal into a mailbox file; the replay tick deletes
    # the file before delivering it. A refusal must put it back, not drop it.
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(agent_id="agent-1", identity="receiver")
    refuse = [True]

    async def receive(message):
        return False if refuse[0] else None

    plugin._on_message_received = receive
    message = HubMessage(
        action="message", from_agent="peer-id-1", from_identity="peer-1",
        to="*", content="PEER_OBSERVATION",
    )
    with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
        await messenger_module.AgentMessenger.send_to_file("receiver", message)

        def tick():
            return messenger_module.AgentMessenger.read_mailboxes(
                ["agent-1", "receiver"]
            )

        await plugin._deliver_inbox_batch(tick())
        assert messenger_module.AgentMessenger.get_inbox_count("receiver") == 1

        refuse[0] = False
        replayed = tick()
        assert [m.id for m in replayed] == [message.id]
        await plugin._deliver_inbox_batch(replayed)
        assert messenger_module.AgentMessenger.get_inbox_count("receiver") == 0


class _RefusingController:
    active = True

    def __init__(self):
        self.accept = False
        self.calls = 0

    def observe_notification(self, message):
        self.calls += 1
        return self.accept


def _acp_hub(controller):
    from kollabor_agent.runtime import AgentRuntime
    from kollabor_events import EventBus

    llm = SimpleNamespace(
        is_processing=False,
        conversation_history=[],
        profile_manager=SimpleNamespace(
            get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
        ),
        submit_human_input=AsyncMock(),
    )
    bus = EventBus()
    bus.register_service("llm_service", llm)
    bus.register_service("acp_orchestrator", controller)
    hub = HubPlugin(event_bus=bus)
    hub._identity = AgentRuntime(
        name="receiver", identity="receiver", project="/tmp/none", state="ready"
    )
    hub._task_ledger = None
    hub._nudge_engine = None
    hub._vault = MagicMock()
    hub._display_hub_message = MagicMock()
    hub._bridge = object()
    hub._bridge_forward = AsyncMock()
    return hub


@pytest.mark.asyncio
async def test_a_refused_message_does_not_repeat_its_receiver_effects_on_retransmission():
    controller = _RefusingController()
    hub = _acp_hub(controller)
    message = HubMessage(
        action="message", from_agent="peer-id-1", from_identity="peer-1",
        to="*", content="PEER_OBSERVATION",
    )

    assert await hub._on_message_received(message) is False
    assert await hub._on_message_received(message) is False
    controller.accept = True
    assert await hub._on_message_received(message) is not False

    # Admission was tried each time, but the message was shown, logged to the
    # vault and mirrored to the bridge once.
    assert controller.calls == 3
    assert hub._display_hub_message.call_count == 1
    assert hub._vault.append_stream.call_count == 1
    assert hub._bridge_forward.await_count == 1
    # Once admitted it is an ordinary duplicate again.
    assert await hub._on_message_received(message) is not False
    assert controller.calls == 3


@pytest.mark.asyncio
async def test_a_refused_message_is_not_acknowledged_after_the_acp_session_ends():
    controller = _RefusingController()
    hub = _acp_hub(controller)
    message = HubMessage(
        action="message", from_agent="peer-id-1", from_identity="peer-1",
        to="*", content="PEER_OBSERVATION",
    )

    assert await hub._on_message_received(message) is False
    # The session ends before the retransmission. Nothing can admit the message,
    # so the sender must not be told it was delivered.
    controller.active = False
    assert await hub._on_message_received(message) is False
    # A session that admits it later acknowledges it.
    controller.active = True
    controller.accept = True
    assert await hub._on_message_received(message) is not False


def _bridge(plugin):
    from plugins.hub.relay_agent import RelayAgentBridge

    bridge = RelayAgentBridge.__new__(RelayAgentBridge)
    plugin._identity = SimpleNamespace(identity="receiver", agent_id="agent-1")
    bridge.plugin = plugin
    bridge.store = MagicMock()
    bridge._state = lambda: SimpleNamespace(
        state=SimpleNamespace(trust="open", peer_trust={}, peer_devices={})
    )
    return bridge


@pytest.mark.asyncio
async def test_a_refused_network_message_stays_queued_for_the_next_tick():
    plugin = SimpleNamespace(_on_message_received=AsyncMock(return_value=False))
    bridge = _bridge(plugin)
    record = {
        "id": "rec-1",
        "peer": "peer-key",
        "payload": {
            "id": "m-1", "from": "peer-key", "from_identity": "remote",
            "from_device": "box", "content": "PEER_OBSERVATION",
            "thread_id": "t-1", "reply_to": "r-1",
        },
    }

    await bridge._deliver_open_message(record)

    bridge.store.transition.assert_not_called()


@pytest.mark.asyncio
async def test_a_refused_deferred_message_is_not_dropped_after_its_ack():
    refused = HubMessage(
        action="message", from_agent="peer-id-1", from_identity="peer-1",
        to="*", content="PEER_OBSERVATION",
    )
    plugin = SimpleNamespace(
        _on_message_received=AsyncMock(return_value=False),
        settle_network_turn=AsyncMock(),
        event_bus=SimpleNamespace(
            get_service=lambda name: SimpleNamespace(
                is_processing=False, _message_handler=None
            )
        ),
    )
    bridge = _bridge(plugin)
    bridge.active = None
    bridge._human_until = 0.0
    bridge._pending_remote_cancellation = None
    bridge._local_pending = deque([refused], maxlen=64)
    bridge._consume_acp_returns = AsyncMock()
    bridge._wake_pending_events = AsyncMock()
    bridge._release_remote_cancellation_if_idle = lambda llm: None

    await bridge._tick()

    assert list(bridge._local_pending) == [refused]


# --- A long outage reaches the bounded replay (#581 review round 2) ----------


class _StopLoop(Exception):
    pass


def _mailbox_plugin(refusals, injected=None):
    """A plugin whose receiver refuses every message, in ACP dispatch mode."""
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(agent_id="agent-1", identity="receiver")
    plugin.config = None
    llm = SimpleNamespace()
    if injected is not None:

        async def inject_system_message(block, subtype=""):
            injected.append(subtype)

        llm.inject_system_message = inject_system_message
    plugin.event_bus = SimpleNamespace(get_service=lambda name: llm)
    plugin._agent_dispatch_mode = lambda service: "acp"

    async def receive(message):
        refusals.append(message.id)
        return False

    plugin._on_message_received = receive
    return plugin


async def _run_one_mailbox_poll(plugin):
    """Run the production poll loop for exactly one tick."""
    ticks = []

    async def fake_sleep(seconds):
        ticks.append(seconds)
        if len(ticks) > 1:
            raise asyncio.CancelledError

    with patch("plugins.hub.plugin.asyncio.sleep", fake_sleep):
        await plugin._mailbox_loop()


async def _fill_mailbox(count):
    sent = []
    for number in range(count):
        message = HubMessage(
            action="message", from_agent="peer-id-1", from_identity="peer-1",
            to="*", content=f"NOTE_{number}", timestamp=time.time() + number / 1000,
        )
        await messenger_module.AgentMessenger.send_to_file("receiver", message)
        sent.append(message.id)
    return sent


@pytest.mark.asyncio
@pytest.mark.parametrize("with_inject", [True, False])
async def test_a_long_outage_poll_offers_every_refused_message_again(
    tmp_path, with_inject
):
    # More waiting files than one bounded replay (INBOX_MAX_REPLAY) carries.
    # The poll deleted every file and coalesced or dropped the overflow, so a
    # refused observation lost its owner while its sender had been told it was
    # queued.
    refusals, injected = [], []
    plugin = _mailbox_plugin(refusals, injected if with_inject else None)
    with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
        sent = await _fill_mailbox(messenger_module.INBOX_MAX_REPLAY + 5)

        await _run_one_mailbox_poll(plugin)

        assert sorted(refusals) == sorted(sent)
        assert injected == []
        assert messenger_module.AgentMessenger.get_inbox_count("receiver") == len(sent)


@pytest.mark.asyncio
async def test_a_refused_message_in_a_summarised_replay_stays_queued(tmp_path):
    # A replay that carries a summary (pre-restart drops) with no way to inject
    # falls back to per-message delivery; that loop ignored the refusal.
    refusals = []
    plugin = _mailbox_plugin(refusals)
    message = HubMessage(
        action="message", from_agent="peer-id-1", from_identity="peer-1",
        to="*", content="PEER_OBSERVATION",
    )
    summary = HubMessage(
        action="inbox_summary", from_identity="hub", content="[offline inbox]",
        timestamp=0.0, metadata={"total": 1},
    )
    plugin.event_bus = SimpleNamespace(get_service=lambda name: None)
    with patch("plugins.hub.messenger.get_messages_dir", return_value=tmp_path):
        await plugin._deliver_inbox_batch([summary, message])

        assert refusals == [message.id]
        assert messenger_module.AgentMessenger.get_inbox_count("receiver") == 1
