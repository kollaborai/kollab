"""Every engine route that takes a folder holds it to one rule (#980, docs/specs/engine-folder-rule.md).

The rule: the folder exists, is a directory, and, once ``~``, ``..`` and symlinks are resolved, is inside
the engine's own folder, the user's home, a folder an agent runs in now, or a recent web session folder.
"""

import os
from types import SimpleNamespace

import kollabor_engine.routes.workspaces as workspaces  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from kollabor_engine.hub_bridge import HubBridge  # type: ignore[import-not-found]
from kollabor_engine.routes import sessions  # type: ignore[import-not-found]
from kollabor_engine.server import create_app  # type: ignore[import-not-found]

# Every GET route that takes a folder, and the name of its parameter.
FOLDER_ROUTES = [
    ("/profiles", "workspace"),
    ("/agents", "workspace"),
    ("/agents/bundles", "workspace"),
    ("/rosters", "workspace"),
    ("/teams", "workspace"),
    ("/workspaces/browse", "path"),
    ("/code/fs/tree", "workspace"),
    ("/code/git", "workspace"),
]
ROUTE_IDS = [url for url, _ in FOLDER_ROUTES]


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A scratch computer: a home folder, the engine's own folder, and a folder that is neither."""
    tmp_path = tmp_path.resolve()  # macOS keeps /var as a link to /private/var
    home, engine, elsewhere = (tmp_path / name for name in ("home", "engine", "elsewhere"))
    for folder in (home, engine, elsewhere, home / "proj"):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(engine)
    return SimpleNamespace(home=home, engine=engine, elsewhere=elsewhere, proj=home / "proj")


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_folder_inside_home_is_served(client, machine, url, param):
    assert (await client.get(url, params={param: str(machine.proj)})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_folder_outside_home_and_the_engine_is_refused(client, machine, url, param):
    response = await client.get(url, params={param: str(machine.elsewhere)})

    assert response.status_code == 400
    assert "outside" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_dot_dot_out_of_home_is_refused(client, machine, url, param):
    sneaky = f"{machine.proj}{os.sep}..{os.sep}..{os.sep}elsewhere"

    response = await client.get(url, params={param: sneaky})

    assert response.status_code == 400
    assert "outside" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_symlink_in_home_to_a_folder_outside_is_refused(client, machine, url, param):
    link = machine.home / "link"
    try:
        link.symlink_to(machine.elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot make symlinks")

    response = await client.get(url, params={param: str(link)})

    assert response.status_code == 400
    assert "outside" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_folder_that_does_not_exist_is_refused(client, machine, url, param):
    response = await client.get(url, params={param: str(machine.home / "nope")})

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_file_is_not_a_folder(client, machine, url, param):
    (machine.home / "notes.txt").write_text("x")

    response = await client.get(url, params={param: str(machine.home / "notes.txt")})

    assert response.status_code == 400
    assert "Not a folder" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_the_engines_own_folder_works_wherever_it_is(client, machine, url, param):
    """An engine started outside home (a server under /srv) can use its own folder and what is below it."""
    (machine.engine / "pkg").mkdir()

    assert (await client.get(url, params={param: str(machine.engine / "pkg")})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_folder_an_agent_runs_in_is_served(client, machine, monkeypatch, url, param):
    live = [{"identity": "lapis", "project": str(machine.elsewhere)}]
    monkeypatch.setattr(HubBridge, "get_agents", lambda self, use_cache=True: live)

    assert (await client.get(url, params={param: str(machine.elsewhere)})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "param"), FOLDER_ROUTES, ids=ROUTE_IDS)
async def test_a_recent_session_folder_is_served(client, machine, url, param):
    workspaces.remember_workspace(str(machine.elsewhere))

    assert (await client.get(url, params={param: str(machine.elsewhere)})).status_code == 200


@pytest.mark.asyncio
async def test_launching_a_team_in_a_folder_outside_the_rule_is_refused(client, machine):
    response = await client.post("/teams/any/launch", json={"workspace": str(machine.elsewhere)})

    assert response.status_code == 400
    assert "outside" in response.json()["detail"]


@pytest.mark.asyncio
async def test_browse_offers_up_only_while_the_parent_is_inside_the_rule(client, machine):
    inside = (await client.get("/workspaces/browse", params={"path": str(machine.proj)})).json()
    top = (await client.get("/workspaces/browse", params={"path": str(machine.home)})).json()
    engine = (await client.get("/workspaces/browse", params={"path": str(machine.engine)})).json()

    assert inside["parent"] == str(machine.home)
    assert top["parent"] is None
    assert engine["parent"] is None


@pytest.mark.asyncio
async def test_browse_with_no_path_lists_home(client, machine):
    body = (await client.get("/workspaces/browse")).json()

    assert body["path"] == str(machine.home)
    assert body["dirs"] == ["proj"]


# --- POST /sessions: the folder is checked before anything starts -------------------------------------


class _Session:
    """Stands in for EngineSession: records what it was built with, spawns nothing."""

    made: list = []

    def __init__(self, **kwargs):
        type(self).made.append(kwargs)
        self.session_id = kwargs["session_id"]
        self.workspace = kwargs.get("workspace")

    async def initialize(self, on_spawned=None):
        return None

    def to_dict(self):
        return {"session_id": self.session_id}


@pytest.fixture
def new_sessions(machine, monkeypatch):
    registry: dict = {}
    monkeypatch.setattr(sessions, "get_session_registry", lambda: registry)
    monkeypatch.setattr(sessions, "EngineSession", _Session)
    _Session.made = []
    return registry


def _body(workspace):
    return sessions.CreateSessionRequest(
        profile="default",
        credentials=sessions.Credentials(
            provider="custom", api_key="test-key", model="test-model", base_url="http://127.0.0.1:9/v1"
        ),
        workspace=workspace,
    )


def _request():
    from starlette.requests import Request

    return Request({"type": "http", "method": "POST", "path": "/sessions", "headers": [], "query_string": b""})


@pytest.mark.asyncio
async def test_a_session_never_starts_in_a_folder_outside_the_rule(machine, new_sessions):
    with pytest.raises(HTTPException) as raised:
        await sessions.create_session(_body(str(machine.elsewhere)), _request())

    assert raised.value.status_code == 400
    assert "outside" in raised.value.detail
    assert _Session.made == [] and new_sessions == {}


@pytest.mark.asyncio
async def test_a_session_starts_in_the_checked_folder_not_the_requested_text(machine, new_sessions):
    await sessions.create_session(_body("~/proj"), _request())

    assert [made["workspace"] for made in _Session.made] == [str(machine.proj)]


@pytest.mark.asyncio
async def test_a_session_with_no_folder_still_starts_in_the_engines_own(machine, new_sessions):
    await sessions.create_session(_body(None), _request())

    assert len(_Session.made) == 1
