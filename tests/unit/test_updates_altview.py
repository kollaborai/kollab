"""Tests for the updates AltView."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.altview.command_integration import AltViewCommandIntegrator
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.updates.release_notes import (
    ReleaseNote,
    ReleaseNoteSection,
    parse_changelog,
)
from kollabor_tui.status.utils import strip_ansi
from plugins.altview.updates_altview import UpdatesAltView


class _Renderer:
    def __init__(self, width=96, height=28):
        self.width = width
        self.height = height
        self.writes = []
        self.cleared = 0

    def get_terminal_size(self):
        return self.width, self.height

    def clear_screen(self):
        self.cleared += 1

    def write_at(self, x, y, text, style=""):
        self.writes.append((x, y, str(text)))


def test_parse_changelog_extracts_recent_versions_sections_and_wrapped_bullets():
    notes = parse_changelog(
        """
# Changelog

## [Unreleased]

## [0.5.14] - 2026-05-28

### Fixed

- Fixed provider requests so local metadata stays local
  across provider APIs.
- Fixed oversized tool output.

## [0.5.13] - 2026-05-27

### Added

- Added the MCP manager.
""",
        limit=2,
    )

    assert [note.version for note in notes] == ["0.5.14", "0.5.13"]
    assert notes[0].date == "2026-05-28"
    assert notes[0].sections[0].title == "Fixed"
    assert notes[0].sections[0].items == [
        "Fixed provider requests so local metadata stays local across provider APIs.",
        "Fixed oversized tool output.",
    ]


@pytest.mark.asyncio
async def test_updates_altview_renders_versions_left_and_release_notes_right():
    view = UpdatesAltView()
    view._current_version = "0.5.14"
    view._notes = [
        ReleaseNote(
            version="0.5.14",
            date="2026-05-28",
            sections=[
                ReleaseNoteSection(
                    title="Fixed",
                    items=[
                        "Fixed provider payload cleanup.",
                        "Fixed oversized tool output handling.",
                    ],
                )
            ],
        ),
        ReleaseNote(
            version="0.5.13",
            date="2026-05-27",
            sections=[
                ReleaseNoteSection(
                    title="Added",
                    items=["Added the MCP manager."],
                )
            ],
        ),
    ]
    view._status_message = "loaded 2 releases"
    view._renderer = _Renderer()

    assert await view.render_frame(0.0) is True

    left_text = "\n".join(
        strip_ansi(text) for x, _, text in view._renderer.writes if x == 0
    )
    right_text = "\n".join(
        strip_ansi(text) for x, _, text in view._renderer.writes if x > 0
    )

    assert "0.5.14" in left_text
    assert "0.5.13" in left_text
    assert "Fixed" in right_text
    assert "Fixed provider payload cleanup." in right_text


@pytest.mark.asyncio
async def test_updates_command_opens_stable_altview_session():
    registry = SlashCommandRegistry()
    event_bus = Mock()
    integrator = AltViewCommandIntegrator(
        command_registry=registry,
        event_bus=event_bus,
        terminal_renderer=Mock(),
        app=Mock(),
    )
    integrator._stack_manager = SimpleNamespace(push=AsyncMock(return_value=True))

    assert integrator._register_plugin_commands(UpdatesAltView) is True
    command_def = registry.get_command("updates")
    result = await command_def.handler(SimpleNamespace(args=[], name="updates"))

    assert result.success
    integrator._stack_manager.push.assert_awaited_once()
    altview, session_name = integrator._stack_manager.push.await_args.args
    assert isinstance(altview, UpdatesAltView)
    assert session_name == "updates"
