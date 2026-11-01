"""Issue #605: blank model/effort readout after an ACP profile switch.

The daemon's ``get_active_profile()`` overlays the live ACP harness's
model/effort onto the snapshot it returns, but the snapshot built at the
tail of ``_set_active_profile_owned`` (returned by ``set_active_profile``,
published on ``profile_changed``, and returned by the engine's
``POST /sessions/{id}/profile``) is built from the bare provider profile
object -- whose saved model/effort stay blank when a loadout or harness
owns them. Consumers therefore read ``''`` until they call
``GET /sessions/{id}/state``.

Regression contract (deterministic fakes, no live ACP):
- a plain ACP switch reports the harness's current model/effort;
- a loadout switch reports the loadout's model/effort and the resolved
  provider profile name;
- the engine route mirrors the resolved profile name so its session
  response is not clobbered by a stale pre-switch session dict.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kollabor.state.local import LocalStateService
from kollabor_ai import ProfileManager

_PROFILE_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "XAI_API_KEY",
    "XAI_MODEL",
    "ZAI_API_KEY",
    "ZAI_MODEL",
    "MOONSHOT_API_KEY",
    "MOONSHOT_MODEL",
    "KOLLAB_MODEL",
    "KOLLAB_API_KEY",
    "KOLLAB_NO_AUTO_DETECT",
)


def _acp_profile_data(tmp_path: Path) -> dict:
    return {
        "provider": "acp",
        "model": "",
        "command": ["claude-agent-acp", "--verbose"],
        "cwd": str(tmp_path),
        "workspace": str(tmp_path / "workspace"),
        "pass_env": ["CLAUDE_CONFIG_DIR"],
        "env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
        "install_guidance": "Install the test ACP adapter",
        "description": "Claude Code over ACP",
        "timeout": 17,
        "extra_headers": {"x-profile-test": "preserved"},
    }


def _write_config(home: Path, llm: dict) -> None:
    config_path = home / ".kollab" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"kollabor": {"llm": llm}}), encoding="utf-8")


class FakeOrchestrator:
    """Records starts and advertises the harness's live model/effort."""

    def __init__(self):
        self.active = False
        self.closed = 0
        self.starts: list = []

    async def start(self, profile, **kwargs):
        self.active = True
        self.starts.append((profile, kwargs))

    async def close(self):
        self.active = False
        self.closed += 1

    def advertised_config_options(self):
        return [
            {"id": "model", "category": "model", "currentValue": "haiku"},
            {"id": "effort", "category": "thought_level", "currentValue": "max"},
        ]


class FakeBus:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    def get_service(self, name):
        return self.orchestrator if name == "acp_orchestrator" else None


class StubConfig:
    """Runtime config handle: loadouts live here, not in the profile file."""

    def __init__(self, loadouts):
        self._loadouts = loadouts

    def get(self, key, default=None):
        if key == "kollabor.llm.loadouts":
            return self._loadouts
        return default


@pytest.fixture
def acp_lane(monkeypatch, tmp_path):
    """Isolated HOME with openai-oauth active + a claude-acp profile.

    The openai profile carries no credentials, so the loadout registry
    synthesizes zero implicit loadouts and the explicit ``review-acp``
    loadout is the only one -- keeping resolution deterministic offline.
    """
    for name in _PROFILE_ENV:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ProfileManager, "_detect_oauth_provider", lambda self: None
    )
    _write_config(
        home,
        {
            "active_profile": "openai-oauth",
            "default_profile": "openai-oauth",
            "profiles": {
                "openai-oauth": {"provider": "openai", "model": "gpt-x"},
                "claude-acp": {
                    "provider": "acp",
                    "model": "",
                    "command": ["claude-agent-acp"],
                },
            },
            "loadouts": {
                "review-acp": {
                    "provider_profile": "claude-acp",
                    "model": "haiku",
                    "effort": "max",
                }
            },
        },
    )
    manager = ProfileManager(
        config=StubConfig({
            "review-acp": {
                "provider_profile": "claude-acp",
                "model": "haiku",
                "effort": "max",
            }
        })
    )
    # claude-acp ships in config.json (the daemon's recovery reload re-reads
    # the file), so the loaded instance is the canonical one.
    harness = manager.get_profile("claude-acp")
    assert harness is not None
    assert manager.set_active_profile("openai-oauth")
    orchestrator = FakeOrchestrator()
    service = LocalStateService(
        Mock(), manager, event_bus=FakeBus(orchestrator)
    )
    return manager, service, orchestrator, harness


def test_plain_acp_switch_reports_harness_model_effort(acp_lane):
    manager, service, orchestrator, harness = acp_lane

    snapshot = asyncio.run(service.set_active_profile("claude-acp"))

    assert snapshot.name == "claude-acp"
    assert snapshot.is_active is True
    # The harness was started bare and advertises haiku/max; the switch
    # response must carry them, not the profile object's saved blanks.
    assert len(orchestrator.starts) == 1
    assert orchestrator.starts[0][0].name == "claude-acp"
    assert orchestrator.starts[0][1] == {}
    assert snapshot.model == "haiku"
    assert snapshot.effort == "max"


def test_loadout_switch_reports_loadout_model_effort_and_profile(acp_lane):
    manager, service, orchestrator, harness = acp_lane

    snapshot = asyncio.run(service.set_active_profile("review-acp"))

    # The loadout resolved onto the provider profile.
    assert snapshot.name == "claude-acp"
    assert manager.get_active_profile().name == "claude-acp"
    assert len(orchestrator.starts) == 1
    assert orchestrator.starts[0][0].name == "claude-acp"
    assert orchestrator.starts[0][1] == {"loadout": ("haiku", "max")}
    # The authoritative read must agree with the switch response.
    assert snapshot.model == "haiku"
    assert snapshot.effort == "max"
    authoritative = asyncio.run(service.get_active_profile())
    assert authoritative.model == "haiku"
    assert authoritative.effort == "max"


def test_engine_route_mirrors_resolved_profile_name(monkeypatch, tmp_path):
    """POST /sessions/{id}/profile must not clobber the response with a
    stale session dict when the request named a loadout."""
    from kollabor_engine.routes import sessions as sessions_routes
    from kollabor_engine.routes.sessions import SetProfileRequest

    for name in _PROFILE_ENV:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ProfileManager, "_detect_oauth_provider", lambda self: None
    )
    _write_config(
        home,
        {
            "active_profile": "openai-oauth",
            "default_profile": "openai-oauth",
            "profiles": {
                "openai-oauth": {"provider": "openai", "model": "gpt-x"},
                "claude-acp": {
                    "provider": "acp",
                    "model": "",
                    "command": ["claude-agent-acp"],
                },
            },
        },
    )

    class StubState:
        async def set_active_profile(self, name, model=None, effort=None):
            snap = SimpleNamespace(
                name="claude-acp",
                model="haiku",
                effort="max",
                provider="acp",
                is_active=True,
                to_dict=lambda: {
                    "name": "claude-acp",
                    "model": "haiku",
                    "effort": "max",
                    "is_active": True,
                },
            )
            return snap

    class StubSession:
        def __init__(self):
            self.session_id = "s1"
            self.alive = True
            self.profile = SimpleNamespace(
                name="default", model="gpt-x", effort="", provider="openai"
            )
            self.state = StubState()

        def to_dict(self):
            return {
                "profile": getattr(self.profile, "name", str(self.profile or "")),
                "model": getattr(self.profile, "model", ""),
                "effort": getattr(self.profile, "effort", "") or "",
            }

    session = StubSession()

    class StubRegistry:
        def get(self, session_id):
            return session if session_id == "s1" else None

    monkeypatch.setattr(
        sessions_routes, "get_session_registry", lambda: StubRegistry()
    )

    response = asyncio.run(
        sessions_routes.set_session_profile(
            "s1", SetProfileRequest(name="review-acp")
        )
    )

    # The response's session dict (spread after the snapshot) now agrees
    # with the daemon snapshot instead of the pre-switch "default".
    assert response["profile"] == "claude-acp"
    assert response["model"] == "haiku"
    assert response["effort"] == "max"
