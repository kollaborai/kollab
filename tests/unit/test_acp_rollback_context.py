"""A rejected ACP switch must say when the rollback lost context or could not restart (#502)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm import acp_orchestrator as acp_mod
from kollabor.llm.acp_state import ACPBinding
from kollabor.state.local import LocalStateService
from kollabor_ai.loadout_manager import Loadout, LoadoutManager
from kollabor_ai.profile_manager import LLMProfile
from tests.unit.test_acp_effort_restore import _options
from tests.unit.test_acp_orchestrator import Bus, FakeRunner
from tests.unit.test_loadout_manager import _FakeProfileManager, _MemoryConfig


async def _switch(tmp_path, monkeypatch, *, resumable, restore_fails):
    """Start `claude-code`, then switch to `other-acp` with an effort the harness refuses."""
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda *a: tmp_path / "data")
    created = []

    class Runner(FakeRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.options = _options()
            created.append(self)

        def advertised_config_options(self):
            return [dict(o) for o in self.options]

        async def start(self, binding=None):
            if restore_fails and self.profile.name == "claude-code" and len(created) > 2:
                raise RuntimeError("adapter exited")
            self.requested_binding = binding
            self.binding = ACPBinding(f"session-{len(created)}", "/w", self.profile.name, ("fixture",), {})
            self.resume_mode = "resumed" if resumable and binding is not None else "fresh"
            await super().start(binding=binding)

        async def set_config_option(self, config_id, value):
            if self.profile.name == "other-acp" and config_id == "effort":
                raise ValueError("effort refused")
            option = next(o for o in self.options if o["id"] == config_id)
            option["currentValue"] = value
            return self.advertised_config_options()

    monkeypatch.setattr(acp_mod, "ACPRunner", Runner)
    first = LLMProfile(name="claude-code", provider="acp", command=("fixture",))
    other = LLMProfile(name="other-acp", provider="acp", command=("fixture",))
    pm = _FakeProfileManager([first, other], config=_MemoryConfig({}))
    pm.set_active_profile("claude-code")
    shown = MagicMock()
    bus = Bus()
    bus.services["llm_service"] = SimpleNamespace(
        config=_MemoryConfig({}), session_stats={}, message_display_service=shown,
    )
    orch = acp_mod.ACPOrchestrator(bus)
    bus.services["acp_orchestrator"] = orch
    state = LocalStateService(bus.services["llm_service"], pm, event_bus=bus)
    await orch.start(first)
    shown.reset_mock()
    with pytest.raises(ValueError) as caught:
        await state.set_active_profile("other-acp", effort="max")
    said = " | ".join(str(c.args[0]) for c in shown.display_system_message.call_args_list)
    return orch, created, str(caught.value), said


@pytest.mark.asyncio
async def test_rollback_on_a_non_resumable_harness_says_the_context_was_lost(tmp_path, monkeypatch):
    orch, created, error, said = await _switch(tmp_path, monkeypatch, resumable=False, restore_fails=False)
    try:
        assert orch.active and orch.runner.profile.name == "claude-code"
        assert created[2].requested_binding is not None  # the rollback did ask for the old session
        assert "lost" in said and "claude-code" in said and "context" in said
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_rollback_on_a_resumable_harness_keeps_context_and_says_nothing_was_lost(tmp_path, monkeypatch):
    orch, created, error, said = await _switch(tmp_path, monkeypatch, resumable=True, restore_fails=False)
    try:
        assert orch.runner.resume_mode == "resumed"
        assert "lost" not in said and "fresh context" not in said
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_a_failed_restart_after_a_rejected_switch_says_it_failed(tmp_path, monkeypatch):
    orch, created, error, said = await _switch(tmp_path, monkeypatch, resumable=False, restore_fails=True)
    assert not orch.active
    assert "effort refused" in error
    assert "could not be restarted" in error


@pytest.mark.asyncio
async def test_a_failed_restart_after_a_rejected_loadout_tells_the_chat():
    previous = LLMProfile(name="previous", provider="acp", command=("fixture",))
    selected = LLMProfile(name="selected", provider="acp", command=("fixture",))
    pm = _FakeProfileManager([previous, selected])
    pm.active_name = "previous"
    manager = LoadoutManager(pm, config=_MemoryConfig())
    orchestrator = SimpleNamespace(
        close=AsyncMock(), _show_notice=MagicMock(),
        start=AsyncMock(side_effect=[RuntimeError("new session failed"), RuntimeError("adapter exited")]),
    )
    bus = SimpleNamespace(get_service=lambda name: orchestrator if name == "acp_orchestrator" else None)
    assert await manager.activate(Loadout("selected-loadout", "selected", "claude-sonnet"), bus) is None
    orchestrator._show_notice.assert_called_once()
    assert "could not be restarted" in orchestrator._show_notice.call_args.args[0]
