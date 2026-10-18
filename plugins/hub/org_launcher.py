"""Team launcher - spin up a whole team from its JSON definition.

One command deploys a team: the lead first, then everyone who reports to them.
Each agent gets its own persona, role prompt, and reporting chain, and they
self-organize through the hub mesh. Team files: ``docs/specs/rosters-and-teams.md``.
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, NamedTuple, Optional, Set

from kollabor_config.config_utils import get_config_directory, get_project_data_dir

from .project_scope import is_project_scoped, resolve_project_root
from .rosters import Team, load_library, team_launch_order, team_member_prompt

logger = logging.getLogger(__name__)

# How long a launched member gets to come online, and how often we look.
ONLINE_TIMEOUT = 30.0
POLL_SECONDS = 0.2


class Launch(NamedTuple):
    """What a team launch did, by persona name."""

    started: List[str]  # launched and online
    running: List[str]  # already online in this folder, left alone
    failed: str = ""  # the member that never came online; the launch stopped there

    def notes(self, team: Team, mission: str = "") -> List[str]:
        """What the launch left alone or undone, one line each."""
        lines = []
        if self.running:
            lines.append(f"already online: {', '.join(self.running)}")
        if mission and team.lead in self.running:
            lines.append(
                f"mission not sent, {team.lead} is already online: kollab --hub msg {team.lead} <mission>"
            )
        if self.failed:
            lines.append(
                f"{self.failed} did not come online, so the launch stopped there; "
                "run the same command again to launch the rest"
            )
        return lines


def list_organizations(folder: Optional[str] = None) -> List[Team]:
    """Every team visible from ``folder`` (bundled, user, project)."""
    return list(load_library(folder).teams.values())


def load_organization(name: str, folder: Optional[str] = None) -> Optional[Team]:
    """Load a team by name, or None."""
    return load_library(folder).teams.get(name)


def _hub_dir(folder: str) -> Path:
    """The hub directory an agent started in ``folder`` joins."""
    if not is_project_scoped():
        return get_config_directory() / "hub"
    return get_project_data_dir(resolve_project_root(folder)) / "hub"


class OrgLauncher:
    """Launch teams as detached kollab processes."""

    def __init__(self, project_dir: str = ""):
        self.project_dir = project_dir or str(Path.cwd())

    def launch_org(self, org_name: str, mission: str = "") -> Launch:
        """Launch every member of a team that is not already online, lead first.

        Each member is waited for before the next one starts: the lead then
        holds the coordinator seat, and a member that cannot start is reported
        instead of counted. The launch stops at the first member that never
        comes online; running the same command again launches only the rest.

        Args:
            org_name: Name of the team to launch
            mission: Optional mission/task for the lead (handed over at launch,
                so a lead that is already online does not get it)
        """
        team = load_organization(org_name, self.project_dir)
        launch = Launch([], [])
        if not team:
            return launch

        for member in team_launch_order(team):
            persona = member.persona
            if persona.name in self._online():
                launch.running.append(persona.name)
                continue
            ref = f"{persona.roster}/{persona.name}" if persona.roster else persona.name
            self._launch_agent(
                ref,
                team_member_prompt(team, member),
                mission if persona.name == team.lead else "",
                member.agent,
                member.profile,
            )
            if not self._wait_online(persona.name):
                return launch._replace(failed=persona.name)
            launch.started.append(persona.name)

        return launch

    def _online(self) -> Set[str]:
        """Identities with a live agent in this folder's hub."""
        from .presence import live_agents_on_machine

        hub = _hub_dir(self.project_dir)
        return {
            a.get("identity")
            for hub_dir, a in live_agents_on_machine()
            if hub_dir == hub
        }

    def _wait_online(self, identity: str) -> bool:
        """True once ``identity`` is online; False after ONLINE_TIMEOUT."""
        deadline = time.monotonic() + ONLINE_TIMEOUT
        while identity not in self._online():
            if time.monotonic() >= deadline:
                logger.warning(
                    f"{identity} did not come online within {ONLINE_TIMEOUT:.0f}s"
                )
                return False
            time.sleep(POLL_SECONDS)
        return True

    def _launch_agent(
        self,
        identity: str,
        brief: str,
        initial_message: str = "",
        agent_bundle: str = "",
        profile: str = "",
    ) -> None:
        """Launch a single kollab agent as a subprocess."""
        # Build the command (--detached forces interactive mode with piped stdin).
        # The kollab this one runs from, never whichever `kollab` PATH finds first.
        from kollabor.binary import kollab_argv

        cmd = kollab_argv()
        cmd.extend(
            [
                "--detached",
                "--as",
                identity,
            ]
        )

        if agent_bundle:
            cmd.extend(["--agent", agent_bundle])

        if profile:
            cmd.extend(["--llm", profile])

        if initial_message:
            cmd.append(initial_message)

        # Launch detached
        proc = subprocess.Popen(
            cmd,
            cwd=self.project_dir,
            env={**os.environ, "KOLLAB_LAUNCH_BRIEF": brief},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info(f"Launched {identity} (pid {proc.pid})")
