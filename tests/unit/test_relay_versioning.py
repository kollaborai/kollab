"""Relay protocol versions (docs/specs/agent-public-beacon.md#versioning).

A client names the versions it speaks in the WebSocket subprotocol header; a
relay picks one it serves, treats a client naming none as kollab-relay/1, and
refuses a client it cannot serve with a 426 naming the versions it does serve.
"""

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from plugins.hub import relay_service as service
from plugins.hub.relay_client import PROTOCOLS, _version_refused

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


@pytest_asyncio.fixture
async def relay():
    config = service.RelayConfig(ORIGIN, NODE_ID, dev_in_memory=True)
    client = TestClient(TestServer(service.create_app(config)))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_client_naming_no_version_speaks_version_1(relay):
    ws = await relay.ws_connect(service.WEBSOCKET_PATH)
    assert ws.protocol is None
    assert (await ws.receive_json())["protocol"] == "kollab-relay/1"
    await ws.close()


@pytest.mark.asyncio
async def test_the_relay_picks_a_version_it_serves_from_the_clients_list(relay):
    ws = await relay.ws_connect(service.WEBSOCKET_PATH, protocols=("kollab-relay/9", *PROTOCOLS))
    assert ws.protocol == "kollab-relay/1"
    assert (await ws.receive_json())["type"] == "challenge"
    await ws.close()


@pytest.mark.asyncio
async def test_a_client_the_relay_cannot_serve_hears_which_versions_it_does(relay):
    with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
        await relay.ws_connect(service.WEBSOCKET_PATH, protocols=("kollab-relay/9",))
    assert refused.value.status == 426
    assert refused.value.headers[service.PROTOCOLS_HEADER] == "kollab-relay/1"
    # Here the client is the newer side.
    assert _version_refused(refused.value.headers, "relay.example") == (
        "relay.example needs an update for this version of kollab"
    )


def test_a_client_older_than_every_version_served_is_told_to_update_itself():
    assert _version_refused({"X-Kollab-Relay-Protocols": "kollab-relay/2,kollab-relay/3"}, "kollabor.ai") == (
        "this version of kollab is too old for kollabor.ai; run kollab --upgrade"
    )
    assert _version_refused({}, "kollabor.ai") == "kollabor.ai needs an update for this version of kollab"


@pytest.mark.asyncio
async def test_health_names_the_versions_served(relay):
    response = await relay.get(service.HEALTH_PATH)
    assert (await response.json())["protocols"] == ["kollab-relay/1"]


def test_the_public_health_behind_the_supervisor_names_them_too(tmp_path):
    # kollabor.ai's /relay/v1/health is the supervisor's, not a worker's.
    from plugins.hub.relay_runtime import RelayRuntime, RuntimeConfig

    config = RuntimeConfig(
        origin=ORIGIN,
        bind_host="127.0.0.1",
        base_port=19078,
        workers=1,
        health_port=19080,
        node_prefix="relay-test",
        state_dir=tmp_path,
        trusted_proxies=(),
        backend={"mode": "external", "cluster": False},
        limits={},
    )
    assert RelayRuntime(config).snapshot()["protocols"] == ["kollab-relay/1"]
