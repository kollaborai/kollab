import asyncio
import json

import pytest

from kollabor.mcp_hub import serve_hub_status


class FakeWriter:
    def __init__(self):
        self.chunks = []
        self.closed = False

    def write(self, data):
        self.chunks.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.mark.asyncio
async def test_hub_status_requires_current_token():
    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps({"id": 1, "method": "hub_status", "auth": "bad"}) + "\n").encode())
    reader.feed_eof()
    writer = FakeWriter()
    await serve_hub_status(reader, writer, token="good")
    response = json.loads(writer.chunks[0])
    assert response["error"]["code"] == -32001
    assert writer.closed


@pytest.mark.asyncio
async def test_hub_status_rejects_stale_generation_binding(monkeypatch):
    monkeypatch.setenv("KOLLAB_ACP_GENERATION", "7")
    reader = asyncio.StreamReader()
    request = {"id": 2, "method": "hub_status", "auth": {"token": "good", "generation": "6"}}
    reader.feed_data((json.dumps(request) + "\n").encode())
    reader.feed_eof()
    writer = FakeWriter()
    await serve_hub_status(reader, writer, token="good")
    response = json.loads(writer.chunks[0])
    assert response["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_hub_status_returns_only_supported_method():
    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps({"id": 2, "method": "hub_status", "auth": "good"}) + "\n").encode())
    reader.feed_eof()
    writer = FakeWriter()
    await serve_hub_status(reader, writer, token="good", status=lambda: {"generation": 3})
    response = json.loads(writer.chunks[0])
    assert response["result"] == {"generation": 3}
