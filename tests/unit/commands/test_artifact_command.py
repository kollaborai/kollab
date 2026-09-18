"""Tests for the generated-image artifact slash command."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from kollabor.commands.registry import SlashCommandRegistry
from kollabor.commands.system_commands.handlers.artifact import ArtifactCommandHandler
from kollabor_events.models import SlashCommand


def _command(*args: str) -> SlashCommand:
    return SlashCommand(name="artifact", args=list(args), raw_input="")


def test_artifact_open_delegates_by_opaque_media_id():
    api_service = SimpleNamespace(open_generated_artifact=MagicMock(return_value=True))
    event_bus = MagicMock()
    event_bus.get_service.return_value = SimpleNamespace(api_service=api_service)
    handler = ArtifactCommandHandler(SlashCommandRegistry(), event_bus)
    media_id = "img_1234567890abcdef"

    result = asyncio.run(handler.handle_artifact(_command("open", media_id)))

    assert result.success is True
    assert media_id in result.message
    api_service.open_generated_artifact.assert_called_once_with(media_id)


def test_artifact_open_rejects_path_like_or_invalid_ids():
    event_bus = MagicMock()
    handler = ArtifactCommandHandler(SlashCommandRegistry(), event_bus)

    result = asyncio.run(handler.handle_artifact(_command("open", "/tmp/image.png")))

    assert result.success is False
    assert result.message == "invalid generated image media ID"
    event_bus.get_service.assert_not_called()


def test_artifact_open_reports_missing_artifact_without_a_path():
    api_service = SimpleNamespace(open_generated_artifact=MagicMock(return_value=False))
    event_bus = MagicMock()
    event_bus.get_service.return_value = SimpleNamespace(api_service=api_service)
    handler = ArtifactCommandHandler(SlashCommandRegistry(), event_bus)
    media_id = "img_1234567890abcdef"

    result = asyncio.run(handler.handle_artifact(_command("open", media_id)))

    assert result.success is False
    assert media_id in result.message
    assert "/tmp" not in result.message
