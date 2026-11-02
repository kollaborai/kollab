"""Tests for the `/hub org` roster picker."""

from pathlib import Path

import pytest

from kollabor_tui.key_parser import KeyPress
from plugins.altview.org_picker_altview import OrgPickerAltView
from plugins.hub.rosters import Member, Persona, Team


def _team(name: str = "example") -> Team:
    people = {
        person: Persona(name=person, display_name=person.title())
        for person in ("director", "engineering", "platform", "builder")
    }
    return Team(
        name=name,
        description="Example team",
        roster=name,
        lead="director",
        members=[
            Member(persona=people["director"], role="director"),
            Member(
                persona=people["engineering"],
                role="engineering lead",
                reports_to="director",
            ),
            Member(
                persona=people["platform"],
                role="platform lead",
                reports_to="engineering",
            ),
            Member(
                persona=people["builder"],
                role="builder",
                reports_to="platform",
            ),
        ],
        source="test",
        path=Path(f"{name}.json"),
    )


def test_no_argument_metadata_probe_starts_with_empty_team_picker():
    view = OrgPickerAltView()

    assert view.metadata.plugin_type == "hub-org-picker"
    assert view.teams == []
    assert view.team is None
    assert view.phase == "teams"
    assert view._message == "No teams are available in this folder."


def _key(name: str, char: str | None = None) -> KeyPress:
    return KeyPress(name=name, code=ord(char) if char else 0, char=char)


@pytest.mark.asyncio
async def test_picker_starts_with_default_team_and_enters_roster():
    team = _team()
    view = OrgPickerAltView([team])

    assert view.phase == "teams"
    assert await view.handle_input(_key("Enter")) is False
    assert view.phase == "members"
    assert view.team is team
    assert view.selected_members == {"director", "engineering", "platform", "builder"}
    assert view.lead == "director"


@pytest.mark.asyncio
async def test_toggling_manager_reparents_selected_reports_in_effective_tree():
    team = _team()
    view = OrgPickerAltView([team], team=team)
    await view.on_enter(None)

    await view.handle_input(_key("ArrowDown"))
    await view.handle_input(_key(" ", " "))
    await view.handle_input(_key("ArrowDown"))
    await view.handle_input(_key(" ", " "))

    effective = view._effective_team()
    assert effective is not None
    builder = next(member for member in effective.members if member.persona.name == "builder")
    assert builder.reports_to == "director"
    assert "engineering" not in view.selected_members
    assert team.members[3].reports_to == "platform"


@pytest.mark.asyncio
async def test_lead_must_be_replaced_before_authored_lead_can_be_removed():
    team = _team()
    view = OrgPickerAltView([team], team=team)
    await view.on_enter(None)

    await view.handle_input(_key(" ", " "))
    assert "director" in view.selected_members
    assert "replacement lead" in view._message

    await view.handle_input(_key("ArrowDown"))
    await view.handle_input(_key("l", "l"))
    assert view.lead == "engineering"

    await view.handle_input(_key("ArrowUp"))
    await view.handle_input(_key(" ", " "))
    await view.handle_input(_key("Enter"))

    assert view.result is not None
    assert view.result.lead == "engineering"
    assert "director" not in view.result.members
    assert "engineering" in view.result.members


@pytest.mark.asyncio
async def test_picker_mission_editor_accepts_text_then_returns_selection():
    team = _team()
    view = OrgPickerAltView([team], team=team)
    await view.on_enter(None)

    await view.handle_input(_key("m", "m"))
    for char in "review launch plan":
        await view.handle_input(_key(char, char))
    assert await view.handle_input(_key("Enter")) is False
    assert view.mission == "review launch plan"
    assert await view.handle_input(_key("Enter")) is True
    assert view.result is not None
    assert view.result.mission == "review launch plan"


class _Renderer:
    def __init__(self, width=80, height=24):
        self.width = width
        self.height = height
        self.writes = []

    def get_terminal_size(self):
        return self.width, self.height

    def write_at(self, x, y, content, style):
        self.writes.append((x, y, content, style))


@pytest.mark.asyncio
async def test_picker_renders_effective_roster_and_controls():
    team = _team()
    view = OrgPickerAltView([team], team=team)
    renderer = _Renderer()
    await view.on_enter(renderer)

    assert await view.render_frame(0.0) is True
    assert renderer.writes
    assert all(0 <= write[1] < renderer.height for write in renderer.writes)
