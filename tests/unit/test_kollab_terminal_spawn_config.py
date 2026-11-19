"""Config wiring for tools.block_kollab_terminal_spawn (#1147): default off, opt-in on."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import kollabor.cli as cli
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_config import ConfigService

FLAG = "tools.block_kollab_terminal_spawn"


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    """A scratch HOME so the real user config is never read or written."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_config(home, data):
    path = home / ".kollab" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _service(home):
    return ConfigService(home / ".kollab" / "config.json", None, fast_mode=True)


def _executor(config):
    executor = ToolExecutor(MagicMock(), MagicMock(), config=config)
    executor.shell_executor.run = AsyncMock(
        return_value=SimpleNamespace(error="", success=True, stdout="synthetic result\n", stderr="", exit_code=0)
    )
    return executor


async def _background_status(executor):
    return await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "bg",
            "command": "kollab --hub status",
            "background": True,
        }
    )


def test_default_config_leaves_kollab_terminal_commands_unblocked(scratch_home):
    assert _service(scratch_home).get(FLAG) is False


def test_user_config_can_turn_the_block_on(scratch_home):
    _write_config(scratch_home, {"tools": {"block_kollab_terminal_spawn": True}})

    assert _service(scratch_home).get(FLAG) is True


@pytest.mark.asyncio
async def test_executor_follows_the_config_service_default(scratch_home):
    executor = _executor(_service(scratch_home))

    result = await _background_status(executor)

    assert result.success
    executor.shell_executor.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_blocks_when_the_config_service_opts_in(scratch_home):
    _write_config(scratch_home, {"tools": {"block_kollab_terminal_spawn": True}})
    executor = _executor(_service(scratch_home))

    result = await _background_status(executor)

    assert not result.success
    assert "foreground" in result.error
    executor.shell_executor.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_ignores_non_boolean_config_values():
    # A mock config or a string value must not switch the block on by accident.
    executor = _executor(MagicMock())

    result = await _background_status(executor)

    assert result.success


def test_cli_helper_is_off_by_default(scratch_home):
    assert cli._block_kollab_terminal_spawn_enabled() is False


def test_cli_helper_reads_the_opt_in_from_user_config(scratch_home):
    _write_config(scratch_home, {"tools": {"block_kollab_terminal_spawn": True}})

    assert cli._block_kollab_terminal_spawn_enabled() is True


def test_cli_helper_stays_off_when_the_config_cannot_be_read(scratch_home, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr("kollabor_config.ConfigService", boom)

    assert cli._block_kollab_terminal_spawn_enabled() is False
