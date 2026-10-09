"""Attach channels between two devices, over an in-process stand-in transport."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from plugins.hub import network_attach
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

    def __init__(self, target=None):
        self.notices = []
        self.refuse = None
        self.dir = Path(tempfile.mkdtemp(prefix="kat"))
        self.agent_path = str(self.dir / "agent.sock")
        self.agent_lines = []
        self.agent_writers = []
        self.mac = NetworkAttach(
            request=self._to(SERVER, MAC),
            target=self._no_agents,
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
