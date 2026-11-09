"""Restart/resume cannot acknowledge a conversation with the old ACP context."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from kollabor_engine.routes import sessions

from kollabor.commands.system_commands.handlers.system import SystemCommandHandler
from kollabor.llm import acp_orchestrator as acp_mod
from kollabor.state.handlers import register_state_handlers
from kollabor_events.data_models import ConversationMessage
from kollabor_rpc.server import RpcServer
from tests.integration.test_acp_startup_ownership import _setup


class Conversation:
    def __init__(self):
        self.current_session_id = 'before'
        self.messages = [{'uuid': 'old', 'role': 'user', 'content': 'old-private-context'}]
        self.clears = 0
        self.loads = 0
        self.saved = []

    def add_message(self, role, content, **kwargs):
        return role + '-id'

    def clear_conversation(self):
        self.clears += 1
        self.current_session_id = 'fresh'
        self.messages = []

    def load_session(self, session_id):
        self.loads += 1
        if session_id != 'saved':
            return False
        self.current_session_id = session_id
        self.messages = [{'uuid': 'saved-id', 'role': 'user', 'content': 'saved-context'}]
        return True

    def save_session(self, session_id):
        self.saved.append(session_id)
        return True


def _wire(state, bus, monkeypatch):
    llm = bus.services['llm_service']
    manager = Conversation()
    llm.conversation_manager = manager
    llm.conversation_history = [ConversationMessage(role='user', content='old-private-context')]
    async def initialize():
        llm.conversation_history.clear()
    llm._initialize_conversation = initialize
    async def snapshot():
        return SimpleNamespace(message_count=len(llm.conversation_history),
                               to_dict=lambda: {'session_id': manager.current_session_id})
    monkeypatch.setattr(state, 'get_conversation', snapshot)
    bus.services['state_service'] = state
    server = RpcServer()
    register_state_handlers(server, state)
    return llm, manager, server


async def _rpc(server, method, params=None):
    return await server.handle_wire({'action': 'rpc_request', 'request_id': method,
                                     'method': method, 'params': params or {}})


def _web(state, monkeypatch):
    refreshed = []
    async def refresh():
        refreshed.append(True)
    session = SimpleNamespace(state=state, refresh_history=refresh, last_turn_error='keep')
    monkeypatch.setattr(sessions, 'get_session_registry', lambda: SimpleNamespace(get=lambda _id: session))
    app = FastAPI()
    app.include_router(sessions.router)
    return app, session, refreshed


@pytest.mark.asyncio
@pytest.mark.parametrize('surface', ['rpc-restart', 'rpc-resume', 'web-clear', 'tui-new', 'modal-resume'])
async def test_effort_owner_rejects_conversation_change_before_mutation_and_retry_succeeds(
    tmp_path, monkeypatch, surface
):
    from plugins.resume_conversation_plugin import ResumeConversationPlugin

    first, pm, bus, orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    base = acp_mod.ACPRunner
    entered, release = asyncio.Event(), asyncio.Event()
    class HoldEffort(base):
        async def set_config_option(self, config_id, value):
            if config_id == 'effort' and value == 'max':
                entered.set()
                await release.wait()
            return await super().set_config_option(config_id, value)
    monkeypatch.setattr(acp_mod, 'ACPRunner', HoldEffort)
    llm, manager, server = _wire(state, bus, monkeypatch)
    web, session, refreshed = _web(state, monkeypatch)
    handler = SystemCommandHandler(None, bus)
    plugin = ResumeConversationPlugin()
    plugin.event_bus = bus
    await orch.start(first)
    old = orch.runner
    await orch.submit('old-private-context', source='rpc')
    task = asyncio.create_task(state.set_active_profile(first.name, effort='max'))

    async def invoke():
        if surface == 'rpc-restart':
            return await _rpc(server, 'state.restart_session')
        if surface == 'rpc-resume':
            return await _rpc(server, 'state.resume_conversation', {'session_id': 'saved'})
        if surface == 'tui-new':
            return await handler.handle_new(SimpleNamespace())
        if surface == 'modal-resume':
            return await plugin._handle_modal_command(
                {'command': {'action': 'resume_session', 'session_id': 'saved'}}, SimpleNamespace()
            )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url='http://test') as client:
            return await client.delete('/sessions/probe/history')

    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        result = await asyncio.wait_for(invoke(), timeout=5)
        if surface.startswith('rpc-'):
            assert 'progress' in result['result']['error'].lower()
        elif surface == 'web-clear':
            assert result.status_code == 409 and 'progress' in result.json()['detail'].lower()
        elif surface == 'modal-resume':
            assert 'progress' in result['display_messages'][0][1].lower()
        else:
            assert not result.success and 'progress' in result.message.lower()
        assert manager.current_session_id == 'before' and manager.clears == manager.loads == 0
        assert llm.conversation_history[0].content == 'old-private-context'
        assert orch.runner is old and not old.closed
        assert not refreshed and session.last_turn_error == 'keep'
        release.set()
        await asyncio.wait_for(task, timeout=5)
        result = await asyncio.wait_for(invoke(), timeout=5)
        if surface.startswith('rpc-'):
            assert not result.get('error') and 'error' not in result['result']
        elif surface == 'web-clear':
            assert result.status_code == 200 and result.json()['ok'] is True
            assert refreshed == [True] and session.last_turn_error is None
        elif surface == 'modal-resume':
            assert result['display_messages'][0][1].startswith('--- Resumed:')
            assert result['display_messages'][-1][1].startswith('[ok] Resumed:')
        else:
            assert result.success
        assert old.closed and orch.runner is not old and orch.active
        assert orch.binding_conversation_id == manager.current_session_id
        if surface in ('rpc-resume', 'modal-resume'):
            assert manager.loads == 1 and llm.conversation_history[0].content == 'saved-context'
        else:
            assert manager.clears == 1 and not llm.conversation_history
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['restart', 'resume'])
@pytest.mark.parametrize('outcome', ['success', 'error', 'cancel'])
async def test_conversation_change_fences_profile_activation_and_retries_after_failure(
    tmp_path, monkeypatch, operation, outcome
):
    first, pm, bus, orch, state, manager, created, _entered, _release = await _setup(tmp_path, monkeypatch)
    base = acp_mod.ACPRunner
    entered, release = asyncio.Event(), asyncio.Event()
    class HoldRestore(base):
        async def start(self, binding=None):
            if operation == 'resume' and len(created) == 2:
                entered.set()
                await release.wait()
                if outcome == 'error':
                    raise RuntimeError('synthetic resume failure')
            await super().start(binding=binding)
    monkeypatch.setattr(acp_mod, 'ACPRunner', HoldRestore)
    llm, conv, server = _wire(state, bus, monkeypatch)
    if operation == 'restart':
        async def initialize():
            entered.set()
            await release.wait()
            if outcome == 'error':
                raise RuntimeError('synthetic initialization failure')
            llm.conversation_history.clear()
        llm._initialize_conversation = initialize
    await orch.start(first)
    old = orch.runner

    async def invoke():
        return await state.restart_session() if operation == 'restart' else await state.resume_conversation('saved')

    task = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not orch.active
        reply = await _rpc(server, 'state.set_active_profile', {'name': 'other-acp'})
        assert 'progress' in reply['result']['error'].lower()
        unknown = await _rpc(server, 'state.set_active_profile', {'name': 'unknown-profile'})
        assert 'progress' in unknown['result']['error'].lower()
        assert orch.runner is not None and not orch.runner.closed
        with pytest.raises(ValueError, match='progress'):
            await manager.activate(manager.get('other-preset'), bus)
        with pytest.raises(ValueError, match='progress'):
            await state.restart_session()
        assert pm.get_active_profile().name == 'claude-code'
        if outcome == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if outcome == 'error':
                with pytest.raises((ValueError, RuntimeError)):
                    await task
            else:
                await task
        assert old.closed
        if outcome != 'success':
            assert not orch.active and orch.runner is None
            assert 'did not complete' in orch.startup_error
            # A rejected/cancelled operation did not acknowledge success. An
            # explicit retry builds a new runner bound to the current session.
            if operation == 'restart':
                async def initialize_after_retry():
                    llm.conversation_history.clear()
                llm._initialize_conversation = initialize_after_retry
            await invoke()
        assert orch.active and orch.runner is not old
        assert orch.binding_conversation_id == conv.current_session_id
        if operation == 'resume':
            assert llm.conversation_history[0].content == 'saved-context'
        else:
            assert not llm.conversation_history
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await orch.shutdown()


@pytest.mark.asyncio
async def test_failed_resume_without_mutation_keeps_existing_runner(tmp_path, monkeypatch):
    first, _pm, bus, orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    llm, conv, _server = _wire(state, bus, monkeypatch)
    await orch.start(first)
    old = orch.runner
    try:
        with pytest.raises(ValueError, match='failed to load'):
            await state.resume_conversation('missing')
        assert orch.active and orch.runner is old and not old.closed
        assert conv.current_session_id == 'before'
        assert llm.conversation_history[0].content == 'old-private-context'
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_real_sdk_child_retries_clear_and_resume_without_old_context(tmp_path, monkeypatch):
    import sys

    import psutil

    from tests.integration.test_acp_startup_ownership import REAL_OVERLAP_AGENT

    actual_runner = acp_mod.ACPRunner
    first, _pm, bus, orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(acp_mod, 'ACPRunner', actual_runner)
    source = REAL_OVERLAP_AGENT.replace(
        "self.effort = 'xhigh'", "self.effort = 'xhigh'\n        self.turns = 0\n        self.remembered = False"
    ).replace(
        "sys.argv[1] == 'first' and config_id == 'model'",
        "sys.argv[1] == 'first' and config_id == 'effort'",
    ).replace(
        "return SetSessionConfigOptionResponse(configOptions=self.options())\n        if config_id == 'model':",
        "pass\n        if config_id == 'model':",
    ).replace(
        'async def prompt(self, session_id, prompt, **kwargs):\n        await',
        'async def prompt(self, session_id, prompt, **kwargs):\n'
        '        self.turns += 1\n'
        '        self.remembered |= any("old-private-context" in getattr(item, "text", "") for item in prompt)\n'
        '        await',
    ).replace(
        "text='synthetic response'",
        "text=('OLD_CONTEXT_AVAILABLE' if self.turns > 1 and self.remembered else 'FRESH_CONTEXT')",
    )
    adapter = tmp_path / 'conversation_agent.py'
    adapter.write_text(source)
    first.command, first.cwd = (sys.executable, str(adapter), 'first'), str(tmp_path)
    llm, conv, server = _wire(state, bus, monkeypatch)
    web, session, refreshed = _web(state, monkeypatch)
    await orch.start(first)
    old_pid = int((tmp_path / 'first.pid').read_text())
    switching = None
    try:
        assert (await orch.submit('old-private-context', source='rpc'))['text'] == 'FRESH_CONTEXT'
        switching = asyncio.create_task(state.set_active_profile(first.name, effort='max'))
        async with asyncio.timeout(15):
            while not (tmp_path / 'blocked').exists():
                if switching.done():
                    await switching
                await asyncio.sleep(.02)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url='http://test') as client:
            response = await client.delete('/sessions/probe/history')
        assert response.status_code == 409
        assert 'progress' in (await _rpc(server, 'state.restart_session'))['result']['error'].lower()
        resume = await _rpc(server, 'state.resume_conversation', {'session_id': 'saved'})
        assert 'progress' in resume['result']['error'].lower()
        assert conv.current_session_id == 'before' and conv.clears == conv.loads == 0
        assert psutil.pid_exists(old_pid) and not refreshed and session.last_turn_error == 'keep'
        (tmp_path / 'release').touch()
        await asyncio.wait_for(switching, timeout=10)
        assert (await orch.submit('confirm-old-context', source='rpc'))['text'] == 'OLD_CONTEXT_AVAILABLE'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url='http://test') as client:
            response = await client.delete('/sessions/probe/history')
        assert response.status_code == 200 and response.json()['ok'] is True
        assert not psutil.pid_exists(old_pid) and orch.binding_conversation_id == conv.current_session_id
        assert (await orch.submit('after-clear', source='rpc'))['text'] == 'FRESH_CONTEXT'
        clear_pid = int((tmp_path / 'first.pid').read_text())
        resumed = await _rpc(server, 'state.resume_conversation', {'session_id': 'saved'})
        assert not resumed.get('error') and 'error' not in resumed['result']
        assert not psutil.pid_exists(clear_pid) and orch.binding_conversation_id == conv.current_session_id
        assert llm.conversation_history[0].content == 'saved-context'
        assert (await orch.submit('after-resume', source='rpc'))['text'] == 'FRESH_CONTEXT'
    finally:
        (tmp_path / 'release').touch()
        if switching is not None and not switching.done():
            switching.cancel()
        if switching is not None:
            await asyncio.gather(switching, return_exceptions=True)
        await orch.shutdown()


@pytest.mark.asyncio
async def test_nested_profile_and_loadout_changes_are_rejected_during_restart(tmp_path, monkeypatch):
    first, pm, bus, orch, state, manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    llm, conv, _server = _wire(state, bus, monkeypatch)
    await orch.start(first)
    old = orch.runner

    async def initialize():
        with pytest.raises(ValueError, match='conversation change'):
            await state.set_active_profile('other-acp')
        with pytest.raises(ValueError, match='conversation change'):
            await manager.activate(manager.get('other-preset'), bus)
        assert pm.get_active_profile().name == 'claude-code'
        llm.conversation_history.clear()

    llm._initialize_conversation = initialize
    try:
        await state.restart_session()
        assert old.closed and orch.active and orch.runner is not old
        assert orch.binding_conversation_id == conv.current_session_id
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', ['stale', 'missing'])
@pytest.mark.parametrize('operation', ['restart', 'resume'])
async def test_retry_uses_selected_profile_even_when_runner_is_inactive(tmp_path, monkeypatch, selected, operation):
    first, pm, bus, orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    _llm, conv, _server = _wire(state, bus, monkeypatch)
    await orch.start(first)
    old = orch.runner
    await orch.close()
    orch._selected_profile = pm.get_profile('other-acp') if selected == 'stale' else None
    if operation == 'restart':
        await state.restart_session()
    else:
        await state.resume_conversation('saved')
    try:
        assert old.closed and orch.active and orch.runner.profile.name == 'claude-code'
        assert orch.binding_conversation_id == conv.current_session_id
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['restart', 'resume'])
async def test_selected_acp_without_orchestrator_refuses_reset_before_mutation(tmp_path, monkeypatch, operation):
    _first, _pm, bus, _orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    llm, conv, _server = _wire(state, bus, monkeypatch)
    bus.services.pop('acp_orchestrator')
    with pytest.raises(ValueError, match='ACP.*unavailable'):
        if operation == 'restart':
            await state.restart_session()
        else:
            await state.resume_conversation('saved')
    assert conv.current_session_id == 'before' and conv.clears == 0
    assert llm.conversation_history[0].content == 'old-private-context'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['load_session', 'resume_session'])
async def test_inbound_acp_resume_reports_busy_as_retryable_not_missing(tmp_path, monkeypatch, operation):
    from unittest.mock import AsyncMock

    from acp.exceptions import RequestError

    from kollabor.acp_server import KollabACPAgent

    agent = KollabACPAgent()
    agent.saved.save('stable', str(tmp_path), 'saved-native')
    shutdown = AsyncMock()
    state = SimpleNamespace(resume_conversation=AsyncMock(
        side_effect=ValueError('ACP startup or profile change is in progress; retry when ready')
    ))
    engine = SimpleNamespace(state=state, shutdown=shutdown)
    session = SimpleNamespace(id='temporary', engine=engine, attached=False, requests=[], permission_tasks={})
    async def create(*args, **kwargs):
        agent.sessions[session.id] = session
        return session
    monkeypatch.setattr(agent, '_create_session', create)
    with pytest.raises(RequestError) as caught:
        await getattr(agent, operation)(str(tmp_path), 'stable')
    assert caught.value.code != -32002 and 'retry' in str(caught.value).lower()
    shutdown.assert_awaited_once()
    assert not agent.sessions and not agent.restoring


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['load_session', 'resume_session'])
@pytest.mark.parametrize('failure,code', [
    (ValueError("failed to load session 'saved-native'"), -32002),
    (RuntimeError('synthetic daemon error'), -32603),
])
async def test_inbound_acp_resume_distinguishes_missing_from_operational_failure(
    tmp_path, monkeypatch, operation, failure, code
):
    from unittest.mock import AsyncMock

    from acp.exceptions import RequestError

    from kollabor.acp_server import KollabACPAgent

    agent = KollabACPAgent()
    agent.saved.save('stable', str(tmp_path), 'saved-native')
    shutdown = AsyncMock()
    state = SimpleNamespace(resume_conversation=AsyncMock(side_effect=failure))
    engine = SimpleNamespace(state=state, shutdown=shutdown)
    session = SimpleNamespace(id='temporary', engine=engine, attached=False, requests=[], permission_tasks={})
    async def create(*args, **kwargs):
        agent.sessions[session.id] = session
        return session
    monkeypatch.setattr(agent, '_create_session', create)
    with pytest.raises(RequestError) as caught:
        await getattr(agent, operation)(str(tmp_path), 'stable')
    assert caught.value.code == code
    shutdown.assert_awaited_once()
    assert not agent.sessions and not agent.restoring


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_conversation_reset_without_reactivating_old_runner(tmp_path, monkeypatch):
    first, _pm, bus, orch, state, _manager, _created, _entered, _release = await _setup(tmp_path, monkeypatch)
    llm, _conv, _server = _wire(state, bus, monkeypatch)
    entered = asyncio.Event()
    async def initialize():
        entered.set()
        await asyncio.Event().wait()
    llm._initialize_conversation = initialize
    await orch.start(first)
    old = orch.runner
    resetting = asyncio.create_task(state.restart_session())
    await asyncio.wait_for(entered.wait(), timeout=5)
    await asyncio.wait_for(orch.shutdown(), timeout=5)
    assert resetting.cancelled()
    assert old.closed and orch.runner is None and not orch.active
    assert not orch.transitioning and 'did not complete' in orch.startup_error
