"""ACP model/effort config option integration tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kollabor.commands.system_commands.handlers.model import ModelCommandHandler
from kollabor.llm.acp_bridge import ACPError, ACPRunner


class _Profile:
    name = "codex"

    def get_provider(self):
        return "acp"

    def get_model(self):
        return "harness-default"


class _Profiles:
    def __init__(self):
        self.profile = _Profile()

    def get_active_profile(self):
        return self.profile

    def list_profiles(self):
        return [self.profile]


class _ACP:
    def __init__(self, options):
        self.options = options
        self.calls = []

    def advertised_config_options(self):
        return [dict(option) for option in self.options]

    async def set_config_option(self, config_id, value):
        self.calls.append((config_id, value))
        for option in self.options:
            if option["id"] == config_id:
                option["currentValue"] = value
        return self.advertised_config_options()


def _handler(acp):
    bus = SimpleNamespace(get_service=lambda name: acp if name == "acp_orchestrator" else None)
    return ModelCommandHandler(None, bus, _Profiles(), None)


def _options():
    return [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": "harness-default",
            "options": [{"value": "harness-default", "name": "Default"}, {"value": "gpt-5", "name": "GPT-5"}],
        },
        {
            "id": "effort",
            "name": "Reasoning effort",
            "category": "thought_level",
            "type": "select",
            "currentValue": "medium",
            "options": [{"value": "low", "name": "Low"}, {"value": "medium", "name": "Medium"}],
        },
    ]


@pytest.mark.asyncio
async def test_advertised_model_and_effort_switch_and_reload():
    acp = _ACP(_options())
    handler = _handler(acp)

    listed = await handler.handle_model(SimpleNamespace(args=[]))
    assert listed.success
    assert "harness-default" in listed.message and "gpt-5" in listed.message

    switched = await handler.handle_model(SimpleNamespace(args=["set", "gpt-5"]))
    assert switched.success
    effort = await handler.handle_model(SimpleNamespace(args=["effort", "low"]))
    assert effort.success
    assert acp.calls == [("model", "gpt-5"), ("effort", "low")]
    assert acp.advertised_config_options()[0]["currentValue"] == "gpt-5"


@pytest.mark.asyncio
async def test_missing_advertised_option_is_refused():
    acp = _ACP([])
    handler = _handler(acp)
    result = await handler.handle_model(SimpleNamespace(args=["set", "gpt-5"]))
    assert not result.success
    assert "does not advertise" in result.message
    assert acp.calls == []


def test_native_profile_path_is_unchanged():
    profile = SimpleNamespace(name="native", get_provider=lambda: "openai", get_model=lambda: "gpt-5")
    manager = SimpleNamespace(get_active_profile=lambda: profile, list_profiles=lambda: [profile])
    handler = ModelCommandHandler(None, SimpleNamespace(get_service=lambda _: None), manager, None)
    assert handler._acp_options() == []


@pytest.mark.asyncio
async def test_runner_set_config_option_validates_and_refreshes():
    runner = object.__new__(ACPRunner)
    runner._connection = SimpleNamespace()
    runner.session_id = "session-1"
    runner.config_options = _options()

    async def set_config_option(config_id, session_id, value):
        assert (config_id, session_id, value) == ("model", "session-1", "gpt-5")
        updated = {**_options()[0], "currentValue": value}
        return SimpleNamespace(config_options=[SimpleNamespace(model_dump=lambda **_: updated)])

    runner._connection.set_config_option = set_config_option
    refreshed = await runner.set_config_option("model", "gpt-5")
    assert refreshed[0]["currentValue"] == "gpt-5"
    with pytest.raises(ACPError, match="not advertised"):
        await runner.set_config_option("model", "missing")

@pytest.mark.asyncio
async def test_model_panel_lists_and_switches_acp_options():
    from kollabor.panels.model import ModelPanel

    acp = _ACP(_options())
    ctx = SimpleNamespace(
        _profile_manager=_Profiles(),
        _event_bus=SimpleNamespace(get_service=lambda name: acp if name == "acp_orchestrator" else None),
        _llm_service=None,
    )
    panel = ModelPanel()
    description = await panel.describe(ctx, {})
    assert [row["id"] for row in description["rows"]] == ["harness-default", "gpt-5"]
    assert description["controls"][0]["options"] == ["low", "medium"]
    result = await panel.act(ctx, "select", {"model": "gpt-5"})
    assert result["ok"] is True
    result = await panel.act(ctx, "effort", {"level": "low"})
    assert result["ok"] is True
    assert acp.calls == [("model", "gpt-5"), ("effort", "low")]


def test_config_options_wire_shape_survives_reload_response():
    result = SimpleNamespace(
        config_options=[
            SimpleNamespace(
                model_dump=lambda **_: {
                    "id": "model",
                    "type": "select",
                    "currentValue": "gpt-5",
                    "options": [],
                }
            )
        ]
    )
    assert ACPRunner._wire_config_options(result)[0]["currentValue"] == "gpt-5"
