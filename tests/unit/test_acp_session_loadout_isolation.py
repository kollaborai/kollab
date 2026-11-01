"""Session-only overrides and explicit loadouts keep distinct scopes."""
import json
from types import SimpleNamespace

import pytest

from kollabor.state.local import LocalStateService
from tests.unit.test_acp_effort_restore import _options, _orchestrator


@pytest.mark.asyncio
async def test_session_effort_does_not_change_saved_options_or_another_daemon(tmp_path):
    saved = {"model": "sonnet", "effort": "xhigh"}
    first_options = _options()
    first = _orchestrator(tmp_path, first_options, saved=saved)
    await LocalStateService._apply_acp_effort(first, "max")

    assert first_options[1]["currentValue"] == "max"
    assert json.loads(first._options_path.read_text())["claude-code"] == saved

    second_options = _options()
    second_options[1]["currentValue"] = "high"
    second = _orchestrator(tmp_path, second_options)
    profile = SimpleNamespace(name="claude-code", model="", effort="")
    await second._restore_options(profile)
    assert second_options[1]["currentValue"] == "xhigh"
    assert ("effort", "max") not in second._runner.calls


@pytest.mark.asyncio
async def test_saved_picks_beat_ordinary_profile_defaults(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options, saved={"model": "haiku", "effort": "max"})
    profile = SimpleNamespace(name="claude-code", model="sonnet", effort="xhigh")
    await orch._restore_options(profile)
    assert options[0]["currentValue"] == "haiku"
    assert options[1]["currentValue"] == "max"


@pytest.mark.asyncio
async def test_explicit_loadout_beats_saved_picks_without_rewriting_them(tmp_path):
    saved = {"model": "sonnet", "effort": "xhigh"}
    options = _options()
    orch = _orchestrator(tmp_path, options, saved=saved)
    orch._loadout_override = ("claude-code", "haiku", "max")
    profile = SimpleNamespace(name="claude-code", model="sonnet", effort="xhigh")
    await orch._restore_options(profile)
    assert options[0]["currentValue"] == "haiku"
    assert options[1]["currentValue"] == "max"
    assert json.loads(orch._options_path.read_text())["claude-code"] == saved


@pytest.mark.asyncio
async def test_session_effort_survives_same_daemon_restore_without_leaking(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options, saved={"effort": "xhigh"})
    await LocalStateService._apply_acp_effort(orch, "max")
    orch._forget_session_picks()
    options[1]["currentValue"] = "high"
    await orch._restore_options(SimpleNamespace(name="claude-code", model="", effort=""))
    assert options[1]["currentValue"] == "max"
    assert json.loads(orch._options_path.read_text())["claude-code"] == {"effort": "xhigh"}


@pytest.mark.asyncio
async def test_custom_effort_category_stays_session_scoped(tmp_path):
    options = _options()
    options[1]['id'] = 'reasoning_budget'
    options[1]['category'] = 'effort'
    orch = _orchestrator(tmp_path, options, saved={'effort': 'xhigh'})
    await LocalStateService._apply_acp_effort(orch, 'max')
    assert options[1]['currentValue'] == 'max'
    assert json.loads(orch._options_path.read_text())['claude-code'] == {'effort': 'xhigh'}
    orch._forget_session_picks()
    options[1]['currentValue'] = 'high'
    await orch._restore_options(SimpleNamespace(name='claude-code', model='', effort=''))
    assert options[1]['currentValue'] == 'max'


@pytest.mark.asyncio
async def test_profile_snapshot_reports_harness_effort_not_profile_default(tmp_path):
    from kollabor_ai.profile_manager import LLMProfile

    options = _options()
    orch = _orchestrator(tmp_path, options)
    await LocalStateService._apply_acp_effort(orch, 'max')
    profile = LLMProfile(name='claude-code', provider='acp', command=('fixture',), effort='xhigh')
    manager = SimpleNamespace(get_active_profile=lambda: profile)
    bus = SimpleNamespace(get_service=lambda name: orch if name == 'acp_orchestrator' else None)
    service = LocalStateService(None, manager, event_bus=bus)
    snapshot = await service.get_active_profile()
    assert snapshot.effort == 'max'
    assert profile.effort == 'xhigh'


async def _loadout_fixture(tmp_path, monkeypatch):
    from kollabor.llm import acp_orchestrator as mod
    from kollabor_ai.loadout_manager import LoadoutManager
    from kollabor_ai.profile_manager import LLMProfile
    from tests.unit.test_acp_orchestrator import Bus, FakeRunner
    from tests.unit.test_loadout_manager import _FakeProfileManager, _MemoryConfig

    class ConfigRunner(FakeRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.options = _options()

        def advertised_config_options(self):
            return [dict(o) for o in self.options]

        async def set_config_option(self, config_id, value):
            option = next(o for o in self.options if o['id'] == config_id)
            if value not in [choice['value'] for choice in option['options']]:
                raise ValueError('unadvertised option')
            option['currentValue'] = value
            return self.advertised_config_options()

    monkeypatch.setattr('kollabor_config.config_utils.get_project_data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(mod, 'ACPRunner', ConfigRunner)
    config = _MemoryConfig({'kollabor': {'llm': {'loadouts': {
        'haiku-max': {'provider_profile': 'claude-code', 'model': 'haiku', 'effort': 'max'},
        'not-offered': {'provider_profile': 'claude-code', 'model': 'haiku', 'effort': 'ultra'},
    }}}})
    profile = LLMProfile(name='claude-code', provider='acp', command=('fixture',))
    pm = _FakeProfileManager([profile], config=config)
    pm.set_active_profile('claude-code')
    bus = Bus()
    bus.services['llm_service'] = SimpleNamespace(config=config, session_stats={})
    orch = mod.ACPOrchestrator(bus)
    await orch.start(profile)
    bus.services['acp_orchestrator'] = orch
    return LoadoutManager(pm, config), pm, bus, orch


@pytest.mark.asyncio
async def test_cli_loadout_command_applies_real_orchestrator_options(tmp_path, monkeypatch):
    from kollabor.commands.system_commands.handlers.loadout import LoadoutCommandHandler

    manager, pm, bus, orch = await _loadout_fixture(tmp_path, monkeypatch)
    handler = LoadoutCommandHandler(None, bus, profile_manager=pm)
    monkeypatch.setattr(handler, '_get_manager', lambda: manager)
    try:
        result = await handler.handle_loadout(SimpleNamespace(args=['haiku-max']))
        assert result.success
        options = orch.advertised_config_options()
        assert [o['currentValue'] for o in options] == ['haiku', 'max']
        assert json.loads(orch._options_path.read_text())['claude-code'] == {'model': 'haiku', 'effort': 'max'}
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_web_loadout_panel_applies_real_orchestrator_options(tmp_path, monkeypatch):
    from kollabor.panels import llm as llm_panel

    manager, pm, bus, orch = await _loadout_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(llm_panel, '_manager', lambda _ctx: manager)
    ctx = SimpleNamespace(_profile_manager=pm, _llm_service=bus.services['llm_service'], _event_bus=bus)
    try:
        result = await llm_panel.PANELS['llm'].act(ctx, 'activate', {'name': 'haiku-max'})
        assert result['ok']
        assert [o['currentValue'] for o in orch.advertised_config_options()] == ['haiku', 'max']
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_refused_loadout_restores_previous_harness(tmp_path, monkeypatch):
    manager, pm, bus, orch = await _loadout_fixture(tmp_path, monkeypatch)
    try:
        result = await manager.activate(manager.get('not-offered'), bus)
        assert result is None
        assert pm.get_active_profile().name == 'claude-code'
        assert orch.active
        assert [o['currentValue'] for o in orch.advertised_config_options()] == ['sonnet', 'xhigh']
        assert not orch._options_path.exists()
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_implicit_acp_harness_entry_does_not_treat_adapter_as_model(tmp_path, monkeypatch):
    manager, _pm, bus, orch = await _loadout_fixture(tmp_path, monkeypatch)
    try:
        implicit = next(lo for lo in manager.list_loadouts() if lo.name == 'claude-code')
        assert implicit.implicit and implicit.model == 'fixture'
        assert await manager.activate(implicit, bus)
        assert [o['currentValue'] for o in orch.advertised_config_options()] == ['sonnet', 'xhigh']
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_web_form_can_create_and_activate_an_acp_loadout(tmp_path, monkeypatch):
    from kollabor.panels import llm as llm_panel

    manager, pm, bus, orch = await _loadout_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(llm_panel, '_manager', lambda _ctx: manager)
    ctx = SimpleNamespace(_profile_manager=pm, _llm_service=bus.services['llm_service'], _event_bus=bus)
    try:
        panel = llm_panel.PANELS['llm']
        form = (await panel.act(ctx, 'new', {'name': 'claude-code'}))['open']
        fields = {field['path']: field for field in form['sections'][0]['fields']}
        assert fields['model']['editable'] is True
        assert fields['temperature']['editable'] is False
        assert fields['max_tokens']['editable'] is False
        result = await panel.act(ctx, 'save', {
            'context': form['context'],
            'changes': {'name': 'custom-haiku', 'model': 'haiku', 'effort': 'max'},
        })
        assert result['ok'] and 'switched to it' in result['message']
        saved = manager.get('custom-haiku')
        assert (saved.model, saved.effort, saved.temperature, saved.max_tokens) == ('haiku', 'max', None, None)
        assert [o['currentValue'] for o in orch.advertised_config_options()] == ['haiku', 'max']
    finally:
        await orch.close()


@pytest.mark.asyncio
async def test_unreadable_saved_options_are_preserved(tmp_path):
    options = _options()
    orch = _orchestrator(tmp_path, options)
    orch._options_path.write_text('{invalid json')
    await orch.set_config_option('effort', 'max')
    assert orch._options_path.read_text() == '{invalid json'
    assert options[1]['currentValue'] == 'max'
    orch._forget_session_picks()
    options[1]['currentValue'] = 'high'
    await orch._restore_options(SimpleNamespace(name='claude-code', model='', effort=''))
    assert options[1]['currentValue'] == 'max'


def test_concurrent_confirmed_picks_preserve_other_profiles(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    first = _orchestrator(tmp_path, _options())
    second = _orchestrator(tmp_path, _options())
    second._profile_name = 'other-profile'
    barrier = Barrier(2)
    def save(orch, model):
        barrier.wait()
        orch._save_confirmed_options({'model': model})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, first, 'haiku'), pool.submit(save, second, 'sonnet')]
        for future in futures:
            future.result()
    assert json.loads(first._options_path.read_text()) == {
        'claude-code': {'model': 'haiku'}, 'other-profile': {'model': 'sonnet'},
    }


@pytest.mark.asyncio
async def test_cancelled_loadout_activation_restores_previous_harness(tmp_path, monkeypatch):
    import asyncio

    manager, pm, bus, orch = await _loadout_fixture(tmp_path, monkeypatch)
    original_start = orch.start
    entered = asyncio.Event()
    never = asyncio.Event()
    async def delayed_start(profile, **kwargs):
        if kwargs.get('loadout'):
            entered.set()
            await never.wait()
        return await original_start(profile, **kwargs)
    monkeypatch.setattr(orch, 'start', delayed_start)
    try:
        task = asyncio.create_task(manager.activate(manager.get('haiku-max'), bus))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert orch.active
        assert pm.get_active_profile().name == 'claude-code'
        assert [o['currentValue'] for o in orch.advertised_config_options()] == ['sonnet', 'xhigh']
    finally:
        await orch.close()
