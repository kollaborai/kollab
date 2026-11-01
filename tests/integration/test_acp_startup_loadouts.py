"""Explicit ACP loadouts must become active or fail before daemon readiness."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import kollabor.application as app_module
from kollabor.llm import acp_orchestrator as acp_module
from kollabor_config import config_utils
from tests.unit.test_acp_effort_restore import _options
from tests.unit.test_acp_orchestrator import Bus, FakeRunner


def _application(tmp_path, monkeypatch, selection, model='haiku', effort='max', **extra):
    project = tmp_path / 'project'
    home = tmp_path / 'home'
    config = tmp_path / 'config'
    for path in (project, home, config):
        path.mkdir(exist_ok=True)
    loadout = {'provider_profile': 'claude-code', 'model': model, 'effort': effort, **extra}
    llm = {'default_profile': 'claude-code', 'loadouts': {'chosen': loadout}}
    if selection in ('default', 'default-harness'):
        llm['default_loadout'] = {'name': 'claude-code' if selection == 'default-harness' else 'chosen'}
    (config / 'config.json').write_text(json.dumps({'kollabor': {'llm': llm}}))
    monkeypatch.chdir(project)
    monkeypatch.setattr(config_utils, 'get_config_directory', lambda: config)
    monkeypatch.setattr(config_utils, 'get_project_data_dir', lambda *args: tmp_path / 'data')
    monkeypatch.setattr(Path, 'home', lambda: home)
    registry = SimpleNamespace(get_merged_config=lambda: {}, list_plugins=lambda: [],
                               instantiate_plugins=lambda *args, **kwargs: {})
    app = app_module.TerminalLLMChat(
        args=SimpleNamespace(simple=True),
        profile_name='chosen' if selection == 'named' else None,
        plugin_registry=registry,
    )
    app.event_bus = Bus()
    return app


def _runner(monkeypatch, scenario, created, entered=None):
    class ControlledRunner(FakeRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.options = _options()
            if scenario == 'missing-model':
                self.options = self.options[1:]
            if scenario == 'missing-effort':
                self.options = self.options[:1]
            if scenario == 'unlisted-current-model':
                self.options[0]['currentValue'] = 'haiku'
                self.options[0]['options'] = [{'value': 'sonnet'}]
            if scenario == 'unlisted-current-effort':
                self.options[1]['currentValue'] = 'max'
                self.options[1]['options'] = [{'value': 'xhigh'}]
            created.append(self)

        def advertised_config_options(self):
            return [dict(o) for o in self.options]

        async def set_config_option(self, config_id, value):
            option = next(o for o in self.options if o['id'] == config_id)
            if scenario == 'cancel' and config_id == 'effort':
                entered.set()
                await asyncio.Event().wait()
            if value not in [item['value'] for item in option['options']]:
                raise ValueError('unadvertised option')
            if (scenario, config_id) not in {('wrong-model', 'model'), ('wrong-effort', 'effort')}:
                option['currentValue'] = value
            return self.advertised_config_options()

    monkeypatch.setattr(acp_module, 'ACPRunner', ControlledRunner)


@pytest.mark.asyncio
@pytest.mark.parametrize('selection', ['named', 'default'])
@pytest.mark.parametrize('scenario,model,effort', [
    ('bad-model', 'unknown-model', 'max'),
    ('bad-effort', 'haiku', 'ultra'),
    ('missing-model', 'haiku', 'max'),
    ('missing-effort', 'haiku', 'max'),
    ('wrong-model', 'haiku', 'max'),
    ('wrong-effort', 'haiku', 'max'),
    ('unlisted-current-model', 'haiku', 'max'),
    ('unlisted-current-effort', 'haiku', 'max'),
    ('valid', 'haiku', 'max'),
])
async def test_startup_loadout_is_confirmed_before_ready(tmp_path, monkeypatch, selection, scenario, model, effort):
    app = _application(tmp_path, monkeypatch, selection, model, effort)
    saved = {'claude-code': {'model': 'sonnet', 'effort': 'xhigh'}}
    options_path = tmp_path / 'data/acp/options.json'
    options_path.parent.mkdir(parents=True)
    options_path.write_text(json.dumps(saved))
    created = []
    _runner(monkeypatch, scenario, created)
    if scenario == 'valid':
        await app._initialize_acp_orchestrator()
        orch = app.acp_orchestrator
        assert orch.active
        assert [x['currentValue'] for x in orch.advertised_config_options()] == ['haiku', 'max']
        assert any(e.get('type') == 'acp_session' and e.get('state') == 'ready'
                   for e in app.event_bus.services['display_tap'].events)
        await orch.close()
    else:
        with pytest.raises(ValueError, match='loadout'):
            await app._initialize_acp_orchestrator()
        orch = app.event_bus.services['acp_orchestrator']
        assert not orch.active and orch.runner is None
        assert app.acp_orchestrator is None and created[0].closed
        assert not any(e.get('type') == 'acp_session' and e.get('state') == 'ready'
                       for e in app.event_bus.services['display_tap'].events)
    assert json.loads(options_path.read_text()) == saved


@pytest.mark.parametrize('selection', ['named', 'default'])
@pytest.mark.parametrize('field,value', [('temperature', 0.2), ('max_tokens', 123)])
def test_unsupported_native_loadout_fields_fail_before_harness(tmp_path, monkeypatch, selection, field, value):
    with pytest.raises(ValueError, match=field):
        _application(tmp_path, monkeypatch, selection, **{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize('selection', ['named', 'default'])
async def test_cancel_during_partial_loadout_start_closes_runner_without_ready(tmp_path, monkeypatch, selection):
    app = _application(tmp_path, monkeypatch, selection)
    created = []
    entered = asyncio.Event()
    _runner(monkeypatch, 'cancel', created, entered)
    task = asyncio.create_task(app._initialize_acp_orchestrator())
    await asyncio.wait_for(entered.wait(), timeout=5)
    orch = app.event_bus.services['acp_orchestrator']
    assert not orch.active
    with pytest.raises(acp_module.ACPError, match='not active|starting'):
        orch.admit('no turn', request_id='test', source='human')
    with pytest.raises(acp_module.ACPError, match='not active|starting'):
        await orch.set_config_option('effort', 'max')
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].closed and orch.runner is None and not orch.active
    assert not any(e.get('type') == 'acp_session' and e.get('state') == 'ready'
                   for e in app.event_bus.services['display_tap'].events)


@pytest.mark.asyncio
async def test_unavailable_saved_pick_without_loadout_remains_nonfatal(tmp_path, monkeypatch):
    app = _application(tmp_path, monkeypatch, 'named')
    app._startup_acp_loadout = None
    options_path = tmp_path / 'data/acp/options.json'
    options_path.parent.mkdir(parents=True)
    saved = {'claude-code': {'effort': 'ultra'}}
    options_path.write_text(json.dumps(saved))
    created = []
    _runner(monkeypatch, 'valid', created)
    await app._initialize_acp_orchestrator()
    assert app.acp_orchestrator.active
    assert any('ultra' in notice for notice in app.acp_orchestrator.restore_notices)
    assert json.loads(options_path.read_text()) == saved
    await app.acp_orchestrator.close()


@pytest.mark.asyncio
async def test_default_implicit_harness_does_not_use_executable_as_model(tmp_path, monkeypatch):
    app = _application(tmp_path, monkeypatch, 'default-harness')
    assert app._startup_acp_loadout == ('claude-code', '', '')
    created = []
    _runner(monkeypatch, 'valid', created)
    await app._initialize_acp_orchestrator()
    assert app.acp_orchestrator.active
    assert [o['currentValue'] for o in app.acp_orchestrator.advertised_config_options()] == ['sonnet', 'xhigh']
    await app.acp_orchestrator.close()


# A real child using the ACP SDK proves the startup boundary and cleanup over
# stdio, without connecting to a provider or using any account credentials.
ACP_SDK_AGENT = r'''
import asyncio, os
from pathlib import Path
import acp
from acp.schema import InitializeResponse, NewSessionResponse, SetSessionConfigOptionResponse

class Agent:
    def __init__(self):
        self.model = 'sonnet'
        self.effort = 'xhigh'
    def options(self):
        return [
            {'id':'model', 'name':'Model', 'category':'model', 'type':'select',
             'currentValue':self.model,
             'options':[{'value':v, 'name':v} for v in ('sonnet','haiku')]},
            {'id':'effort', 'name':'Effort', 'category':'thought_level', 'type':'select',
             'currentValue':self.effort,
             'options':[{'value':v, 'name':v} for v in ('high','xhigh','max')]},
        ]
    async def initialize(self, protocol_version, **kwargs):
        Path('adapter.pid').write_text(str(os.getpid()))
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId='test-session', configOptions=self.options())
    async def set_config_option(self, config_id, session_id, value, **kwargs):
        if config_id == 'model': self.model = value
        if config_id == 'effort': self.effort = value
        return SetSessionConfigOptionResponse(configOptions=self.options())
    def on_connect(self, conn):
        pass

asyncio.run(acp.run_agent(Agent()))
'''


@pytest.mark.asyncio
@pytest.mark.parametrize('model,effort,valid', [
    ('unknown-model', 'max', False),
    ('haiku', 'ultra', False),
    ('haiku', 'max', True),
])
async def test_real_acp_child_confirms_or_cleans_up_startup(tmp_path, monkeypatch, model, effort, valid):
    import sys

    import psutil

    app = _application(tmp_path, monkeypatch, 'named', model, effort)
    adapter = tmp_path / 'sdk_agent.py'
    adapter.write_text(ACP_SDK_AGENT)
    profile = app.profile_manager.get_active_profile()
    profile.command = (sys.executable, str(adapter))
    profile.cwd = str(tmp_path)
    if valid:
        await app._initialize_acp_orchestrator()
        assert app.acp_orchestrator.active
        assert [o['currentValue'] for o in app.acp_orchestrator.advertised_config_options()] == ['haiku', 'max']
        await app.acp_orchestrator.close()
    else:
        with pytest.raises(ValueError, match='loadout'):
            await app._initialize_acp_orchestrator()
        assert app.acp_orchestrator is None
        assert not app.event_bus.services['acp_orchestrator'].active
        assert not list((tmp_path / 'data/acp/sessions').glob('*.json'))
    pid = int((tmp_path / 'adapter.pid').read_text())
    assert not psutil.pid_exists(pid)
