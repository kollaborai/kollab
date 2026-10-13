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


def request(request_id, method, params=None):
    params = dict(params or {})
    params["_meta"] = {"token": "good", "generation": "7"}
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


@pytest.mark.asyncio
async def test_mcp_initialize_and_initialized_notification(monkeypatch):
    monkeypatch.setenv("KOLLAB_ACP_GENERATION", "7")
    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps(request(1, "initialize")) + "\n").encode())
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    reader.feed_data((json.dumps(initialized) + "\n").encode())
    reader.feed_eof()
    writer = FakeWriter()
    await serve_hub_status(reader, writer, token="good")
    response = json.loads(writer.chunks[0])
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert len(writer.chunks) == 1


@pytest.mark.asyncio
async def test_mcp_tools_list_and_call_are_standard():
    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps(request(2, "tools/list")) + "\n").encode())
    reader.feed_data((json.dumps(request(3, "tools/call", {"name": "hub_status", "arguments": {}})) + "\n").encode())
    reader.feed_eof()
    writer = FakeWriter()
    await serve_hub_status(reader, writer, token="good", status=lambda: {"generation": 3})
    listed = json.loads(writer.chunks[0])
    called = json.loads(writer.chunks[1])
    assert listed["result"]["tools"][0]["name"] == "hub_status"
    assert json.loads(called["result"]["content"][0]["text"]) == {"generation": 3}


@pytest.mark.asyncio
async def test_mcp_rejects_stale_generation_binding(monkeypatch):
    monkeypatch.setenv("KOLLAB_ACP_GENERATION", "7")
    stale = request(4, "tools/list")
    stale["params"]["_meta"]["generation"] = "6"
    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps(stale) + "\n").encode())
    reader.feed_eof()
    writer = FakeWriter()
    await serve_hub_status(reader, writer, token="good")
    response = json.loads(writer.chunks[0])
    assert response["error"]["code"] == -32001
