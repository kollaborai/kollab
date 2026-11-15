"""#968: GET /agents tells the web UI which version each live agent runs, and whether Restart Agent is on."""

import os

import kollabor_engine.routes.agents as agents_routes  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

SHA = "b" * 40


class StubBridge:
    def __init__(self, agents=()):
        self.agents = list(agents)

    def get_agents(self, use_cache=True):
        return list(self.agents)


def row(agent_id, identity, **extra):
    return {"agent_id": agent_id, "identity": identity, "state": "idle", "project": os.getcwd(), **extra}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_live_rows_carry_the_version_and_build_the_agent_published(client, monkeypatch):
    monkeypatch.setattr(
        agents_routes,
        "_bridge",
        StubBridge([row("a1", "lapis", version="0.20.0+abc12345", build=SHA), row("a2", "ruby")]),
    )
    body = (await client.get("/agents")).json()
    by_id = {r["agent_id"]: r for r in body["live"]}
    assert (by_id["a1"]["version"], by_id["a1"]["build"]) == ("0.20.0+abc12345", SHA)
    # An agent from before this feature publishes none: empty, never a guess.
    assert (by_id["a2"]["version"], by_id["a2"]["build"]) == ("", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_response_says_whether_restart_agents_is_on(client, monkeypatch, enabled):
    monkeypatch.setattr(agents_routes, "_bridge", StubBridge([]))
    monkeypatch.setattr(agents_routes, "restart_agents_enabled", lambda: enabled)
    assert (await client.get("/agents")).json()["restart_agents"] is enabled
