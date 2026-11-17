import asyncio
from unittest.mock import AsyncMock, MagicMock


def test_system_message_is_published_to_web_clients():
    from kollabor_tui.display_tap import DisplayTap
    from kollabor_tui.message_display_service import MessageDisplayService

    tap = DisplayTap()
    events = tap.subscribe("web")
    coordinator = MagicMock()
    coordinator._display_tap = tap
    renderer = MagicMock(message_coordinator=coordinator)
    service = MessageDisplayService(renderer)

    service.display_system_message("Conversation history not saved for this reply.")

    event = tap._subscribers["web"].get_nowait()
    assert event["type"] == "notice"
    assert event["message"] == "Conversation history not saved for this reply."
    coordinator.display_message_sequence.assert_called_once()


def test_failed_hub_history_row_shows_the_message_save_notice(monkeypatch):
    import kollabor.llm.llm_coordinator as llm_coordinator
    from kollabor.llm.llm_coordinator import LLMService

    monkeypatch.setattr(llm_coordinator, "format_agent_hud", lambda entries: "hud entry")
    service = LLMService.__new__(LLMService)
    service.queue_agent_hud = MagicMock(return_value=object())
    service.conversation_logger = MagicMock()
    service.conversation_logger.log_system_message = AsyncMock(
        side_effect=OSError("permission denied")
    )
    service.current_parent_uuid = "parent"
    service.message_display_service = MagicMock()

    asyncio.run(service.inject_system_message("hub row", subtype="hub_message"))

    service.message_display_service.display_system_message.assert_called_once_with(
        "Conversation history not saved for this message."
    )
