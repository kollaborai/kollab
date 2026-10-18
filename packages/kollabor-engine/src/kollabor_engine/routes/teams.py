"""Team routes: list the teams and launch one into a folder (docs/specs/rosters-and-teams.md)."""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request  # type: ignore[import-not-found]
from pydantic import BaseModel

from ..folders import agents_in_folder
from ..hub_bridge import HubBridge
from .rosters import folder_of, load, persona_dict
from .sessions import CreateSessionRequest, create_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])
_bridge = HubBridge()


class LaunchTeamRequest(BaseModel):
    workspace: str
    profile: Optional[str] = None  # one profile for every member; default: each member's own


def team_dict(team: Any) -> Dict[str, Any]:
    return {
        "name": team.name,
        "description": team.description,
        "roster": team.roster,
        "lead": team.lead,
        "source": team.source,
        "path": str(team.path),
        "members": [
            {
                "persona": persona_dict(m.persona),
                "role": m.role,
                "agent": m.agent,
                "profile": m.profile,
                "prompt": m.prompt,
                "reports_to": m.reports_to,
            }
            for m in team.members
        ],
    }


@router.get("")
async def list_teams(
    workspace: Optional[str] = Query(None, description="Folder whose project teams apply"),
) -> Dict[str, Any]:
    lib = load(folder_of(workspace))
    return {"teams": [team_dict(t) for t in lib.teams.values()], "problems": list(lib.problems)}


@router.post("/{name}/launch")
async def launch_team(name: str, body: LaunchTeamRequest, request: Request) -> Dict[str, Any]:
    """Start every member of a team that is not already live in the folder, lead first.

    Each member starts through ``POST /sessions`` (same code, same registry), so it
    shows in the sidebar like any chat. A member already live is skipped, which makes
    a second call a no-op (every folder has its own hub, so a name live in another
    folder does not count), and a member that fails to start is reported without
    stopping the rest.
    """
    from plugins.hub.rosters import team_launch_order, team_member_prompt

    folder = folder_of(body.workspace)
    team = load(folder).teams.get(name)
    if team is None:
        raise HTTPException(status_code=404, detail=f"team '{name}' not found")

    result: Dict[str, Any] = {
        "team": team.name,
        "workspace": folder,
        "started": [],
        "skipped": [],
        "failed": [],
        "sessions": [],
    }
    for member in team_launch_order(team):
        persona = member.persona
        if any(
            a.get("identity") == persona.name
            for a in agents_in_folder(_bridge.get_agents(use_cache=False), folder)
        ):
            result["skipped"].append({"persona": persona.name, "reason": "live"})
            continue
        ref = f"{persona.roster}/{persona.name}" if persona.roster else persona.name
        try:
            session = await create_session(
                CreateSessionRequest(
                    profile=body.profile or member.profile or "default",
                    agent=member.agent or None,
                    identity=ref,
                    launch_brief=team_member_prompt(team, member),
                    workspace=folder,
                ),
                request,
            )
        except HTTPException as exc:
            result["failed"].append({"persona": persona.name, "error": str(exc.detail)})
            continue
        result["started"].append(persona.name)
        result["sessions"].append(session)
    return result
