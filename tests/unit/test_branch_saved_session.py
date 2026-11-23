from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.remote import RemoteStateService
from plugins.resume_conversation_plugin import ResumeConversationPlugin


def _branch_result() -> dict:
    return {
        "old_session_id": "current-before",
        "new_session_id": "branch-created",
        "message_count": 1,
        "branch_point": 0,
        "header": "--- Branched from saved-source at message 0 ---",
        "success_message": "[ok] Created branch: branch-created. Continue below.",
        "messages": [{"role": "user", "content": "saved branch"}],
    }


def _plugin_for_branch(state_service: SimpleNamespace) -> ResumeConversationPlugin:
    manager = SimpleNamespace(
        current_session_id="current-before",
        messages=[{"role": "user", "content": "saved branch"}],
        branch_session=Mock(
            return_value={
                "success": True,
                "session_id": "branch-created",
                "branch_point": 0,
                "message_count": 1,
            }
        ),
    )
    event_bus = SimpleNamespace(
        get_service=Mock(return_value=state_service), emit_with_hooks=AsyncMock()
    )
    plugin = object.__new__(ResumeConversationPlugin)
    plugin.conversation_manager = manager
    plugin.llm_service = SimpleNamespace(conversation_history=[], session_stats={})
    plugin.event_bus = event_bus
    plugin.renderer = None
    plugin.logger = Mock()
    plugin._ensure_conversation_manager = lambda: True
    return plugin


@pytest.mark.asyncio
async def test_saved_branch_command_uses_state_service_and_returns_visible_result():
    state_service = SimpleNamespace(
        branch_conversation=AsyncMock(return_value=_branch_result())
    )
    plugin = _plugin_for_branch(state_service)
    plugin._ensure_conversation_manager = Mock(return_value=False)

    result = await plugin.handle_branch(SimpleNamespace(args=["saved-source", "0"]))

    assert result.success is True
    assert result.message == _branch_result()["success_message"]
    plugin._ensure_conversation_manager.assert_not_called()
    state_service.branch_conversation.assert_awaited_once_with("saved-source", 0)
    plugin.conversation_manager.branch_session.assert_not_called()
    plugin.event_bus.emit_with_hooks.assert_not_awaited()


@pytest.mark.asyncio
async def test_branch_modal_execute_uses_state_service():
    state_service = SimpleNamespace(
        branch_conversation=AsyncMock(return_value=_branch_result())
    )
    plugin = _plugin_for_branch(state_service)
    data = {
        "command": {
            "action": "branch_execute",
            "metadata": {"session_id": "saved-source", "message_index": 0},
        }
    }

    result = await plugin._handle_modal_command(data, None)

    state_service.branch_conversation.assert_awaited_once_with("saved-source", 0)
    assert result["display_messages"][0][1] == _branch_result()["header"]
    assert result["display_messages"][-1][1] == _branch_result()["success_message"]
    plugin.conversation_manager.branch_session.assert_not_called()


@pytest.mark.asyncio
async def test_local_branch_saves_current_and_moves_session_owners_together():
    calls: list[str] = []
    changed: list[bool] = []

    class ConversationLogger:
        session_id = "current-before"

        def reset_session(self, session_id: str) -> None:
            self.session_id = session_id

    class ApiService:
        session_id = "current-before"

        def set_session_id(self, session_id: str) -> None:
            self.session_id = session_id

    class ConversationManager:
        current_session_id = "current-before"
        messages = [{"role": "user", "content": "saved branch"}]
        conversation_logger = ConversationLogger()

        def save_conversation(self) -> None:
            calls.append("save-current")

        def branch_session(self, session_id: str, index: int) -> dict:
            calls.append("branch")
            self.current_session_id = "branch-created"
            self.messages = [
                {
                    "role": "user",
                    "content": "saved branch",
                    "metadata": {"branched_from": session_id},
                }
            ]
            self.save_session(self.current_session_id)
            return {
                "success": True,
                "session_id": self.current_session_id,
                "branch_point": index,
                "message_count": len(self.messages),
            }

        def save_session(self, session_id: str) -> bool:
            calls.append(f"save-{session_id}")
            return True

    manager = ConversationManager()
    api_service = ApiService()
    history = [SimpleNamespace(content="current conversation")]
    llm_service = SimpleNamespace(
        conversation_manager=manager,
        conversation_history=history,
        session_stats={"messages": 1},
        api_service=api_service,
    )
    state = object.__new__(LocalStateService)
    state._llm_service = llm_service

    @asynccontextmanager
    async def ownership(action: str):
        assert action == "branch"
        yield object(), False, lambda: changed.append(True)

    state._conversation_ownership = ownership

    result = await state.branch_conversation("saved-source", 0)

    assert calls.index("save-current") < calls.index("branch")
    assert manager.current_session_id == manager.conversation_logger.session_id
    assert manager.current_session_id == api_service.session_id == "branch-created"
    assert llm_service.conversation_history is history
    assert [message.content for message in history] == ["saved branch"]
    assert llm_service.session_stats["messages"] == 1
    assert changed == [True]
    assert result["success_message"] == _branch_result()["success_message"]
    assert result["messages"] == [{"role": "user", "content": "saved branch"}]


@pytest.mark.asyncio
async def test_failed_branch_restores_current_session_after_invalid_index():
    changed: list[bool] = []

    class ConversationManager:
        current_session_id = "current-before"
        messages = [{"role": "user", "content": "current conversation"}]

        def save_conversation(self) -> None:
            pass

        def branch_session(self, session_id: str, index: int) -> dict:
            self.current_session_id = session_id
            self.messages = [{"role": "user", "content": "saved source"}]
            return {"success": False, "error": "invalid branch point"}

        def load_session(self, session_id: str) -> bool:
            assert session_id == "current-before"
            self.current_session_id = session_id
            self.messages = [{"role": "user", "content": "current conversation"}]
            return True

    manager = ConversationManager()
    state = object.__new__(LocalStateService)
    state._llm_service = SimpleNamespace(conversation_manager=manager)

    @asynccontextmanager
    async def ownership(action: str):
        assert action == "branch"
        yield object(), False, lambda: changed.append(True)

    state._conversation_ownership = ownership

    with pytest.raises(ValueError, match="invalid branch point"):
        await state.branch_conversation("saved-source", 99)

    assert manager.current_session_id == "current-before"
    assert manager.messages == [
        {"role": "user", "content": "current conversation"}
    ]
    assert changed == []


@pytest.mark.asyncio
async def test_branch_state_rpc_validates_and_forwards_saved_session():
    payload = _branch_result()
    state_service = SimpleNamespace(
        branch_conversation=AsyncMock(return_value=payload)
    )
    registered: dict[str, object] = {}
    rpc_server = SimpleNamespace(register=registered.__setitem__)

    register_state_handlers(rpc_server, state_service)
    handler = registered["state.branch_conversation"]
    result = await handler({"session_id": " saved-source ", "branch_index": 0})  # type: ignore[operator]

    assert result == payload
    state_service.branch_conversation.assert_awaited_once_with("saved-source", 0)


@pytest.mark.asyncio
async def test_branch_state_rpc_rejects_missing_session_and_boolean_index():
    state_service = SimpleNamespace(branch_conversation=AsyncMock())
    registered: dict[str, object] = {}
    register_state_handlers(
        SimpleNamespace(register=registered.__setitem__), state_service
    )
    handler = registered["state.branch_conversation"]

    missing_session = await handler(  # type: ignore[operator]
        {"session_id": " ", "branch_index": 0}
    )
    boolean_index = await handler(  # type: ignore[operator]
        {"session_id": "saved-source", "branch_index": True}
    )

    assert missing_session == {"error": "session_id is required"}
    assert boolean_index == {"error": "branch_index must be an integer"}
    state_service.branch_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_branch_calls_state_rpc_and_returns_metadata():
    payload = _branch_result()
    rpc = SimpleNamespace(call=AsyncMock(return_value=payload))
    remote = object.__new__(RemoteStateService)
    remote._rpc = rpc
    remote._timeout = 3.0

    result = await remote.branch_conversation("saved-source", 0)

    assert result == payload
    rpc.call.assert_awaited_once_with(
        "state.branch_conversation",
        {"session_id": "saved-source", "branch_index": 0},
        timeout=3.0,
    )


@pytest.mark.asyncio
async def test_branch_aborts_when_saving_current_conversation_fails():
    branch_calls: list[str] = []

    class ConversationManager:
        current_session_id = "current-before"
        messages = [{"role": "user", "content": "unsaved current turn"}]

        def save_conversation(self) -> None:
            raise OSError("conversation storage is read-only")

        def branch_session(self, session_id: str, index: int) -> dict:
            branch_calls.append(session_id)
            self.current_session_id = "branch-created"
            self.messages = [{"role": "user", "content": "saved branch"}]
            return {
                "success": True,
                "session_id": self.current_session_id,
                "branch_point": index,
            }

    manager = ConversationManager()
    state = object.__new__(LocalStateService)
    state._llm_service = SimpleNamespace(
        conversation_manager=manager,
        conversation_history=[],
    )

    @asynccontextmanager
    async def ownership(_action: str):
        yield object(), False, lambda: None

    state._conversation_ownership = ownership

    with pytest.raises(ValueError, match="save current conversation"):
        await state.branch_conversation("saved-source", 0)

    assert branch_calls == []
    assert manager.current_session_id == "current-before"
    assert manager.messages == [
        {"role": "user", "content": "unsaved current turn"}
    ]
