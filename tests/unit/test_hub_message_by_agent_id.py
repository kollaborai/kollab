"""A hub message reaches the agent whose row was selected, not a namesake (#568)."""
import json
import os
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from kollabor_engine import hub_bridge
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import hub as hub_route


def _record(tmp_path, agent_id, project):
    socket = tmp_path / f"{agent_id}.sock"
    socket.touch()
    return {
        "agent_id": agent_id,
        "identity": "lapis",
        "project": project,
        "socket_path": str(socket),
        "pid": os.getpid(),
        "last_heartbeat": time.time(),
    }


@pytest.fixture
def two_lapis(tmp_path, monkeypatch):
    """Folder A's lapis is listed first; folder B's lapis is the one the user selects."""
    presence = tmp_path / "presence"
    presence.mkdir()
    records = [_record(tmp_path, "a-lapis", "/w/a"), _record(tmp_path, "b-lapis", "/w/b")]
    for record in records:
        (presence / f"{record['agent_id']}.json").write_text(json.dumps(record))
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [presence])
    bridge = HubBridge()
    assert [a["agent_id"] for a in bridge.get_agents()] == ["a-lapis", "b-lapis"]  # cached, A first
    send = AsyncMock(return_value={"type": "ack"})
    monkeypatch.setattr(bridge, "_query_socket_path", send)
    monkeypatch.setattr(hub_route, "_bridge", bridge)
    app = FastAPI()
    app.include_router(hub_route.router)
    return app, send, records


@pytest.mark.asyncio
async def test_message_with_agent_id_goes_to_that_agents_socket(two_lapis):
    app, send, records = two_lapis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/hub/messages", json={"target": "lapis", "agent_id": "b-lapis", "content": "hello"}
        )

    assert response.status_code == 200
    send.assert_awaited_once()
    assert send.await_args.args[0] == records[1]["socket_path"]  # B's socket, not A's
    assert send.await_args.args[2]["to"] == "lapis"  # the recipient still wakes for its identity


@pytest.mark.asyncio
async def test_message_for_a_gone_agent_id_is_refused_not_sent_to_a_namesake(two_lapis):
    app, send, _ = two_lapis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/hub/messages", json={"target": "lapis", "agent_id": "c-lapis", "content": "hello"}
        )

    assert response.status_code == 503
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_with_only_an_identity_still_routes_by_identity(two_lapis):
    app, send, records = two_lapis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/hub/messages", json={"target": "lapis", "content": "hello"})

    assert response.status_code == 200
    assert send.await_args.args[0] == records[0]["socket_path"]
