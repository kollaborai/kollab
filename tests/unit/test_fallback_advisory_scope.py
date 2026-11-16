"""Automatic fallback has no routing capability, including transient routing."""

import asyncio
import inspect
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor_ai.loadout_manager import Loadout, LoadoutManager
from kollabor_ai.profile_manager import LLMProfile, ProfileManager
from kollabor_events.models import EventType
from kollabor_tui.display_tap import DisplayTap
from plugins.model_fallback_plugin import ModelFallbackPlugin
from tests.unit.test_model_fallback import _Bus, _Config


def _setup(tmp_path, shared, target_provider="openai", same_profile=False):
    path = tmp_path / "config.json"
    original = b'{"active_profile":"primary","profiles":{"backup":{"model":"saved"}}}\n'
    path.write_bytes(original)
    config = _Config({"plugins.model_fallback.loadout": "recovery"})
    # Use the actual manager's accessors without startup discovery or user state.
    profiles = object.__new__(ProfileManager)
    profiles._profiles = {
        "primary": LLMProfile("primary", "openai", model="limited"),
        "backup": LLMProfile("backup", target_provider, model="saved"),
    }
    profiles._active_profile_name = "primary"
    profiles.config = config
    snapshot = deepcopy(profiles._profiles)
    # Any activation, even an in-memory mutation followed by restoration, fails.
    profiles.update_profile = Mock(side_effect=AssertionError("profile mutation"))
    profiles.set_active_profile = Mock(side_effect=AssertionError("shared activation"))
    config.save_key = Mock(side_effect=AssertionError("config write"))
    loadout = Loadout("recovery", "primary" if same_profile else "backup", "replacement")
    tap = DisplayTap()
    api = SimpleNamespace(model="limited", reinitialize_provider=AsyncMock())
    harness = SimpleNamespace(
        runner=SimpleNamespace(busy=False), closing_reason="", active=True,
        hold_observations=Mock(), release_observations=Mock(),
        close=AsyncMock(), start=AsyncMock(),
    )
    state = SimpleNamespace(set_active_profile=AsyncMock())
    services = {"profile_manager": profiles, "display_tap": tap,
                "llm_service": SimpleNamespace(api_service=api), "acp_orchestrator": harness}
    if shared:
        services["state_service"] = state
    bus = _Bus(services)
    plugin = ModelFallbackPlugin("model_fallback", bus, None, config)
    plugin.resolve = Mock(return_value=loadout)
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True], ids=["direct-manager", "shared-state"])
@pytest.mark.parametrize("source", ["native", "acp"])
@pytest.mark.parametrize("target_provider", ["openai", "acp"])
@pytest.mark.parametrize("same_profile", [False, True], ids=["different-profile", "same-profile"])
async def test_failure_advises_manual_selection_without_any_routing_window(
    tmp_path, shared, source, target_provider, same_profile
):
    case = _setup(tmp_path, shared, target_provider, same_profile)
    await case.plugin.initialize()
    await case.plugin.register_hooks()
    if source == "native":
        data = {"message": "Rate limit exceeded.", "streamed": False}
        await case.plugin._on_request_error(data, None)
        assert data.get("retry") is not True
    else:
        case.tap.publish({"type": "error", "message": "Rate limit exceeded.", "generation": 1})
        case.tap.publish({"type": "turn_complete"})
        for _ in range(5):
            await asyncio.sleep(0)

    notices = [e for e in case.tap.get_snapshot() if e["type"] == "model_fallback"]
    assert len(notices) == 1
    assert "Automatic switching is disabled" in notices[0]["message"]
    assert "/llm recovery" in notices[0]["message"]
    assert "Switched" not in notices[0]["message"]
    # Another session shares these exact objects, including during the notice.
    assert case.profiles.get_active_profile().name == "primary"
    assert case.profiles._profiles == case.snapshot
    assert case.path.read_bytes() == case.original
    case.profiles.update_profile.assert_not_called()
    case.profiles.set_active_profile.assert_not_called()
    case.config.save_key.assert_not_called()
    case.state.set_active_profile.assert_not_awaited()
    case.api.reinitialize_provider.assert_not_awaited()
    case.harness.hold_observations.assert_not_called()
    case.harness.release_observations.assert_not_called()
    case.harness.close.assert_not_awaited()
    case.harness.start.assert_not_awaited()
    assert case.harness.closing_reason == ""
    assert not [e for e, _ in case.bus.emitted if e == EventType.TRIGGER_LLM_CONTINUE]
    # Later input and completion have no deferred restoration or resend behavior.
    await case.bus.fire(EventType.USER_INPUT_PRE)
    case.tap.publish({"type": "turn_complete"})
    await asyncio.sleep(0)
    assert case.profiles._profiles == case.snapshot
    assert len([e for e in case.tap.get_snapshot() if e["type"] == "model_fallback"]) == 1
    await case.plugin.shutdown()


def test_activation_has_no_temporary_or_current_run_capability():
    for method in (LoadoutManager.activate, LoadoutManager._activate_owned):
        parameters = inspect.signature(method).parameters
        assert "for_current_run" not in parameters
        assert "persist" not in parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_native_notice_is_once_per_failure_and_does_not_request_a_retry(tmp_path, streamed):
    case = _setup(tmp_path, False)
    await case.plugin.initialize()
    await case.plugin.register_hooks()
    data = {"message": "Usage limit reached.", "streamed": streamed}
    await case.plugin._on_request_error(data, None)
    assert data.get("retry") is not True
    assert len([e for e in case.tap.get_snapshot() if e["type"] == "model_fallback"]) == 1
    await case.plugin.shutdown()


@pytest.mark.asyncio
async def test_busy_harness_is_untouched_and_notice_waits_only_for_failed_turn_end(tmp_path):
    case = _setup(tmp_path, False)
    case.harness.runner.busy = True
    await case.plugin.initialize()
    case.tap.publish({"type": "error", "message": "Rate limit exceeded.", "generation": 1})
    assert not [e for e in case.tap.get_snapshot() if e["type"] == "model_fallback"]
    case.tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    notices = [e for e in case.tap.get_snapshot() if e["type"] == "model_fallback"]
    assert len(notices) == 1
    assert "Automatic switching is disabled" in notices[0]["message"]
    case.harness.hold_observations.assert_not_called()
    await case.plugin.shutdown()


@pytest.mark.asyncio
async def test_late_and_replaced_taps_receive_advice_only_on_current_tap(tmp_path):
    case = _setup(tmp_path, False)
    case.bus.services.pop("display_tap")
    await case.plugin.initialize()
    await case.plugin.register_hooks()
    case.bus.services["display_tap"] = case.tap
    await case.bus.fire(EventType.SYSTEM_STARTUP)
    old = case.tap
    current = DisplayTap()
    case.bus.services["display_tap"] = current
    await case.bus.fire(EventType.SYSTEM_STARTUP)
    for tap in (old, current):
        tap.publish({"type": "error", "message": "Rate limit exceeded.", "generation": 1})
        tap.publish({"type": "turn_complete"})
    for _ in range(5):
        await asyncio.sleep(0)
    assert len([e for e in current.get_snapshot() if e["type"] == "model_fallback"]) == 1
    assert not [e for e in old.get_snapshot() if e["type"] == "model_fallback"]
    await case.plugin.shutdown()


@pytest.mark.asyncio
async def test_missing_target_and_profile_resolution_remain_visible(tmp_path):
    case = _setup(tmp_path, True)
    await case.plugin.initialize()
    del case.plugin.resolve  # exercise the actual loadout/profile resolver
    assert case.plugin.resolve("backup").provider_profile == "backup"
    assert case.plugin.resolve("missing") is None
    await case.plugin._on_request_error({"message": "Rate limit exceeded."}, None)
    notices = [e for e in case.tap.get_snapshot() if e["type"] == "model_fallback"]
    assert len(notices) == 1
    assert "not found" in notices[0]["message"]
    await case.plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "recovery"])
async def test_command_explains_that_fallback_is_advisory(tmp_path, query):
    from kollabor.commands.system_commands.handlers.loadout import LoadoutCommandHandler

    case = _setup(tmp_path, True)
    await case.plugin.initialize()
    case.plugin.set_choice = Mock(return_value=True)
    handler = LoadoutCommandHandler(None, case.bus)
    handler._get_manager = Mock(return_value=None)
    result = await handler._fallback(query)
    assert result.success
    assert "Automatic switching is disabled" in result.message
    await case.plugin.shutdown()
