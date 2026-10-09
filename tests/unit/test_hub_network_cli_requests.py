"""Slice D (CLI) of the agent network: docs/specs/agent-network-simple-flow.md.

`kollab --hub status` and `kollab --hub msg agent@device text` talk to one
local agent's daemon over its socket (new `network_status`/`network_send`
frames in plugins/hub/messenger.py). These tests exercise the daemon side
directly -- the plugin handlers wired as AgentSocketServer.on_network_status
/on_network_send -- and the CLI-waiter feed in _on_message_received.
`network_send` streams: one `network_reply` frame per message on the request's
thread, then a terminal frame once the far agent's turn ends. Slice A owns the
real relay bridge (plugins/hub/relay_agent.py); everything here mocks its
documented interface (device_name, trust_level, remote_agents, resolve_handle)
the same way test_hub_network_surface.py does, rather than importing it. The
answering side (turn binding, end-of-turn frame) is in
test_hub_network_turns.py.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_events import EventType
from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin, _network_display_text
from plugins.hub.relay_state import RelayError

HANDLE = "infra@home-server"
DONE = "The receiving agent finished this request."


def _make_plugin(*, relay_agent=None) -> HubPlugin:
    """A minimal HubPlugin, same stubbing pattern as test_hub_content_dedup.py."""
    bus = MagicMock()
    bus.emit_with_hooks = AsyncMock()
    plugin = HubPlugin(event_bus=bus)
    plugin._vault = None
    plugin._task_ledger = None
    plugin._nudge_engine = None
    plugin._identity = SimpleNamespace(
        identity="lapis", agent_id="lapis-1", state="idle"
    )
    plugin._election = None
    plugin._roster = []
    plugin._announce_presence = AsyncMock()
    plugin._relay_agent = relay_agent
    plugin._start_relay_agent = AsyncMock()
    return plugin


async def _frames(plugin: HubPlugin, to: str, content: str, wait: float) -> list:
    return [f async for f in plugin._handle_network_send_request(to, content, wait)]


def _bridge() -> SimpleNamespace:
    """The parts of the relay bridge a message on its way through the plugin touches."""
    return SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: None),
        _injecting_message=None,
        defer_local=AsyncMock(return_value=False),
        resolve_handle=lambda handle: f"relay:resolved:{handle}",
        send=None,
    )


def _reply(thread_id: str, content: str) -> HubMessage:
    """A message from the far agent on a request's thread, as the relay delivers it."""
    return HubMessage(
        action="message",
        from_agent="infra-1",
        from_identity=HANDLE,
        to="lapis",
        content=content,
        thread_id=thread_id,
        reply_to="a" * 32,
    )


def _end(plugin: HubPlugin, thread_id: str, replies: int, *, failed: bool = False):
    """The far runtime's end-of-turn frame, as the relay bridge hands it over."""
    plugin.on_network_turn_end(
        thread_id,
        {"replies": replies, "failed": failed},
        "The receiving agent could not complete this request." if failed else DONE,
    )


# --------------------------------------------------------------------- #
# network_status
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_network_status_returns_device_trust_and_remote_rows():
    rows = [{"handle": "infra@home-server", "state": "idle", "online": True}]
    relay = SimpleNamespace(
        device_name=lambda: "laptop-kollab",
        trust_level=lambda: "open",
        network_name=lambda: "laptop-kollab-net",
        _state=lambda: SimpleNamespace(state=SimpleNamespace(origin="https://kollabor.ai")),
        remote_agents=AsyncMock(return_value=rows),
        _owner_call=AsyncMock(return_value={"state": "online"}),
    )
    plugin = _make_plugin(relay_agent=relay)

    result = await plugin._handle_network_status_request()

    # The network's own name, apart from this device's.
    assert result == {
        "network": "laptop-kollab-net  via kollabor.ai",
        "device": "laptop-kollab",
        "trust": "open",
        "agents": rows,
    }
    plugin._start_relay_agent.assert_not_awaited()

    # While the relay is down the directory is the cached one: no rows, as in
    # the web UI, never agents that only look online.
    relay._owner_call = AsyncMock(return_value={"state": "connecting"})
    offline = await plugin._handle_network_status_request()
    assert offline["agents"] == [] and offline["device"] == "laptop-kollab"


@pytest.mark.asyncio
async def test_network_status_without_a_relay_agent_is_empty_not_an_error():
    plugin = _make_plugin(relay_agent=None)

    result = await plugin._handle_network_status_request()

    assert result == {"network": "", "device": "", "trust": "", "agents": []}
    plugin._start_relay_agent.assert_awaited_once()


# --------------------------------------------------------------------- #
# network_send: a stream of frames
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_network_send_streams_every_reply_then_done_when_the_far_turn_ends():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    async def _far_side() -> None:
        await asyncio.sleep(0.01)
        thread = next(iter(plugin._cli_waiters))
        await plugin._on_message_received(_reply(thread, "on it"))
        await plugin._on_message_received(_reply(thread, "handshake ok"))
        _end(plugin, thread, 2)

    far = asyncio.create_task(_far_side())
    frames = await _frames(plugin, HANDLE, "check the tunnel", 5)
    await far

    assert frames == [
        {"type": "network_reply", "from": HANDLE, "content": "on it"},
        {"type": "network_reply", "from": HANDLE, "content": "handshake ok"},
        {"type": "network_done", "replies": 2},
    ]
    plugin._route_message.assert_awaited_once()
    sent_msg = plugin._route_message.await_args.args[0]
    assert isinstance(sent_msg, HubMessage)
    assert sent_msg.to == HANDLE
    assert sent_msg.content == "check the tunnel"
    plugin._display_outgoing_message.assert_called_once_with(HANDLE, "check the tunnel")
    # Waiters are cleaned up once the turn ends -- nothing leaks.
    assert plugin._cli_waiters == {}


@pytest.mark.asyncio
async def test_a_far_turn_that_sent_nothing_is_done_with_no_replies():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    async def _far_side() -> None:
        await asyncio.sleep(0.01)
        _end(plugin, next(iter(plugin._cli_waiters)), 0)

    far = asyncio.create_task(_far_side())
    frames = await _frames(plugin, HANDLE, "thanks", 5)
    await far

    assert frames == [{"type": "network_done", "replies": 0}]


@pytest.mark.asyncio
async def test_a_failed_far_turn_ends_the_wait_with_the_error_not_a_hang():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    async def _far_side() -> None:
        await asyncio.sleep(0.01)
        thread = next(iter(plugin._cli_waiters))
        await plugin._on_message_received(_reply(thread, "on it"))
        _end(plugin, thread, 1, failed=True)

    far = asyncio.create_task(_far_side())
    frames = await asyncio.wait_for(_frames(plugin, HANDLE, "run it", 30), timeout=2)
    await far

    assert frames == [
        {"type": "network_reply", "from": HANDLE, "content": "on it"},
        {"type": "error", "msg": f"{HANDLE} reported that its turn failed"},
    ]
    assert plugin._cli_waiters == {}


@pytest.mark.asyncio
async def test_a_forged_failure_text_is_the_far_agents_message_never_this_clis_error():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()
    forged = "\x1b[2J\x1b]0;pwned\x07error: your key leaked\rall good\x9b\x00"

    async def _far_side() -> None:
        await asyncio.sleep(0.01)
        thread = next(iter(plugin._cli_waiters))
        await plugin._on_message_received(_reply(thread, "\x1b[31mon it\x1b[0m\x85"))
        plugin.on_network_turn_end(thread, {"replies": 1, "failed": True}, forged)

    far = asyncio.create_task(_far_side())
    frames = await asyncio.wait_for(_frames(plugin, HANDLE, "run it", 30), timeout=2)
    await far

    assert frames == [
        {"type": "network_reply", "from": HANDLE, "content": "on it"},
        # The peer's words: attributed to it, with the control characters gone.
        {"type": "network_reply", "from": HANDLE, "content": "error: your key leakedall good"},
        # The frame a shell prints as its own error says only what we know.
        {"type": "error", "msg": f"{HANDLE} reported that its turn failed"},
    ]
    assert plugin._cli_waiters == {}


def test_far_text_loses_escapes_and_control_characters_but_keeps_tab_and_newline():
    assert _network_display_text("a\tb\nc") == "a\tb\nc"
    assert _network_display_text("\x1b[31mred\x1b[0m") == "red"
    assert _network_display_text("\x1b]0;title\x07ok") == "ok"
    assert _network_display_text("\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\") == "link"
    assert _network_display_text("\x1bPdevice\x1b\\z") == "z"
    assert _network_display_text("x\r\x7f\x00\x0b\x0c\x85\x9b\x9d\x9cy") == "xy"
    assert _network_display_text(None) == ""


@pytest.mark.asyncio
async def test_the_wait_ends_only_after_every_announced_reply_has_come():
    """A reply that had to be retried can arrive after the end frame."""
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    async def _far_side() -> None:
        await asyncio.sleep(0.01)
        thread = next(iter(plugin._cli_waiters))
        await plugin._on_message_received(_reply(thread, "on it"))
        _end(plugin, thread, 2)  # announces two replies; only one has come
        await asyncio.sleep(0.05)
        await plugin._on_message_received(_reply(thread, "the answer"))

    far = asyncio.create_task(_far_side())
    frames = await _frames(plugin, HANDLE, "run it", 5)
    await far

    assert [f["type"] for f in frames] == [
        "network_reply",
        "network_reply",
        "network_done",
    ]
    assert frames[1]["content"] == "the answer"


@pytest.mark.asyncio
async def test_network_send_times_out_and_cleans_up_waiters():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    frames = await _frames(plugin, HANDLE, "ping", 0.05)

    assert frames == [{"type": "network_timeout", "replies": 0}]
    assert plugin._cli_waiters == {}


@pytest.mark.asyncio
async def test_a_timeout_after_replies_says_how_many_came():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    async def _far_side() -> None:
        await asyncio.sleep(0.01)
        await plugin._on_message_received(
            _reply(next(iter(plugin._cli_waiters)), "on it")
        )

    far = asyncio.create_task(_far_side())
    frames = await _frames(plugin, HANDLE, "ping", 0.1)
    await far

    assert [f["type"] for f in frames] == ["network_reply", "network_timeout"]
    assert frames[-1]["replies"] == 1


@pytest.mark.asyncio
async def test_network_send_no_wait_returns_immediately_without_waiting():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    frames = await _frames(plugin, HANDLE, "ping", 0)

    assert frames == [{"type": "network_sent", "to": HANDLE}]
    assert plugin._cli_waiters == {}


@pytest.mark.asyncio
async def test_network_send_reports_a_relay_error_from_resolve_handle():
    def raise_unknown(handle):
        raise RelayError(
            "unknown agent@device: run /connect status to see who is online"
        )

    relay = SimpleNamespace(resolve_handle=raise_unknown)
    plugin = _make_plugin(relay_agent=relay)
    plugin._route_message = AsyncMock(
        side_effect=AssertionError("must not route when resolve_handle fails")
    )

    frames = await _frames(plugin, "ghost@nowhere", "hi", 5)

    assert frames == [
        {
            "type": "error",
            "msg": "unknown agent@device: run /connect status to see who is online",
        }
    ]
    plugin._route_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_network_send_rejects_a_target_that_is_not_a_handle():
    plugin = _make_plugin(relay_agent=SimpleNamespace(resolve_handle=lambda h: h))

    [frame] = await _frames(plugin, "lapis", "hi", 5)

    assert frame["type"] == "error"
    assert "agent@device" in frame["msg"]


@pytest.mark.asyncio
async def test_network_send_without_a_relay_agent_is_a_clean_error():
    plugin = _make_plugin(relay_agent=None)

    frames = await _frames(plugin, HANDLE, "hi", 5)

    assert frames == [
        {
            "type": "error",
            "msg": "network messaging is not available on this build",
        }
    ]


@pytest.mark.asyncio
async def test_network_send_surfaces_a_routing_rejection():
    plugin = _make_plugin(relay_agent=_bridge())
    plugin._route_message = AsyncMock(return_value=[(HANDLE, "remote task rejected")])
    plugin._display_outgoing_message = MagicMock()

    frames = await _frames(plugin, HANDLE, "ping", 5)

    assert frames == [{"type": "error", "msg": "remote task rejected"}]
    assert plugin._cli_waiters == {}


# --------------------------------------------------------------------- #
# _on_message_received feeds a pending CLI waiter (hook right after the
# msg_id dedup check)
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_on_message_received_hands_the_waiter_every_reply_on_its_thread():
    plugin = _make_plugin(relay_agent=None)
    events: asyncio.Queue = asyncio.Queue()
    plugin._cli_waiters["thread-xyz"] = events

    await plugin._on_message_received(_reply("thread-xyz", "on it"))
    await plugin._on_message_received(_reply("thread-xyz", "handshake ok"))

    assert events.get_nowait() == ("reply", HANDLE, "on it")
    assert events.get_nowait() == ("reply", HANDLE, "handshake ok")
    # The waiter stays until the far turn ends; only the wait itself removes it.
    assert plugin._cli_waiters == {"thread-xyz": events}


@pytest.mark.asyncio
async def test_a_message_from_the_agent_on_another_thread_is_not_the_reply():
    plugin = _make_plugin(relay_agent=None)
    events: asyncio.Queue = asyncio.Queue()
    plugin._cli_waiters["thread-xyz"] = events

    other = HubMessage(
        action="message",
        from_agent="infra-1",
        from_identity=HANDLE,
        to="lapis",
        content="an older answer",
    )
    await plugin._on_message_received(other)

    assert events.empty()
    assert plugin._cli_waiters == {"thread-xyz": events}


def _idle_llm() -> SimpleNamespace:
    return SimpleNamespace(is_processing=False, conversation_history=[])


@pytest.mark.asyncio
async def test_a_reply_to_a_shell_request_does_not_wake_the_model():
    """The shell has the reply. A model turn on it would spend a turn nobody
    asked for, and the relay delivers the next frame only once the model is idle,
    so the next reply (and the end of the wait) would sit behind that turn."""
    plugin = _make_plugin(relay_agent=None)
    plugin._display_hub_message = MagicMock()
    llm = _idle_llm()
    plugin.event_bus.get_service.side_effect = (
        lambda name: llm if name == "llm_service" else None
    )
    plugin._cli_waiters["thread-xyz"] = asyncio.Queue()

    await plugin._on_message_received(_reply("thread-xyz", "handshake ok"))

    plugin._display_hub_message.assert_called_once()  # still shown on this screen
    assert llm.conversation_history == []
    triggers = [
        c
        for c in plugin.event_bus.emit_with_hooks.await_args_list
        if c.args[0] is EventType.TRIGGER_LLM_CONTINUE
    ]
    assert triggers == []

    # The same message on a thread nobody waits on still wakes the agent.
    await plugin._on_message_received(_reply("thread-abc", "unrelated"))
    assert len(llm.conversation_history) == 1
    triggers = [
        c
        for c in plugin.event_bus.emit_with_hooks.await_args_list
        if c.args[0] is EventType.TRIGGER_LLM_CONTINUE
    ]
    assert len(triggers) == 1


def test_an_end_frame_with_no_waiter_is_ignored():
    plugin = _make_plugin(relay_agent=None)

    _end(plugin, "b" * 32, 3)  # an agent asked, not a shell: nothing waits

    assert plugin._cli_waiters == {}


# --------------------------------------------------------------------- #
# Overlapping requests to one agent: each waiter gets only its own thread
# (the proof run printed request 1's answer for request 2).
# --------------------------------------------------------------------- #


async def _start_requests(plugin, *texts):
    """Start one CLI wait per text on HANDLE; return ([task...], [thread...])."""
    threads: list[str] = []

    async def route(msg):
        threads.append(msg.thread_id)
        return []

    plugin._route_message = route
    plugin._display_outgoing_message = MagicMock()
    tasks = []
    for text in texts:
        tasks.append(asyncio.create_task(_frames(plugin, HANDLE, text, 5)))
        await asyncio.sleep(0.01)
    assert len(set(threads)) == len(texts)
    return tasks, threads


@pytest.mark.asyncio
async def test_overlapping_requests_each_get_their_own_answer_in_any_order():
    plugin = _make_plugin(relay_agent=_bridge())
    (first, second), (thread1, thread2) = await _start_requests(
        plugin, "first question", "second question"
    )

    # The answer to the newer request comes first, with its own end.
    await plugin._on_message_received(_reply(thread2, "answer two"))
    _end(plugin, thread2, 1)
    assert not first.done()
    assert [f.get("content") for f in await second] == ["answer two", None]

    await plugin._on_message_received(_reply(thread1, "answer one"))
    _end(plugin, thread1, 1)
    assert [f.get("content") for f in await first] == ["answer one", None]
    assert plugin._cli_waiters == {}


@pytest.mark.asyncio
async def test_three_waiters_on_one_agent_each_get_their_own_answers_interleaved():
    plugin = _make_plugin(relay_agent=_bridge())
    tasks, threads = await _start_requests(plugin, "one", "two", "three")

    # Frames from three turns arrive interleaved, out of request order.
    await plugin._on_message_received(_reply(threads[2], "three: on it"))
    await plugin._on_message_received(_reply(threads[0], "one: on it"))
    await plugin._on_message_received(_reply(threads[1], "two: the answer"))
    await plugin._on_message_received(_reply(threads[2], "three: the answer"))
    _end(plugin, threads[1], 1)
    await plugin._on_message_received(_reply(threads[0], "one: the answer"))
    _end(plugin, threads[2], 2)
    _end(plugin, threads[0], 2)

    results = [
        [f["content"] for f in await task if f["type"] == "network_reply"]
        for task in tasks
    ]
    assert results == [
        ["one: on it", "one: the answer"],
        ["two: the answer"],
        ["three: on it", "three: the answer"],
    ]
    assert plugin._cli_waiters == {}


@pytest.mark.asyncio
async def test_a_late_answer_to_an_older_request_never_answers_a_newer_one():
    plugin = _make_plugin(relay_agent=_bridge())
    threads: list[str] = []

    async def route(msg):
        threads.append(msg.thread_id)
        return []

    plugin._route_message = route
    plugin._display_outgoing_message = MagicMock()

    # Request 1 is answered and finished.
    first = asyncio.create_task(_frames(plugin, HANDLE, "one", 5))
    await asyncio.sleep(0.01)
    await plugin._on_message_received(_reply(threads[0], "answer one"))
    _end(plugin, threads[0], 1)
    assert [f.get("content") for f in await first] == ["answer one", None]

    # Request 2 is waiting when a duplicate answer to request 1 arrives.
    second = asyncio.create_task(_frames(plugin, HANDLE, "two", 5))
    await asyncio.sleep(0.01)
    await plugin._on_message_received(_reply(threads[0], "answer one again"))
    _end(plugin, threads[0], 2)
    await asyncio.sleep(0.01)
    assert not second.done()

    await plugin._on_message_received(_reply(threads[1], "answer two"))
    _end(plugin, threads[1], 1)
    assert [f.get("content") for f in await second] == ["answer two", None]


# --------------------------------------------------------------------- #
# Wire protocol: AgentSocketServer <-> AgentMessenger client helpers, over
# a real unix socket (messenger.py frame plumbing).
# --------------------------------------------------------------------- #


def test_network_status_frame_round_trips_over_a_real_socket():
    async def run():
        async def on_status():
            return {
                "network": "laptop-kollab-net  via kollabor.ai",
                "device": "laptop-kollab",
                "trust": "open",
                "agents": [
                    {"handle": "infra@home-server", "state": "idle", "online": True}
                ],
            }

        server = AgentSocketServer(
            "status-id",
            lambda _message: None,
            on_network_status=on_status,
            socket_name=f"net-status-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            result = await AgentMessenger.request_network_status(sock_path)
        finally:
            await server.stop()

        assert result == {
            "type": "network_status",
            "network": "laptop-kollab-net  via kollabor.ai",
            "device": "laptop-kollab",
            "trust": "open",
            "agents": [
                {"handle": "infra@home-server", "state": "idle", "online": True}
            ],
        }

    asyncio.run(run())


def test_network_status_with_no_handler_is_not_connected():
    async def run():
        server = AgentSocketServer(
            "status-none-id",
            lambda _message: None,
            socket_name=f"net-status-none-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_network_status(sock_path)
        finally:
            await server.stop()

    result = asyncio.run(run())
    assert result == {"type": "network_status", "network": "", "device": "", "trust": "", "agents": []}


def test_network_send_streams_replies_to_the_client_then_returns_the_terminal_frame():
    async def run():
        seen = {}
        closed = []

        async def on_send(to, content, wait_seconds):
            seen.update(to=to, content=content, wait_seconds=wait_seconds)
            try:
                yield {"type": "network_reply", "from": to, "content": "on it"}
                # A pause between frames: the client must hand over the first
                # one before the second exists, not batch them at the end.
                await asyncio.sleep(0.05)
                yield {"type": "network_reply", "from": to, "content": "handshake ok"}
                yield {"type": "network_done", "replies": 2}
            finally:
                closed.append(True)

        server = AgentSocketServer(
            "send-id",
            lambda _message: None,
            on_network_send=on_send,
            socket_name=f"net-send-{os.getpid()}",
        )
        sock_path = await server.start()
        replies: list[dict] = []
        stamps: list[float] = []
        loop = asyncio.get_running_loop()

        def on_reply(frame):
            replies.append(frame)
            stamps.append(loop.time())

        try:
            result = await AgentMessenger.request_network_send(
                sock_path,
                "infra@home-server",
                "check the tunnel",
                wait_seconds=5,
                on_reply=on_reply,
            )
        finally:
            await server.stop()

        assert result == {"type": "network_done", "replies": 2}
        assert [r["content"] for r in replies] == ["on it", "handshake ok"]
        assert stamps[1] - stamps[0] >= 0.04  # arrived as sent, not batched
        assert seen == {
            "to": "infra@home-server",
            "content": "check the tunnel",
            "wait_seconds": 5,
        }
        assert closed == [True]

    asyncio.run(run())


def test_network_send_with_a_single_frame_handler_still_round_trips():
    async def run():
        async def on_send(to, content, wait_seconds):
            return {"type": "network_sent", "to": to}

        server = AgentSocketServer(
            "send-one-id",
            lambda _message: None,
            on_network_send=on_send,
            socket_name=f"net-send-one-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_network_send(
                sock_path, "infra@home-server", "hi", wait_seconds=0
            )
        finally:
            await server.stop()

    assert asyncio.run(run()) == {"type": "network_sent", "to": "infra@home-server"}


def test_a_handler_that_fails_midway_ends_the_stream_with_an_error_frame():
    async def run():
        async def on_send(to, content, wait_seconds):
            yield {"type": "network_reply", "from": to, "content": "on it"}
            raise RuntimeError("boom with /secret/path")

        server = AgentSocketServer(
            "send-fail-id",
            lambda _message: None,
            on_network_send=on_send,
            socket_name=f"net-send-fail-{os.getpid()}",
        )
        sock_path = await server.start()
        replies: list[dict] = []
        try:
            result = await AgentMessenger.request_network_send(
                sock_path,
                "infra@home-server",
                "hi",
                wait_seconds=5,
                on_reply=replies.append,
            )
        finally:
            await server.stop()
        return replies, result

    replies, result = asyncio.run(run())
    assert [r["content"] for r in replies] == ["on it"]
    assert result == {"type": "error", "msg": "network send failed"}  # no exception text


def test_network_send_with_no_handler_is_a_clean_error():
    async def run():
        server = AgentSocketServer(
            "send-none-id",
            lambda _message: None,
            socket_name=f"net-send-none-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_network_send(
                sock_path, "infra@home-server", "hi", wait_seconds=1
            )
        finally:
            await server.stop()

    result = asyncio.run(run())
    assert result == {
        "type": "error",
        "msg": "network messaging is not available on this build",
    }


@pytest.mark.asyncio
async def test_a_request_this_daemon_does_not_know_is_answered_at_once():
    """A newer kollab asking an older daemon gets an answer, not a wait to its timeout."""
    server = AgentSocketServer("lapis-1", AsyncMock(), socket_name=f"unknown-{os.getpid()}")
    sock = await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(b'{"action": "from_the_future"}\n')
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        writer.close()
    finally:
        await server.stop()
    assert json.loads(line) == {
        "type": "error",
        "msg": "this agent does not know 'from_the_future': restart it on the current kollab",
    }
