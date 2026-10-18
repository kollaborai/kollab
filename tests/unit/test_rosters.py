"""Rosters and teams: parsing, validation, resolution order and name lookup."""

import json

import pytest

from plugins.hub import rosters
from plugins.hub.coordinator import IdentityAssigner
from plugins.hub.models import desired_bundle_for_identity


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A user config directory of its own; the project folder is tmp_path / "proj"."""
    user = tmp_path / "home" / ".kollab"
    monkeypatch.setattr(rosters, "get_config_directory", lambda: user)
    return user


@pytest.fixture
def proj(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def crew(name="crew", names=("ana", "ben"), **extra):
    return {"name": name, "personas": [{"name": n} for n in names], **extra}


def test_bundled_library_is_complete_and_clean(home, proj):
    lib = rosters.load_library(proj)
    assert len(lib.rosters["gems"].personas) == 24
    assert {"gems", "marketing-crew"} <= set(lib.rosters)
    assert {"engineering", "startup", "marketing"} <= set(lib.teams)
    assert lib.problems == []
    assert all(r.source == "bundled" for r in lib.rosters.values())


def test_persona_defaults(home, proj):
    write(proj / ".kollab/rosters/crew.json", crew(names=("ana-maria",)))
    persona = rosters.find_persona("crew/ana-maria", proj)
    assert persona.display_name == "Ana Maria"
    assert persona.avatar == {"kind": "initials"}
    assert persona.agent == "" and persona.skills == []
    # a color of its own is stable per name
    assert persona.color == rosters.persona_color("ana-maria", proj)


def test_roster_avatar_is_the_default_for_its_personas(home, proj):
    data = crew(avatar={"kind": "emoji", "value": "x"})
    data["personas"][1]["avatar"] = {"kind": "image", "src": "faces/ben.png"}
    write(proj / ".kollab/rosters/crew.json", data)
    ana, ben = rosters.load_library(proj).rosters["crew"].personas
    assert ana.avatar == {"kind": "emoji", "value": "x"}
    assert ben.avatar == {"kind": "image", "src": "faces/ben.png"}


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "Bad Name"},
        {"name": "ok", "color": [1, 2]},
        {"name": "ok", "color": [1, 2, 300]},
        {"name": "ok", "avatar": {"kind": "hologram"}},
        {"name": "ok", "avatar": {"kind": "emoji"}},
        {"name": "ok", "avatar": {"kind": "image", "src": "../secret.png"}},
        {"name": "ok", "avatar": {"kind": "image", "src": "/etc/passwd"}},
        {"name": "ok", "skills": "coding"},
    ],
)
def test_a_bad_persona_is_skipped_and_reported(home, proj, entry):
    write(proj / ".kollab/rosters/crew.json", {"name": "crew", "personas": [{"name": "good"}, entry]})
    lib = rosters.load_library(proj)
    assert [p.name for p in lib.rosters["crew"].personas] == ["good"]
    assert len(lib.problems) == 1 and "crew.json" in lib.problems[0]["file"]


def test_duplicate_persona_names_keep_the_first(home, proj):
    write(proj / ".kollab/rosters/crew.json", crew(names=("ana", "ana", "ben")))
    lib = rosters.load_library(proj)
    assert [p.name for p in lib.rosters["crew"].personas] == ["ana", "ben"]
    assert "duplicate" in lib.problems[0]["message"]


def test_unusable_roster_files_are_reported_not_fatal(home, proj):
    write(proj / ".kollab/rosters/broken.json", "{not json")
    write(proj / ".kollab/rosters/empty.json", {"name": "empty", "personas": []})
    write(proj / ".kollab/rosters/nolist.json", {"name": "nolist"})
    lib = rosters.load_library(proj)
    assert "gems" in lib.rosters and not {"broken", "empty", "nolist"} & set(lib.rosters)
    assert len(lib.problems) == 3


def test_later_source_replaces_by_name_and_keeps_position(home, proj):
    write(home / "rosters/gems.json", crew(name="gems", names=("mine",)))
    lib = rosters.load_library(proj)
    assert [p.name for p in lib.rosters["gems"].personas] == ["mine"]
    assert lib.rosters["gems"].source == "user"
    assert list(lib.rosters)[0] == "gems"

    write(proj / ".kollab/rosters/gems.json", crew(name="gems", names=("theirs",)))
    lib = rosters.load_library(proj)
    assert [p.name for p in lib.rosters["gems"].personas] == ["theirs"]
    assert lib.rosters["gems"].source == "project"
    # another folder does not see the project file
    assert [p.name for p in rosters.load_library(proj.parent).rosters["gems"].personas] == ["mine"]


def test_editing_a_file_is_seen_without_a_restart(home, proj):
    path = write(proj / ".kollab/rosters/crew.json", crew(names=("ana",)))
    assert rosters.find_persona("ana", proj) is not None
    path.write_text(json.dumps(crew(names=("zed", "yan"))))
    assert rosters.find_persona("ana", proj) is None
    assert rosters.find_persona("zed", proj) is not None


def test_folder_roster_is_project_over_global(home, proj):
    write(home / "rosters/crew.json", crew())
    assert rosters.folder_roster_name(proj) == "gems"
    write(home / "config.json", {"plugins": {"hub": {"roster": "crew"}}})
    assert rosters.folder_roster_name(proj) == "crew"
    write(proj / ".kollab/config.json", {"plugins": {"hub": {"roster": "marketing-crew"}}})
    assert rosters.folder_roster_name(proj) == "marketing-crew"
    assert rosters.folder_roster_name(proj.parent) == "crew"


def test_designations_come_from_the_folders_roster(home, proj):
    write(proj / ".kollab/config.json", {"plugins": {"hub": {"roster": "marketing-crew"}}})
    assigner = IdentityAssigner(folder=str(proj))
    assert assigner.assign([]) == "omar"
    assert assigner.assign(["omar", "maya"]) == "theo"
    # a persona of any roster can still be asked for by name
    assert assigner.assign(["omar"], preferred="lapis") == "lapis"
    # once the roster is used up, names are numbered
    taken = rosters.roster_names(proj)
    assert assigner.assign(taken) == "omar-2"
    # another folder still gets gems
    assert IdentityAssigner(folder=str(proj.parent)).assign([]) == "lapis"


def test_find_persona_bare_prefixed_numbered_and_ambiguous(home, proj):
    write(proj / ".kollab/rosters/crew.json", crew(names=("lapis", "ana")))
    assert rosters.find_persona("lapis", proj).roster == "gems"  # folder roster first
    assert rosters.find_persona("crew/lapis", proj).roster == "crew"
    assert rosters.find_persona("lapis-2", proj).roster == "gems"
    assert rosters.find_persona("crew/ana-3", proj).name == "ana"
    assert rosters.find_persona("nobody", proj) is None
    assert rosters.find_persona("nope/ana", proj) is None
    write(proj / ".kollab/config.json", {"plugins": {"hub": {"roster": "crew"}}})
    assert rosters.find_persona("lapis", proj).roster == "crew"


def test_as_resolution(home, proj):
    assert rosters.split_ref("marketing-crew/maya") == ("marketing-crew", "maya")
    assert rosters.split_ref("maya") == ("", "maya")
    assert desired_bundle_for_identity("marketing-crew/theo") == "creative-writer"
    assert desired_bundle_for_identity("maya") == "research"
    assert desired_bundle_for_identity("lapis-2") == "coder"
    assert desired_bundle_for_identity("not-a-persona") == "default"


def test_persona_context_only_for_authored_behavior(home, proj):
    assert rosters.persona_context(rosters.find_persona("lapis", proj)) == []
    lines = rosters.persona_context(rosters.find_persona("maya", proj))
    assert lines[0] == "your persona: Maya Lindqvist (Brand Strategist)"
    assert "positioning" in lines[1]


def team(members, lead="ana", **extra):
    return {"name": "t", "lead": lead, "members": members, **extra}


def parse(home, proj, data):
    write(proj / ".kollab/teams/t.json", data)
    lib = rosters.load_library(proj)
    return lib.teams.get("t"), lib.problems


def test_team_launch_order_is_lead_first_then_chain_of_command(home, proj):
    write(proj / ".kollab/rosters/crew.json", crew(names=("ana", "ben", "cy", "di")))
    members = [
        {"persona": "di", "reports_to": "cy"},
        {"persona": "cy", "reports_to": "ana"},
        {"persona": "ben", "reports_to": "ana"},
        {"persona": "ana"},
    ]
    t, problems = parse(home, proj, team(members, roster="crew"))
    assert problems == []
    assert [m.persona.name for m in rosters.team_launch_order(t)] == ["ana", "cy", "ben", "di"]


def test_team_members_fall_back_to_the_persona_and_can_be_inline(home, proj):
    data = crew(names=("ana",))
    data["personas"][0].update(role="Chief", agent="research", profile="fast")
    write(proj / ".kollab/rosters/crew.json", data)
    members = [
        {"persona": "ana"},
        {
            "persona": {"name": "bot-1", "display_name": "Bot One"},
            "role": "Helper",
            "agent": "coder",
            "reports_to": "ana",
        },
    ]
    t, problems = parse(home, proj, team(members, roster="crew"))
    assert problems == []
    ana, bot = t.members
    assert (ana.role, ana.agent, ana.profile) == ("Chief", "research", "fast")
    assert (bot.persona.roster, bot.persona.display_name, bot.agent) == ("", "Bot One", "coder")
    assert rosters.find_persona("bot-1", proj) is bot.persona  # inline personas are found by name


@pytest.mark.parametrize(
    "members, lead, message",
    [
        ([{"persona": "ghost"}], "ghost", "not found in any roster"),
        ([{"persona": "ana"}], "someone", "lead 'someone' is not a member"),
        ([{"persona": "ana"}, {"persona": "ben", "reports_to": "zzz"}], "ana", "who is not a member"),
        ([{"persona": "ana", "reports_to": "ben"}, {"persona": "ben", "reports_to": "ana"}], "ana", "reporting loop"),
        ([{"persona": "ana"}, {"persona": "ana"}], "ana", "twice"),
        ([], "ana", "non-empty"),
        ([{"persona": 7}], "ana", "must be a name"),
    ],
)
def test_a_broken_team_is_dropped_with_a_reason(home, proj, members, lead, message):
    write(proj / ".kollab/rosters/crew.json", crew())
    t, problems = parse(home, proj, team(members, lead=lead, roster="crew"))
    assert t is None
    assert any(message in p["message"] for p in problems)


def test_a_team_naming_a_missing_roster_is_dropped(home, proj):
    t, problems = parse(home, proj, team([{"persona": "lapis"}], lead="lapis", roster="nonesuch"))
    assert t is None and "roster 'nonesuch' not found" in problems[0]["message"]


def test_team_member_prompt_carries_the_chain_of_command(home, proj):
    t = rosters.load_library(proj).teams["marketing"]
    jonas = next(m for m in t.members if m.persona.name == "jonas")
    text = rosters.team_member_prompt(t, jonas)
    assert text.startswith("Review Theo's drafts")
    assert "you report to: theo" in text
    assert "your identity: jonas" in text and "omar (Campaign Lead)" in text
    omar = t.members[0]
    assert "your direct reports:" in rosters.team_member_prompt(t, omar)


def test_the_bundled_org_charts_launch_in_order(home, proj):
    t = rosters.load_library(proj).teams["engineering"]
    order = [m.persona.name for m in rosters.team_launch_order(t)]
    assert order[0] == "director" and len(order) == 9
    assert order.index("platform-lead") < order.index("backend-eng")
    assert order.index("apps-lead") < order.index("qa-eng")


def test_config_modal_offers_the_rosters_a_folder_can_pick(home, proj, monkeypatch):
    from plugins.hub.plugin import HubPlugin

    write(proj / ".kollab/rosters/crew.json", crew())
    monkeypatch.chdir(proj)
    row = next(w for w in HubPlugin.get_config_widgets()["widgets"] if w["config_path"] == "plugins.hub.roster")
    assert row["type"] == "dropdown" and row["label"] == "Roster"
    assert row["options"][0] == "gems" and set(row["options"]) == {"gems", "marketing-crew", "crew"}
    assert HubPlugin.get_default_config()["plugins"]["hub"]["roster"] == "gems"
