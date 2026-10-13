"""HubBridge.get_agent_output reads the socket's {"type": "output", "lines": [...]} reply."""
import asyncio

from kollabor_engine.hub_bridge import HubBridge


def test_get_agent_output_joins_socket_lines(monkeypatch):
    bridge = HubBridge()

    async def reply(agent_id, action, payload=None):
        assert (agent_id, action, payload) == ("a1", "get_output", {"lines": 5})
        return {"type": "output", "lines": ["first", "second"]}

    monkeypatch.setattr(bridge, "query_socket", reply)
    assert asyncio.run(bridge.get_agent_output("a1", lines=5)) == "first\nsecond"


def test_get_agent_capture_passes_opt_in_parameters(monkeypatch):
    bridge = HubBridge()

    async def reply(agent_id, action, payload=None):
        assert payload == {"lines": 5, "format": "json", "max_chars": 100, "cursor": "2"}
        return {"type": "output", "lines": [], "next_cursor": None}

    monkeypatch.setattr(bridge, "query_socket", reply)
    result = asyncio.run(bridge.get_agent_capture("a1", lines=5, max_chars=100, cursor="2"))
    assert result["type"] == "output"


def test_get_agent_output_is_none_without_an_output_reply(monkeypatch):
    bridge = HubBridge()

    async def reply(agent_id, action, payload=None):
        return None

    monkeypatch.setattr(bridge, "query_socket", reply)
    assert asyncio.run(bridge.get_agent_output("a1")) is None
