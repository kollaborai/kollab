"""Native-only agent and skill generation fail safely under ACP profiles."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.commands.system_commands.handlers.agent_actions import (
    handle_agent_modal_actions,
)
from kollabor.commands.system_commands.handlers.skill_actions import (
    handle_skill_modal_actions,
)


@pytest.mark.asyncio
async def test_create_agent_generation_keeps_form_open_when_native_provider_required():
    llm_service = SimpleNamespace(
        process_user_input=AsyncMock(
            return_value={"status": "rejected", "reason": "native_provider_required"}
        )
    )
    handler = SimpleNamespace(
        event_bus=None,
        llm_service=llm_service,
        _build_agent_generation_prompt=lambda **kwargs: "generate agent",
    )
    data = {
        "command": {
            "action": "create_agent_submit",
            "form_data": {
                "name": "helper",
                "description": "A helper agent",
                "profile": "",
                "source": "global",
            },
        }
    }

    result = await handle_agent_modal_actions(data, MagicMock(), handler)

    llm_service.process_user_input.assert_awaited_once_with(
        "generate agent", native_only=True
    )
    assert not result.get("close_modal", False)
    assert "native provider" in result["display_messages"][0][1].lower()


@pytest.mark.asyncio
async def test_create_skill_generation_keeps_form_open_when_native_provider_required():
    llm_service = SimpleNamespace(
        process_user_input=AsyncMock(
            return_value={"status": "rejected", "reason": "native_provider_required"}
        )
    )
    handler = SimpleNamespace(
        event_bus=None,
        llm_service=llm_service,
        agent_manager=SimpleNamespace(
            get_active_agent=MagicMock(return_value=SimpleNamespace(name="helper"))
        ),
        _build_skill_generation_prompt=lambda **kwargs: "generate skill",
    )
    data = {
        "command": {
            "action": "create_skill_submit",
            "form_data": {"name": "research", "description": "Research helper"},
        }
    }

    result = await handle_skill_modal_actions(data, MagicMock(), handler)

    llm_service.process_user_input.assert_awaited_once_with(
        "generate skill", native_only=True
    )
    assert not result.get("close_modal", False)
    assert "native provider" in result["display_messages"][0][1].lower()
