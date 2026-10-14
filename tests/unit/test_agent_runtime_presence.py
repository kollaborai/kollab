"""Public ACP state survives the presence file boundary."""

import json

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.presence import PresenceManager


PUBLIC_TAGS = {
    "harness": "Codex CLI",
    "provider": "acp",
    "acp_waiting": False,
    "acp_ask_waiting": True,
}


def test_presence_round_trip_retains_only_public_operational_tags():
    runtime = AgentRuntime(
        name="coder", identity="alpha", project="/workspace", profile="codex-cli",
        tags={**PUBLIC_TAGS, "private_key": "private", "api_token": "secret"},
    )
    record = runtime.to_presence_dict()
    assert record["tags"] == PUBLIC_TAGS
    assert record["agent_name"] == "coder" and record["profile_name"] == "codex-cli"
    record["tags"]["api_token"] = "secret"
    restored = AgentRuntime.from_presence_dict(json.loads(json.dumps(record)))
    assert restored.tags == PUBLIC_TAGS
    restored.tags["acp_ask_waiting"] = False
    assert runtime.tags["acp_ask_waiting"] is True
    assert {"private_key", "api_token"}.isdisjoint(restored.to_dict()["tags"])


@pytest.mark.parametrize("tags", [None, [], "private", {
    "harness": {"private_key": "secret"}, "provider": ["acp"],
    "acp_waiting": "false", "acp_ask_waiting": 1, "private_key": "secret",
}])
def test_presence_reader_ignores_malformed_or_private_tags(tags):
    restored = AgentRuntime.from_presence_dict({"agent_name": "alpha", "tags": tags})
    assert restored.tags == {}
    assert AgentRuntime(name="alpha", tags=tags).to_presence_dict()["tags"] == {}
    assert AgentRuntime.from_presence_dict({"agent_name": "alpha"}).tags == {}


@pytest.mark.asyncio
async def test_published_waiting_state_is_discovered_and_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: tmp_path)
    alpha = PresenceManager(AgentRuntime(name="coder", identity="alpha", tags=dict(PUBLIC_TAGS)))
    beta = PresenceManager(AgentRuntime(name="coder", identity="beta"))
    alpha.publish()
    assert beta.discover_agents()[0].tags == PUBLIC_TAGS
    assert (await beta.discover_agents_async())[0].tags == PUBLIC_TAGS
    alpha.identity.tags["acp_ask_waiting"] = False
    alpha.publish()
    assert beta.discover_agents()[0].tags["acp_ask_waiting"] is False
    alpha.identity.tags.clear()
    alpha.publish()
    assert (await beta.discover_agents_async())[0].tags == {}
