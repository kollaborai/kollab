"""The app's ToolExecutor must see the config that holds the spawn-block flag (#1147).

LLMCoordinator._init_tools_and_parsers builds the ToolExecutor every terminal
tool goes through; if it does not pass the config service, an owner's
``tools.block_kollab_terminal_spawn`` opt-in never reaches the guard.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_config import ConfigService


@pytest.fixture
def config(tmp_path):
    cfg = tmp_path / ".kollab" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"tools": {"block_kollab_terminal_spawn": True}}))
    return ConfigService(cfg, None, fast_mode=True)


def _coordinator_stub(config) -> SimpleNamespace:
    stub = SimpleNamespace(
        config=config,
        agent_manager=None,
        conversation_manager=MagicMock(),
        _observe_tool_operation=MagicMock(),
        _refresh_native_tools_after_scope_change=MagicMock(),
        cancel_processing=False,
    )
    return stub


def _build_executor(config) -> ToolExecutor:
    stub = _coordinator_stub(config)
    bus = MagicMock()
    bus.emit_with_hooks = AsyncMock()
    LLMService._init_tools_and_parsers(stub, config, bus, MagicMock())
    return stub.tool_executor


@pytest.mark.asyncio
async def test_coordinator_tool_executor_honors_spawn_block_opt_in(config):
    executor = _build_executor(config)
    assert executor._block_kollab_terminal_spawn_enabled() is True

    executor.shell_executor.run = AsyncMock(return_value=MagicMock(success=True, stdout="ok\n", error=""))
    result = await executor._execute_terminal_command(
        {"type": "terminal", "id": "stop-all", "command": "kollab --hub stop all"}
    )

    assert not result.success
    assert "tools.block_kollab_terminal_spawn" in result.error
    executor.shell_executor.run.assert_not_awaited()
