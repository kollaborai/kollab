"""In-flight output must stop at the documented trust-withdrawal boundary."""

import asyncio
import contextlib
import time

import pytest

from plugins.hub import network_attach as na
from plugins.hub.relay_client import SEND_BURST, RelayClient

MAC = "a" * 64
SERVER = "b" * 64
CHANNEL = "0" * 32
SYNTHETIC_OUTPUT = b"synthetic private agent output\n"


class RecordingWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = asyncio.Event()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed.set()


class Pair:
    def __init__(self, request):
        self.allowed = True
        self.authorized = asyncio.Event()
        self.sink = RecordingWriter()
        self.stream = asyncio.StreamReader()

        async def target(peer, agent_id, name):
            if not self.allowed:
                raise na.AttachRefused("trust withdrawn")
            self.authorized.set()
            return "/unused"

        async def unused(*args, **kwargs):
            raise AssertionError("requester did not send data")

        self.requester = na.NetworkAttach(
            request=unused, target=target, peer_name=lambda _: "device", notice=lambda _: None
        )
        self.device = na.NetworkAttach(
            request=request, target=target, peer_name=lambda _: "requester", notice=lambda _: None
        )
        self.requester_channel = na._Channel(
            id=CHANNEL, peer=SERVER, name="agent", requester=True, writer=self.sink
        )
        self.device_channel = na._Channel(
            id=CHANNEL, peer=MAC, name="agent", requester=False, agent_id="agent-1",
            reader=self.stream, writer=RecordingWriter(),
        )
        self.requester._channels[(SERVER, CHANNEL)] = self.requester_channel
        self.device._channels[(MAC, CHANNEL)] = self.device_channel

    def start_output(self):
        self.device_channel.pump = asyncio.create_task(self.device._pump(self.device_channel))
        self.stream.feed_data(SYNTHETIC_OUTPUT)
        self.stream.feed_eof()

    async def receive(self, peer, method, payload, timeout):
        assert peer == MAC
        return await self.requester.receive(SERVER, method, payload)

    async def finish(self):
        pump = self.device_channel.pump
        if pump is not None and not pump.done():
            pump.cancel()
        if pump is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await pump


@pytest.mark.asyncio
@pytest.mark.parametrize("forced_recheck", [False, True])
async def test_withdrawal_while_real_pacer_waits_does_not_send_output(monkeypatch, forced_recheck):
    monkeypatch.setattr(na, "FLUSH_SECONDS", 0)
    pair = None

    async def send(*args, **kwargs):
        return await pair.receive(*args, **kwargs)

    pair = Pair(send)
    assert isinstance(pair.device._pacer, na._Pacer)
    await pair.device._pacer.lock.acquire()
    held = True
    try:
        pair.start_output()
        await asyncio.wait_for(pair.authorized.wait(), 2)
        pair.allowed = False
        recheck = None
        if forced_recheck:
            recheck = asyncio.create_task(pair.device.recheck())
            for _ in range(100):
                if pair.device_channel.closed:
                    break
                await asyncio.sleep(0)
            assert pair.device_channel.closed
        pair.device._pacer.lock.release()
        held = False
        if recheck is not None:
            await asyncio.wait_for(recheck, 3)
        await asyncio.wait_for(pair.sink.closed.wait(), 3)
        assert pair.sink.data == b""
    finally:
        if held:
            pair.device._pacer.lock.release()
        await pair.finish()


@pytest.mark.asyncio
async def test_withdrawal_between_failed_send_and_retry_does_not_send_output(monkeypatch):
    monkeypatch.setattr(na, "FLUSH_SECONDS", 0)
    first_failed = asyncio.Event()
    attempts = 0
    pair = None

    async def send(peer, method, payload, timeout):
        nonlocal attempts
        if method == na.ATTACH_DATA:
            attempts += 1
            if attempts == 1:
                first_failed.set()
                raise na.RelayError("synthetic failure before delivery")
        return await pair.receive(peer, method, payload, timeout)

    pair = Pair(send)
    try:
        pair.start_output()
        await asyncio.wait_for(first_failed.wait(), 2)
        pair.allowed = False
        await asyncio.wait_for(pair.sink.closed.wait(), 3)
        assert pair.sink.data == b""
        assert attempts == 1
    finally:
        await pair.finish()


@pytest.mark.asyncio
async def test_forced_withdrawal_cancels_send_waiting_inside_transport(monkeypatch):
    monkeypatch.setattr(na, "FLUSH_SECONDS", 0)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()
    pair = None

    async def send(peer, method, payload, timeout):
        if method == na.ATTACH_DATA:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return await pair.receive(peer, method, payload, timeout)

    pair = Pair(send)
    try:
        pair.start_output()
        await asyncio.wait_for(started.wait(), 2)
        pair.allowed = False
        await asyncio.wait_for(pair.device.recheck(), 3)
        assert cancelled.is_set()
        release.set()
        await asyncio.wait_for(pair.sink.closed.wait(), 3)
        assert pair.sink.data == b""
    finally:
        release.set()
        await pair.finish()


@pytest.mark.asyncio
async def test_cancelled_recheck_does_not_detach_close_from_shutdown(monkeypatch):
    monkeypatch.setattr(na, "FLUSH_SECONDS", 0)
    pair = None

    async def send(*args, **kwargs):
        return await pair.receive(*args, **kwargs)

    pair = Pair(send)
    await pair.device._pacer.lock.acquire()
    held = True
    recheck = shutdown = None
    try:
        pair.start_output()
        await asyncio.wait_for(pair.authorized.wait(), 2)
        pair.allowed = False
        recheck = asyncio.create_task(pair.device.recheck())
        for _ in range(100):
            if pair.device_channel.closed:
                break
            await asyncio.sleep(0)
        assert pair.device_channel.closed
        recheck.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await recheck
        shutdown = asyncio.create_task(pair.device.close())
        await asyncio.sleep(0)
        assert not shutdown.done()
        pair.device._pacer.lock.release()
        held = False
        await asyncio.wait_for(shutdown, 3)
        assert pair.sink.data == b""
        assert pair.sink.closed.is_set()
    finally:
        if held:
            pair.device._pacer.lock.release()
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
        await pair.finish()


@pytest.mark.asyncio
async def test_relay_guard_runs_in_the_task_that_submits_the_frame():
    """A scheduled revoke cannot run between the final guard and send start."""
    allowed = True
    sent_while_allowed = []

    class Socket:
        closed = False

        async def send_str(self, raw):
            sent_while_allowed.append(allowed)

    client = RelayClient.__new__(RelayClient)
    client._ws = Socket()
    client._send_lock = asyncio.Lock()
    client._tokens = SEND_BURST
    client._token_time = time.monotonic()

    def revoke():
        nonlocal allowed
        allowed = False

    async def guard():
        if not allowed:
            raise na._Withdrawn("synthetic withdrawal")
        asyncio.get_running_loop().call_soon(revoke)

    await client._send_frame({"synthetic": "protected"}, before_send=guard)
    await asyncio.sleep(0)
    assert not allowed
    assert sent_while_allowed == [True]


@pytest.mark.asyncio
async def test_withdrawal_during_local_connect_refuses_the_pending_open(monkeypatch):
    allowed = True
    dialing = asyncio.Event()
    release = asyncio.Event()
    local_writer = RecordingWriter()

    async def target(peer, agent_id, name):
        if not allowed:
            raise na.AttachRefused("trust withdrawn")
        return "/synthetic-agent"

    async def connect(path, **kwargs):
        dialing.set()
        await release.wait()
        return asyncio.StreamReader(), local_writer

    async def send(*args, **kwargs):
        return {"closed": True}

    monkeypatch.setattr(na.local_ipc, "connect", connect)
    device = na.NetworkAttach(
        request=send, target=target, peer_name=lambda _: "requester", notice=lambda _: None
    )
    opening = asyncio.create_task(
        device.receive(MAC, na.ATTACH_OPEN, {
            "channel": CHANNEL, "agent_id": "agent-1", "name": "agent"
        })
    )
    try:
        await asyncio.wait_for(dialing.wait(), 2)
        allowed = False
        await device.recheck()
        release.set()
        result = await asyncio.wait_for(opening, 2)
        assert "refused" in result
        assert local_writer.closed.is_set()
        assert not device._channels
    finally:
        release.set()
        if not opening.done():
            opening.cancel()
        await device.close()


@pytest.mark.asyncio
async def test_halt_during_remote_open_closes_the_new_channel(monkeypatch):
    halted = False
    opening_remote = asyncio.Event()
    release = asyncio.Event()
    close_sent = asyncio.Event()

    async def request(peer, method, payload, timeout):
        if method == na.ATTACH_OPEN:
            opening_remote.set()
            await release.wait()
            return {"open": True}
        if method == na.ATTACH_CLOSE:
            close_sent.set()
            return {"closed": True}
        raise AssertionError("unexpected request")

    async def target(*args):
        raise na.AttachRefused("unused")

    async def no_local_server(*args, **kwargs):
        raise AssertionError("a halted device must not publish an attach socket")

    monkeypatch.setattr(na.local_ipc, "serve", no_local_server)
    requester = na.NetworkAttach(
        request=request, target=target, peer_name=lambda _: "device", notice=lambda _: None,
        halt_active=lambda: halted,
    )
    opening = asyncio.create_task(requester.open(SERVER, "agent-1", "agent"))
    try:
        await asyncio.wait_for(opening_remote.wait(), 2)
        halted = True
        await requester.recheck()
        release.set()
        with pytest.raises(na.AttachRefused, match="halted"):
            await asyncio.wait_for(opening, 2)
        assert close_sent.is_set()
        assert not requester._channels
    finally:
        release.set()
        if not opening.done():
            opening.cancel()
        await requester.close()


@pytest.mark.asyncio
async def test_halt_during_local_listener_start_retires_late_listener(monkeypatch):
    halted = False
    listening = asyncio.Event()
    release = asyncio.Event()
    close_sent = asyncio.Event()

    class Server:
        closed = False

        def close(self):
            self.closed = True

    server = Server()

    async def request(peer, method, payload, timeout):
        if method == na.ATTACH_OPEN:
            return {"open": True}
        if method == na.ATTACH_CLOSE:
            close_sent.set()
            return {"closed": True}
        raise AssertionError("unexpected request")

    async def target(*args):
        raise na.AttachRefused("unused")

    async def serve(*args, **kwargs):
        listening.set()
        await release.wait()
        return server

    monkeypatch.setattr(na.local_ipc, "serve", serve)
    requester = na.NetworkAttach(
        request=request, target=target, peer_name=lambda _: "device", notice=lambda _: None,
        halt_active=lambda: halted,
    )
    opening = asyncio.create_task(requester.open(SERVER, "agent-1", "agent"))
    try:
        await asyncio.wait_for(listening.wait(), 2)
        halted = True
        await requester.recheck()
        release.set()
        with pytest.raises(na.AttachRefused, match="halted"):
            await asyncio.wait_for(opening, 2)
        assert server.closed
        assert close_sent.is_set()
        assert not requester._channels
    finally:
        release.set()
        if not opening.done():
            opening.cancel()
        await requester.close()
