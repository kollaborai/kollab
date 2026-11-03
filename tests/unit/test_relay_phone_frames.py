import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

import plugins.hub.relay_client as relay_client
from plugins.hub.relay_client import (
    MAX_APPLICATION_PAYLOAD,
    MAX_CIPHERTEXT,
    MAX_FRAME,
    MAX_PHONE_APPLICATION_PAYLOAD,
    RelayClient,
    RelayError,
    _application_payload_limit,
)
from plugins.hub.relay_service import MAX_CIPHERTEXT_CHARS, MAX_FRAME_BYTES


def test_phone_methods_get_a_bounded_large_payload_while_regular_methods_stay_small():
    assert _application_payload_limit("phone_http") == MAX_PHONE_APPLICATION_PAYLOAD
    assert _application_payload_limit("phone_session") == MAX_PHONE_APPLICATION_PAYLOAD
    assert _application_payload_limit("message") == MAX_APPLICATION_PAYLOAD

    overhead = len(json.dumps({"packet": ""}, separators=(",", ":")).encode())
    payload = {"packet": "x" * (MAX_PHONE_APPLICATION_PAYLOAD - overhead)}
    assert (
        RelayClient._application_payload(
            payload,
            limit=_application_payload_limit("phone_http"),
        )
        == payload
    )
    with pytest.raises(RelayError, match="oversized application payload"):
        RelayClient._application_payload(payload)


def test_phone_payload_and_ciphertext_limits_remain_finite_and_aligned():
    assert MAX_PHONE_APPLICATION_PAYLOAD < MAX_CIPHERTEXT
    assert MAX_CIPHERTEXT <= MAX_CIPHERTEXT_CHARS
    assert MAX_FRAME <= MAX_FRAME_BYTES
    with pytest.raises(RelayError, match="oversized application payload"):
        RelayClient._application_payload(
            {"packet": "x" * MAX_PHONE_APPLICATION_PAYLOAD},
            limit=_application_payload_limit("phone_http"),
        )


def wire_frame(size):
    prefix = {
        "type": "message",
        "from": "a" * 64,
        "session": "b" * 32,
        "id": "c" * 32,
        "ciphertext": "",
    }
    overhead = len(json.dumps(prefix, separators=(",", ":")).encode())
    prefix["ciphertext"] = "A" * (size - overhead)
    return json.dumps(prefix, separators=(",", ":"))


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [100 * 1024, MAX_FRAME, MAX_FRAME + 1])
async def test_handshake_receiver_uses_the_finite_wire_limit(size):
    raw = wire_frame(size)

    async def receive():
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=raw)

    socket = SimpleNamespace(receive=receive)
    if size > MAX_FRAME:
        with pytest.raises(RelayError):
            await RelayClient._receive_frame(socket)
    else:
        assert (await RelayClient._receive_frame(socket))["type"] == "message"


@pytest.mark.asyncio
async def test_connection_reads_large_phone_frame_then_the_next_frame(
    tmp_path, monkeypatch
):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    client.state.origin = "https://relay.example"
    client._ws_url = "wss://relay.example/relay/v1/ws"
    large = wire_frame(100 * 1024)
    next_frame = {"type": "peers", "peers": []}

    class Socket:
        def __init__(self):
            self.received = 0
            self.closed = False

        async def receive(self):
            self.received += 1
            if self.received == 1:
                frame = {
                    "type": "challenge",
                    "protocol": relay_client.PROTOCOL,
                    "origin": client.state.origin,
                    "nonce": "d" * 64,
                }
            elif self.received == 2:
                frame = {
                    "type": "registered",
                    "protocol": relay_client.PROTOCOL,
                    "key": client.public_key,
                    "session": client._session_id,
                }
            else:
                frame = next_frame
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(frame))

        async def send_str(self, _frame):
            pass

        async def close(self):
            self.closed = True

        async def __aiter__(self):
            for raw in (large, json.dumps(next_frame)):
                yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=raw)

    socket = Socket()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def ws_connect(self, *_args, **kwargs):
            assert kwargs["max_msg_size"] == MAX_FRAME
            return socket

    monkeypatch.setattr(relay_client.aiohttp, "TCPConnector", lambda **_kwargs: None)
    monkeypatch.setattr(
        relay_client.aiohttp, "ClientSession", lambda **_kwargs: Session()
    )
    handled = AsyncMock(wraps=client._handle_frame)
    monkeypatch.setattr(client, "_handle_frame", handled)
    await client._connection()
    assert len(handled.call_args_list) == 2
    assert handled.call_args_list[1].args[0] == next_frame
    # The large unknown peer is still rejected by the existing auth boundary.
    assert client._counts["rejected_messages"] == 1
