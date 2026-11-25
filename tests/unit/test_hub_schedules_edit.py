"""Editing schedules from the Web UI (#612, stage 4).

The engine never writes ``schedules.json`` itself while a hub is running:
it sends the whole list to the hub coordinator's local-only
``schedules_save`` socket op. The coordinator validates the rows, then
either forwards them to the network primary (this device is a secondary)
or writes the file and wakes the grid timer (this device is the primary,
or is not on a network).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]
from kollabor_engine.routes import hub as hub_routes

from plugins.hub.messenger import AgentSocketServer
from plugins.hub.plugin import HubPlugin
from plugins.hub.schedules import RunLog, ScheduleStore


def _row(row_id="ab12cd34", **over):
    row = {
        "id": row_id,
        "agents": ["lapis"],
        "prompt": "report the pulse",
        "interval_seconds": 3600.0,
        "anchor": 1000.0,
        "active": True,
        "when_busy": "queue",
        "skill": "",
        "created_by": "web",
        "created_at": 900.0,
        "updated_at": 900.0,
        "revision": 1,
    }
    row.update(over)
    return row


class _Timer:
    def __init__(self):
        self.woken = 0

    def wake(self):
        self.woken += 1


class _Sync:
    def __init__(self, reply):
        self.reply = reply
        self.sent = None

    async def send_schedule_edit(self, rows):
        self.sent = rows
        return self.reply


class _Identity:
    def __init__(self, coordinator):
        self.is_coordinator = coordinator


def _plugin(tmp_path, monkeypatch, *, coordinator=True, sync=None):
    plugin = object.__new__(HubPlugin)
    plugin._identity = _Identity(coordinator)
    plugin._schedule_timer = _Timer()
    plugin._relay_agent = type("Relay", (), {"config_sync": sync})() if sync else None
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: tmp_path
    )
    return plugin


# -- the coordinator's socket op ------------------------------------------


def test_coordinator_off_network_writes_the_file_and_wakes_the_timer(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)
    reply = asyncio.run(plugin._schedules_save([_row()]))
    assert reply == {"ok": True, "via": "local"}
    assert [r.id for r in ScheduleStore(tmp_path / "schedules.json").load()] == ["ab12cd34"]
    assert plugin._schedule_timer.woken == 1


def test_primary_writes_locally(tmp_path, monkeypatch):
    sync = _Sync({"error": "is_primary"})
    plugin = _plugin(tmp_path, monkeypatch, sync=sync)
    reply = asyncio.run(plugin._schedules_save([_row()]))
    assert reply == {"ok": True, "via": "local"}
    assert sync.sent is not None
    assert (tmp_path / "schedules.json").exists()


def test_primary_binds_bare_agents_to_its_device_before_sync(tmp_path, monkeypatch):
    sync = _Sync({"error": "is_primary"})
    plugin = _plugin(tmp_path, monkeypatch, sync=sync)
    plugin._relay_agent = type(
        "Relay", (), {"config_sync": sync, "device_name": lambda _self: "home-server"}
    )()

    reply = asyncio.run(plugin._schedules_save([_row()]))

    assert reply == {"ok": True, "via": "local"}
    assert sync.sent[0]["agents"] == ["lapis@home-server"]
    assert ScheduleStore(tmp_path / "schedules.json").load()[0].agents == [
        "lapis@home-server"
    ]


def test_secondary_forwards_to_the_primary_and_leaves_the_file_alone(tmp_path, monkeypatch):
    sync = _Sync({"ok": True})
    plugin = _plugin(tmp_path, monkeypatch, sync=sync)
    reply = asyncio.run(plugin._schedules_save([_row()]))
    assert reply == {"ok": True, "via": "primary"}
    assert [r["id"] for r in sync.sent] == ["ab12cd34"]
    assert not (tmp_path / "schedules.json").exists()


def test_secondary_reports_a_primary_refusal(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch, sync=_Sync({"error": "failed"}))
    reply = asyncio.run(plugin._schedules_save([_row()]))
    assert "failed" in reply["error"]
    assert not (tmp_path / "schedules.json").exists()


def test_a_non_coordinator_declines(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch, coordinator=False)
    assert asyncio.run(plugin._schedules_save([_row()])) == {"error": "not_coordinator"}
    assert not (tmp_path / "schedules.json").exists()


@pytest.mark.parametrize(
    "rows",
    [
        "nope",
        [_row(prompt="")],
        [_row(interval_seconds=0)],
        [_row(when_busy="later")],
        [_row(), _row()],  # duplicate id
    ],
)
def test_invalid_rows_are_refused_and_nothing_is_written(tmp_path, monkeypatch, rows):
    plugin = _plugin(tmp_path, monkeypatch)
    reply = asyncio.run(plugin._schedules_save(rows))
    assert reply.get("error", "").startswith("invalid")
    assert not (tmp_path / "schedules.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="unix socket")
def test_schedules_save_round_trips_over_a_real_socket(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)

    async def run():
        server = AgentSocketServer(
            "sched-save-id",
            lambda _message: None,
            on_schedules_save=plugin._schedules_save,
            socket_name=f"sched-save-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(sock_path)
            writer.write(
                (json.dumps({"action": "schedules_save", "rows": [_row()]}) + "\n").encode()
            )
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=3.0)
            writer.close()
            return json.loads(line)
        finally:
            await server.stop()

    frame = asyncio.run(run())
    assert frame == {"type": "schedules_save", "ok": True, "via": "local"}


# -- the engine routes ----------------------------------------------------


class _Bridge:
    """Live agents and their schedules_save replies; records what was sent."""

    def __init__(self, agents, replies):
        self._agents = agents
        self._replies = replies
        self.sent = {}

    def get_agents(self, use_cache=True):
        return self._agents

    async def query_socket(self, agent_id, action, payload=None, timeout=5.0):
        assert action == "schedules_save"
        self.sent[agent_id] = payload["rows"]
        return self._replies.get(agent_id)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_routes, "_schedules_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(hub_routes.router)
    return TestClient(app)


def _install(monkeypatch, bridge):
    monkeypatch.setattr(hub_routes, "_bridge", bridge)


def _coordinator_bridge():
    return _Bridge(
        agents=[
            {"agent_id": "lapis-1", "identity": "lapis"},
            {"agent_id": "koord-1", "identity": "koordinator"},
        ],
        replies={
            "lapis-1": {"type": "schedules_save", "error": "not_coordinator"},
            "koord-1": {"type": "schedules_save", "ok": True, "via": "local"},
        },
    )


def test_list_reads_the_file_with_the_last_run(client, tmp_path):
    ScheduleStore(tmp_path / "schedules.json").save([])
    (tmp_path / "schedules.json").write_text(json.dumps([_row()]))
    RunLog(tmp_path / "schedules-runs.jsonl").append(
        {"id": "ab12cd34", "agent": "lapis", "due": 1000, "fired_at": 1001, "result": "error", "error": "peer gone"}
    )
    data = client.get("/hub/schedules").json()
    assert [s["id"] for s in data["schedules"]] == ["ab12cd34"]
    assert data["schedules"][0]["last_run"]["error"] == "peer gone"


def test_list_is_empty_without_a_file(client):
    assert client.get("/hub/schedules").json()["schedules"] == []


def test_create_sends_the_whole_list_to_the_coordinator(client, monkeypatch, tmp_path):
    (tmp_path / "schedules.json").write_text(json.dumps([_row()]))
    bridge = _coordinator_bridge()
    _install(monkeypatch, bridge)
    res = client.post(
        "/hub/schedules",
        json={"agents": ["infra@devbox"], "prompt": "check the tunnel", "interval_seconds": 300},
    )
    assert res.status_code == 200, res.text
    rows = bridge.sent["koord-1"]
    assert [r["id"] for r in rows][0] == "ab12cd34"
    new = rows[1]
    assert new["agents"] == ["infra@devbox"]
    assert new["prompt"] == "check the tunnel"
    assert new["interval_seconds"] == 300
    assert new["when_busy"] == "queue" and new["active"] is True
    assert res.json()["schedule"]["id"] == new["id"]


def test_update_changes_only_the_given_fields_and_bumps_revision(client, monkeypatch, tmp_path):
    (tmp_path / "schedules.json").write_text(json.dumps([_row()]))
    bridge = _coordinator_bridge()
    _install(monkeypatch, bridge)
    res = client.put("/hub/schedules/ab12cd34", json={"active": False, "prompt": "new words"})
    assert res.status_code == 200, res.text
    (row,) = bridge.sent["koord-1"]
    assert row["active"] is False and row["prompt"] == "new words"
    assert row["agents"] == ["lapis"] and row["anchor"] == 1000.0
    assert row["revision"] == 2


def test_delete_drops_the_row(client, monkeypatch, tmp_path):
    (tmp_path / "schedules.json").write_text(json.dumps([_row(), _row("ff00ff00")]))
    bridge = _coordinator_bridge()
    _install(monkeypatch, bridge)
    assert client.delete("/hub/schedules/ab12cd34").status_code == 200
    assert [r["id"] for r in bridge.sent["koord-1"]] == ["ff00ff00"]


def test_unknown_id_is_404(client, monkeypatch, tmp_path):
    (tmp_path / "schedules.json").write_text(json.dumps([_row()]))
    _install(monkeypatch, _coordinator_bridge())
    assert client.put("/hub/schedules/nope", json={"active": False}).status_code == 404
    assert client.delete("/hub/schedules/nope").status_code == 404


def test_a_bad_create_is_422_before_any_socket(client, monkeypatch):
    bridge = _coordinator_bridge()
    _install(monkeypatch, bridge)
    res = client.post("/hub/schedules", json={"agents": [], "prompt": "x", "interval_seconds": 60})
    assert res.status_code == 422
    assert bridge.sent == {}


def test_no_coordinator_taking_it_is_503(client, monkeypatch, tmp_path):
    bridge = _Bridge(
        agents=[{"agent_id": "lapis-1", "identity": "lapis"}],
        replies={"lapis-1": {"type": "schedules_save", "error": "not_coordinator"}},
    )
    _install(monkeypatch, bridge)
    res = client.post("/hub/schedules", json={"agents": ["lapis"], "prompt": "x", "interval_seconds": 60})
    assert res.status_code == 503
    assert not (tmp_path / "schedules.json").exists()


def test_a_coordinator_error_is_passed_through(client, monkeypatch):
    bridge = _Bridge(
        agents=[{"agent_id": "koord-1", "identity": "koordinator"}],
        replies={
            "koord-1": {
                "type": "schedules_save",
                "error": "the network primary did not take the change (failed)",
            }
        },
    )
    _install(monkeypatch, bridge)
    res = client.post("/hub/schedules", json={"agents": ["lapis"], "prompt": "x", "interval_seconds": 60})
    assert res.status_code == 502
    assert "primary" in res.json()["detail"]


def test_no_live_agent_writes_the_file_directly(client, monkeypatch, tmp_path):
    _install(monkeypatch, _Bridge(agents=[], replies={}))
    res = client.post("/hub/schedules", json={"agents": ["lapis"], "prompt": "x", "interval_seconds": 60})
    assert res.status_code == 200, res.text
    assert res.json()["via"] == "file"
    assert [r.prompt for r in ScheduleStore(tmp_path / "schedules.json").load()] == ["x"]


def test_one_coordinator_saving_wins_over_another_one_failing(client, monkeypatch):
    # Each project folder has its own coordinator and timer over the one
    # schedules.json; all of them are asked (so all wake), and one success
    # is a saved change even when another coordinator failed.
    bridge = _Bridge(
        agents=[
            {"agent_id": "koord-a", "identity": "koordinator"},
            {"agent_id": "koord-b", "identity": "koordinator"},
        ],
        replies={
            "koord-a": {"type": "schedules_save", "error": "save failed (OSError)"},
            "koord-b": {"type": "schedules_save", "ok": True, "via": "local"},
        },
    )
    _install(monkeypatch, bridge)
    res = client.post("/hub/schedules", json={"agents": ["lapis"], "prompt": "x", "interval_seconds": 60})
    assert res.status_code == 200, res.text
    assert set(bridge.sent) == {"koord-a", "koord-b"}
