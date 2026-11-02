"""Attach streams must not die when the thread executor hiccups.

Regression: _stream_to_attacher's heartbeat path ran sub_queue.get via
asyncio.to_thread. If the default executor raised (e.g. torn down while an
owning process is shutting down), the exception escaped _send_events and the
whole attach stream -- display events included -- terminated, even though the
attached agent process was still alive. During the P0-c incident this turned
a transient executor failure into a dropped operator session.

Now: executor failures retry with backoff; heartbeat and event writes catch
connection errors and exit cleanly; CancelledError still propagates.
"""

import asyncio
import json
import os
import secrets
import threading
from pathlib import Path

import pytest

from kollabor_tui.display_tap import DisplayTap
from plugins.hub.messenger import AgentSocketServer


@pytest.fixture
def socket_dir(monkeypatch):
    # /tmp base: unix socket paths are length-limited (AF_UNIX), so tmp_path
    # under pytest's long cache dirs is too deep. Matches existing hub tests.
    d = Path("/tmp") / f"kollab-attach-test-{secrets.token_hex(4)}"
    d.mkdir(mode=0o700)
    monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: d)
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def _server(tap) -> AgentSocketServer:
    server = AgentSocketServer(
        agent_id="attach-survival-test",
        on_message=None,
        socket_name="attach-survival-" + os.urandom(4).hex(),
    )
    server._display_tap = tap
    return server


class FlakyThenRealTap(DisplayTap):
    """DisplayTap whose first N queue reads explode (executor death)."""

    def __init__(self, fail_first=2, **kwargs):
        super().__init__(**kwargs)
        self.fail_first = fail_first
        self.failures = 0


def _install_executor_faults(monkeypatch, fail_first=2):
    """Make asyncio.to_thread raise for the first N calls to _get_event.

    Simulates the default thread executor dying mid-attach (what happens
    when an owning process tears down while daemons keep running).
    """

    real_to_thread = asyncio.to_thread
    state = {"failures": 0}

    async def flaky_to_thread(func, *args, **kwargs):
        if (
            getattr(func, "__name__", "") == "_get_event"
            and state["failures"] < fail_first
        ):
            state["failures"] += 1
            raise RuntimeError("simulated executor shutdown")
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("plugins.hub.messenger.asyncio.to_thread", flaky_to_thread)
    return state


async def _read_ack(reader):
    line = await asyncio.wait_for(reader.readline(), 5)
    return json.loads(line.decode())


async def _skip_snapshot(reader, per_line_timeout=12):
    """Read until the first heartbeat, draining any snapshot lines.

    12s: the post-fault path adds 2 x 0.5s backoff plus one 5s queue-get
    before the heartbeat, so the first beat can legitimately take ~6s.
    """
    while True:
        line = await asyncio.wait_for(reader.readline(), per_line_timeout)
        msg = json.loads(line.decode())
        if msg.get("type") == "heartbeat":
            return


@pytest.mark.asyncio
async def test_attach_stream_survives_executor_death(socket_dir, monkeypatch):
    """Executor exceptions on the heartbeat path must not end the stream."""
    state = _install_executor_faults(monkeypatch, fail_first=2)
    tap = FlakyThenRealTap(history_size=10)
    server = _server(tap)
    path = await server.start()
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        writer.write(
            (
                json.dumps(
                    {"action": "attach", "mode": "interactive", "client_id": "t1"}
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        ack = await _read_ack(reader)
        assert ack["type"] == "attach_ack"

        # Two simulated executor explosions happen here; the stream must
        # recover and deliver a heartbeat afterwards (retry, not teardown).
        await _skip_snapshot(reader)

        # A real event published after recovery must still arrive.
        tap.publish({"type": "message", "text": "still-alive"})
        got_event = False
        for _ in range(20):
            line = await asyncio.wait_for(reader.readline(), 5)
            msg = json.loads(line.decode())
            if msg.get("type") == "message" and msg.get("text") == "still-alive":
                got_event = True
                break
        assert got_event, "attach stream did not survive executor death"
        assert state["failures"] == 2
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await server.stop()


@pytest.mark.asyncio
async def test_attach_stream_ends_cleanly_on_client_disconnect(socket_dir):
    """A dead client still terminates the stream (write error path)."""
    tap = DisplayTap(history_size=10)
    server = _server(tap)
    path = await server.start()
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(
        (
            json.dumps({"action": "attach", "mode": "interactive", "client_id": "t2"})
            + "\n"
        ).encode()
    )
    await writer.drain()
    ack = await _read_ack(reader)
    assert ack["type"] == "attach_ack"

    # Close the client hard, then make sure the server drains the dead
    # connection and stops the stream task without hanging.
    writer.close()
    tap.publish({"type": "message", "text": "after-close"})
    # Wait for the write failure to be noticed and the stream to end.
    async with asyncio.timeout(10):
        while server._display_tap.subscriber_count:
            await asyncio.sleep(0.1)
    assert server._display_tap.subscriber_count == 0


@pytest.mark.asyncio
async def test_disconnect_wakes_the_blocked_display_queue_reader(socket_dir):
    """Ending an attach must release its thread without waiting for the heartbeat."""
    entered, exited = threading.Event(), threading.Event()
    tap = DisplayTap(history_size=10)
    subscribe = tap.subscribe

    def tracked_subscribe(client_id):
        sub_queue = subscribe(client_id)
        read = sub_queue.get

        def tracked_read(*args, **kwargs):
            entered.set()
            try:
                return read(*args, **kwargs)
            finally:
                exited.set()

        sub_queue.get = tracked_read
        return sub_queue

    tap.subscribe = tracked_subscribe
    server = _server(tap)
    path = await server.start()
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        writer.write(b'{"action":"attach","mode":"interactive","client_id":"wake-test"}\n')
        await writer.drain()
        assert (await _read_ack(reader))["type"] == "attach_ack"
        assert await asyncio.to_thread(entered.wait, 2)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()

    assert await asyncio.to_thread(exited.wait, 3)


@pytest.mark.asyncio
async def test_disconnecting_one_attacher_keeps_the_other_stream_live(socket_dir):
    tap = DisplayTap(history_size=10)
    server = _server(tap)
    path = await server.start()
    first_reader, first_writer = await asyncio.open_unix_connection(path)
    second_reader, second_writer = await asyncio.open_unix_connection(path)
    try:
        for client_id, reader, writer in (
            ("first", first_reader, first_writer),
            ("second", second_reader, second_writer),
        ):
            request = json.dumps({"action": "attach", "client_id": client_id}) + "\n"
            writer.write(request.encode())
            await writer.drain()
            assert (await _read_ack(reader))["type"] == "attach_ack"

        first_writer.close()
        await first_writer.wait_closed()
        async with asyncio.timeout(5):
            while tap.subscriber_count != 1:
                await asyncio.sleep(0.01)
        tap.publish({"type": "message", "text": "still connected"})
        event = json.loads(await asyncio.wait_for(second_reader.readline(), 5))
        assert event["type"] == "message"
        assert event["text"] == "still connected"
    finally:
        first_writer.close()
        second_writer.close()
        await first_writer.wait_closed()
        await second_writer.wait_closed()
        await server.stop()


@pytest.mark.asyncio
async def test_attach_cancelled_error_still_propagates(socket_dir):
    """Cancellation must still tear the stream down (no swallow)."""
    tap = DisplayTap(history_size=10)
    server = _server(tap)
    path = await server.start()
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        writer.write(
            (
                json.dumps(
                    {"action": "attach", "mode": "interactive", "client_id": "t3"}
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        await _read_ack(reader)
        # Find the streaming task and cancel it directly.
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert tasks
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert any(isinstance(r, asyncio.CancelledError) for r in results)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await server.stop()
