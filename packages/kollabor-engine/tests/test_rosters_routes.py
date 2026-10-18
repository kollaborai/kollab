"""Roster and team routes: listing, avatar files, and launching a team into a folder."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import kollabor_engine.routes.rosters as rosters_routes  # type: ignore[import-not-found]
import kollabor_engine.routes.teams as teams_routes  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

from plugins.hub import rosters


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """A user config directory and a project folder of their own."""
    monkeypatch.setattr(rosters, "get_config_directory", lambda: tmp_path / "home" / ".kollab")
    folder = tmp_path / "proj"
    folder.mkdir()
    return SimpleNamespace(user=tmp_path / "home" / ".kollab", project=os.path.realpath(folder))


@pytest_asyncio.fixture
async def client(folders):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


def put(path, data, binary=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(data)
    else:
        path.write_text(data if isinstance(data, str) else json.dumps(data))


@pytest.mark.asyncio
async def test_rosters_lists_gems_and_the_example_crew_with_avatar_descriptors(client, folders):
    body = (await client.get("/rosters", params={"workspace": folders.project})).json()
    by_name = {r["name"]: r for r in body["rosters"]}
    assert body["default"] == "gems" and body["workspace"] == folders.project
    assert len(by_name["gems"]["personas"]) == 24
    lapis = by_name["gems"]["personas"][0]
    assert lapis["name"] == "lapis" and lapis["avatar"] == {"kind": "gem"} and lapis["agent"] == "coder"
    kinds = {p["avatar"]["kind"] for p in by_name["marketing-crew"]["personas"]}
    assert kinds == {"emoji", "initials"}
    assert body["problems"] == []


@pytest.mark.asyncio
async def test_project_roster_with_an_image_avatar_and_the_default_roster(client, folders):
    put(
        folders.project + "/.kollab/rosters/crew.json",
        {"name": "crew", "personas": [{"name": "ana", "avatar": {"kind": "image", "src": "faces/ana.png"}},
                                      {"name": "web", "avatar": {"kind": "image", "src": "https://x.test/w.png"}}]},
    )
    put(folders.project + "/.kollab/config.json", {"plugins": {"hub": {"roster": "crew"}}})
    body = (await client.get("/rosters", params={"workspace": folders.project})).json()
    assert body["default"] == "crew"
    crew = next(r for r in body["rosters"] if r["name"] == "crew")
    ana, web = crew["personas"]
    assert ana["avatar"] == {"kind": "image", "url": "/rosters/crew/avatars/faces/ana.png"}
    assert web["avatar"] == {"kind": "image", "url": "https://x.test/w.png"}


@pytest.mark.asyncio
async def test_a_bad_workspace_is_a_400(client, folders):
    assert (await client.get("/rosters", params={"workspace": "/no/such/folder"})).status_code == 400


@pytest.mark.asyncio
async def test_avatar_files_are_served_from_the_roster_folder_only(client, folders):
    base = folders.project + "/.kollab/rosters"
    put(base + "/crew.json", {"name": "crew", "personas": [{"name": "ana"}]})
    put(base + "/faces/ana.png", b"\x89PNG-bytes", binary=True)
    put(base + "/faces/bad.html", "<script>", binary=False)
    put(os.path.dirname(base) + "/secret.png", b"secret", binary=True)  # outside the roster folder
    params = {"workspace": folders.project}

    ok = await client.get("/rosters/crew/avatars/faces/ana.png", params=params)
    assert ok.status_code == 200 and ok.content == b"\x89PNG-bytes" and ok.headers["content-type"] == "image/png"
    assert "sandbox" in ok.headers["content-security-policy"]

    for refused in (
        "/rosters/crew/avatars/../secret.png",
        "/rosters/crew/avatars/%2e%2e/secret.png",
        "/rosters/crew/avatars/..%2fsecret.png",
        "/rosters/crew/avatars//etc/passwd",
        "/rosters/crew/avatars/crew.json",  # not an image type: the roster file is never served
        "/rosters/crew/avatars/faces/bad.html",
        "/rosters/crew/avatars/faces/missing.png",
        "/rosters/nonesuch/avatars/faces/ana.png",
    ):
        assert (await client.get(refused, params=params)).status_code == 404, refused


@pytest.mark.asyncio
async def test_teams_list_resolves_members(client, folders):
    body = (await client.get("/teams", params={"workspace": folders.project})).json()
    marketing = next(t for t in body["teams"] if t["name"] == "marketing")
    assert marketing["lead"] == "omar" and marketing["roster"] == "marketing-crew"
    maya = next(m for m in marketing["members"] if m["persona"]["name"] == "maya")
    assert maya["reports_to"] == "omar" and maya["agent"] == "research" and maya["persona"]["avatar"]["kind"] == "emoji"
    assert {"engineering", "startup"} <= {t["name"] for t in body["teams"]}


class FakeHub:
    """Stands in for the presence view and for POST /sessions: starting a session makes it live."""

    def __init__(self, folder, live=()):
        self.folder, self.calls = folder, []
        self.agents = [{"identity": n, "project": folder} for n in live]

    def get_agents(self, use_cache=True):
        return list(self.agents)

    async def create_session(self, body, request):
        self.calls.append(body)
        self.agents.append({"identity": body.identity.rpartition("/")[2], "project": self.folder})
        return {"session_id": f"s-{len(self.calls)}", "identity": body.identity}


@pytest.fixture
def hub(folders, monkeypatch):
    fake = FakeHub(folders.project)
    monkeypatch.setattr(teams_routes, "_bridge", fake)
    monkeypatch.setattr(teams_routes, "create_session", fake.create_session)
    return fake


@pytest.mark.asyncio
async def test_launch_starts_every_member_lead_first_and_a_second_call_starts_none(client, folders, hub):
    first = (await client.post("/teams/marketing/launch", json={"workspace": folders.project})).json()
    assert first["started"] == ["omar", "maya", "theo", "priya", "luz", "jonas"]
    assert first["skipped"] == [] and first["failed"] == []
    assert [s["session_id"] for s in first["sessions"]] == [f"s-{i}" for i in range(1, 7)]

    lead, maya = hub.calls[0], hub.calls[1]
    assert (lead.identity, lead.agent, lead.workspace) == ("marketing-crew/omar", "default", folders.project)
    assert maya.agent == "research" and "you report to: omar" in maya.launch_brief

    again = (await client.post("/teams/marketing/launch", json={"workspace": folders.project})).json()
    assert again["started"] == [] and again["sessions"] == []
    assert [s["reason"] for s in again["skipped"]] == ["live"] * 6
    assert len(hub.calls) == 6


@pytest.mark.asyncio
async def test_launch_skips_members_already_live_and_reports_failures(client, folders, hub, monkeypatch):
    hub.agents.append({"identity": "omar", "project": folders.project})
    hub.agents.append({"identity": "maya", "project": "/some/other/folder"})  # another folder's hub does not count

    real = hub.create_session

    async def flaky(body, request):
        if body.identity.endswith("theo"):
            raise teams_routes.HTTPException(status_code=404, detail="Profile 'x' not found")
        return await real(body, request)

    monkeypatch.setattr(teams_routes, "create_session", flaky)
    body = (await client.post("/teams/marketing/launch", json={"workspace": folders.project, "profile": "fast"})).json()
    assert body["skipped"] == [{"persona": "omar", "reason": "live"}]
    assert body["started"] == ["maya", "priya", "luz", "jonas"]
    assert body["failed"] == [{"persona": "theo", "error": "Profile 'x' not found"}]
    assert all(c.profile == "fast" for c in hub.calls)


@pytest.mark.asyncio
async def test_launch_rejects_an_unknown_team_and_a_bad_folder(client, folders, hub):
    assert (await client.post("/teams/nonesuch/launch", json={"workspace": folders.project})).status_code == 404
    assert (await client.post("/teams/marketing/launch", json={"workspace": "/no/such/folder"})).status_code == 400


def test_a_persona_fills_the_session_request_but_the_caller_wins(folders):
    body = SimpleNamespace(identity="maya", workspace=folders.project, agent=None, profile="default")
    rosters_routes.apply_persona(body)
    assert body.agent == "research"

    body = SimpleNamespace(identity="maya", workspace=folders.project, agent="coder", profile="mine")
    rosters_routes.apply_persona(body)
    assert (body.agent, body.profile) == ("coder", "mine")

    body = SimpleNamespace(identity="not-a-persona", workspace=folders.project, agent=None, profile="default")
    rosters_routes.apply_persona(body)
    assert body.agent is None
