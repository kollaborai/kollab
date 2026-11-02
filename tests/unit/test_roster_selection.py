"""Tests for member-filtered effective teams (issue #621)."""

from pathlib import Path

import pytest

from plugins.hub.rosters import (
    LibraryError,
    Member,
    Persona,
    Team,
    effective_team,
    team_launch_order,
    team_member_prompt,
)


def _team() -> Team:
    names = ("director", "engineering", "platform", "builder", "sales")
    personas = {name: Persona(name=name, display_name=name.title()) for name in names}
    return Team(
        name="example",
        description="Example team",
        roster="example",
        lead="director",
        members=[
            Member(persona=personas["director"], role="director"),
            Member(
                persona=personas["engineering"],
                role="engineering lead",
                reports_to="director",
            ),
            Member(
                persona=personas["platform"],
                role="platform lead",
                reports_to="engineering",
            ),
            Member(
                persona=personas["builder"],
                role="builder",
                reports_to="platform",
            ),
            Member(
                persona=personas["sales"],
                role="sales lead",
                reports_to="director",
            ),
        ],
        source="test",
        path=Path("example.json"),
    )


def test_exclusion_reparents_to_nearest_selected_ancestor_and_updates_prompt():
    original = _team()

    selected, excluded = effective_team(original, exclude=["engineering", "platform"])

    assert [member.persona.name for member in selected.members] == [
        "director",
        "builder",
        "sales",
    ]
    assert selected.members[1].reports_to == "director"
    assert selected.members[2].reports_to == "director"
    assert excluded == ["engineering", "platform"]
    prompt = team_member_prompt(selected, selected.members[1])
    assert "you report to: director" in prompt
    assert "engineering" not in prompt
    assert "platform" not in prompt
    assert "your direct reports:" not in prompt


def test_allowlist_is_authored_order_and_excluded_is_authored_order():
    selected, excluded = effective_team(_team(), members=["builder", "director", "sales"])

    assert [member.persona.name for member in selected.members] == [
        "director",
        "builder",
        "sales",
    ]
    assert selected.members[1].reports_to == "director"
    assert excluded == ["engineering", "platform"]


def test_replacement_lead_is_first_but_reporting_edges_only_change_for_exclusions():
    original = _team()

    selected, excluded = effective_team(original, lead="platform")

    assert selected.lead == "platform"
    assert team_launch_order(selected)[0].persona.name == "platform"
    assert selected.members[2].reports_to == "engineering"
    assert excluded == []
    assert original.lead == "director"
    assert original.members[2].reports_to == "engineering"


def test_no_selection_preserves_full_team_and_returns_no_exclusions():
    selected, excluded = effective_team(_team())

    assert [member.persona.name for member in selected.members] == [
        "director",
        "engineering",
        "platform",
        "builder",
        "sales",
    ]
    assert selected.lead == "director"
    assert excluded == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"members": ["director"], "exclude": ["sales"]}, "mutually exclusive"),
        ({"members": ["unknown"]}, "unknown member"),
        ({"exclude": ["unknown"]}, "unknown member"),
        ({"members": ["director", "director"]}, "duplicate"),
        ({"exclude": ["sales", "sales"]}, "duplicate"),
        ({"exclude": ["director"]}, "replacement lead"),
        ({"members": ["engineering"]}, "replacement lead"),
        ({"members": ["director", "engineering"], "lead": "sales"}, "selected"),
    ],
)
def test_invalid_selection_is_rejected(kwargs, message):
    with pytest.raises(LibraryError, match=message):
        effective_team(_team(), **kwargs)
