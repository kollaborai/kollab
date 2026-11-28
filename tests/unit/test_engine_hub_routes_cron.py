"""GET /hub/cron-jobs: aggregate scheduled cron jobs across live agents (#612).

Each agent owns its own cron schedule in memory; the engine route asks
every live agent's socket the local-only `cron_jobs` question and tags
each row with the agent's identity. An agent that does not answer is
reported separately and never blocks the others.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]
from kollabor_engine.routes import hub as hub_routes

from plugins.hub.messenger import AgentSocketServer


def _job(job_id, target="lapis", agent_message="report the pulse"):
    return {
        "id": job_id,
        "target": target,
        "message": agent_message,
        "interval_seconds": 3600,
        "interval": "1h",
        "next_fire": 2800.0,
        "next_in_seconds": 1800,
        "recurring": True,
        "mode": "recurring",
        "created_at": -2600.0,
        "last_error": "",
    }


class _StubBridge:
    """Presence rows + canned socket replies, exactly what the route touches."""

    def __init__(self, agents, replies):
        self._agents = agents
        self._replies = replies
        self.cache_flags = []

    def get_agents(self, use_cache=True):
        self.cache_flags.append(use_cache)
        return self._agents

    async def query_socket(self, agent_id, action, payload=None, timeout=5.0):
        return self._replies.get(agent_id)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="hub-cron-routes-test-"))
        self.app = FastAPI()
        self.app.include_router(hub_routes.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _install(self, bridge):
        self._old_bridge = hub_routes._bridge
        hub_routes._bridge = bridge
        self.addCleanup(setattr, hub_routes, "_bridge", self._old_bridge)


class TestAggregateCronJobs(_Fixture):
    def test_rows_come_back_tagged_with_the_agent_identity(self):
        bridge = _StubBridge(
            agents=[
                {"agent_id": "moonstone-1", "identity": "moonstone"},
                {"agent_id": "koordinator-1", "identity": "koordinator"},
            ],
            replies={
                "moonstone-1": {
                    "type": "cron_jobs",
                    "jobs": [_job("aaaa1111"), _job("bbbb2222")],
                    "error": "",
                },
                "koordinator-1": {
                    "type": "cron_jobs",
                    "jobs": [_job("cccc3333", target="all")],
                    "error": "",
                },
            },
        )
        self._install(bridge)

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(data["count"], 3)
        self.assertEqual(
            [(row["agent"], row["id"]) for row in data["jobs"]],
            [("moonstone", "aaaa1111"), ("moonstone", "bbbb2222"), ("koordinator", "cccc3333")],
        )
        self.assertEqual(data["agents_responded"], ["moonstone", "koordinator"])
        self.assertEqual(data["agents_unavailable"], [])

    def test_a_silent_agent_is_reported_and_never_blocks_the_others(self):
        bridge = _StubBridge(
            agents=[
                {"agent_id": "ghost-1", "identity": "ghost"},
                {"agent_id": "ruby-1", "identity": "ruby"},
            ],
            replies={
                # ghost answers nothing; ruby has an empty schedule.
                "ruby-1": {"type": "cron_jobs", "jobs": [], "error": ""},
            },
        )
        self._install(bridge)

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(data["jobs"], [])
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["agents_responded"], ["ruby"])
        self.assertEqual(data["agents_unavailable"], ["ghost"])

    def test_a_wrong_frame_type_counts_as_unavailable(self):
        bridge = _StubBridge(
            agents=[{"agent_id": "odd-1", "identity": "odd"}],
            replies={"odd-1": {"type": "status"}},  # not cron_jobs
        )
        self._install(bridge)

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(data["jobs"], [])
        self.assertEqual(data["agents_unavailable"], ["odd"])

    def test_malformed_job_rows_are_reported_without_crashing_aggregation(self):
        bridge = _StubBridge(
            agents=[{"agent_id": "lapis-1", "identity": "lapis"}],
            replies={
                "lapis-1": {"type": "cron_jobs", "jobs": ["bad-row"], "error": ""}
            },
        )
        self._install(bridge)

        data = asyncio.run(hub_routes.list_cron_jobs())

        self.assertEqual(data["jobs"], [])
        self.assertIn("lapis", data["agents_errors"])

    def test_empty_roster_returns_an_empty_view(self):
        self._install(_StubBridge(agents=[], replies={}))

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(
            data,
            {
                "jobs": [],
                "count": 0,
                "agents_responded": [],
                "agents_unavailable": [],
                "agents_errors": {},
            },
        )

    def test_an_operator_gate_rejection_is_surfaced_not_swallowed(self):
        """The dispatcher's local-operator gate answers a typed error frame.

        The engine must report the agent as unavailable AND carry the
        rejection message, not silently render an empty schedule.
        """
        bridge = _StubBridge(
            agents=[{"agent_id": "ghost-1", "identity": "ghost"}],
            replies={
                "ghost-1": {
                    "type": "error",
                    "msg": "local operator authorization required",
                }
            },
        )
        self._install(bridge)

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(data["jobs"], [])
        self.assertEqual(data["agents_responded"], [])
        self.assertEqual(data["agents_unavailable"], ["ghost"])
        self.assertEqual(
            data["agents_errors"], {"ghost": "local operator authorization required"}
        )

    def test_a_cron_reply_with_an_error_keeps_rows_and_names_the_failure(self):
        bridge = _StubBridge(
            agents=[{"agent_id": "sick-1", "identity": "sick"}],
            replies={
                "sick-1": {
                    "type": "cron_jobs",
                    "jobs": [_job("dddd4444")],
                    "error": "cron jobs callback returned a non-list",
                }
            },
        )
        self._install(bridge)

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(data["count"], 1)
        self.assertEqual(data["agents_responded"], ["sick"])
        self.assertEqual(
            data["agents_errors"], {"sick": "cron jobs callback returned a non-list"}
        )

    def test_queries_run_concurrently_not_one_agent_at_a_time(self):
        """The aggregation loop must not serialize per-agent awaits.

        Each stub query parks until all expected calls are in flight;
        if the route awaited them one at a time, at most one would ever
        be in flight and the barrier would time out.
        """
        import asyncio

        class _BarrierBridge(_StubBridge):
            def __init__(self, agents, replies):
                super().__init__(agents, replies)
                self.expected = len(agents)
                self.in_flight = 0
                self.max_in_flight = 0
                self.release = asyncio.Event()

            async def query_socket(self, agent_id, action, payload=None, timeout=5.0):
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                if self.in_flight >= self.expected:
                    self.release.set()
                try:
                    await asyncio.wait_for(self.release.wait(), timeout=2.0)
                finally:
                    self.in_flight -= 1
                return self._replies.get(agent_id)

        roster = [{"agent_id": f"a{i}-1", "identity": f"a{i}"} for i in range(3)]
        bridge = _BarrierBridge(
            agents=roster,
            replies={row["agent_id"]: {"type": "cron_jobs", "jobs": [], "error": ""} for row in roster},
        )
        self._install(bridge)

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(data["agents_responded"], ["a0", "a1", "a2"])
        self.assertGreaterEqual(bridge.max_in_flight, 3)

    def test_refresh_query_bypasses_the_presence_cache(self):
        bridge = _StubBridge(agents=[], replies={})
        self._install(bridge)

        self.client.get("/hub/cron-jobs?refresh=true").json()

        self.assertEqual(bridge.cache_flags, [False])


@pytest.mark.asyncio
async def test_engine_route_reads_cron_jobs_from_a_real_agent_socket(monkeypatch):
    socket_name = f"cron-route-{os.getpid()}-{id(monkeypatch)}"
    server = AgentSocketServer(
        "cron-route-agent",
        lambda _message: None,
        on_cron_jobs=lambda: [_job("real-socket-row")],
        socket_name=socket_name,
    )
    socket_path = await server.start()

    class SocketBridge:
        def get_agents(self, use_cache=True):
            return [{"agent_id": "cron-route-agent", "identity": "socket-agent"}]

        async def query_socket(self, agent_id, action, payload=None, timeout=5.0):
            assert agent_id == "cron-route-agent"
            assert action == "cron_jobs"
            reader, writer = await asyncio.open_unix_connection(socket_path)
            try:
                writer.write((json.dumps({"action": action}) + "\n").encode())
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout)
                return json.loads(line.decode())
            finally:
                writer.close()
                await writer.wait_closed()

    old_bridge = hub_routes._bridge
    hub_routes._bridge = SocketBridge()
    app = FastAPI()
    app.include_router(hub_routes.router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/hub/cron-jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["agents_responded"] == ["socket-agent"]
        assert [(row["agent"], row["id"]) for row in data["jobs"]] == [
            ("socket-agent", "real-socket-row")
        ]
    finally:
        hub_routes._bridge = old_bridge
        await server.stop()


def test_cron_jobs_route_requires_engine_bearer_auth(monkeypatch):
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    from kollabor_engine.server import create_app

    response = TestClient(create_app()).get("/hub/cron-jobs")

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing bearer token"}


if __name__ == "__main__":
    unittest.main()
