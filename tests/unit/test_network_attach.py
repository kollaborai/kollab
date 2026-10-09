"""Attach channels between two devices, over an in-process stand-in transport."""

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.hub import network_attach, relay_client
from plugins.hub.network_attach import ATTACH_DATA, AttachRefused, NetworkAttach
from plugins.hub.relay_state import RelayError

MAC = "a" * 64
SERVER = "b" * 64
AGENT_ID = "lapis-1"


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(network_attach, "FLUSH_SECONDS", 0.01)


class Pair:
    """A requester (the Mac) and the device running the agent (the server)."""

    def __init__(self, target=None, mac_runs_agent=False, latency=0.0):
        self.notices = []
        self.latency = latency  # seconds each request spends on the wire
        self.refuse = None
        self.dir = Path(tempfile.mkdtemp(prefix="kat"))
        self.agent_path = str(self.dir / "agent.sock")
        self.agent_lines = []
        self.agent_writers = []
        self.mac = NetworkAttach(
            request=self._to(SERVER, MAC),
            target=self._agent_socket if mac_runs_agent else self._no_agents,
            peer_name=lambda key: "server" if key == SERVER else "mac",
            notice=self.notices.append,
        )
        self.server = NetworkAttach(
            request=self._to(MAC, SERVER),
            target=target or self._agent_socket,
            peer_name=lambda key: "mac" if key == MAC else "server",
            notice=self.notices.append,
        )

    def _to(self, peer, sender):
        async def request(to, method, payload, timeout):
            assert to == peer
            await asyncio.sleep(self.latency)
            side = self.server if peer == SERVER else self.mac
            return await side.receive(sender, method, payload)

        return request

    async def _no_agents(self, peer, agent_id, name):
        raise AttachRefused("nothing runs here")

    async def _agent_socket(self, peer, agent_id, name):
        if self.refuse:
            raise AttachRefused(self.refuse)
        return self.agent_path

    async def start_agent(self):
        """A stand-in agent socket: greets, then echoes each line back upper-cased."""

        async def serve(reader, writer):
            self.agent_writers.append(writer)
            writer.write(b"hello\n")
            await writer.drain()
            while line := await reader.readline():
                self.agent_lines.append(line)
                writer.write(line.upper())
                await writer.drain()

        self.agent_server = await asyncio.start_unix_server(serve, path=self.agent_path)

    async def stop(self):
        await self.mac.close()
        await self.server.close()
        self.agent_server.close()
        shutil.rmtree(self.dir, ignore_errors=True)


async def _settle(predicate, seconds=3.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def test_an_opened_agent_talks_both_ways_and_closes_on_both_ends():
    async def run():
        pair = Pair()
        await pair.start_agent()
        path = await pair.mac.open(SERVER, AGENT_ID, "lapis")
        reader, writer = await asyncio.open_unix_connection(path)
        assert await reader.readline() == b"hello\n"
        writer.write(b'{"action": "attach"}\n')
        await writer.drain()
        assert await reader.readline() == b'{"ACTION": "ATTACH"}\n'
        assert pair.notices == ["mac opened lapis from the network"]
        assert not Path(path).exists(), "the socket is one-shot"

        writer.close()
        await _settle(lambda: not pair.server._channels)
        assert pair.notices[-1] == "mac closed lapis"
        await pair.stop()

    asyncio.run(run())


def test_the_device_says_why_it_will_not_open_an_agent():
    async def run():
        pair = Pair()
        await pair.start_agent()
        pair.refuse = "server has not let mac open its agents"
        with pytest.raises(AttachRefused, match="has not let mac open"):
            await pair.mac.open(SERVER, AGENT_ID, "lapis")
        assert not pair.mac._channels and not pair.server._channels
        await pair.stop()

    asyncio.run(run())


def test_the_agent_exiting_ends_the_callers_connection():
    async def run():
        pair = Pair()
        await pair.start_agent()
        path = await pair.mac.open(SERVER, AGENT_ID, "lapis")
        reader, writer = await asyncio.open_unix_connection(path)
        assert await reader.readline() == b"hello\n"
        await _settle(lambda: pair.agent_writers)
        pair.agent_writers[0].close()
        assert await asyncio.wait_for(reader.read(), 3.0) == b""
        await _settle(lambda: not pair.mac._channels and not pair.server._channels)
        await pair.stop()

    asyncio.run(run())


def test_a_retried_send_is_written_once_and_a_gap_closes_the_channel():
    async def run():
        pair = Pair()
        await pair.start_agent()
        path = await pair.mac.open(SERVER, AGENT_ID, "lapis")
        reader, writer = await asyncio.open_unix_connection(path)
        assert await reader.readline() == b"hello\n"
        (key, channel), = pair.server._channels.items()
        packed = network_attach._pack(b"once\n")
        frame = {"channel": channel.id, "seq": channel.recv_seq, "data": packed}
        assert (await pair.server.receive(MAC, ATTACH_DATA, frame))["ack"] == frame["seq"]
        assert (await pair.server.receive(MAC, ATTACH_DATA, frame))["ack"] == frame["seq"]
        await _settle(lambda: pair.agent_lines)
        assert pair.agent_lines == [b"once\n"]

        gap = {"channel": channel.id, "seq": channel.recv_seq + 5, "data": packed}
        assert (await pair.server.receive(MAC, ATTACH_DATA, gap)) == {"closed": True}
        assert not pair.server._channels
        await pair.stop()

    asyncio.run(run())


def test_one_device_opens_at_most_four_agents_on_another():
    async def run():
        pair = Pair()
        await pair.start_agent()
        for _ in range(network_attach.MAX_CHANNELS_PER_PEER):
            await pair.mac.open(SERVER, AGENT_ID, "lapis")
        with pytest.raises(AttachRefused, match="too many"):
            await pair.mac.open(SERVER, AGENT_ID, "lapis")
        await pair.stop()

    asyncio.run(run())


def test_a_socket_nobody_connects_closes_on_both_ends(monkeypatch):
    monkeypatch.setattr(network_attach, "CONNECT_SECONDS", 0.05)

    async def run():
        pair = Pair()
        await pair.start_agent()
        path = await pair.mac.open(SERVER, AGENT_ID, "lapis")
        await _settle(lambda: not pair.mac._channels and not pair.server._channels)
        assert not Path(path).exists()
        await pair.stop()

    asyncio.run(run())


def test_requests_that_name_no_valid_agent_or_bomb_the_inflater_are_refused():
    async def run():
        pair = Pair()
        await pair.start_agent()
        with pytest.raises(RelayError):
            await pair.server.receive(MAC, "attach_open", {"channel": "0" * 32, "agent_id": "../x", "name": "lapis"})
        with pytest.raises(RelayError):
            await pair.server.receive(MAC, "attach_open", {"channel": "nothex", "agent_id": AGENT_ID, "name": "lapis"})
        await pair.stop()

    asyncio.run(run())
    bomb = network_attach._pack(b"\0" * (network_attach.MAX_DATA_BYTES + 1))
    with pytest.raises(RelayError):
        network_attach._unpack(bomb)


async def _opened(pair):
    path = await pair.mac.open(SERVER, AGENT_ID, "lapis")
    reader, writer = await asyncio.open_unix_connection(path)
    assert await reader.readline() == b"hello\n"
    return reader, writer


def test_keepalives_never_take_the_seq_of_real_data(monkeypatch):
    """The pump is the channel's only sender: a keepalive due mid-stream cannot race a line."""
    monkeypatch.setattr(network_attach, "IDLE_SECONDS", 0.005)
    monkeypatch.setattr(network_attach, "KEEPALIVE_SECONDS", 0.005)

    async def run():
        pair = Pair(latency=0.02)  # a keepalive is still on the wire when the next line goes
        for side in (pair.mac, pair.server):  # keepalives this often would drain the frame budget
            side._pacer = network_attach._Pacer(rate=1e6, burst=1e6)
        await pair.start_agent()
        reader, writer = await _opened(pair)
        for n in range(20):
            writer.write(f"line {n}\n".encode())
            await writer.drain()
            assert await asyncio.wait_for(reader.readline(), 3) == f"LINE {n}\n".encode()
            await asyncio.sleep(0.01)  # quiet long enough for a keepalive to come due
        assert pair.agent_lines == [f"line {n}\n".encode() for n in range(20)]
        await pair.stop()

    asyncio.run(run())


def test_an_idle_channel_closes_once_the_device_withdraws_it(monkeypatch):
    monkeypatch.setattr(network_attach, "RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(network_attach, "IDLE_SECONDS", 0.02)

    async def run():
        pair = Pair()
        await pair.start_agent()
        reader, _ = await _opened(pair)
        pair.refuse = "server has not let mac open its agents"
        assert await asyncio.wait_for(reader.read(), 3) == b""
        await _settle(lambda: not pair.mac._channels and not pair.server._channels)
        assert pair.notices[-1] == "closed lapis for mac: it may no longer open it"
        await pair.stop()

    asyncio.run(run())


def test_a_line_sent_after_the_device_withdraws_it_never_reaches_the_agent(monkeypatch):
    monkeypatch.setattr(network_attach, "RECHECK_SECONDS", 0.0)

    async def run():
        pair = Pair()
        await pair.start_agent()
        reader, writer = await _opened(pair)
        pair.refuse = "server uses trust manual"
        writer.write(b"one more command\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 3) == b""
        assert pair.agent_lines == []
        await pair.stop()

    asyncio.run(run())


def test_recheck_closes_a_withdrawn_channel_at_once():
    async def run():
        pair = Pair()
        await pair.start_agent()
        reader, _ = await _opened(pair)
        pair.refuse = "server has not let mac open its agents"
        await pair.server.recheck()
        assert not pair.server._channels
        assert await asyncio.wait_for(reader.read(), 3) == b""
        await pair.stop()

    asyncio.run(run())


def test_deny_and_the_cap_count_only_the_agents_the_peer_holds_here():
    async def run():
        pair = Pair(mac_runs_agent=True)
        await pair.start_agent()
        for _ in range(network_attach.MAX_CHANNELS_PER_PEER):
            await pair.server.open(MAC, AGENT_ID, "lapis")  # the server opens 4 of the mac's agents
        await pair.mac.open(SERVER, AGENT_ID, "lapis")  # the mac still opens one of the server's
        await pair.server.close_peer(MAC)  # the server withdraws the mac's access
        assert [ch.requester for ch in pair.server._channels.values()] == [True] * 4
        await pair.stop()

    asyncio.run(run())


def test_closing_tells_every_peer_at_once(monkeypatch):
    monkeypatch.setattr(network_attach, "CLOSE_SECONDS", 0.1)

    async def run():
        pair = Pair()
        await pair.start_agent()
        for _ in range(3):
            await pair.mac.open(SERVER, AGENT_ID, "lapis")
        real = pair.server._request_fn

        async def silent(to, method, payload, timeout):  # the mac stopped answering closes
            if method == network_attach.ATTACH_CLOSE:
                await asyncio.sleep(30)
            return await real(to, method, payload, timeout)

        pair.server._request_fn = silent
        loop = asyncio.get_running_loop()
        started = loop.time()
        await pair.server.close_peer(MAC)
        assert loop.time() - started < 2.0  # one close's bound, not three
        assert not pair.server._channels
        await pair.stop()

    asyncio.run(run())


def test_the_peers_data_counts_against_this_devices_frame_budget():
    async def run():
        pacer = network_attach._Pacer(rate=10.0, burst=2.0)
        pacer.charge(4)  # down to -2
        loop = asyncio.get_running_loop()
        started = loop.time()
        await pacer.take(1)
        assert loop.time() - started >= 0.25  # back up to 1 at 10 a second

        pair = Pair()
        await pair.start_agent()
        await _opened(pair)
        (channel,) = pair.server._channels.values()
        packed = network_attach._pack(os.urandom(20000))
        frame = {"channel": channel.id, "seq": channel.recv_seq, "data": packed}
        before = pair.server._pacer.tokens
        await pair.server.receive(MAC, ATTACH_DATA, frame)
        assert pair.server._pacer.tokens < before - 5  # about 7 reply frames for 27 KiB
        await pair.stop()

    asyncio.run(run())


def test_a_burst_waits_for_the_relay_send_budget_instead_of_failing():
    """Every frame (tunnel data, replies, hub messages) rides the client's budget."""

    async def run():
        sent = []

        async def send_str(raw):
            sent.append(raw)

        client = SimpleNamespace(
            _send_lock=asyncio.Lock(),
            _tokens=0.0,
            _token_time=time.monotonic(),
            _ws=SimpleNamespace(closed=False, send_str=send_str),
        )
        started = time.monotonic()
        await relay_client.RelayClient._send_frame(client, {"type": "ping"})
        assert len(sent) == 1
        assert time.monotonic() - started >= 0.9 / relay_client.SEND_RATE_PER_SECOND

    asyncio.run(run())
