"""Organization launcher - spin up entire teams from a JSON definition.

One command deploys a full org: director, managers, and engineers.
Each agent gets its own identity, role prompt, and reporting chain.
They self-organize through the hub mesh.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kollabor_config.config_utils import get_config_directory

logger = logging.getLogger(__name__)


def get_orgs_dir() -> Path:
    """Get the organizations directory (bundled with plugin)."""
    return Path(__file__).parent / "organizations"


def get_user_orgs_dir(create: bool = False) -> Path:
    """Get user's custom organizations directory."""
    d = get_config_directory() / "hub" / "organizations"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def list_organizations() -> List[Dict[str, str]]:
    """List all available organization definitions."""
    orgs = []

    # Bundled orgs
    bundled = get_orgs_dir()
    if bundled.exists():
        for f in sorted(bundled.glob("*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                orgs.append(
                    {
                        "name": data.get("name", f.stem),
                        "description": data.get("description", ""),
                        "source": "bundled",
                        "path": str(f),
                    }
                )
            except Exception:
                pass

    # User orgs (override bundled if same name)
    user = get_user_orgs_dir()
    bundled_names = {o["name"] for o in orgs}
    for f in sorted(user.glob("*.json")) if user.exists() else []:
        try:
            with open(f) as fh:
                data = json.load(fh)
            name = data.get("name", f.stem)
            if name in bundled_names:
                # User override
                orgs = [o for o in orgs if o["name"] != name]
            orgs.append(
                {
                    "name": name,
                    "description": data.get("description", ""),
                    "source": "user",
                    "path": str(f),
                }
            )
        except Exception:
            pass

    return orgs


def load_organization(name: str) -> Optional[Dict[str, Any]]:
    """Load an organization definition by name."""
    # User orgs first (override)
    user_path = get_user_orgs_dir() / f"{name}.json"
    if user_path.exists():
        with open(user_path) as f:
            user_data: Dict[str, Any] = json.load(f)
            return user_data

    # Bundled orgs
    bundled_path = get_orgs_dir() / f"{name}.json"
    if bundled_path.exists():
        with open(bundled_path) as f:
            bundled_data: Dict[str, Any] = json.load(f)
            return bundled_data

    return None


def get_org_agents(org: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract all agents from an org definition in launch order.

    Returns agents ordered: director first, then managers, then members.
    This ensures the reporting chain is established top-down.
    """
    agents = []

    # Director
    director = org.get("director", {})
    if director:
        agents.append(
            {
                **director,
                "level": "director",
                "team": None,
            }
        )

    # Teams: manager first, then members
    for team in org.get("teams", []):
        manager = team.get("manager", {})
        if manager:
            agents.append(
                {
                    **manager,
                    "level": "manager",
                    "team": team.get("name", ""),
                }
            )
        for member in team.get("members", []):
            agents.append(
                {
                    **member,
                    "level": "member",
                    "team": team.get("name", ""),
                }
            )

    return agents


def build_agent_prompt(agent: Dict, org: Dict, all_agents: List[Dict]) -> str:
    """Build the full system prompt for an agent including org context.

    Adds the reporting chain, team structure, and communication
    instructions to the agent's base prompt.
    """
    lines = []

    # Base role prompt
    lines.append(agent.get("prompt", ""))
    lines.append("")

    # Org context
    org_name = org.get("name", "unknown")
    lines.append(f"--- organization: {org_name} ---")
    lines.append(f"your identity: {agent['identity']}")
    lines.append(f"your role: {agent.get('role', 'team member')}")

    if agent.get("reports_to"):
        lines.append(f"you report to: {agent['reports_to']}")

    # Who reports to this agent
    direct_reports = [a for a in all_agents if a.get("reports_to") == agent["identity"]]
    if direct_reports:
        report_names = [
            f"{a['identity']} ({a.get('role', '')})" for a in direct_reports
        ]
        lines.append(f"your direct reports: {', '.join(report_names)}")

    # Team context
    if agent.get("team"):
        team_members = [
            a
            for a in all_agents
            if a.get("team") == agent["team"] and a["identity"] != agent["identity"]
        ]
        if team_members:
            member_names = [
                f"{a['identity']} ({a.get('role', '')})" for a in team_members
            ]
            lines.append(f"your team ({agent['team']}): {', '.join(member_names)}")

    lines.append("")
    lines.append(
        "communicate with other agents using: "
        '<hub_msg to="identity">your message</hub_msg>'
    )
    lines.append(
        "when you finish a task, report to your manager. "
        "when you need help, ask a teammate."
    )
    lines.append("--- end organization ---")

    return "\n".join(lines)


class OrgLauncher:
    """Launch and manage organizations."""

    def __init__(self, project_dir: str = ""):
        self.project_dir = project_dir or str(Path.cwd())
        self._launched: List[subprocess.Popen] = []

    def launch_org(self, org_name: str, mission: str = "") -> Tuple[int, List[str]]:
        """Launch all agents in an organization.

        Args:
            org_name: Name of the organization to launch
            mission: Optional mission/task for the director

        Returns:
            Tuple of (agent_count, list of identities)
        """
        org = load_organization(org_name)
        if not org:
            return 0, []

        agents = get_org_agents(org)
        if not agents:
            return 0, []

        identities = []

        for i, agent in enumerate(agents):
            identity = agent["identity"]
            prompt = build_agent_prompt(agent, org, agents)

            # For the director, append the mission if provided
            initial_message = ""
            if agent["level"] == "director" and mission:
                initial_message = mission

            # Launch as a subprocess
            bundle = agent.get("agent_bundle", "")
            self._launch_agent(identity, prompt, initial_message, bundle)
            identities.append(identity)

            # Stagger launches so hub registration doesn't race
            if i < len(agents) - 1:
                import time

                time.sleep(2)

        return len(agents), identities

    def _launch_agent(
        self,
        identity: str,
        system_prompt: str,
        initial_message: str = "",
        agent_bundle: str = "",
    ) -> None:
        """Launch a single kollab agent as a subprocess."""
        # Write the system prompt to a temp file
        from .presence import get_hub_dir

        prompt_dir = get_hub_dir() / "org-prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"{identity}.md"
        prompt_file.write_text(system_prompt)

        # Build the command (--detached forces interactive mode with piped stdin)
        import shutil

        kollab_bin = shutil.which("kollab")
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
                "--system-prompt",
                str(prompt_file),
            ]
        )

        if agent_bundle:
            cmd.extend(["--agent", agent_bundle])

        if initial_message:
            cmd.append(initial_message)

        # Launch detached
        proc = subprocess.Popen(
            cmd,
            cwd=self.project_dir,
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
