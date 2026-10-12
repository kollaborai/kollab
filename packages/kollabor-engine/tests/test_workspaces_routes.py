"""The New Session workspace picker: recent folders and a folder browser."""

import os

import kollabor_engine.routes.workspaces as workspaces  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.hub_bridge import HubBridge  # type: ignore[import-not-found]
from kollabor_engine.server import create_app  # type: ignore[import-not-found]


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_recent_lists_live_folders_then_used_ones(client, tmp_path, monkeypatch):
    home, busy, used, gone = (tmp_path / name for name in ("home", "busy", "used", "gone"))
    for folder in (home, busy, used):
        folder.mkdir()
    monkeypatch.chdir(home)
    live = [{"identity": "lapis", "project": str(busy)}, {"identity": "ruby", "project": str(busy)},
            {"identity": "koordinator", "project": str(home)}]
    monkeypatch.setattr(HubBridge, "get_agents", lambda self, use_cache=True: live)
    workspaces.remember_workspace(str(gone))
    workspaces.remember_workspace(str(used))

    body = (await client.get("/workspaces")).json()

    assert body["home"] == {"path": os.path.realpath(home), "name": "home", "live": 1}
    assert [(row["name"], row["live"]) for row in body["recent"]] == [("busy", 2), ("used", 0)]


@pytest.mark.asyncio
async def test_browse_lists_subfolders_without_hidden_ones(client, tmp_path):
    for name in ("beta", "Alpha", ".git"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("x")

    body = (await client.get("/workspaces/browse", params={"path": str(tmp_path)})).json()

    assert body["dirs"] == ["Alpha", "beta"]
    assert body["path"] == str(tmp_path.resolve()) and body["parent"] == str(tmp_path.resolve().parent)
    missing = await client.get("/workspaces/browse", params={"path": str(tmp_path / "nope")})
    assert missing.status_code == 400
