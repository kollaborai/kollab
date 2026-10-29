"""Pipe mode loads config hooks before the LLM starts, as interactive startup does."""

from types import SimpleNamespace

import pytest

from kollabor.application import TerminalLLMChat


@pytest.mark.asyncio
async def test_pipe_mode_loads_config_hooks_before_the_llm():
    app = object.__new__(TerminalLLMChat)
    app.renderer = SimpleNamespace()
    calls = []

    async def load_hooks():
        calls.append("hooks")

    class Stop(Exception):
        pass

    async def start_llm():
        calls.append("llm")
        raise Stop

    async def cleanup():
        calls.append("cleanup")

    app._load_config_hooks = load_hooks
    app._initialize_llm_core = start_llm
    app.cleanup = cleanup

    with pytest.raises(Stop):
        await app.start_pipe_mode("hello")

    assert calls == ["hooks", "llm", "cleanup"]
    assert app.pipe_mode is True
