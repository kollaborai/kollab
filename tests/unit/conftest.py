"""Fixtures shared by several tests/unit modules."""

import pytest

from plugins.hub import relay_client
from tests.unit.test_relay_agent_bridge import bridges  # noqa: F401


@pytest.fixture
def unthrottled_relay(monkeypatch):
    """Lift the relay client's send budget (20 burst, 8/s) for in-process meshes.

    Several nodes handshake and sync back to back on one loop, which spends far more
    frames per real second than any device does; a fast run drains the bucket and the
    refused sends fail the test on timing alone. The limiter has its own tests.
    """
    monkeypatch.setattr(relay_client, "SEND_BURST", 1e9)
    monkeypatch.setattr(relay_client, "SEND_RATE_PER_SECOND", 1e9)
