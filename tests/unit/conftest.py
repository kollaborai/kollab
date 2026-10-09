"""Fixtures shared by several tests/unit modules."""

import pytest

from kollabor_config import config_utils
from plugins.hub import presence, relay_client
from tests.unit.test_relay_agent_bridge import bridges  # noqa: F401

_REAL_CONFIG_DIR = config_utils.get_config_directory()


@pytest.fixture(autouse=True)
def _no_live_agents_from_unit_tests(monkeypatch):
    """No unit test reaches an agent running on this computer.

    live_agents_on_machine() reads the real ~/.kollab presence, and a "machine" or
    "network" broadcast sends to every live agent it lists: three broadcast tests
    sent "shipped phase B" and "stand down" into the live proofs' sessions and any
    chat open here. A test with its own config directory (where_home) reads that.
    """
    real = presence.live_agents_on_machine

    def guarded():
        if config_utils.get_config_directory() == _REAL_CONFIG_DIR:
            return []
        return real()

    monkeypatch.setattr(presence, "live_agents_on_machine", guarded)


@pytest.fixture
def unthrottled_relay(monkeypatch):
    """Lift the relay client's send budget (20 burst, 8/s) for in-process meshes.

    Several nodes handshake and sync back to back on one loop, which spends far more
    frames per real second than any device does; a fast run drains the bucket and the
    refused sends fail the test on timing alone. The limiter has its own tests.
    """
    monkeypatch.setattr(relay_client, "SEND_BURST", 1e9)
    monkeypatch.setattr(relay_client, "SEND_RATE_PER_SECOND", 1e9)
