"""A finished subagent on an ACP chat (#579, PR #793 review round 2).

`/sub create` runs a background agent. When it finishes, the plugin injects the
completion into history and wakes the model with TRIGGER_LLM_CONTINUE. An ACP
chat refuses that wake, so the completion must not be written into native
history, and the user must still be able to read it.

Run: python -m pytest tests/unit/test_agent_orchestrator_acp_completion.py -v
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor.llm.message_handler import MessageHandler
from kollabor_events import EventType
from plugins.agent_orchestrator.message_injector import MessageInjector
from tests.unit.llm.test_message_handler import (
    _acp_runner,
    _make_coordinator,
    _set_acp_state,
)
from tests.unit.test_agent_orchestrator_pipeline import _make_plugin

SUMMARY = "found the bug in foo.py"


def _wire(*, acp: bool):
    """The plugin with a real injector and a real MessageHandler behind its wake."""
    coord = _make_coordinator()
    if acp:
        _set_acp_state(coord, runner=_acp_runner(), selects_acp=True)
    handler = MessageHandler(coord)

    history = []
    conversation_manager = MagicMock()
    conversation_manager.add_message = MagicMock(
        side_effect=lambda role, content, **kw: history.append((role, content))
    )

    bus = MagicMock()
    bus.get_service = lambda name: coord if name == "llm_service" else None
    wakes = []

    async def emit(event_type, data, source=None):
        if event_type == EventType.TRIGGER_LLM_CONTINUE:
            wakes.append(data)
            return await handler.handle_llm_continue(data, MagicMock())
        return data  # PRE/POST_MESSAGE_INJECT hand the context back

    bus.emit_with_hooks = AsyncMock(side_effect=emit)

    plugin = _make_plugin()
    plugin.event_bus = bus
    plugin.conversation_manager = conversation_manager
    plugin.message_injector = MessageInjector(bus, conversation_manager)
    plugin.renderer = MagicMock()
    return plugin, coord, history, wakes


def _shown(plugin) -> str:
    """Everything the plugin put on screen through the message coordinator."""
    calls = plugin.renderer.message_coordinator.display_message_sequence.call_args_list
    return "\n".join(str(c) for c in calls)


class TestSubagentDoneOnAcpChat(unittest.IsolatedAsyncioTestCase):
    async def test_completion_is_shown_and_history_is_untouched(self):
        plugin, coord, history, _wakes = _wire(acp=True)

        await plugin._on_agent_complete("bug-fix", "2m", SUMMARY)

        self.assertEqual(history, [])  # no stray user turn in native history
        coord.create_background_task.assert_not_called()  # no native turn
        self.assertIn(SUMMARY, _shown(plugin))  # the user can read it

    async def test_user_is_not_told_to_resend_a_message_they_cannot_see(self):
        plugin, coord, _history, _wakes = _wire(acp=True)

        await plugin._on_agent_complete("bug-fix", "2m", SUMMARY)

        coord.message_display_service.display_error_message.assert_not_called()

    async def test_injector_refuses_a_waking_inject_before_writing_history(self):
        plugin, _coord, history, wakes = _wire(acp=True)

        ok = await plugin.message_injector.inject(
            source="bug-fix", content=SUMMARY, trigger_llm=True
        )

        self.assertFalse(ok)  # not reported as injected
        self.assertEqual(history, [])
        self.assertEqual(wakes, [])

    async def test_native_chat_still_injects_and_wakes(self):
        plugin, coord, history, wakes = _wire(acp=False)

        await plugin._on_agent_complete("bug-fix", "2m", SUMMARY)

        self.assertEqual(len(history), 1)
        self.assertIn(SUMMARY, history[0][1])
        self.assertEqual(len(wakes), 1)
        coord.message_display_service.display_error_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
