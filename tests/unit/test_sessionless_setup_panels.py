"""Sessionless setup panels (#1114, #1115): the first-run web UI has no
session, yet the setup guide's Provider and Network steps must still show
their Settings panels. The engine serves the ``setup`` wizard and the
``connect`` picker through ``/setup/panels/*`` with a bare context: a
ProfileManager over the engine host's config and no LLM service - the path
``create_and_activate_profile`` already reserves for first-run setups.
"""

import pytest

pytestmark = pytest.mark.asyncio

SETUP_FINISH_PAYLOAD = {
    "values": {
        "provider": "anthropic",
        "api_key": "sk-test-not-real",
        "base_url": "",
        "model": "claude-sonnet-5",
    },
    "step": "api_key",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from httpx import ASGITransport, AsyncClient

    from kollabor_engine.server import create_app

    # Test-client auth bypass (the same gate every engine route test uses).
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    # The sessionless context builds a ProfileManager over HOME's config.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


async def test_setup_panel_describes_wizard_sessionless(client):
    async with client as c:
        response = await c.get("/setup/panels/setup")
    assert response.status_code == 200
    body = response.json()
    assert body["panel"] == "setup"
    assert body["kind"] == "wizard"
    assert [step["id"] for step in body["steps"]] == ["provider", "api_key", "endpoint", "model"]


async def test_connect_panel_describes_unavailable_sessionless(client):
    async with client as c:
        response = await c.get("/setup/panels/connect")
    assert response.status_code == 200
    body = response.json()
    assert body["panel"] == "connect"
    assert body["kind"] == "picker"
    # No hub runs without a session; the panel degrades to its notice
    # instead of failing, exactly as a session's no-hub daemon does.
    assert body["notice"]
    assert body["toolbar_actions"] == []


async def test_unknown_setup_panel_is_404(client):
    async with client as c:
        response = await c.get("/setup/panels/config")
    assert response.status_code == 404


async def test_setup_finish_creates_and_activates_profile_sessionless(client):
    async with client as c:
        response = await c.post(
            "/setup/panels/setup/actions/finish", json=SETUP_FINISH_PAYLOAD
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True

    from kollabor_ai import ProfileManager

    manager = ProfileManager()
    name = body["message"].split("profile '")[1].split("'")[0]
    assert manager.get_profile(name) is not None
    active = manager.get_active_profile()
    assert active.name == name


async def test_connect_join_degrades_gracefully_sessionless(client):
    async with client as c:
        response = await c.post(
            "/setup/panels/connect/actions/join",
            json={"values": {"code": "abcd-1234"}},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


async def test_connect_decide_needs_a_hub_sessionless(client):
    async with client as c:
        response = await c.post(
            "/setup/panels/connect/actions/accept",
            json={"values": {"enrollment_id": "e1"}},
        )
    assert response.status_code == 503
    assert response.json()["ok"] is False


async def test_setup_unknown_action_is_rejected(client):
    async with client as c:
        response = await c.post(
            "/setup/panels/setup/actions/explode", json={"values": {}}
        )
    assert response.status_code == 404
