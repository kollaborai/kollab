"""Tests for the agent pool colors and the hub feed's live state events."""

import json
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

from plugins.hub.models import PoolIdentity


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
            PoolIdentity(
                name="lapis",
                color_rgb=(30, 90, 180),
                role_aliases=["herald"],
                personality="calm mediator",
                caste="communication",
            ),
            PoolIdentity(
                name="ruby",
                color_rgb=(200, 20, 60),
                role_aliases=[],
                personality="bold",
                caste="defense",
            ),
        ]
        monkeypatch.setattr(
            "plugins.hub.models.load_pool_identities", lambda *a, **k: identities
        )

    async def test_every_entry_carries_its_gem_color(self, client, pool, monkeypatch):
        live = presence("a1", "lapis", "working", "fix tests")
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([live]))

        body = (await client.get("/agents")).json()

        by_name = {agent["name"]: agent for agent in body["agents"]}
        assert by_name["lapis"]["color"] == [30, 90, 180]
        assert by_name["ruby"]["color"] == [200, 20, 60]
        assert body["count"] == 2

    async def test_live_fields_still_flow(self, client, pool, monkeypatch):
        live = presence("a1", "lapis", "working", "fix tests")
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([live]))

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
        assert body["available"] == ["ruby"]

    async def test_a_live_gem_is_born_once(self, client, pool, monkeypatch, appearance_store):
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge([presence("a1", "lapis", "idle")]))

        await client.get("/agents")
        born = json.loads(appearance_store.read_text())["born"]
        await client.get("/agents")

        assert set(born) == {"lapis"}
        assert json.loads(appearance_store.read_text())["born"] == born

    async def test_missing_color_falls_back_to_neutral_gray(self, client, monkeypatch):
        monkeypatch.setattr(
            "plugins.hub.models.load_pool_identities",
            lambda *a, **k: [SimpleNamespace(name="opal")],
        )
        monkeypatch.setattr(agents_routes, "_bridge", StubBridge())

        [agent] = (await client.get("/agents")).json()["agents"]

        assert agent["color"] == [128, 128, 128]


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
