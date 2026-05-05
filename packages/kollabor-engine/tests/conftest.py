"""Test configuration and fixtures."""

import pytest


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    """Bypass auth middleware for all tests."""
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
