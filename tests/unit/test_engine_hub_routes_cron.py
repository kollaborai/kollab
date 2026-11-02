"""GET /hub/cron-jobs: aggregate scheduled cron jobs across live agents (#612).

Each agent owns its own cron schedule in memory; the engine route asks
every live agent's socket the local-only `cron_jobs` question and tags
each row with the agent's identity. An agent that does not answer is
reported separately and never blocks the others.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]

from kollabor_engine.routes import hub as hub_routes


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

    def test_empty_roster_returns_an_empty_view(self):
        self._install(_StubBridge(agents=[], replies={}))

        data = self.client.get("/hub/cron-jobs").json()

        self.assertEqual(
            data, {"jobs": [], "count": 0, "agents_responded": [], "agents_unavailable": []}
        )

    def test_refresh_query_bypasses_the_presence_cache(self):
        bridge = _StubBridge(agents=[], replies={})
        self._install(bridge)

        self.client.get("/hub/cron-jobs?refresh=true").json()

        self.assertEqual(bridge.cache_flags, [False])


if __name__ == "__main__":
    unittest.main()
