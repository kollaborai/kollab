"""Operator stop scope and tool-shell option spelling regressions (#875)."""

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from kollabor.cli import _handle_cli_hub, _is_tool_shell_agent_launch
from kollabor_agent.tool_executor import _is_blocked_kollab_spawn


def test_operator_stop_all_only_uses_current_folder(tmp_path, monkeypatch):
    current = tmp_path / 'current' / 'presence'
    other = tmp_path / 'other' / 'presence'
    for directory, name in ((current, 'local'), (other, 'other')):
        directory.mkdir(parents=True)
        (directory / f'{name}.json').write_text(json.dumps({
            'agent_id': name, 'identity': name, 'pid': os.getpid(),
            'last_heartbeat': time.time(), 'socket_path': str(directory / name),
        }))
    stale = other / 'stale.json'
    stale.write_text(json.dumps({'pid': 999999999, 'identity': 'stale'}))
    monkeypatch.setattr('plugins.hub.presence.get_presence_dir', lambda: current)
    monkeypatch.setattr('plugins.hub.presence.all_presence_dirs', lambda **kw: [current, other])
    monkeypatch.setattr('kollabor.cli._cli_is_hub_agent_context', lambda: False)
    stop = AsyncMock(return_value=(True, 'stopped'))
    monkeypatch.setattr('kollabor.cli.stop_agent_record', stop)

    asyncio.run(_handle_cli_hub(['stop', 'all']))

    assert [call.args[0]['identity'] for call in stop.await_args_list] == ['local']
    assert stale.exists()


@pytest.mark.parametrize('command', [
    'kollab -a x', 'kollab -ax', 'kollab --ag x', 'kollab -d',
    'kollab --det', 'kollab --hub stop --reason r all',
    'kollab --agent x', 'kollab --detached', 'kollab --hub stop all',
    'kollab --hub=kill all',
])
def test_terminal_guard_blocks_argparse_spellings(command):
    assert _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize('option', ['-a', '--ag', '-d', '--det', '--agent', '--detached'])
def test_cli_guard_blocks_launch_options(option, monkeypatch):
    monkeypatch.setenv('KOLLAB_TOOL_SHELL', '1')
    assert _is_tool_shell_agent_launch(['kollab', option, 'x'])
