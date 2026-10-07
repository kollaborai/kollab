"""Test configuration and fixtures."""

import pytest


@pytest.fixture(autouse=True)
def appearance_store(tmp_path, monkeypatch):
    """Gem looks go to a temp file: listing live gems records their births."""
    import kollabor_engine.gem_appearance as gem_appearance  # type: ignore[import-not-found]

    path = tmp_path / "hub" / "appearance.json"
    monkeypatch.setattr(gem_appearance, "appearance_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    """Bypass auth middleware for all tests."""
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
