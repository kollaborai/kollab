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


@pytest.fixture(autouse=True)
def no_real_network_roster(tmp_path, monkeypatch):
    """The network roster GET /sessions keeps goes to a temp file."""
    import kollabor_engine.routes.sessions as sessions  # type: ignore[import-not-found]

    path = tmp_path / "engine-network.json"
    monkeypatch.setattr(sessions, "_network_cache_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def no_real_vaults(tmp_path, monkeypatch):
    """Vaults (which agents ran here) are read from a temp hub, not the developer's."""
    monkeypatch.setattr("plugins.hub.presence.get_hub_dir", lambda: tmp_path / "hub")


@pytest.fixture(autouse=True)
def no_real_memberships(tmp_path, monkeypatch):
    """Network memberships (#158 aggregate) read from a temp root, not the
    developer's real device record."""
    import kollabor_engine.network_aggregate as network_aggregate  # type: ignore[import-not-found]

    monkeypatch.setattr(
        network_aggregate,
        "_root",
        lambda home=None: home if home is not None else tmp_path,
    )
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_recent_workspaces(tmp_path, monkeypatch):
    """Recent workspaces go to a temp file."""
    import kollabor_engine.routes.workspaces as workspaces  # type: ignore[import-not-found]

    path = tmp_path / "engine-workspaces.json"
    monkeypatch.setattr(workspaces, "_recent_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def no_real_hidden_agents(tmp_path, monkeypatch):
    """The sidebar rows the user hid go to a temp file."""
    import kollabor_engine.hidden_agents as hidden_agents  # type: ignore[import-not-found]

    path = tmp_path / "hub" / "hidden-agents.json"
    monkeypatch.setattr(hidden_agents, "hidden_path", lambda: path)
    monkeypatch.setattr(hidden_agents, "_learned", {})
    return path


@pytest.fixture(autouse=True)
def no_real_sidebar_order(tmp_path, monkeypatch):
    """The saved sidebar order goes to a temp file."""
    import kollabor_engine.sidebar_order as sidebar_order  # type: ignore[import-not-found]

    path = tmp_path / ".kollab" / "hub" / "sidebar-order.json"
    monkeypatch.setattr(sidebar_order, "order_path", lambda: path)
    return path
