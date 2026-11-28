"""Production hook coverage for restoring Hub state after a restart."""

from argparse import Namespace
from types import SimpleNamespace

import pytest

from kollabor_events.data_models import ConversationMessage
from plugins.hub.plugin import HubPlugin
from plugins.hub.session_state import SessionState, SessionStateManager


class _EventBus:
    def __init__(self):
        self.hooks = []
        self.llm_service = SimpleNamespace(
            conversation_history=[
                ConversationMessage(role="system", content="base prompt")
            ]
        )

    async def register_hook(self, hook):
        self.hooks.append(hook)

    def get_service(self, name):
        if name == "llm_service":
            return self.llm_service
        return None


@pytest.mark.asyncio
async def test_first_request_restores_saved_state_once_for_solo_agent(tmp_path):
    event_bus = _EventBus()
    plugin = HubPlugin(event_bus=event_bus)
    plugin._identity = SimpleNamespace(solo=True)
    plugin._cli_args = Namespace(attach=True)
    plugin._vault = SimpleNamespace(_vault_dir=tmp_path)
    plugin._session_state_mgr = SessionStateManager()
    plugin._session_state_mgr.save_state(
        tmp_path,
        SessionState(
            identity="lapis",
            focus_file="café.py",
            investigation_notes="resume the deployment handoff",
        ),
    )

    await plugin.register_hooks()
    restore_hook = next(
        (hook for hook in event_bus.hooks if hook.name == "hub_session_state_restore"),
        None,
    )
    assert restore_hook is not None, "the production request pipeline must restore state"

    await restore_hook.callback({})
    await restore_hook.callback({})

    prompt = event_bus.llm_service.conversation_history[0].content
    assert "--- previous session state ---" in prompt
    assert "focus file: café.py" in prompt
    assert "resume the deployment handoff" in prompt
    assert prompt.count("--- previous session state ---") == 1


@pytest.mark.asyncio
async def test_first_request_without_saved_state_keeps_prompt_unchanged(tmp_path):
    event_bus = _EventBus()
    plugin = HubPlugin(event_bus=event_bus)
    plugin._identity = SimpleNamespace(solo=True)
    plugin._cli_args = Namespace(attach=True)
    plugin._vault = SimpleNamespace(_vault_dir=tmp_path)
    plugin._session_state_mgr = SessionStateManager()

    await plugin.register_hooks()
    restore_hook = next(
        (hook for hook in event_bus.hooks if hook.name == "hub_session_state_restore"),
        None,
    )
    assert restore_hook is not None, "the production request pipeline must restore state"

    await restore_hook.callback({})

    assert event_bus.llm_service.conversation_history[0].content == "base prompt"
