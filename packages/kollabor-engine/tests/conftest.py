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


@pytest.fixture(autouse=True)
def no_real_presence(monkeypatch):
    """Hub presence starts empty: the engine lists every live agent on this
    computer, so without this a test would see the developer's own agents."""
    import kollabor_engine.hub_bridge as hub_bridge  # type: ignore[import-not-found]

    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [])
