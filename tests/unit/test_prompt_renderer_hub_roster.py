"""_render_hub_roster merges remote agents into the hub roster.

docs/specs/agent-network-simple-flow.md section 7: `<trender type="hub_roster" />`
renders the same merged agent@device list as the hub context block. Run with
PYTHONPATH=packages/kollabor-ai/src so the edited copy of prompt_renderer.py
is imported instead of the canonical dev install's.
"""

from __future__ import annotations

from types import SimpleNamespace

from kollabor_ai.prompt_renderer import PromptRenderer


def _agent(identity, *, is_coordinator=False, state="idle", current_task="", profile_name=""):
    return SimpleNamespace(
        effective_identity=lambda: identity,
        is_coordinator=is_coordinator,
        capabilities=[],
        state=state,
        current_task=current_task,
        state_changed_at=0,
        profile_name=profile_name,
    )


def _renderer_with_hub(hub):
    event_bus = SimpleNamespace(get_service=lambda name: hub if name == "hub_plugin" else None)
    return PromptRenderer(event_bus=event_bus)


def test_no_peers_and_no_remote_agents_shows_empty_roster():
    hub = SimpleNamespace(_presence=SimpleNamespace(get_cached_agents=lambda: []))
    renderer = _renderer_with_hub(hub)

    roster = renderer._render_hub_roster()

    assert "no peers online." in roster


def test_remote_agents_are_merged_as_agent_at_device():
    hub = SimpleNamespace(
        _presence=SimpleNamespace(get_cached_agents=lambda: [_agent("koordinator", is_coordinator=True)]),
        _remote_agent_rows=lambda: [
                {"name": "infra", "device": "home-server", "handle": "infra@home-server", "state": "idle"},
                {
                    "name": "ops",
                    "device": "home-server",
                    "handle": "ops@home-server",
                    "state": "working",
                    "task": "rotating logs",
                },
            ],
    )
    renderer = _renderer_with_hub(hub)

    roster = renderer._render_hub_roster()

    assert "koordinator (coordinator)" in roster
    assert "infra@home-server - idle" in roster
    assert "ops@home-server - working: rotating logs" in roster


def test_remote_agents_alone_still_render_a_roster():
    """No local peers, but the network has agents -- still not 'no peers online'."""
    hub = SimpleNamespace(
        _presence=SimpleNamespace(get_cached_agents=lambda: []),
        _remote_agent_rows=lambda: [{"handle": "infra@home-server", "state": "idle"}],
    )
    renderer = _renderer_with_hub(hub)

    roster = renderer._render_hub_roster()

    assert "no peers online." not in roster
    assert "infra@home-server - idle" in roster


def test_missing_remote_agents_method_degrades_gracefully():
    hub = SimpleNamespace(
        _presence=SimpleNamespace(get_cached_agents=lambda: [_agent("koordinator")]),
        _relay_agent=None,
    )
    renderer = _renderer_with_hub(hub)

    roster = renderer._render_hub_roster()

    assert "koordinator" in roster
