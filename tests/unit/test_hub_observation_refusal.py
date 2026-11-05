"""A hub message the receiver refuses is answered with an error, never an ack (#581).

The sender counts a message as delivered only on an ack, so an ack for a
refused observation would make a lost message look delivered.
"""

import asyncio
import json

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
