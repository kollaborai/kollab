"""Tests for the agent pool colors and the hub feed's live state events."""

import json
import os
import subprocess
import time
from types import SimpleNamespace

import kollabor_engine.auth as engine_auth  # type: ignore[import-not-found]
import kollabor_engine.routes.agents as agents_routes  # type: ignore[import-not-found]
import kollabor_engine.routes.hub_ws as hub_ws  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def gem(name, color_rgb=(128, 128, 128), role_aliases=(), personality="", caste=""):
    """A pool gem as the route reads it (the roster's class is not under test here)."""
    return SimpleNamespace(
        name=name,
        color=color_rgb,
        role_aliases=list(role_aliases),
        personality=personality,
        caste=caste,
    )


class StubBridge:
    """HubBridge stand-in: presence rows are whatever the test sets."""

    def __init__(self, agents=()):
        self.agents = list(agents)

    def get_agents(self, use_cache=True):
        return list(self.agents)


def presence(agent_id, identity, state, task=""):
    return {
        "agent_id": agent_id,
        "identity": identity,
        "state": state,
        "current_task": task,
    }


def live(agent_id, identity, state="idle", task="", project=None):
    """A presence row for an agent running in ``project`` (default: the engine's own folder)."""
    return {**presence(agent_id, identity, state, task), "project": project or os.getcwd()}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


# Marked so it also runs from the repo root, whose pytest config is strict
# (the engine package's own config sets asyncio_mode = "auto").
@pytest.mark.asyncio
class TestAgentPoolColor:
    @pytest.fixture
    def pool(self, monkeypatch):
        identities = [
            gem(
                "lapis",
                color_rgb=(30, 90, 180),
                role_aliases=["herald"],
                personality="calm mediator",
                caste="communication",
            ),
            gem("ruby", color_rgb=(200, 20, 60), personality="bold", caste="defense"),
        ]
        monkeypatch.setattr(agents_routes, "pool_identities", lambda *_: identities)

    async def test_every_entry_carries_its_gem_color(self, client, pool, monkeypatch):
        row = live("a1", "lapis", "working", "fix tests")
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([row]))

        body = (await client.get("/agents")).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["lapis"]["color"] == [30, 90, 180]
        assert by_name["ruby"]["color"] == [200, 20, 60]
        assert by_name["koordinator"]["color"] is None  # not a pool gem: the web UI gives it a born color
        assert body["count"] == 3

    async def test_live_fields_still_flow(self, client, pool, monkeypatch):
        row = live("a1", "lapis", "working", "fix tests")
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([row]))

        body = (await client.get("/agents")).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["lapis"]["active"] is True
        assert by_name["lapis"]["state"] == "working"
        assert by_name["lapis"]["current_task"] == "fix tests"
        assert by_name["lapis"]["agent_id"] == "a1"
        assert by_name["ruby"]["agent_id"] == ""
        assert by_name["ruby"]["available"] is True
        assert by_name["ruby"]["state"] == "available"
        assert body["active"] == ["lapis"]
        assert body["available"] == ["koordinator", "ruby"]

    async def test_a_solo_agent_is_live_but_off_the_hub(self, client, pool, monkeypatch):
        solo = {**live("a1", "lapis"), "solo": True}
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([solo]))

        body = (await client.get("/agents")).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["lapis"]["active"] is True
        assert by_name["lapis"]["solo"] is True
        assert by_name["ruby"]["solo"] is False

    async def test_a_live_agent_names_its_folder(self, client, pool, monkeypatch):
        there = live("a1", "lapis", project="/home/dev/kollab")
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([there]))

        body = (await client.get("/agents", params={"workspace": "/home/dev/kollab"})).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["lapis"]["project"] == "/home/dev/kollab"
        assert by_name["ruby"]["project"] == ""

    async def test_every_live_agent_is_listed_but_only_pool_gems_launch(self, client, pool, monkeypatch):
        rows = [live("a2", "lapis-2", "working"), live("a3", "zoe")]
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge(rows))

        body = (await client.get("/agents")).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["zoe"]["active"] and by_name["zoe"]["pool"] is False
        assert by_name["zoe"]["color"] is None  # no pool gem is its base: the web UI gives it a born color
        assert by_name["lapis-2"]["color"] == by_name["lapis"]["color"]  # wears its base gem
        assert by_name["lapis-2"]["state"] == "working"
        assert by_name["lapis"]["pool"] is True and by_name["lapis"]["available"]
        assert "zoe" not in body["available"]

    async def test_each_agent_says_when_it_last_ran_here(self, client, pool, monkeypatch, tmp_path):
        monkeypatch.setattr("plugins.hub.presence.get_hub_dir", lambda: tmp_path)
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge())
        stream = tmp_path / "vaults" / "lapis" / "stream.jsonl"
        stream.parent.mkdir(parents=True)
        stream.write_text('{"type": "session_start"}\n')
        (tmp_path / "vaults" / "ruby").mkdir()  # an empty vault never ran

        body = (await client.get("/agents")).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["lapis"]["last_ran"] == stream.stat().st_mtime
        assert by_name["ruby"]["last_ran"] is None
        assert by_name["koordinator"]["last_ran"] is None

    async def test_koordinator_comes_first_until_this_folder_runs_one(self, client, pool, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        elsewhere = {**presence("a1", "koordinator", "idle"), "project": "/somewhere/else"}
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([elsewhere]))

        body = (await client.get("/agents")).json()

        first = body["agents"][0]
        assert first["name"] == "koordinator" and first["agent_type"] == "koordinator"
        assert first["pool"] is True and first["available"] is True
        assert body["available"][0] == "koordinator"

        here = {**presence("a2", "koordinator", "working"), "project": str(tmp_path)}
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([elsewhere, here]))

        body = (await client.get("/agents")).json()

        first = body["agents"][0]
        assert first["active"] is True and first["agent_id"] == "a2"
        assert "koordinator" not in body["available"]
        assert [agent["name"] for agent in body["agents"]].count("koordinator") == 1

    async def test_a_live_gem_is_born_once(self, client, pool, monkeypatch, appearance_store):
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([live("a1", "lapis")]))

        await client.get("/agents")
        born = json.loads(appearance_store.read_text())["born"]
        await client.get("/agents")

        assert set(born) == {"lapis"}
        assert json.loads(appearance_store.read_text())["born"] == born

    async def test_missing_color_falls_back_to_neutral_gray(self, client, monkeypatch):
        monkeypatch.setattr(agents_routes, "pool_identities", lambda *_: [SimpleNamespace(name="opal")])
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge())

        _, agent = (await client.get("/agents")).json()["agents"]  # koordinator, opal

        assert agent["color"] == [128, 128, 128]


@pytest.mark.asyncio
class TestAgentPoolFolders:
    """Every folder has its own hub: a name live in one folder is free in the others."""

    @pytest.fixture
    def folders(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            agents_routes, "pool_identities", lambda *_: [gem("lapis", color_rgb=(30, 90, 180)), gem("ruby")]
        )
        home, other = tmp_path / "home", tmp_path / "other"
        home.mkdir()
        other.mkdir()
        monkeypatch.chdir(home)  # the engine's own folder
        return home, other

    @staticmethod
    async def pool(client, **params):
        body = (await client.get("/agents", params=params)).json()
        return {agent["name"]: agent for agent in body["agents"]}

    async def test_koordinator_in_one_folder_is_free_in_another(self, client, folders, monkeypatch):
        home, other = folders
        row = live("k1", "koordinator", "working", project=str(home))
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([row]))

        in_home = await self.pool(client, workspace=str(home))
        in_other = await self.pool(client, workspace=str(other))

        assert in_home["koordinator"]["active"] and in_home["koordinator"]["agent_id"] == "k1"
        assert not in_home["koordinator"]["available"]
        assert in_other["koordinator"]["available"] and not in_other["koordinator"]["active"]
        assert in_other["koordinator"]["agent_id"] == "" and in_other["koordinator"]["project"] == ""

    async def test_a_gem_in_one_folder_is_free_in_another(self, client, folders, monkeypatch):
        home, other = folders
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([live("g1", "lapis", project=str(home))]))

        in_home = await self.pool(client, workspace=str(home))
        in_other = await self.pool(client, workspace=str(other))

        assert in_home["lapis"]["active"] and in_home["ruby"]["available"]
        assert in_other["lapis"]["available"] and in_other["lapis"]["state"] == "available"

    async def test_no_workspace_is_the_engines_folder(self, client, folders, monkeypatch):
        home, other = folders
        rows = [live("g1", "lapis", project=str(home)), live("k2", "koordinator", project=str(other))]
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge(rows))

        bare = await self.pool(client)

        assert bare["lapis"]["active"] and bare["koordinator"]["available"]

    async def test_the_computer_view_shows_an_agent_in_any_folder(self, client, folders, monkeypatch):
        home, other = folders
        rows = [live("g1", "lapis", "working", project=str(other)), live("g2", "ruby", project=str(home))]
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge(rows))

        everywhere = await self.pool(client, scope="computer")

        assert everywhere["lapis"]["active"] and everywhere["lapis"]["state"] == "working"
        assert everywhere["lapis"]["project"] == str(other)
        assert everywhere["ruby"]["project"] == str(home)

    async def test_this_folders_agent_wins_the_computer_view(self, client, folders, monkeypatch):
        home, other = folders
        rows = [live("g1", "lapis", project=str(other)), live("g2", "lapis", project=str(home))]
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge(rows))

        assert (await self.pool(client, scope="computer"))["lapis"]["agent_id"] == "g2"
        assert (await self.pool(client, scope="computer", workspace=str(other)))["lapis"]["agent_id"] == "g1"

    async def test_last_ran_is_the_asked_folders(self, client, folders, monkeypatch, tmp_path):
        home, other = folders
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge())
        stream = tmp_path / "vaults" / "lapis" / "stream.jsonl"
        stream.parent.mkdir(parents=True)
        stream.write_text('{"type": "session_start"}\n')
        monkeypatch.setattr(
            agents_routes, "_hub_dir", lambda folder: tmp_path if folder == str(other) else tmp_path / "none"
        )

        assert (await self.pool(client, workspace=str(other)))["lapis"]["last_ran"] == stream.stat().st_mtime
        assert (await self.pool(client, workspace=str(home)))["lapis"]["last_ran"] is None

    async def test_a_folder_inside_a_repo_shares_the_repos_hub(self, client, folders, monkeypatch, tmp_path):
        repo = tmp_path / "repo"
        (repo / "pkg").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([live("g1", "lapis", project=str(repo))]))

        inside = await self.pool(client, workspace=str(repo / "pkg"))

        assert inside["lapis"]["active"] and inside["lapis"]["agent_id"] == "g1"

    async def test_a_global_hub_is_one_hub_for_every_folder(self, client, folders, monkeypatch):
        home, other = folders
        monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "false")
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([live("g1", "lapis", project=str(home))]))

        in_other = await self.pool(client, workspace=str(other))

        assert in_other["lapis"]["active"] and in_other["lapis"]["agent_id"] == "g1"


class TestSnapshotDiff:
    def test_snapshot_carries_current_task(self):
        snapshot = hub_ws._snapshot_agents(
            [
                presence("a1", "lapis", "working", "fix tests"),
                presence("a2", "ruby", "idle", None),
                {"agent_id": "a3", "identity": "opal", "state": "idle"},
            ]
        )

        assert snapshot["a1"]["current_task"] == "fix tests"
        assert snapshot["a2"]["current_task"] == ""
        assert snapshot["a3"]["current_task"] == ""

    def test_state_change_event_payload(self):
        old = hub_ws._snapshot_agents([presence("a1", "lapis", "idle")])
        new = hub_ws._snapshot_agents([presence("a1", "lapis", "working", "fix tests")])

        assert hub_ws._diff_snapshots(old, new, 123.0) == [
            {
                "type": "agent_state_changed",
                "agent_id": "a1",
                "identity": "lapis",
                "state": "working",
                "old_state": "idle",
                "new_state": "working",
                "current_task": "fix tests",
                "ts": 123.0,
            }
        ]

    def test_task_only_change_fires_state_changed(self):
        old = hub_ws._snapshot_agents([presence("a1", "lapis", "working", "fix tests")])
        new = hub_ws._snapshot_agents(
            [presence("a1", "lapis", "working", "write docs")]
        )

        [event] = hub_ws._diff_snapshots(old, new, 1.0)

        assert event["type"] == "agent_state_changed"
        assert event["state"] == event["old_state"] == "working"
        assert event["current_task"] == "write docs"

    def test_unchanged_agents_emit_nothing(self):
        snapshot = hub_ws._snapshot_agents(
            [presence("a1", "lapis", "working", "fix tests")]
        )

        assert hub_ws._diff_snapshots(snapshot, dict(snapshot), 1.0) == []

    def test_joins_then_leaves_then_changes(self):
        old = hub_ws._snapshot_agents(
            [presence("a1", "lapis", "idle"), presence("a2", "ruby", "idle")]
        )
        new = hub_ws._snapshot_agents(
            [presence("a2", "ruby", "working"), presence("a3", "opal", "idle", "x")]
        )

        events = hub_ws._diff_snapshots(old, new, 1.0)

        assert [
            (e["type"], e.get("agent_id") or e["agent"]["agent_id"]) for e in events
        ] == [
            ("agent_joined", "a3"),
            ("agent_left", "a1"),
            ("agent_state_changed", "a2"),
        ]
        assert events[0]["agent"]["current_task"] == "x"
        assert events[1]["agent"]["identity"] == "lapis"


class TestHubFeedSocket:
    @pytest.fixture
    def bridge(self, monkeypatch):
        """Isolate the module-level watcher state and poll fast."""
        bridge = StubBridge()
        monkeypatch.setattr(hub_ws, "_bridge", bridge)
        monkeypatch.setattr(hub_ws, "_connections", set())
        monkeypatch.setattr(hub_ws, "_last_snapshot", {})
        monkeypatch.setattr(hub_ws, "_watcher_task", None)
        monkeypatch.setattr(hub_ws, "POLL_INTERVAL", 0.01)
        return bridge

    @staticmethod
    def events_until_pong(ws):
        """Ping and collect whatever the feed pushed first; never blocks forever."""
        ws.send_json({"command": "ping"})
        events = []
        while (message := ws.receive_json())["type"] != "pong":
            events.append(message)
        return events

    def test_snapshot_then_task_change_over_the_wire(self, bridge):
        bridge.agents = [presence("a1", "lapis", "working", "fix tests")]

        with TestClient(create_app()).websocket_connect("/ws/hub/feed") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "hub_snapshot"
            assert snapshot["agents"][0]["current_task"] == "fix tests"

            # The watcher starts from what the client was told: no join replay.
            replay = []
            for _ in range(10):
                replay += self.events_until_pong(ws)
                time.sleep(0.02)
            assert replay == []

            bridge.agents = [presence("a1", "lapis", "working", "write docs")]
            seen = []
            for _ in range(100):
                seen += self.events_until_pong(ws)
                if seen:
                    break
                time.sleep(0.02)

        [event] = seen
        assert event["type"] == "agent_state_changed"
        assert event["identity"] == "lapis"
        assert event["state"] == event["old_state"] == "working"
        assert event["current_task"] == "write docs"

    @pytest.fixture
    def real_auth(self, monkeypatch, tmp_path):
        """Switch the pytest-only bypass off and give validate_token a known token."""
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
        monkeypatch.setattr(engine_auth, "_current_token", "good-token")
        monkeypatch.setattr(engine_auth, "_TOKEN_FILE", tmp_path / "engine.token")
        monkeypatch.setattr(
            engine_auth, "resolve_global_path", lambda name: tmp_path / "global" / name
        )

    @pytest.mark.parametrize("query", ["", "?token=", "?token=wrong", "?token=%C3%A9"])
    def test_missing_or_bad_token_is_closed_1008_before_any_data(
        self, bridge, real_auth, query
    ):
        bridge.agents = [presence("a1", "lapis", "working", "fix tests")]

        with pytest.raises(WebSocketDisconnect) as closed:
            with TestClient(create_app()).websocket_connect(
                f"/ws/hub/feed{query}"
            ) as ws:
                ws.receive_json()  # the hub_snapshot, had auth let us through

        assert closed.value.code == 1008
        assert hub_ws._connections == set()
        assert hub_ws._watcher_task is None

    def test_good_token_gets_the_snapshot(self, bridge, real_auth):
        bridge.agents = [presence("a1", "lapis", "working", "fix tests")]

        with TestClient(create_app()).websocket_connect(
            "/ws/hub/feed?token=good-token"
        ) as ws:
            snapshot = ws.receive_json()

        assert snapshot["type"] == "hub_snapshot"
        assert snapshot["agents"][0]["identity"] == "lapis"
