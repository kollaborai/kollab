"""A join code typed after /connect in the browser never reaches saved history."""

import asyncio
from types import SimpleNamespace

from kollabor.state.local import LocalStateService


class _Recorder:
    def __init__(self):
        self.messages = []

    def _add_conversation_message(self, role, content, metadata=None):
        self.messages.append((role, content, metadata))


def _run(text: str, name: str) -> list:
    service = LocalStateService.__new__(LocalStateService)
    llm = _Recorder()
    service._llm_service = llm
    service._event_bus = None  # publish_semantic no-ops without a tap

    parser = SimpleNamespace(parse_command=lambda _text: SimpleNamespace(name=name))

    async def execute_command(command, event_bus):
        return SimpleNamespace(success=False, message="refused", ui_config=None)

    executor = SimpleNamespace(execute_command=execute_command)
    asyncio.run(service._execute_slash_command(text, parser, executor))
    return llm.messages


def test_connect_join_code_is_redacted_from_history():
    messages = _run("/connect ABCD-EFGH", "connect")
    saved = repr(messages)
    assert "ABCD-EFGH" not in saved
    assert messages[0][1] == "/connect [join code redacted]"


def test_other_commands_are_recorded_verbatim():
    messages = _run("/cd test-case", "cd")
    assert messages[0][1] == "/cd test-case"
