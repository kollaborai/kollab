"""Team launcher - spin up a whole team from its JSON definition.

One command deploys a team: the lead first, then everyone who reports to them.
Each agent gets its own persona, role prompt, and reporting chain, and they
self-organize through the hub mesh. Team files: ``docs/specs/rosters-and-teams.md``.
"""

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .rosters import Team, load_library, team_launch_order, team_member_prompt

logger = logging.getLogger(__name__)


def list_organizations(folder: Optional[str] = None) -> List[Team]:
    """Every team visible from ``folder`` (bundled, user, project)."""
    return list(load_library(folder).teams.values())


def load_organization(name: str, folder: Optional[str] = None) -> Optional[Team]:
    """Load a team by name, or None."""
    return load_library(folder).teams.get(name)


class OrgLauncher:
    """Launch and manage teams as detached kollab processes."""

    def __init__(self, project_dir: str = ""):
        self.project_dir = project_dir or str(Path.cwd())
        self._launched: List[subprocess.Popen] = []

    def launch_org(self, org_name: str, mission: str = "") -> Tuple[int, List[str]]:
        """Launch every member of a team, lead first.

        Args:
            org_name: Name of the team to launch
            mission: Optional mission/task for the lead

        Returns:
            Tuple of (agent_count, list of identities)
        """
        team = load_organization(org_name, self.project_dir)
        if not team:
            return 0, []

        members = team_launch_order(team)
        identities = []

        for i, member in enumerate(members):
            persona = member.persona
            ref = f"{persona.roster}/{persona.name}" if persona.roster else persona.name
            self._launch_agent(
                ref,
                team_member_prompt(team, member),
                mission if persona.name == team.lead else "",
                member.agent,
                member.profile,
            )
            identities.append(persona.name)

            # Stagger launches so hub registration doesn't race
            if i < len(members) - 1:
                time.sleep(2)

        return len(members), identities

    def _launch_agent(
        self,
        identity: str,
        brief: str,
        initial_message: str = "",
        agent_bundle: str = "",
        profile: str = "",
    ) -> None:
        """Launch a single kollab agent as a subprocess."""
        # Build the command (--detached forces interactive mode with piped stdin)
        from kollabor.binary import own_launcher

        # The binary or Kollab.app this runs from, then a kollab on PATH.
        launcher = own_launcher()
        kollab_bin = str(launcher) if launcher else shutil.which("kollab")
        if kollab_bin:
            cmd = [kollab_bin]
        else:
            # Dev mode fallback
            main_py = Path(__file__).resolve().parents[2] / "main.py"
            cmd = [sys.executable, str(main_py)]

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
        self._launched.append(proc)
        logger.info(f"Launched {identity} (pid {proc.pid})")

    def get_launched_count(self) -> int:
        return len(self._launched)

    def kill_all(self) -> int:
        """Kill all launched agents."""
        killed = 0
        for proc in self._launched:
            try:
                proc.terminate()
                killed += 1
            except Exception:
                pass
        self._launched.clear()
        return killed
