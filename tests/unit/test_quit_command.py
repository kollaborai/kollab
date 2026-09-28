import pytest

from kollabor.commands.system_commands.handlers.system import SystemCommandHandler


@pytest.mark.asyncio
async def test_quit_exits_like_a_second_ctrl_c():
    with pytest.raises(KeyboardInterrupt):
        await SystemCommandHandler.handle_quit(None, None)


def test_session_context_uses_a_portable_host_name():
    from pathlib import Path

    section = Path(__file__).resolve().parents[2] / "bundles/agents/_base/sections/01-session-context.md"
    text = section.read_text()

    assert "<trender>uname -n</trender>" in text
    assert "<trender>hostname</trender>" not in text
