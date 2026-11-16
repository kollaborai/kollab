"""Team launcher: lead first, one member at a time, truthful about what ran."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor import cli
from kollabor_tui.key_parser import KeyPress
from plugins.hub import org_launcher, presence, rosters
from plugins.hub.org_launcher import Launch, OrgLauncher
from plugins.hub.plugin import HubPlugin

MARKETING = ["campaign-lead", "brand-strategist", "copywriter", "growth-analyst", "social-lead", "content-editor"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A user config directory of its own, so the bundled library is all there is."""
    monkeypatch.setattr(
        rosters, "get_config_directory", lambda: tmp_path / "home" / ".kollab"
    )
    return tmp_path


class Fleet:
    """Stands in for the machine: launches start members, each poll lets one more come online."""

    def __init__(self, online=()):
        self.online = set(online)
        self.starting = []
        self.launched = []
        self.briefs = []
        self.never = set()

    def launch(self, identity, brief, message, bundle, profile):
        # Members already launched must be up before the next one starts.
        assert (
            not self.starting
        ), f"{identity} launched while {self.starting} still starting"
        name = identity.split("/")[-1]
        self.launched.append((name, message))
        self.briefs.append((name, brief))
        if name not in self.never:
            self.starting.append(name)

    def poll(self, *_):
        if self.starting:
            self.online.add(self.starting.pop(0))


@pytest.fixture
def fleet(home, monkeypatch):
    fleet = Fleet()
    monkeypatch.setattr(OrgLauncher, "_launch_agent", fleet.launch)
    monkeypatch.setattr(OrgLauncher, "_online", lambda self: set(fleet.online))
    monkeypatch.setattr(
        org_launcher,
        "time",
        SimpleNamespace(monotonic=time.monotonic, sleep=fleet.poll),
    )
    return fleet


def test_the_lead_comes_online_first_and_each_member_is_waited_for(fleet, home):
    launch = OrgLauncher(str(home)).launch_org("marketing", "plan the launch")

    assert launch.started == MARKETING and launch.running == [] and launch.failed == ""
    assert [name for name, _ in fleet.launched] == MARKETING
    # only the lead is handed the mission
    assert [name for name, message in fleet.launched if message] == ["campaign-lead"]


def test_members_already_online_are_left_alone_and_reported(fleet, home):
    fleet.online = {"campaign-lead", "copywriter"}
    team = org_launcher.load_organization("marketing", str(home))

    launch = OrgLauncher(str(home)).launch_org("marketing", "plan the launch")

    assert launch.started == ["brand-strategist", "growth-analyst", "social-lead", "content-editor"]
    assert launch.running == ["campaign-lead", "copywriter"]
    assert launch.notes(team, "plan the launch") == [
        "already online: campaign-lead, copywriter",
        "mission not sent, campaign-lead is already online: kollab --hub msg campaign-lead <mission>",
    ]
    assert launch.notes(team) == ["already online: campaign-lead, copywriter"]


def test_a_second_launch_starts_nothing(fleet, home):
    fleet.online = set(MARKETING)

    launch = OrgLauncher(str(home)).launch_org("marketing")

    assert launch == Launch([], MARKETING)
    assert fleet.launched == []


def test_the_launch_stops_at_a_member_that_never_comes_online(fleet, home, monkeypatch):
    fleet.never = {"copywriter"}
    monkeypatch.setattr(org_launcher, "ONLINE_TIMEOUT", 0.05)
    real_sleep = time.sleep
    monkeypatch.setattr(
        org_launcher,
        "time",
        SimpleNamespace(
            monotonic=time.monotonic, sleep=lambda s: (fleet.poll(), real_sleep(0.01))
        ),
    )
    team = org_launcher.load_organization("marketing", str(home))

    launch = OrgLauncher(str(home)).launch_org("marketing")

    assert launch.started == ["campaign-lead", "brand-strategist"] and launch.failed == "copywriter"
    assert [name for name, _ in fleet.launched] == [
        "campaign-lead",
        "brand-strategist",
        "copywriter",
    ]  # nobody after theo
    assert "copywriter did not come online" in launch.notes(team)[0]


def test_an_unknown_team_launches_nothing(fleet, home):
    assert OrgLauncher(str(home)).launch_org("no-such-team") == Launch([], [])
    assert fleet.launched == []


def test_online_means_live_in_this_folders_hub(home, monkeypatch):
    mine, other = home / "mine" / "hub", home / "other" / "hub"
    monkeypatch.setattr(
        org_launcher, "resolve_project_root", lambda folder: Path(folder)
    )
    monkeypatch.setattr(
        org_launcher, "get_project_data_dir", lambda root: home / root.name
    )
    monkeypatch.setattr(
        presence,
        "live_agents_on_machine",
        lambda: [(mine, {"identity": "campaign-lead"}), (other, {"identity": "brand-strategist"})],
    )

    assert OrgLauncher(str(home / "mine"))._online() == {"campaign-lead"}
    assert OrgLauncher(str(home / "other"))._online() == {"brand-strategist"}

    # a machine-wide hub is one room for every folder
    monkeypatch.setattr(org_launcher, "is_project_scoped", lambda: False)
    monkeypatch.setattr(org_launcher, "get_config_directory", lambda: home / "mine")
    assert OrgLauncher(str(home / "other"))._online() == {"campaign-lead"}


class _SelectingOrgPickerStack:
    async def push(self, picker, name, reuse=False):
        assert name == "hub-org-picker"
        assert reuse is False
        await picker.on_enter(None)
        return await picker.handle_input(KeyPress(name="Enter", code=0, char=None))


def _hub_with_org_picker(project):
    hub = SimpleNamespace(
        _identity=SimpleNamespace(project=str(project)), event_bus=object()
    )
    hub._altview_stack = lambda: _SelectingOrgPickerStack()
    return hub


def test_the_hub_command_does_not_freeze_the_event_loop(home, monkeypatch):
    def slow_launch(self, org_name, mission="", *, members=None, exclude=None, lead=None):
        time.sleep(0.3)  # waiting for members to come online
        return Launch(["campaign-lead"], ["brand-strategist"])

    monkeypatch.setattr(OrgLauncher, "launch_org", slow_launch)
    hub = _hub_with_org_picker(home)

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(ticker())
        reply = await HubPlugin._handle_org_command(hub, "marketing plan the launch")
        task.cancel()
        return reply, ticks

    reply, ticks = asyncio.run(main())

    assert ticks >= 5, "the loop was blocked while the team launched"
    assert reply.startswith("launched team 'marketing': 1 agents")
    assert "already online: brand-strategist" in reply


def test_the_hub_command_lists_each_member_under_who_they_report_to(home, monkeypatch):
    monkeypatch.setattr(
        OrgLauncher,
        "launch_org",
        lambda self, name, mission="", *, members=None, exclude=None, lead=None: Launch(
            MARKETING, []
        ),
    )
    hub = _hub_with_org_picker(home)

    reply = asyncio.run(HubPlugin._handle_org_command(hub, "marketing"))

    assert reply.splitlines()[1:] == [
        "campaign-lead: Campaign Lead",
        "  brand-strategist: Brand Strategist",
        "  copywriter: Copywriter",
        "    content-editor: Content Editor",
        "  growth-analyst: Growth Analyst",
        "  social-lead: Social Lead",
    ]


@pytest.mark.parametrize(
    "launch, exit_code, lines",
    [
        (
            Launch(["campaign-lead", "brand-strategist"], []),
            None,
            ["launched 2 agent(s): campaign-lead, brand-strategist"],
        ),
        (Launch([], ["campaign-lead"]), None, ["launched 0 agent(s)", "already online: campaign-lead"]),
        (
            Launch(["campaign-lead"], [], "brand-strategist"),
            1,
            ["launched 1 agent(s): campaign-lead", "brand-strategist did not come online"],
        ),
    ],
)
def test_the_hub_org_cli_prints_what_happened(
    home, monkeypatch, capsys, launch, exit_code, lines
):
    monkeypatch.setattr(
        OrgLauncher, "launch_org", lambda self, name, mission="": launch
    )
    monkeypatch.chdir(home)

    if exit_code:
        with pytest.raises(SystemExit) as raised:
            asyncio.run(cli._handle_cli_hub(["org", "marketing"]))
        assert raised.value.code == exit_code
    else:
        asyncio.run(cli._handle_cli_hub(["org", "marketing"]))

    out = capsys.readouterr().out
    for line in lines:
        assert line in out



def test_allowlist_launches_only_selected_members_and_reports_omissions(fleet, home):
    launch = OrgLauncher(str(home)).launch_org(
        "marketing", "selected mission", members=["brand-strategist", "campaign-lead"]
    )

    assert launch.started == ["campaign-lead", "brand-strategist"]
    assert launch.excluded == ("copywriter", "growth-analyst", "content-editor", "social-lead")
    assert fleet.launched == [("campaign-lead", "selected mission"), ("brand-strategist", "")]


def test_excluding_manager_reparents_prompt_before_launch(fleet, home):
    launch = OrgLauncher(str(home)).launch_org("marketing", exclude=["copywriter"])

    prompts = dict(fleet.briefs)
    assert launch.excluded == ("copywriter",)
    assert "you report to: campaign-lead" in prompts["content-editor"]
    assert "you report to: copywriter" not in prompts["content-editor"]
    assert "copywriter" not in prompts["content-editor"]


def test_replacement_lead_receives_mission_and_launches_first(fleet, home):
    launch = OrgLauncher(str(home)).launch_org(
        "marketing",
        "brand-strategist's mission",
        members=["campaign-lead", "brand-strategist"],
        lead="brand-strategist",
    )

    assert launch.started == ["brand-strategist", "campaign-lead"]
    assert fleet.launched == [("brand-strategist", "brand-strategist's mission"), ("campaign-lead", "")]


def test_omitting_authored_lead_without_replacement_is_rejected(fleet, home):
    with pytest.raises(rosters.LibraryError, match="replacement lead"):
        OrgLauncher(str(home)).launch_org("marketing", exclude=["campaign-lead"])

    assert fleet.launched == []


def test_org_cli_parses_and_forwards_selection_flags():
    args = cli.parse_arguments(
        plugin_classes=[],
        argv=[
            "--hub",
            "org",
            "marketing",
            "plan",
            "the",
            "launch",
            "--dir",
            "/tmp/project",
            "--without",
            "copywriter,social-lead",
            "--without",
            "content-editor",
        ],
    )

    assert args.hub == ["org", "marketing", "plan", "the", "launch"]
    assert cli._hub_args_with_org_options(args) == [
        "org",
        "marketing",
        "plan",
        "the",
        "launch",
        "--dir",
        "/tmp/project",
        "--without",
        "copywriter,social-lead",
        "--without",
        "content-editor",
    ]


def test_org_cli_rejects_selection_flags_outside_org_and_mixed_selectors():
    with pytest.raises(SystemExit) as wrong_command:
        cli.parse_arguments(
            plugin_classes=[], argv=["--hub", "status", "--only", "brand-strategist"]
        )
    assert wrong_command.value.code == 2

    with pytest.raises(SystemExit) as conflicting_selectors:
        cli.parse_arguments(
            plugin_classes=[],
            argv=["--hub", "org", "marketing", "--only", "brand-strategist", "--without", "copywriter"],
        )
    assert conflicting_selectors.value.code == 2


def test_org_cli_scopes_team_and_launch_to_folder_with_mission(
    home, monkeypatch, capsys
):
    project_dir = home / "project"
    project_dir.mkdir()
    team = org_launcher.load_organization("marketing", str(home))
    captured = {}

    def load_team(name, folder=None):
        captured["loaded"] = (name, folder)
        return team

    class CapturingLauncher:
        def __init__(self, folder):
            captured["launcher_folder"] = folder

        def launch_org(self, name, mission="", *, members=None, exclude=None):
            captured["launch"] = (name, mission, members, exclude)
            return SimpleNamespace(
                started=["campaign-lead"],
                failed="",
                notes=lambda _team, _mission: ["excluded: copywriter, social-lead"],
            )

    monkeypatch.setattr(cli, "_apply_hub_project_scope_from_config", lambda: None)
    monkeypatch.setattr(presence, "get_presence_dir", lambda: home / "presence")
    monkeypatch.setattr(org_launcher, "load_organization", load_team)
    monkeypatch.setattr(org_launcher, "OrgLauncher", CapturingLauncher)
    monkeypatch.chdir(home)

    asyncio.run(
        cli._handle_cli_hub(
            [
                "org",
                "marketing",
                "plan",
                "the",
                "launch",
                "--dir",
                str(project_dir),
                "--without",
                "copywriter,social-lead",
            ]
        )
    )

    assert captured["loaded"] == ("marketing", str(project_dir))
    assert captured["launcher_folder"] == str(project_dir)
    assert captured["launch"] == (
        "marketing",
        "plan the launch",
        None,
        ["copywriter", "social-lead"],
    )
    assert "excluded: copywriter, social-lead" in capsys.readouterr().out


def test_org_cli_reports_missing_replacement_lead(
    home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_apply_hub_project_scope_from_config", lambda: None)
    monkeypatch.setattr(presence, "get_presence_dir", lambda: home / "presence")
    monkeypatch.chdir(home)

    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(
            cli._handle_cli_hub(
                ["org", "marketing", "--without", "campaign-lead"]
            )
        )

    assert exit_info.value.code == 2
    assert "replacement lead" in capsys.readouterr().err
