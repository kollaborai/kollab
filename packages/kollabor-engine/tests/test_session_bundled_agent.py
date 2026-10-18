"""POST /sessions and a team launch start a bundled agent on a computer nothing has seeded."""

from types import SimpleNamespace

import kollabor_engine.routes.sessions as sessions_routes  # type: ignore[import-not-found]
import kollabor_engine.routes.teams as teams_routes  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app, get_session_registry  # type: ignore[import-not-found]


@pytest.fixture
def empty_home(tmp_path, monkeypatch):
    """No ~/.kollab, no project .kollab: the bundled agents are all there is."""
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    return SimpleNamespace(
        project=str(project), global_agents=home / ".kollab" / "agents"
    )


class FakeSession:
    """Stands in for the daemon: records what it was asked to start."""

    made: list = []

    def __init__(self, **kwargs):
        self.kwargs, self.workspace = kwargs, kwargs.get("workspace")
        FakeSession.made.append(self)

    async def initialize(self):
        pass

    def to_dict(self):
        return {"session_id": self.kwargs["session_id"], "agent": self.kwargs["agent"]}


@pytest_asyncio.fixture
async def client(empty_home, monkeypatch):
    FakeSession.made = []
    monkeypatch.setattr(sessions_routes, "EngineSession", FakeSession)
    monkeypatch.setattr(sessions_routes, "remember_workspace", lambda *_: None)
    profiles = SimpleNamespace(
        active_profile_name="saved", get_profile=lambda name: SimpleNamespace(name=name)
    )
    monkeypatch.setattr("kollabor_ai.ProfileManager", lambda: profiles)
    monkeypatch.setattr(
        teams_routes, "_bridge", SimpleNamespace(get_agents=lambda use_cache=True: [])
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac
    registry = get_session_registry()
    for session in FakeSession.made:
        registry.pop(session.kwargs["session_id"], None)


@pytest.mark.asyncio
async def test_a_session_starts_a_bundled_agent_on_an_empty_home(client, empty_home):
    response = await client.post(
        "/sessions", json={"agent": "coder", "workspace": empty_home.project}
    )

    assert response.status_code == 200, response.text
    started = FakeSession.made[0].kwargs
    assert started["agent"] == "coder"
    assert (
        "kollabor" in started["system_prompt"]
    )  # the bundle's rendered prompt, not an empty one
    assert not empty_home.global_agents.exists()  # resolving seeds nothing


@pytest.mark.asyncio
async def test_a_session_still_rejects_an_agent_nobody_has(client, empty_home):
    response = await client.post(
        "/sessions", json={"agent": "no-such-agent", "workspace": empty_home.project}
    )

    assert response.status_code == 404 and "no-such-agent" in response.json()["detail"]
    assert FakeSession.made == []


@pytest.mark.asyncio
async def test_a_team_launches_with_every_members_bundle_on_an_empty_home(
    client, empty_home
):
    response = await client.post(
        "/teams/marketing/launch", json={"workspace": empty_home.project}
    )

    body = response.json()
    assert body["failed"] == [], body
    assert body["started"] == ["omar", "maya", "theo", "priya", "luz", "jonas"]
    # No member names a profile, so each starts on the active one, as New Session would.
    assert {s.kwargs["profile"].name for s in FakeSession.made} == {"saved"}
    assert [s.kwargs["agent"] for s in FakeSession.made] == [
        "default",
        "research",
        "creative-writer",
        "data-analyst",
        "creative-writer",
        "technical-writer",
    ]


def write_agent(agents_dir, name, prompt):
    (agents_dir / name).mkdir(parents=True)
    (agents_dir / name / "system_prompt.md").write_text(prompt)


@pytest.mark.asyncio
async def test_a_session_finds_the_local_agent_of_its_own_folder_not_the_engines(
    client, empty_home, tmp_path
):
    # the engine runs in empty_home.project; the chat is in another folder
    chat = tmp_path / "chat"
    write_agent(chat / ".kollab" / "agents", "only-here", "ONLY-IN-THE-CHAT-FOLDER")

    response = await client.post(
        "/sessions", json={"agent": "only-here", "workspace": str(chat)}
    )

    assert response.status_code == 200, response.text
    assert "ONLY-IN-THE-CHAT-FOLDER" in FakeSession.made[0].kwargs["system_prompt"]

    # the engine's own folder does not have it
    response = await client.post(
        "/sessions", json={"agent": "only-here", "workspace": empty_home.project}
    )

    assert response.status_code == 404 and len(FakeSession.made) == 1


@pytest.mark.asyncio
async def test_local_beats_global_beats_bundled_for_the_chats_folder(
    client, empty_home, tmp_path
):
    chat, bare = tmp_path / "chat", tmp_path / "bare"
    bare.mkdir()
    chat.mkdir()

    async def prompt_in(folder):
        response = await client.post(
            "/sessions", json={"agent": "coder", "workspace": str(folder)}
        )
        assert response.status_code == 200, response.text
        return FakeSession.made[-1].kwargs["system_prompt"]

    bundled = await prompt_in(chat)
    assert "GLOBAL-CODER" not in bundled and "LOCAL-CODER" not in bundled

    write_agent(empty_home.global_agents, "coder", "GLOBAL-CODER")
    assert "GLOBAL-CODER" in await prompt_in(chat)

    write_agent(chat / ".kollab" / "agents", "coder", "LOCAL-CODER")
    local = await prompt_in(chat)
    assert "LOCAL-CODER" in local and "GLOBAL-CODER" not in local
    assert "GLOBAL-CODER" in await prompt_in(bare)  # another folder: no local tier


@pytest.mark.asyncio
async def test_a_team_member_finds_the_local_agent_of_the_teams_folder(
    client, empty_home, tmp_path
):
    folder = tmp_path / "team-folder"
    write_agent(folder / ".kollab" / "agents", "research", "LOCAL-RESEARCH")

    response = await client.post(
        "/teams/marketing/launch", json={"workspace": str(folder)}
    )

    body = response.json()
    assert body["failed"] == [], body
    researcher = next(s for s in FakeSession.made if s.kwargs["agent"] == "research")
    assert "LOCAL-RESEARCH" in researcher.kwargs["system_prompt"]
