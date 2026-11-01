"""An ACP startup owns its runner until success or cleanup completes."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from kollabor_engine.routes import sessions

from kollabor.llm import acp_orchestrator as acp_mod
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor_ai.loadout_manager import LoadoutManager
from kollabor_ai.profile_manager import LLMProfile
from kollabor_rpc.server import RpcServer
from tests.unit.test_acp_effort_restore import _options
from tests.unit.test_acp_orchestrator import Bus, FakeRunner
from tests.unit.test_loadout_manager import _FakeProfileManager, _MemoryConfig


async def _setup(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    created = []

    class Runner(FakeRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.options = _options()
            created.append(self)

        def advertised_config_options(self):
            return [dict(o) for o in self.options]

        async def set_config_option(self, config_id, value):
            if self.profile.name == 'claude-code' and config_id == 'model':
                entered.set()
                await release.wait()
                if self.closed:
                    raise RuntimeError('original runner was closed')
                raise ValueError('harness refuses model')
            option = next(o for o in self.options if o['id'] == config_id)
            option['currentValue'] = value
            return self.advertised_config_options()

    monkeypatch.setattr(acp_mod, 'ACPRunner', Runner)
    monkeypatch.setattr('kollabor_config.config_utils.get_project_data_dir', lambda *args: tmp_path / 'data')
    first = LLMProfile(name='claude-code', provider='acp', command=('fixture',))
    other = LLMProfile(name='other-acp', provider='acp', command=('fixture',))
    native = LLMProfile(name='native-profile', provider='openai', model='fixture')
    config = _MemoryConfig({'kollabor': {'llm': {'loadouts': {
        'other-preset': {'provider_profile': 'other-acp', 'model': 'haiku', 'effort': 'max'},
    }}}})
    pm = _FakeProfileManager([first, other, native], config=config)
    pm.set_active_profile('claude-code')
    bus = Bus()
    bus.services['llm_service'] = SimpleNamespace(config=config, session_stats={})
    orch = acp_mod.ACPOrchestrator(bus)
    bus.services['acp_orchestrator'] = orch
    state = LocalStateService(bus.services['llm_service'], pm, event_bus=bus)
    manager = LoadoutManager(pm, config)
    return first, pm, bus, orch, state, manager, created, entered, release


async def _rpc(server, request_id, **params):
    return await server.handle_wire({'action': 'rpc_request', 'request_id': request_id,
                                     'method': 'state.set_active_profile', 'params': params})


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['error', 'cancel'])
async def test_overlapping_state_rpc_web_and_loadouts_cannot_replace_start_owner(tmp_path, monkeypatch, outcome):
    from kollabor.commands.system_commands.handlers.loadout import LoadoutCommandHandler
    from kollabor.panels import llm as llm_panel

    first, pm, bus, orch, state, manager, created, entered, release = await _setup(tmp_path, monkeypatch)
    server = RpcServer()
    register_state_handlers(server, state)
    startup = asyncio.create_task(orch.start(first, loadout=('haiku', 'max')))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        owner = created[0]
        assert not orch.active
        assert pm.get_active_profile().name == 'claude-code'

        # The real registered state-RPC dispatcher returns an actionable refusal.
        reply = await asyncio.wait_for(_rpc(server, 'during-start', name='other-acp'), timeout=5)
        assert 'start' in reply['result']['error'].lower()
        assert len(created) == 1 and orch.runner is owner and not owner.closed

        # The actual web Session Apply route reaches the same state service.
        session = SimpleNamespace(alive=True, state=state, session_id='probe')
        monkeypatch.setattr(sessions, 'get_session_registry', lambda: SimpleNamespace(get=lambda _id: session))
        web = FastAPI()
        web.include_router(sessions.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url='http://test') as client:
            response = await client.post('/sessions/probe/profile', json={'name': 'other-acp'})
        assert response.status_code == 400 and 'start' in response.json()['detail'].lower()

        # Native profile changes, terminal loadouts, and web loadouts are siblings.
        native = await _rpc(server, 'native-during-start', name='native-profile')
        assert 'start' in native['result']['error'].lower()
        handler = LoadoutCommandHandler(None, bus, profile_manager=pm)
        monkeypatch.setattr(handler, '_get_manager', lambda: manager)
        terminal = await handler.handle_loadout(SimpleNamespace(args=['other-preset']))
        assert not terminal.success
        monkeypatch.setattr(llm_panel, '_manager', lambda _ctx: manager)
        ctx = SimpleNamespace(_profile_manager=pm, _llm_service=bus.services['llm_service'], _event_bus=bus)
        panel = await llm_panel.PANELS['llm'].act(ctx, 'activate', {'name': 'other-preset'})
        assert not panel['ok']
        assert len(created) == 1 and orch.runner is owner and not owner.closed
        assert pm.get_active_profile().name == 'claude-code'

        # Direct callers cannot bypass the startup owner either.
        with pytest.raises(acp_mod.ACPError, match='progress|starting'):
            await orch.start(pm.get_profile('other-acp'))
        with pytest.raises(acp_mod.ACPError, match='progress|starting'):
            await orch.close()
        assert orch.runner is owner and not owner.closed

        if outcome == 'cancel':
            startup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await startup
        else:
            release.set()
            with pytest.raises(ValueError, match='loadout'):
                await startup
        assert owner.closed and orch.runner is None and not orch.active

        # Once the older attempt has finished, a retry may activate the other
        # profile and the resulting runner can actually accept a turn.
        reply = await asyncio.wait_for(_rpc(server, 'after-start', name='other-acp'), timeout=5)
        assert not reply.get('error') and 'error' not in reply['result']
        successor = orch.runner
        assert successor is not owner and orch.active and not successor.closed
        assert pm.get_active_profile().name == 'other-acp'
        result = await orch.submit('hello', source='rpc')
        assert result['text'] == 'hello'

        # A late callback from the previous runner cannot publish readiness for
        # the successor or alter its binding/ownership.
        events = bus.services['display_tap'].events
        ready_before = sum(e.get('type') == 'acp_session' and e.get('state') == 'ready' for e in events)
        await orch._runner_lifecycle('ready', owner)
        ready_after = sum(e.get('type') == 'acp_session' and e.get('state') == 'ready' for e in events)
        assert ready_after == ready_before and orch.runner is successor and orch.active
    finally:
        release.set()
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await orch.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['error', 'cancel', 'success'])
async def test_session_effort_activation_owns_rollback_until_previous_runner_restored(tmp_path, monkeypatch, outcome):
    first, pm, bus, orch, state, _manager, created, _entered, _release = await _setup(tmp_path, monkeypatch)
    base_runner = acp_mod.ACPRunner
    effort_entered, effort_release = asyncio.Event(), asyncio.Event()
    third = LLMProfile(name='third-acp', provider='acp', command=('fixture',))
    pm._profiles[third.name] = third

    class BlockEffort(base_runner):
        async def set_config_option(self, config_id, value):
            if self.profile.name == 'other-acp' and config_id == 'effort':
                effort_entered.set()
                await effort_release.wait()
                if outcome != 'success':
                    raise ValueError('effort refused')
            return await super().set_config_option(config_id, value)

    monkeypatch.setattr(acp_mod, 'ACPRunner', BlockEffort)
    await orch.start(first)
    events = bus.services['display_tap'].events
    ready_before = sum(e.get('type') == 'acp_session' and e.get('state') == 'ready' for e in events)
    server = RpcServer()
    register_state_handlers(server, state)
    switching = asyncio.create_task(state.set_active_profile('other-acp', effort='max'))
    try:
        await asyncio.wait_for(effort_entered.wait(), timeout=5)
        partial = orch.runner
        assert partial.profile.name == 'other-acp' and not orch.active
        assert sum(e.get('type') == 'acp_session' and e.get('state') == 'ready' for e in events) == ready_before
        with pytest.raises(acp_mod.ACPError, match='not active'):
            orch.admit('no turn', request_id='pending', source='rpc')
        with pytest.raises(acp_mod.ACPError, match='not active'):
            await orch.set_config_option('effort', 'high')
        reply = await asyncio.wait_for(_rpc(server, 'other-while-effort', name='third-acp'), timeout=5)
        assert 'progress' in reply['result']['error'].lower()
        assert orch.runner is partial and not partial.closed
        assert pm.get_active_profile().name == 'claude-code'
        if outcome == 'cancel':
            switching.cancel()
            with pytest.raises(asyncio.CancelledError):
                await switching
        elif outcome == 'success':
            effort_release.set()
            result = await switching
            assert result.name == 'other-acp' and orch.runner is partial
            assert orch.active and not partial.closed
            assert pm.get_active_profile().name == 'other-acp'
        else:
            effort_release.set()
            with pytest.raises(ValueError, match='effort'):
                await switching
        if outcome != 'success':
            assert partial.closed and orch.active
            assert orch.runner.profile.name == 'claude-code'
            assert pm.get_active_profile().name == 'claude-code'
        assert sum(e.get('type') == 'acp_session' and e.get('state') == 'ready' for e in events) == ready_before + 1
        reply = await asyncio.wait_for(_rpc(server, 'third-after-rollback', name='third-acp'), timeout=5)
        assert not reply.get('error') and 'error' not in reply['result']
        successor = orch.runner
        assert successor.profile.name == 'third-acp' and not successor.closed
        assert (await orch.submit('hello', source='rpc'))['text'] == 'hello'
        assert orch.runner is successor and orch.active
    finally:
        effort_release.set()
        if not switching.done():
            switching.cancel()
        await asyncio.gather(switching, return_exceptions=True)
        await orch.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_owned_loadout_transition_and_closes_restored_runner(tmp_path, monkeypatch):
    first, _pm, bus, orch, _state, manager, created, _entered, _release = await _setup(tmp_path, monkeypatch)
    base_runner = acp_mod.ACPRunner
    entered = asyncio.Event()

    class BlockModel(base_runner):
        async def set_config_option(self, config_id, value):
            if self.profile.name == 'other-acp' and config_id == 'model':
                entered.set()
                await asyncio.Event().wait()
            return await super().set_config_option(config_id, value)

    monkeypatch.setattr(acp_mod, 'ACPRunner', BlockModel)
    await orch.start(first)
    activation = asyncio.create_task(manager.activate(manager.get('other-preset'), bus))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await asyncio.wait_for(orch.shutdown(), timeout=5)
    assert activation.cancelled()
    assert not orch.active and orch.runner is None
    assert all(runner.closed for runner in created)


REAL_OVERLAP_AGENT = r'''
import asyncio, os, sys
from pathlib import Path
import acp
from acp.schema import (AgentMessageChunk, InitializeResponse, NewSessionResponse,
                        PromptResponse, SetSessionConfigOptionResponse, TextContentBlock)

class Agent:
    def __init__(self):
        self.model = 'sonnet'
        self.effort = 'xhigh'
    def options(self):
        return [
            {'id':'model', 'name':'Model', 'category':'model', 'type':'select',
             'currentValue':self.model,
             'options':[{'value':v,'name':v} for v in ('sonnet','haiku')]},
            {'id':'effort', 'name':'Effort', 'category':'thought_level', 'type':'select',
             'currentValue':self.effort,
             'options':[{'value':v,'name':v} for v in ('high','xhigh','max')]},
        ]
    async def initialize(self, protocol_version, **kwargs):
        Path(sys.argv[1] + '.pid').write_text(str(os.getpid()))
        return InitializeResponse(protocolVersion=1)
    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        return NewSessionResponse(sessionId=sys.argv[1], configOptions=self.options())
    async def set_config_option(self, config_id, session_id, value, **kwargs):
        if sys.argv[1] == 'first' and config_id == 'model':
            Path('blocked').write_text('yes')
            while not Path('release').exists():
                await asyncio.sleep(0.02)
            return SetSessionConfigOptionResponse(configOptions=self.options())
        if config_id == 'model': self.model = value
        if config_id == 'effort': self.effort = value
        return SetSessionConfigOptionResponse(configOptions=self.options())
    async def prompt(self, session_id, prompt, **kwargs):
        await self.conn.session_update(session_id, AgentMessageChunk(
            sessionUpdate='agent_message_chunk',
            content=TextContentBlock(type='text', text='synthetic response'),
        ))
        return PromptResponse(stopReason='end_turn')
    def on_connect(self, conn):
        self.conn = conn

asyncio.run(acp.run_agent(Agent()))
'''


@pytest.mark.asyncio
async def test_real_acp_child_overlap_refuses_rpc_then_successor_handles_turn(tmp_path, monkeypatch):
    import sys

    import psutil

    actual_runner = acp_mod.ACPRunner
    first, pm, bus, orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(acp_mod, 'ACPRunner', actual_runner)
    adapter = tmp_path / 'agent.py'
    adapter.write_text(REAL_OVERLAP_AGENT)
    first.command, first.cwd = (sys.executable, str(adapter), 'first'), str(tmp_path)
    other = pm.get_profile('other-acp')
    other.command, other.cwd = (sys.executable, str(adapter), 'second'), str(tmp_path)
    server = RpcServer()
    register_state_handlers(server, state)
    startup = asyncio.create_task(orch.start(first, loadout=('haiku', 'max')))
    try:
        async with asyncio.timeout(20):
            while not (tmp_path / 'blocked').exists():
                if startup.done():
                    await startup
                await asyncio.sleep(0.02)
        first_pid = int((tmp_path / 'first.pid').read_text())
        owner = orch.runner
        assert psutil.pid_exists(first_pid) and not orch.active
        reply = await asyncio.wait_for(_rpc(server, 'real-overlap', name='other-acp'), timeout=5)
        assert 'progress' in reply['result']['error'].lower()
        assert orch.runner is owner and psutil.pid_exists(first_pid)
        (tmp_path / 'release').write_text('yes')
        with pytest.raises(ValueError, match='loadout'):
            await asyncio.wait_for(startup, timeout=10)
        assert not psutil.pid_exists(first_pid) and not orch.active
        reply = await asyncio.wait_for(_rpc(server, 'real-retry', name='other-acp'), timeout=20)
        assert not reply.get('error') and 'error' not in reply['result']
        successor = orch.runner
        second_pid = int((tmp_path / 'second.pid').read_text())
        assert orch.active and psutil.pid_exists(second_pid)
        result = await asyncio.wait_for(orch.submit('hello', source='rpc'), timeout=20)
        assert result['text'] == 'synthetic response'
        assert orch.runner is successor and orch.active
    finally:
        (tmp_path / 'release').touch()
        if not startup.done():
            startup.cancel()
        await asyncio.gather(startup, return_exceptions=True)
        await orch.shutdown()
    assert not psutil.pid_exists(second_pid)
