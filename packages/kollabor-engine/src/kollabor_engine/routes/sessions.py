"""Session lifecycle routes."""

import asyncio
import json
import logging
import os
import re
import socket
import threading
import time
from contextlib import aclosing
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request  # type: ignore[import-not-found]
from pydantic import BaseModel

from kollabor_ai import LLMProfile
from kollabor_ai.session_naming import generate_session_name

from .. import hidden_agents, sidebar_order
from ..daemon_pool import get_daemon_pool
from ..history_paging import (
    HistoryPageTooLarge,
    InvalidHistoryCursor,
    StaleHistoryCursor,
    build_history_page,
    resolve_history_output,
)
from ..hub_bridge import HubBridge, network_daemons
from ..network_aggregate import merge_view_and_memberships
from ..server import get_session_registry
from ..session import INLINE_PROFILE, EngineSession
from .rosters import apply_persona
from .workspaces import remember_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


# The engine is a singleton process with a module-level session registry; its
# active per-port store is configured by the server lifespan for route cleanup.
_handover_store: Any = None


def set_handover_store(store: Any) -> None:
    global _handover_store
    _handover_store = store



def _request_handover_store(request: Request) -> Any:
    try:
        return getattr(request.app.state, "handover_store", _handover_store)
    except (AttributeError, KeyError):
        # Direct route-unit invocations may construct a minimal Starlette
        # Request without an ASGI app in scope.
        return _handover_store

_session_reservation_lock = threading.Lock()
_pending_session_ids: set[str] = set()

# Pattern to detect <trender> tags (security: block user-controlled dynamic content)
# Matches: <trender>...</trender> and <trender type="..." ... />
TRENDER_PATTERN = re.compile(
    r"<trender\b[^>]*>.*?</trender>|<trender\b[^>]*/?>", re.DOTALL | re.IGNORECASE
)


def _reserve_session_id(
    registry: Dict[str, EngineSession], requested_id: Optional[str]
) -> str:
    """Reserve an ID before any daemon work can begin.

    A generated name ends in a random token, so two members of one team launch
    should never draw the same; if they do, redraw. A requested ID is the
    caller's to fix.
    """
    with _session_reservation_lock:
        for _ in range(1 if requested_id else 20):
            session_id = requested_id or generate_session_name()
            if session_id not in registry and session_id not in _pending_session_ids:
                _pending_session_ids.add(session_id)
                return session_id
    raise HTTPException(
        status_code=409,
        detail=f"Session '{session_id}' is already in use",
    )


def _release_session_id(session_id: str) -> None:
    with _session_reservation_lock:
        _pending_session_ids.discard(session_id)


async def _shutdown_failed_session(session: EngineSession, session_id: str) -> None:
    """Best-effort cleanup for a session that never registered."""
    try:
        await session.shutdown()
    except asyncio.CancelledError:
        logger.warning("Session %s cleanup was cancelled", session_id)
    except Exception:
        logger.exception("Session %s cleanup failed", session_id)


class Credentials(BaseModel):
    """Raw API credentials - used when caller manages its own LLM profiles."""

    provider: str = "anthropic"
    api_key: str
    model: str
    base_url: str = ""
    max_tokens: Optional[int] = None


class CreateSessionRequest(BaseModel):
    # None (or the placeholder "default") names no profile: the persona's or the
    # agent's applies, else the active profile, which New Session also starts on.
    profile: Optional[str] = None
    agent: Optional[str] = None
    identity: Optional[str] = None
    system_prompt: Optional[str] = None
    # Instructions for this agent from whoever launched it (a team seat: role,
    # reporting chain). The daemon adds them to its hub context
    # (KOLLAB_LAUNCH_BRIEF); a bundle's own prompt is untouched.
    launch_brief: Optional[str] = None
    workspace: Optional[str] = None
    approval_mode: str = "trust_all"
    mcp_servers: List[str] = []
    metadata: dict = {}
    credentials: Optional[Credentials] = None
    user_token: Optional[str] = None
    session_id: Optional[str] = (
        None  # caller-supplied ID (proxy pre-generates to enable token injection)
    )


class SetProfileRequest(BaseModel):
    name: str
    model: Optional[str] = None
    effort: Optional[str] = None


def _sanitize_system_prompt(prompt: Optional[str]) -> Optional[str]:
    """Sanitize user-provided system prompt to block command injection.

    Blocks <trender> tags which could execute arbitrary shell commands.
    Replaces them with a safe placeholder string.

    Args:
        prompt: User-provided system prompt string

    Returns:
        Sanitized prompt with trender tags replaced, or None if input is None
    """
    if not prompt:
        return prompt

    # Check for trender tags and replace with safe placeholder
    if TRENDER_PATTERN.search(prompt):
        logger.warning(
            "Blocked <trender> tags in user-provided system prompt (command injection prevention)"
        )
        return TRENDER_PATTERN.sub("[dynamic content removed for security]", prompt)

    return prompt


def _resolve_agent(name: str, workspace: Optional[str] = None) -> Optional[dict]:
    """Load agent definition by name: local, then global, then bundled (AgentManager's order).

    The local tier is ``workspace``'s own ``.kollab/agents``, the folder the
    daemon will run in (the engine's folder when none is given), never the
    engine's cwd when the chat is elsewhere.

    Returns a dict with resolved keys:
      system_prompt: str - fully rendered (trender includes resolved)
      profile: str|None  - agent's preferred profile (may be None)
      mcp_servers: list  - MCP servers declared in agent.json
      tools: list        - native tools declared in agent.json

    Returns None if the agent is not found.
    """
    try:
        from kollabor_agent.agent_manager import Agent, AgentManager
    except ImportError:
        logger.warning("kollabor_agent not available — cannot resolve agent")
        return None

    try:
        project = Path(workspace).expanduser().resolve() if workspace else None
        am = AgentManager(project_path=project)
        found = am.find_agent_dir(name)
        agent_obj = Agent.from_directory(found[0], source=found[1]) if found else None
        if not agent_obj:
            return None

        system_prompt = agent_obj.get_full_system_prompt(agent_manager=am)

        # Read mcp_servers from agent.json directly (not parsed by Agent.from_directory)
        mcp_servers: list = []
        hub = True
        config_file = (
            agent_obj.directory / "agent.json" if agent_obj.directory else None
        )
        if config_file and config_file.exists():
            try:
                import json as _json

                cfg = _json.loads(config_file.read_text())
                mcp_servers = list(cfg.get("mcp_servers", []) or [])
                hub = cfg.get("hub", True) is not False
            except Exception:
                pass

        return {
            "system_prompt": system_prompt,
            "profile": getattr(agent_obj, "profile", None),
            "mcp_servers": mcp_servers,
            "tools": list(getattr(agent_obj, "tools", []) or []),
            "hub": hub,
        }
    except Exception as e:
        logger.warning(f"Failed to resolve agent '{name}': {e}")
        return None


@router.post("")
async def create_session(body: CreateSessionRequest, request: Request):
    registry = get_session_registry()
    is_webui_launch = request.headers.get("x-kollab-session-source") == "webui"
    handover_store = _request_handover_store(request) if is_webui_launch else None

    # Resolve user_token: body takes priority, header is fallback (proxy injects it)
    user_token = body.user_token or request.headers.get("x-mentiko-session-token")

    # A persona names its own bundle and profile; what the caller set wins.
    apply_persona(body)

    # Resolve agent definition if requested
    agent_data = None
    if body.agent:
        agent_data = _resolve_agent(body.agent, body.workspace)
        if agent_data is None:
            raise HTTPException(
                status_code=404, detail=f"Agent '{body.agent}' not found"
            )

    # Determine effective system prompt:
    # agent system_prompt wins over caller-supplied (already rendered, safe to use as-is)
    if agent_data:
        effective_system_prompt = agent_data["system_prompt"] or body.system_prompt
    else:
        # Sanitize user-provided system prompt to block command injection
        effective_system_prompt = _sanitize_system_prompt(body.system_prompt)

    safe_system_prompt = effective_system_prompt

    # Determine effective profile:
    # caller's profile wins; agent profile used only when the caller names none.
    # "default" in a bundle's agent.json (coder has it) is no preference, only the
    # built-in auto-detect profile's own name.
    effective_profile = body.profile
    agent_profile = agent_data.get("profile") if agent_data else None
    if agent_profile not in (None, "", "default") and body.profile in (None, "default"):
        effective_profile = agent_profile

    # Merge MCP servers: deduplicate, agent's list appended to caller's
    effective_mcp_servers = list(body.mcp_servers)
    if agent_data:
        for s in agent_data.get("mcp_servers", []):
            if s not in effective_mcp_servers:
                effective_mcp_servers.append(s)

    # Load profile from credentials or ProfileManager
    profile: Optional[LLMProfile] = None

    if body.credentials:

        creds = body.credentials

        # For custom provider, send the URL as-is (user provides full path)
        # For openai/anthropic, strip /chat/completions since SDK appends it
        base_url = creds.base_url or ""

        profile = LLMProfile(
            name=INLINE_PROFILE,
            provider=creds.provider,
            model=creds.model,
            api_key=creds.api_key,
            base_url=base_url,
            max_tokens=creds.max_tokens,
            streaming=True,
            supports_tools=True,
        )
    else:
        from kollabor_ai import ProfileManager

        pm = ProfileManager()
        effective_profile = effective_profile or pm.active_profile_name
        profile = pm.get_profile(effective_profile)
        if not profile:
            raise HTTPException(
                status_code=404, detail=f"Profile '{effective_profile}' not found"
            )
        assert profile is not None  # narrowed by raise above

    # Match the normal CLI's memorable session IDs. Reserve both caller-supplied
    # and generated IDs before constructing or spawning a daemon so concurrent
    # requests cannot replace an owned registry entry.
    session_id = _reserve_session_id(registry, body.session_id)
    try:
        try:
            session = EngineSession(
                session_id=session_id,
                profile=profile,
                approval_mode=body.approval_mode,
                workspace=body.workspace,
                system_prompt=safe_system_prompt,
                mcp_server_names=effective_mcp_servers,
                user_token=user_token,
                agent=body.agent,
                identity=body.identity,
                launch_brief=body.launch_brief,
                # Bundles with "hub": false (product assistants) get a solo
                # daemon: attachable by this engine, invisible to the mesh.
                solo=bool(agent_data) and agent_data.get("hub") is False,
                webui_owned=handover_store is not None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        if handover_store is not None:
            session.on_persistence_changed = handover_store.register_session


        # Spawning the daemon is the slow part of session creation (plugin
        # discovery plus the hub join). Every failure after construction must
        # stop the candidate daemon before the error leaves this route.
        async def persist_spawned(candidate: EngineSession) -> None:
            if handover_store is not None:
                handover_store.register_session(candidate)

        try:
            if handover_store is None:
                await session.initialize()
            else:
                await session.initialize(on_spawned=persist_spawned)
            # Refresh metadata once initialization has a conversation ID and
            # the daemon has had time to publish its session-log path. The first
            # callback above remains the crash-safe early registration.
            if handover_store is not None:
                handover_store.register_session(session)
        except asyncio.CancelledError:
            await _shutdown_failed_session(session, session_id)
            if handover_store is not None and session.daemon is None:
                handover_store.remove_session(session_id)
            raise
        except ValueError as e:
            await _shutdown_failed_session(session, session_id)
            if handover_store is not None and session.daemon is None:
                handover_store.remove_session(session_id)
            raise HTTPException(status_code=409, detail=str(e)) from e
        except Exception as e:
            logger.exception("Session %s daemon failed to start", session_id)
            await _shutdown_failed_session(session, session_id)
            if handover_store is not None and session.daemon is None:
                handover_store.remove_session(session_id)
            raise HTTPException(
                status_code=503,
                detail=f"daemon failed to start: {e}",
            ) from e

        registry[session_id] = session

        logger.info("Session %s created", session_id)
        remember_workspace(getattr(session, "workspace", None))
        return session.to_dict()
    finally:
        _release_session_id(session_id)


async def _reap_dead_sessions(handover_store: Any = None) -> None:
    """Drop sessions whose daemon is gone.

    Nothing noticed daemons exiting, so the registry kept serving rows for
    processes that had been dead for hours. A client that reused one got
    HTTP 409 "Session daemon is not running" on its first turn. A session
    whose daemon is dead is not a session — stop reporting it as one.
    """
    registry = get_session_registry()
    for session_id in [sid for sid, s in registry.items() if not s.alive]:
        session = registry.pop(session_id, None)
        if session is None:
            continue
        try:
            await session.shutdown()
            if handover_store is not None and session.webui_owned:
                handover_store.remove_session(session_id)
        except Exception as e:
            logger.warning("reaping dead session %s: %s", session_id, e)


async def _network_snapshot(registry: Dict[str, EngineSession]) -> Dict[str, Any]:
    """Every network this computer is on, asked of the agents running here.

    Folders can be on different networks, so every live agent on this computer
    is asked over its hub socket, this engine's chats too (hub_bridge.network_daemons,
    this engine's folder first): after a restart no chat runs here yet, and the
    networks must still show and open. The first agent to answer for a network
    speaks for it. A solo chat is off the mesh, and an agent opened on another
    computer answers for that computer, so neither is asked.

    ``networks`` holds one answer per network: ``network`` (its name), ``device``
    (this computer's name on it), ``trust``, ``remote`` and ``folder`` (where the
    answering agent runs, the folder whose network it is). The flat ``device``
    and ``folder`` are the first answer's, ``remote`` every answer's rows. No
    answer means no network to show: no remote rows.
    """
    fallback = socket.gethostname().split(".")[0]
    here = os.path.realpath(os.getcwd())
    skip = {
        key
        for session in registry.values()
        if getattr(session, "solo", False) or getattr(session, "device", "")
        for key in (
            session.session_id,
            getattr(getattr(session, "daemon", None), "pid", 0),
        )
        if key
    }
    answers: List[Dict[str, Any]] = []
    try:
        async with aclosing(network_daemons(skip)) as found:
            async for row, status in found:
                # Named for people (relay_commands.network_label: "home-net  via
                # kollabor.ai"); the memberships on disk know it as "home-net".
                name = str(status.get("network") or "").split("  via ", 1)[0]
                device = status.get("device") or fallback
                if any(
                    (answer["network"] or answer["device"]) == (name or device)
                    for answer in answers
                ):
                    continue  # another agent here answered for this network
                answers.append(
                    {
                        "network": name,
                        "device": device,
                        "trust": status.get("trust") or "",
                        "remote": status.get("agents") or [],
                        "folder": row.get("workspace") or here,
                    }
                )
    except Exception as exc:
        logger.debug("Local daemon network read failed: %s", exc)
    if not answers:
        return {"device": fallback, "remote": []}
    return {
        "device": answers[0]["device"],
        "remote": [row for answer in answers for row in answer["remote"]],
        "folder": answers[0]["folder"],
        "networks": answers,
    }


# Every agent the network has shown, so the sidebar keeps them while they are
# offline or no agent here is on the network (packages/kollabor-webui/README.md).
NETWORK_CACHE = "engine-network.json"
NETWORK_CACHE_DAYS = 30


def _network_cache_path() -> Path:
    from kollabor_config.config_utils import get_config_directory

    return get_config_directory() / NETWORK_CACHE


def _network_view(live: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """The live networks plus every agent seen on them lately.

    ``live`` is ``_network_snapshot``'s answer. On a network an agent here
    answered for, its agents show as they are and the ones it no longer lists
    show with ``state: "offline"``. A network seen within NETWORK_CACHE_DAYS
    that no agent here answers for now shows its last roster with ``live:
    False``: nothing here can tell who is up, and opening one first starts an
    agent in that network's ``folder`` to carry it. Each row keeps
    ``last_seen`` (epoch seconds). ``networks`` lists them, live ones first;
    the flat ``remote`` holds every row once, ``live`` is whether any network
    is live and, with none, ``folder`` is the folder of the one seen last.
    """
    now = time.time() if now is None else now
    month = NETWORK_CACHE_DAYS * 86400
    path = _network_cache_path()
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    saved = (
        cache.get("networks")
        if isinstance(cache, dict) and isinstance(cache.get("networks"), dict)
        else {}
    )

    def last_seen(row: Dict[str, Any]) -> float:
        return float(row.get("last_seen") or 0)

    def by_last_seen(rows: Any) -> List[Dict[str, Any]]:
        return sorted(rows, key=lambda row: -last_seen(row))

    networks: Dict[str, Dict[str, Any]] = {}
    for answer in live.get("networks") or []:
        key = answer["network"] or answer["device"]
        before = saved.get(key) if isinstance(saved.get(key), dict) else {}
        # This computer under another name there: what the old name saw no longer applies.
        same = before.get("device") == answer["device"] and isinstance(
            before.get("agents"), dict
        )
        seen = before["agents"] if same else {}
        current = {}
        for row in answer["remote"]:
            handle = (
                row.get("handle") or f"{row.get('name', '')}@{row.get('device', '')}"
            )
            was = last_seen(seen.get(handle) or {})
            # A minute's precision: the roster is written only when it changes.
            current[handle] = {
                **row,
                "handle": handle,
                "last_seen": was if now - was < 60 else now,
            }
        kept = {
            handle: {**row, "state": "offline"}
            for handle, row in seen.items()
            if handle not in current and now - last_seen(row) < month
        }
        networks[key] = {
            **{
                field: answer[field]
                for field in ("network", "device", "trust", "folder")
            },
            "agents": {
                **kept,
                # A running tool is live-only: the saved roster never keeps one, so
                # a tool change does not rewrite it and an offline row shows none.
                **{
                    handle: {k: v for k, v in row.items() if k != "current_tool"}
                    for handle, row in current.items()
                },
            },
            "remote": list(current.values()) + by_last_seen(kept.values()),
            "live": True,
        }
    for key, entry in saved.items():
        if (
            key in networks
            or not isinstance(entry, dict)
            or not isinstance(entry.get("agents"), dict)
        ):
            continue
        agents = {
            handle: row
            for handle, row in entry["agents"].items()
            if now - last_seen(row) < month
        }
        if agents and entry.get("folder"):
            networks[key] = {
                **entry,
                "agents": agents,
                "remote": by_last_seen(agents.values()),
                "live": False,
            }

    stored = {
        "networks": {
            key: {
                field: value
                for field, value in entry.items()
                if field not in ("remote", "live")
            }
            for key, entry in networks.items()
        }
    }
    try:
        if stored != cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(stored))
    except OSError as exc:
        logger.debug("Could not save the network roster: %s", exc)

    blocks = [
        {field: value for field, value in entry.items() if field != "agents"}
        for entry in networks.values()
    ]
    if not blocks:
        return {"device": live.get("device", ""), "remote": [], "live": False}
    remote: Dict[str, Dict[str, Any]] = {}
    for block in blocks:
        for row in block["remote"]:
            remote.setdefault(row["handle"], row)
    view = {
        "device": live.get("device") or blocks[0]["device"],
        "remote": list(remote.values()),
        "live": any(block["live"] for block in blocks),
        "networks": blocks,
    }
    if not view["live"]:
        last = max(
            blocks,
            key=lambda block: max(
                (last_seen(row) for row in block["remote"]), default=0
            ),
        )
        view["device"], view["folder"] = last["device"], last["folder"]
    return view


def _offline_agents(
    found: List[Dict[str, Any]], registry: Dict[str, EngineSession]
) -> List[Dict[str, Any]]:
    """Agents that ran in this engine's folder and are not running now.

    The sidebar lists them as offline; opening one starts it again (its vault
    brings back what it knew).
    """
    from ..folders import hub_folder
    from .agents import persona_in, ran_in

    here = os.path.realpath(os.getcwd())

    def in_here(folder: Any) -> bool:
        return bool(folder) and hub_folder(folder) == hub_folder(here)

    running = {
        str(row.get("identity") or "") for row in found if in_here(row.get("workspace"))
    }
    running.update(
        str(
            getattr(getattr(s, "daemon", None), "identity", "")
            or getattr(s, "identity", "")
            or ""
        )
        for s in registry.values()
        if in_here(getattr(s, "workspace", None) or here)
    )
    rows = []
    for name, last_ran in sorted(ran_in(here).items(), key=lambda item: -item[1]):
        if name in running:
            continue
        # A persona of any roster keeps its name, picture and color offline.
        known = persona_in(name, here)
        look = {key: known[key] for key in ("display_name", "avatar", "color", "caste") if known.get(key)}
        rows.append({"identity": name, "workspace": here, "last_ran": last_ran, **look})
    return rows


@router.get("")
async def list_sessions():
    """List this engine's sessions plus every other live agent on this computer.

    The others open on first use (server.open_named_session). This engine's own
    daemons are in presence too, so they are matched by pid and listed once.
    """
    await _reap_dead_sessions(_handover_store)
    registry = get_session_registry()
    found = HubBridge().discover_sessions(use_cache=False)
    # A terminal can switch conversations (/new, /resume): keep the name current.
    names = {row.get("session_id"): row.get("name") for row in found}
    for session in registry.values():
        if getattr(session, "external", False) and names.get(session.session_id):
            session.display_name = names[session.session_id]
    sessions = [s.to_dict() for s in registry.values()]
    local_ids = {str(item.get("session_id")) for item in sessions}
    local_pids = {item.get("daemon_pid") for item in sessions} - {0, None}
    pool = get_daemon_pool()
    discovered = [
        item
        for item in found
        if item.get("session_id") not in local_ids
        and item.get("daemon_pid") not in local_pids
        and not pool.owns(item)
    ]
    rows = sessions + discovered
    hidden_agents.stamp_instances(rows, found)
    order_state = sidebar_order.read_order_state()
    return {
        "sessions": rows,
        "discovered": discovered,
        "active_count": len(sessions) + len(discovered),
        "offline": _offline_agents(found, registry),
        # The rows the user hid, for every client of this engine (hidden_agents.py).
        "hidden": hidden_agents.visible({row["instance"] for row in rows}),
        "order": order_state.order,
        "order_revision": order_state.revision,
        "order_warning": order_state.warning,
        "network": merge_view_and_memberships(
            _network_view(await _network_snapshot(registry))
        ),
    }


@router.get("/{session_id}")
async def get_session(session_id: str):
    await _reap_dead_sessions(_handover_store)
    registry = get_session_registry()
    session = registry.get(session_id)
    if session:
        return session.to_dict()
    raise HTTPException(status_code=404, detail="Session not found")


@router.get("/{session_id}/state")
async def get_session_state(session_id: str):
    """Return live daemon state used by the web settings/inspector panels."""
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def read(method_name: str, fallback: Any = None) -> Any:
        try:
            snapshot = await getattr(session.state, method_name)()
            to_dict = getattr(snapshot, "to_dict", None)
            return to_dict() if callable(to_dict) else snapshot
        except Exception as exc:
            logger.debug("Session %s state %s failed: %s", session_id, method_name, exc)
            return fallback

    profile, agent, system, hub, processing = await asyncio.gather(
        read("get_active_profile"),
        read("get_active_agent"),
        read("get_system_info"),
        read("get_hub_state"),
        read("get_processing_state"),
    )
    return {
        "session_id": session_id,
        "profile": profile,
        "agent": agent,
        "system": system,
        "hub": hub,
        "processing": processing,
    }


@router.get("/{session_id}/commands")
async def list_session_commands(session_id: str):
    """Return the daemon-owned visible slash-command catalog."""
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        commands = await session.state.list_commands()
    except Exception as exc:
        logger.error("Session %s command catalog failed: %s", session_id, exc)
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {exc}")
    return {"session_id": session_id, "commands": commands}


def apply_profile_mirror(
    session: Any,
    name: str,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> None:
    """Keep the engine list response in sync with the daemon's active profile.

    The profile manager here is only a redacted mirror; the daemon remains the
    authority for the active provider/model. Shared by ``POST /profile`` and the
    panel routes, so a model change made in a panel never leaves the toolbar or
    the session list stale. Never raises.
    """
    try:
        from kollabor_ai import ProfileManager

        selected = ProfileManager().get_profile(name)
        if selected is not None:
            session.profile = selected
            session._profile_revision = getattr(session, "_profile_revision", 0) + 1
            if model:
                selected.model = model
            if effort is not None:
                selected.effort = effort
    except Exception as exc:
        logger.debug(
            "Session %s profile mirror update failed: %s",
            getattr(session, "session_id", "?"),
            exc,
        )


@router.post("/{session_id}/profile")
async def set_session_profile(session_id: str, body: SetProfileRequest):
    """Switch the live daemon profile/model without restarting the session."""
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.alive:
        raise HTTPException(status_code=409, detail="Session daemon is not running")

    try:
        snapshot = await session.state.set_active_profile(
            body.name,
            model=body.model,
            effort=body.effort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Session %s profile switch failed: %s", session_id, exc)
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {exc}")

    # The daemon's snapshot is the truth: an override applied earlier stays on
    # its in-memory profile, so mirror what it reports, not just this request.
    apply_profile_mirror(
        session,
        body.name,
        getattr(snapshot, "model", None) or body.model,
        getattr(snapshot, "effort", None),
    )

    result = getattr(snapshot, "to_dict", None)
    return {
        "ok": True,
        "session_id": session_id,
        "profile": result() if callable(result) else snapshot,
        **session.to_dict(),
    }


@router.patch("/{session_id}")
async def patch_session(session_id: str, request: Request):
    """Update mutable session fields: user_token, and hub (true puts the
    agent on the hub mesh, false takes it off, live; see HubPlugin.set_solo)."""
    from ..auth import validate_token as validate_engine_token

    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token or not validate_engine_token(token):
        raise HTTPException(status_code=401, detail="Invalid engine token")

    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    body = await request.json()
    if "user_token" in body:
        session.user_token = body["user_token"]
        logger.info(f"Session {session_id}: user_token updated")
    if "hub" in body:
        enabled = body["hub"]
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="hub must be true or false")
        if not session.alive:
            raise HTTPException(status_code=409, detail="Session daemon is not running")
        try:
            await session.state.set_hub_participation(enabled)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            # An agent started before the switch existed answers "method not
            # found"; say what failed rather than guess why.
            logger.error("Session %s hub switch failed: %s", session_id, exc)
            raise HTTPException(status_code=502, detail=f"hub switch failed: {exc}")
        session.solo = not enabled
        # Echoed so a client can tell this engine switched it; one that
        # predates the switch answers ok without the field.
        return {"ok": True, "session_id": session_id, "hub": enabled}
    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/token")
async def get_session_token(session_id: str, request: Request):
    """Refresh and return a current session JWT.

    Called by the MCP subprocess when its in-process token expires (401 from ops routes).
    Auth: engine bearer token (read from ~/.kollab/engine.token by subprocess).

    Flow:
      1. Validates engine bearer token
      2. Verifies stored JWT signature with BETTER_AUTH_SECRET (skips expiry check)
      3. Calls web refresh-token endpoint using INTERNAL_SERVICE_SECRET as internal auth,
         passing the original token as X-Session-Token so web verifies identity server-side
      4. Stores fresh token on session (so next subprocess spawn also gets it)
      5. Returns fresh token to subprocess for in-memory update + retry
    """
    import os

    import httpx  # type: ignore[import-not-found]

    from ..auth import validate_token as validate_engine_token

    auth_header = request.headers.get("authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token or not validate_engine_token(token):
        raise HTTPException(status_code=401, detail="Invalid engine token")

    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    web_url = os.environ.get("MENTIKO_WEB_URL", "http://127.0.0.1:3000")

    # Verify JWT signature and extract claims — do NOT trust unsigned/base64 payloads.
    # verify_exp=False because the stored token may be expired (that's exactly why we refresh).
    import jwt as _jwt

    better_auth_secret = os.environ.get("BETTER_AUTH_SECRET", "")
    if not better_auth_secret:
        raise HTTPException(status_code=503, detail="BETTER_AUTH_SECRET not set")

    sub, ns, org = "service", "default", "default"
    if session.user_token:
        try:
            claims = _jwt.decode(
                session.user_token,
                better_auth_secret.encode("utf-8"),
                algorithms=["HS256"],
                options={"verify_exp": False},
                audience="mentiko-mcp-ops",
                issuer="mentiko-web",
            )
            sub = claims.get("sub", sub)
            ns = claims.get("ns", ns)
            org = claims.get("org", org)
        except Exception as e:
            logger.error(
                f"Session {session_id}: JWT decode failed (possible forged token): {e}"
            )
            raise HTTPException(
                status_code=403, detail="Token signature verification failed"
            )

    internal_service_secret = os.environ.get("INTERNAL_SERVICE_SECRET", "")
    if not internal_service_secret:
        raise HTTPException(
            status_code=503, detail="INTERNAL_SERVICE_SECRET not set in engine env"
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{web_url}/api/kollabor/engine/sessions/{session_id}/refresh-token",
                headers={
                    "Authorization": f"Bearer {internal_service_secret}",
                    "X-Session-Token": session.user_token or "",
                },
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Web refresh-token failed: "
                    f"{resp.status_code} {resp.text[:200]}"
                ),
            )
        data = resp.json()
        fresh_token = data.get("session_token")
        if not fresh_token:
            raise HTTPException(status_code=502, detail="Web returned no session_token")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach web: {e}")

    # Store on session so next MCP subprocess spawn also gets the fresh token
    session.user_token = fresh_token
    logger.info(f"Session {session_id}: token refreshed via engine endpoint")
    return {"session_token": fresh_token}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    registry = get_session_registry()
    session = registry.pop(session_id, None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # A Delete whose owner-record removal failed already stopped the daemon:
    # retry only the durable part, never stop a stopped agent again.
    if not getattr(session, "deletion_pending", False):
        try:
            await session.shutdown()
        except Exception as exc:
            # Keep the live session and manifest discoverable when the PID could not
            # be stopped; the user can retry Delete or send /quit in the agent.
            registry[session_id] = session

            logger.exception("failed to stop deleted session %s", session_id)
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Could not confirm that agent {session_id} stopped. "
                    "Retry Delete or send /quit to the agent."
                ),
            ) from exc
    if _handover_store is not None and session.webui_owned:
        # Delete is an explicit stop: remove the durable owner record only after
        # the pool successfully stopped the daemon.
        try:
            _handover_store.remove_session(session_id)
        except Exception as exc:
            # The agent is stopped but still owns a durable record: keep the
            # row listed so a restart cannot resurrect a zombie, and let the
            # user clear it by retrying Delete.
            session.deletion_pending = True
            session.deletion_warning = (
                "Stopped, but its saved owner record could not be removed. "
                "It stays listed so it is not lost; retry Delete to clear it."
            )
            registry[session_id] = session
            logger.exception(
                "could not remove owner record for stopped session %s", session_id
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Agent {session_id} stopped, but its owner record could not "
                    "be removed. It stays in the list; retry Delete to clear it."
                ),
            ) from exc
    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/history")
async def get_history(
    session_id: str,
    limit: Optional[int] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Never waits on the daemon: the warm mirror, or a disk seed, answers at
    # once; the reconciling refresh runs in the background and its result
    # arrives as a history_updated event. source says where the history came
    # from and stale whether it is still the disk seed (every answer before
    # the daemon's replaces it, not only the first).
    snapshot = await session.get_history_nonblocking()
    history = snapshot.get("history", [])
    base_payload = {key: value for key, value in snapshot.items() if key != "history"}
    try:
        page = build_history_page(
            session_id,
            history,
            limit=limit,
            before=before,
            after=after,
            base_payload=base_payload,
        )
    except StaleHistoryCursor as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidHistoryCursor as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except HistoryPageTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error

    # Preserve the existing turn-error offset semantics, now relative to the
    # bounded page. An error outside an older page is not duplicated there.
    turn_error = page.get("last_turn_error")
    if isinstance(turn_error, dict):
        error_position = turn_error.get("history_length")
        if isinstance(error_position, int) and not isinstance(error_position, bool):
            if not page["start_index"] <= error_position <= page["end_index"]:
                page["last_turn_error"] = None
            else:
                page["last_turn_error"] = {
                    **turn_error,
                    "history_length": error_position - page["start_index"],
                }
    return page


@router.get("/{session_id}/history/output")
async def get_history_output(session_id: str, ref: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = await session.get_history_nonblocking()
    try:
        content = resolve_history_output(session_id, snapshot.get("history", []), ref)
    except StaleHistoryCursor as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidHistoryCursor as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"session_id": session_id, "content": content}


@router.delete("/{session_id}/history")
async def clear_history(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # state.restart_session clears the conversation and rebuilds the system
    # prompt, which is what "clear history but keep the prompt" means here.
    await session.state.restart_session()
    await session.refresh_history()
    session.last_turn_error = None
    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/system-prompt")
async def get_system_prompt(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # The daemon's rendered prompt is the live one; fall back to what the
    # session was created with if the daemon can't answer.
    try:
        snapshot = await session.state.get_system_prompt()
        prompt = snapshot.content
    except Exception as e:
        logger.debug(f"Session {session_id}: system prompt read failed: {e}")
        prompt = session.system_prompt

    return {"session_id": session_id, "system_prompt": prompt or ""}


@router.post("/{session_id}/system-prompt/rebuild")
async def rebuild_system_prompt(session_id: str):
    """Rebuild system prompt by rendering <trender> tags.

    Security note: Only renders prompts that were set at session creation time
    (which have already been sanitized). User-provided trender tags are blocked.
    """
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get raw prompt - what the session was created with, falling back to
    # whatever the daemon currently has installed.
    raw_prompt = session.system_prompt or ""
    if not raw_prompt:
        try:
            raw_prompt = (await session.state.get_system_prompt()).content
        except Exception as e:
            logger.debug(f"Session {session_id}: system prompt read failed: {e}")

    if not raw_prompt:
        return {"session_id": session_id, "system_prompt": ""}

    # Render using prompt_renderer if available with comprehensive exception handling
    rendered = raw_prompt  # Fallback to original if rendering fails
    try:
        from kollabor_ai.prompt_renderer import render_system_prompt

        # Wrap render in try/except for all possible exceptions
        rendered = render_system_prompt(raw_prompt, timeout=5)

    except ImportError:
        # Fallback: simple regex replacement for <trender> tags
        import re

        rendered = re.sub(
            r"<trender>.*?</trender>", "[command output]", raw_prompt, flags=re.DOTALL
        )
        rendered = re.sub(
            r'<trender\s+type="[^"]*"[^>]*/>', "[included content]", rendered
        )
        logger.info(
            f"Session {session_id}: prompt_renderer not available, used regex fallback"
        )

    except Exception as e:
        # Log error but don't crash - return original prompt
        logger.error(
            f"Session {session_id}: failed to render system prompt: {type(e).__name__}: {e}"
        )
        # Keep original prompt, don't update storage/history
        return {
            "session_id": session_id,
            "system_prompt": raw_prompt,
            "warning": f"Rendering failed: {type(e).__name__}",
        }

    # Install the rendered prompt on the daemon, which owns the conversation
    session.system_prompt = rendered
    try:
        await session.state.set_system_prompt(rendered, source="engine")
    except Exception as e:
        logger.error(f"Session {session_id}: failed to install system prompt: {e}")
        return {
            "session_id": session_id,
            "system_prompt": rendered,
            "warning": f"Rendered but not installed: {type(e).__name__}",
        }

    return {"session_id": session_id, "system_prompt": rendered}


# Session-specific MCP endpoints
# These are in the sessions router (not mcp router) to avoid double /mcp prefix.
# MCP lives in the session's daemon, so these read and write through state.*
# rather than owning an MCPIntegration of their own.


async def _mcp_snapshot(session):
    try:
        return await session.state.get_mcp_state()
    except Exception as e:
        logger.error(f"Session {session.session_id}: MCP state read failed: {e}")
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {e}")


def _find_server(snapshot, server_name: str):
    for server in snapshot.servers:
        if server.name == server_name:
            return server
    return None


@router.get("/{session_id}/mcp")
async def get_session_mcp(session_id: str):
    """Get MCP connection status for a specific session."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = await _mcp_snapshot(session)

    result: Dict[str, Any] = {"session_id": session_id, "servers": {}}
    for server in snapshot.servers:
        if server.connected:
            result["servers"][server.name] = {
                "status": "connected",
                "tool_count": server.tool_count,
                "tools": server.tools,
            }
        elif server.enabled:
            result["servers"][server.name] = {
                "status": "disconnected",
                "error": "Connection not attempted or failed",
            }
        else:
            result["servers"][server.name] = {
                "status": "disconnected",
                "error": "Server disabled in configuration",
            }

    result["total_tools"] = snapshot.total_tools
    return result


@router.post("/{session_id}/mcp/{server_name}/connect")
async def connect_server(session_id: str, server_name: str):
    """Enable an MCP server for a session's daemon."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = await _mcp_snapshot(session)
    server = _find_server(snapshot, server_name)
    if server is None:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' not found in configuration",
        )
    if server.connected:
        raise HTTPException(
            status_code=409, detail=f"Server '{server_name}' is already connected"
        )

    await session.state.enable_mcp_server(server_name)
    await session.state.reload_mcp_servers()
    updated = await _mcp_snapshot(session)
    server = _find_server(updated, server_name)

    return {
        "ok": True,
        "server_name": server_name,
        "status": "connected" if server and server.connected else "enabled",
        "tool_count": server.tool_count if server else 0,
        "tools": server.tools if server else [],
        "session_id": session_id,
    }


@router.post("/{session_id}/mcp/{server_name}/disconnect")
async def disconnect_server(session_id: str, server_name: str):
    """Disable an MCP server for a session's daemon."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = await _mcp_snapshot(session)
    server = _find_server(snapshot, server_name)
    if server is None:
        raise HTTPException(
            status_code=404, detail=f"No such MCP server '{server_name}'"
        )

    tools_removed = server.tool_count
    await session.state.disable_mcp_server(server_name)

    return {
        "ok": True,
        "server_name": server_name,
        "status": "disconnected",
        "tools_removed": tools_removed,
        "session_id": session_id,
    }


@router.get("/{session_id}/mcp/{server_name}/tools")
async def list_server_tools(session_id: str, server_name: str):
    """List tools provided by a connected MCP server."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        grouped = await session.state.get_mcp_tools(server_filter=server_name)
    except Exception as e:
        logger.error(f"Session {session_id}: MCP tool list failed: {e}")
        raise HTTPException(status_code=502, detail=f"daemon unreachable: {e}")

    tools = [
        {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", tool.get("inputSchema", {})),
        }
        for tool in grouped.get(server_name, [])
    ]

    return {
        "server_name": server_name,
        "status": "connected" if tools else "disconnected",
        "tools": tools,
        "total": len(tools),
    }


@router.get("/{session_id}/mcp/{server_name}/status")
async def get_server_status(session_id: str, server_name: str):
    """Get detailed status of a single MCP server connection."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    snapshot = await _mcp_snapshot(session)
    server = _find_server(snapshot, server_name)

    if server is not None and server.connected:
        return {
            "server_name": server_name,
            "status": "connected",
            "connected_at": None,
            "uptime_seconds": 0,
            "tool_count": server.tool_count,
            "tools": server.tools,
            "process": {"pid": None, "command": ""},
            "error": None,
        }

    return {
        "server_name": server_name,
        "status": "disconnected",
        "error": "Not connected",
        "connected_at": None,
        "uptime_seconds": 0,
        "tool_count": 0,
        "tools": [],
    }
