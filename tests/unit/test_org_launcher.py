"""Team launcher: lead first, one member at a time, truthful about what ran."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor import cli
from plugins.hub import org_launcher, presence, rosters
from plugins.hub.org_launcher import Launch, OrgLauncher
from plugins.hub.plugin import HubPlugin

MARKETING = ["omar", "maya", "theo", "priya", "luz", "jonas"]


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
        self.never = set()

    def launch(self, identity, brief, message, bundle, profile):
        # Members already launched must be up before the next one starts.
        assert (
            not self.starting
        ), f"{identity} launched while {self.starting} still starting"
        name = identity.split("/")[-1]
        self.launched.append((name, message))
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
    assert [name for name, message in fleet.launched if message] == ["omar"]


def test_members_already_online_are_left_alone_and_reported(fleet, home):
    fleet.online = {"omar", "theo"}
    team = org_launcher.load_organization("marketing", str(home))

    launch = OrgLauncher(str(home)).launch_org("marketing", "plan the launch")

    assert launch.started == ["maya", "priya", "luz", "jonas"]
    assert launch.running == ["omar", "theo"]
    assert launch.notes(team, "plan the launch") == [
        "already online: omar, theo",
        "mission not sent, omar is already online: kollab --hub msg omar <mission>",
    ]
    assert launch.notes(team) == ["already online: omar, theo"]


def test_a_second_launch_starts_nothing(fleet, home):
    fleet.online = set(MARKETING)

    launch = OrgLauncher(str(home)).launch_org("marketing")

    assert launch == Launch([], MARKETING)
    assert fleet.launched == []


def test_the_launch_stops_at_a_member_that_never_comes_online(fleet, home, monkeypatch):
    fleet.never = {"theo"}
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

    assert launch.started == ["omar", "maya"] and launch.failed == "theo"
    assert [name for name, _ in fleet.launched] == [
        "omar",
        "maya",
        "theo",
    ]  # nobody after theo
    assert "theo did not come online" in launch.notes(team)[0]


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
        lambda: [(mine, {"identity": "omar"}), (other, {"identity": "maya"})],
    )

    assert OrgLauncher(str(home / "mine"))._online() == {"omar"}
    assert OrgLauncher(str(home / "other"))._online() == {"maya"}

    # a machine-wide hub is one room for every folder
    monkeypatch.setattr(org_launcher, "is_project_scoped", lambda: False)
    monkeypatch.setattr(org_launcher, "get_config_directory", lambda: home / "mine")
    assert OrgLauncher(str(home / "other"))._online() == {"omar"}


def test_the_hub_command_does_not_freeze_the_event_loop(home, monkeypatch):
    def slow_launch(self, org_name, mission=""):
        time.sleep(0.3)  # waiting for members to come online
        return Launch(["omar"], ["maya"])

    monkeypatch.setattr(OrgLauncher, "launch_org", slow_launch)
    hub = SimpleNamespace(_identity=SimpleNamespace(project=str(home)))

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
    assert "already online: maya" in reply


def test_the_hub_command_lists_each_member_under_who_they_report_to(home, monkeypatch):
    monkeypatch.setattr(
        OrgLauncher, "launch_org", lambda self, name, mission="": Launch(MARKETING, [])
    )
    hub = SimpleNamespace(_identity=SimpleNamespace(project=str(home)))

    reply = asyncio.run(HubPlugin._handle_org_command(hub, "marketing"))

    assert reply.splitlines()[1:] == [
        "omar: Campaign Lead",
        "  maya: Brand Strategist",
        "  theo: Copywriter",
        "    jonas: Content Editor",
        "  priya: Growth Analyst",
        "  luz: Social Lead",
    ]


@pytest.mark.parametrize(
    "launch, exit_code, lines",
    [
        (Launch(["omar", "maya"], []), None, ["launched 2 agent(s): omar, maya"]),
        (Launch([], ["omar"]), None, ["launched 0 agent(s)", "already online: omar"]),
        (
            Launch(["omar"], [], "maya"),
            1,
            ["launched 1 agent(s): omar", "maya did not come online"],
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
