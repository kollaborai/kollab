"""Hub mesh REST routes for kollabor-engine.

Exposes agent presence, output, status, and messaging through
the engine REST API by reading presence files and querying
agent unix sockets via HubBridge.

All endpoints require bearer token auth (server-level middleware).
"""

import asyncio
import logging
import weakref
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query  # type: ignore[import-not-found]
from pydantic import BaseModel

from ..hub_bridge import HubBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hub", tags=["hub"])
_schedule_mutation_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_schedule_rows_cache: list | None = None
_schedule_rows_cache_signature: tuple | None = None


def _schedule_mutation_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _schedule_mutation_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _schedule_mutation_locks[loop] = lock
    return lock

# Shared bridge instance (stateless, safe to reuse)
_bridge = HubBridge()


def _resolve_agent(agent_ref: str) -> tuple[Dict[str, Any] | None, str]:
    """Resolve either the stable agent id or the human-facing identity."""
    agent = _bridge.get_agent(agent_ref)
    if agent is not None:
        return agent, agent_ref

    agent = _bridge.get_agent_by_identity(agent_ref)
    if agent is None:
        return None, agent_ref
    return agent, str(agent.get("agent_id") or agent_ref)


class SendMessageRequest(BaseModel):
    target: str  # identity; the agent_id below wins when both are sent
    agent_id: str | None = None  # the selected row's presence id: names are shared across folders (#568)
    content: str
    from_identity: str = "api"


@router.get("/agents")
async def list_agents(
    refresh: bool = Query(False, description="Bypass cache, re-read presence"),
):
    """List all active hub agents from presence files."""
    agents = _bridge.get_agents(use_cache=not refresh)
    return {"agents": agents, "count": len(agents)}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, ping: bool = Query(False)):
    """Get details for a single hub agent."""
    agent, resolved_id = _resolve_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    result: Dict[str, Any] = {"agent": agent}

    if ping:
        alive = await _bridge.ping_agent(resolved_id)
        result["socket_alive"] = alive

    return result


@router.get("/agents/{agent_id}/output")
async def get_agent_output(
    agent_id: str,
    lines: int = Query(100, ge=1, le=1000),
    format: str = Query("terminal", pattern="^(terminal|plain|json)$"),
    max_chars: int | None = Query(None, ge=1, le=200000),
    cursor: str | None = Query(None),
):
    """Fetch recent output; plain/json are explicit browser formats."""
    agent, resolved_id = _resolve_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    if format == "terminal":
        output = await _bridge.get_agent_output(resolved_id, lines=lines)
        if output is None:
            return {
                "agent_id": agent_id,
                "output": None,
                "error": "Agent socket unavailable or timed out",
            }
        return {"agent_id": agent_id, "output": output}

    capture = await _bridge.get_agent_capture(
        resolved_id,
        capture_format=format,
        lines=lines,
        max_chars=max_chars,
        cursor=cursor,
    )
    if capture is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "agent_unavailable", "agent": agent_id},
        )
    capture.pop("type", None)
    capture["agent_id"] = agent_id
    capture["agent"] = {
        "identity": agent.get("identity", agent_id),
        "status": agent.get("status"),
        "duration": agent.get("duration"),
    }
    if format == "plain":
        capture["output"] = capture.pop("text", "")
        capture.pop("lines", None)
        return capture
    return capture


@router.get("/agents/{agent_id}/status")
async def get_agent_status(agent_id: str):
    """Fetch current status from an agent via unix socket."""
    agent, resolved_id = _resolve_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    status = await _bridge.get_agent_status(resolved_id)
    if status is None:
        return {
            "agent_id": agent_id,
            "status": None,
            "error": "Agent socket unavailable or timed out",
        }

    return {"agent_id": agent_id, "status": status}


@router.post("/messages")
async def send_message(body: SendMessageRequest):
    """Send a message to a hub agent via unix socket.

    Pass ``agent_id`` (the presence id of the selected agent) to reach exactly
    that agent: a same-named agent in another folder is never a fallback.
    ``target`` alone is an identity string (e.g. 'koordinator') and reaches the
    first agent with that name. The 'to' field is set correctly so
    TRIGGER_LLM_CONTINUE fires.
    """
    if body.agent_id:
        success = await _bridge.send_message(
            body.agent_id,
            body.content,
            from_identity=body.from_identity,
        )
    else:
        success = await _bridge.send_message_to_identity(
            body.target,
            body.content,
            from_identity=body.from_identity,
        )

    if not success:
        raise HTTPException(
            status_code=503,
            detail=f"Could not deliver message to '{body.target}' "
            "(agent offline or socket unavailable)",
        )

    return {"ok": True, "target": body.target, "delivered": True}


@router.get("/feed")
async def hub_feed():
    """SSE endpoint streaming hub events (roster changes, messages).

    Note: Full event streaming requires a watcher on presence/message
    files. This initial version provides a snapshot on connect and
    will be extended with live events in phase 7b (WebSocket).
    """
    from ..sse import serialize

    agents = _bridge.get_agents()

    # For now, return initial snapshot as SSE event
    event = {
        "type": "hub_snapshot",
        "agents": agents,
        "count": len(agents),
    }

    async def snapshot_generator():
        yield f"data: {serialize(event)}\n\n"

    from starlette.responses import StreamingResponse  # type: ignore[import-not-found]

    return StreamingResponse(
        snapshot_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/cron-jobs")
async def list_cron_jobs(
    refresh: bool = Query(False, description="Bypass cache, re-read presence"),
    timeout: float = Query(3.0, gt=0, le=15),
):
    """Aggregate scheduled hub cron jobs across live agents (issue #612).

    Read-only: every job stays owned by the agent process that scheduled
    it; each live agent's unix socket answers a local-only `cron_jobs`
    action. An agent that does not answer (offline, socket gone) is
    reported separately and never blocks the others.
    """
    agents = _bridge.get_agents(use_cache=not refresh)
    jobs: list = []
    responded: list = []
    unavailable: list = []
    errors: dict = {}

    # Ask everyone at once: each socket gets its own timeout budget, so a
    # serialized loop would spend N x timeout on a dead roster.
    replies = await asyncio.gather(
        *[
            _bridge.query_socket(
                str(agent.get("agent_id") or ""), "cron_jobs", timeout=timeout
            )
            for agent in agents
        ]
    )
    for agent, reply in zip(agents, replies):
        identity = str(agent.get("identity") or agent.get("agent_id") or "")
        if not isinstance(reply, dict) or reply.get("type") != "cron_jobs":
            unavailable.append(identity)
            # Surface the dispatcher's operator-gate rejection and any other
            # framed refusal verbatim; transport failures have no frame.
            if isinstance(reply, dict):
                reason = reply.get("msg") or reply.get("error")
                if reason:
                    errors[identity] = str(reason)
            continue
        responded.append(identity)
        # A cron_jobs frame whose callback failed still answers with `error`
        # set; keep its rows but surface the failure too.
        if reply.get("error"):
            errors[identity] = str(reply["error"])
        job_rows = reply.get("jobs")
        if not isinstance(job_rows, list):
            errors[identity] = "cron jobs response contained a non-list jobs field"
            continue
        for job in job_rows:
            if not isinstance(job, dict):
                errors[identity] = "cron jobs response contained an invalid job row"
                continue
            row = dict(job)
            row["agent"] = identity
            jobs.append(row)

    return {
        "jobs": jobs,
        "count": len(jobs),
        "agents_responded": responded,
        "agents_unavailable": unavailable,
        "agents_errors": errors,
    }


# -- persistent schedules (#612, stage 4) -------------------------------------
#
# Reads come straight from ~/.kollab/schedules.json. Writes never touch the
# file while a hub runs: the whole new list goes to the hub coordinator's
# local-only `schedules_save` socket op, which forwards it to the network
# primary or writes it and wakes the grid timer. With no live agent at all
# nothing else can write, so the route writes the file itself.


def _schedules_dir():
    from kollabor_config.config_utils import get_config_directory

    return get_config_directory()


def _load_schedule_rows() -> list:
    from plugins.hub.schedules import ScheduleStore

    return [row.to_dict() for row in ScheduleStore(_schedules_dir() / "schedules.json").load()]


def _schedule_rows_signature() -> tuple:
    path = _schedules_dir() / "schedules.json"
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)


def _mutable_schedule_rows() -> list:
    """Return the latest roster, including a remote save not synced to disk yet."""
    global _schedule_rows_cache, _schedule_rows_cache_signature

    signature = _schedule_rows_signature()
    if _schedule_rows_cache is None or signature != _schedule_rows_cache_signature:
        _schedule_rows_cache = _load_schedule_rows()
        _schedule_rows_cache_signature = signature
    return [dict(row) for row in _schedule_rows_cache]


def _remember_schedule_rows(rows: list) -> None:
    """Keep later mutations based on the accepted roster while sync catches up."""
    global _schedule_rows_cache, _schedule_rows_cache_signature

    _schedule_rows_cache = [dict(row) for row in rows]
    _schedule_rows_cache_signature = _schedule_rows_signature()


class ScheduleFields(BaseModel):
    agents: list[str] | None = None
    prompt: str | None = None
    interval_seconds: float | None = None
    active: bool | None = None
    when_busy: str | None = None
    skill: str | None = None


def _checked(row: dict) -> dict:
    from plugins.hub.schedules import ScheduleRow

    row["agents"] = [a.strip() for a in row.get("agents") or [] if a and a.strip()]
    row["prompt"] = str(row.get("prompt") or "").strip()
    try:
        return ScheduleRow.from_dict(row).to_dict()
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _save_schedule_rows(rows: list) -> str:
    agents = _bridge.get_agents(use_cache=False)
    if not agents:
        from plugins.hub.device_names import parse_handle
        from plugins.hub.schedules import ScheduleRow, ScheduleStore

        clean = [ScheduleRow.from_dict(r) for r in rows]
        if any(parse_handle(agent) is None for row in clean for agent in row.agents):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Cannot save schedules with unqualified agent names while no "
                    "hub agent is live. Start an agent and try again."
                ),
            )
        ScheduleStore(_schedules_dir() / "schedules.json").save(clean)
        return "file"
    replies = await asyncio.gather(
        *[
            _bridge.query_socket(
                str(agent.get("agent_id") or ""), "schedules_save", {"rows": rows}, timeout=35.0
            )
            for agent in agents
        ]
    )
    # Every agent is asked on purpose: each project folder has its own
    # coordinator and timer over the one schedules.json, and only the ones
    # asked wake up. Identical rows written twice are harmless. One success
    # is a saved change; a coordinator's failure only counts when none saved.
    frames = [r for r in replies if isinstance(r, dict) and r.get("type") == "schedules_save"]
    for reply in frames:
        if reply.get("ok"):
            return str(reply.get("via") or "local")
    for reply in frames:
        if reply.get("error") and reply["error"] != "not_coordinator":
            raise HTTPException(status_code=502, detail=str(reply["error"]))
    raise HTTPException(
        status_code=503,
        detail="No hub coordinator took the change, so nothing was saved. Start an agent and try again.",
    )


@router.get("/schedules")
async def list_schedules():
    """Every row of schedules.json plus its last run record."""
    from plugins.hub.schedules import RunLog

    runs = RunLog(_schedules_dir() / "schedules-runs.jsonl")
    rows = _load_schedule_rows()
    for row in rows:
        row["last_run"] = runs.last_run(row["id"])
    return {"schedules": rows, "file": str(_schedules_dir() / "schedules.json")}


@router.post("/schedules")
async def create_schedule(body: ScheduleFields):
    async with _schedule_mutation_lock():
        import time
        import uuid

        now = time.time()
        fields = body.model_dump(exclude_none=True)
        interval = float(fields.get("interval_seconds") or 0)
        row = _checked(
            {
                "id": uuid.uuid4().hex[:8],
                "agents": fields.get("agents") or [],
                "prompt": fields.get("prompt") or "",
                "interval_seconds": interval,
                "anchor": now + interval,  # first fire one interval from now
                "active": fields.get("active", True),
                "when_busy": fields.get("when_busy", "queue"),
                "skill": fields.get("skill", ""),
                "created_by": "web",
                "created_at": now,
                "updated_at": now,
                "revision": 1,
            }
        )
        rows = _mutable_schedule_rows()
        rows.append(row)
        via = await _save_schedule_rows(rows)
        _remember_schedule_rows(rows)
        return {"schedule": row, "via": via}


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleFields):
    async with _schedule_mutation_lock():
        import time

        rows = _mutable_schedule_rows()
        for i, old in enumerate(rows):
            if old["id"] == schedule_id:
                row = _checked(
                    {
                        **old,
                        **body.model_dump(exclude_none=True),
                        "updated_at": time.time(),
                        "revision": int(old.get("revision") or 1) + 1,
                    }
                )
                rows[i] = row
                via = await _save_schedule_rows(rows)
                _remember_schedule_rows(rows)
                return {"schedule": row, "via": via}
        raise HTTPException(status_code=404, detail=f"No schedule '{schedule_id}'")


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    async with _schedule_mutation_lock():
        rows = _mutable_schedule_rows()
        kept = [row for row in rows if row["id"] != schedule_id]
        if len(kept) == len(rows):
            raise HTTPException(status_code=404, detail=f"No schedule '{schedule_id}'")
        via = await _save_schedule_rows(kept)
        _remember_schedule_rows(kept)
        return {"ok": True, "via": via}
