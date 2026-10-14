"""Restoration capabilities must match the connection's session ownership."""

from pathlib import Path

import pytest

from kollabor.acp_server import KollabACPAgent


@pytest.mark.asyncio
@pytest.mark.parametrize("attach", [None, "owned-peer"])
async def test_initialize_advertises_restoration_only_for_owned_sessions(
    tmp_path, monkeypatch, attach
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agent = KollabACPAgent(attach=attach)
    response = await agent.initialize(1)
    capabilities = response.model_dump(by_alias=True, exclude_none=True)[
        "agentCapabilities"
    ]

    assert capabilities["loadSession"] is (attach is None)
    assert "close" in capabilities["sessionCapabilities"]
    assert ("resume" in capabilities["sessionCapabilities"]) is (attach is None)
    assert agent.sessions == {}
    assert not (tmp_path / ".kollab").exists()
