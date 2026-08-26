"""Session lifecycle routes."""

import asyncio
import logging
import re
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request  # type: ignore[import-not-found]
from pydantic import BaseModel

from kollabor_ai import LLMProfile
from kollabor_ai.session_naming import generate_session_name

from ..server import get_session_registry
from ..session import EngineSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])

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
    """Reserve an ID before any daemon work can begin."""
    session_id = requested_id or generate_session_name()
    with _session_reservation_lock:
        if session_id in registry or session_id in _pending_session_ids:
            raise HTTPException(
                status_code=409,
                detail=f"Session '{session_id}' is already in use",
            )
        _pending_session_ids.add(session_id)
    return session_id


def _release_session_id(session_id: str) -> None:
    with _session_reservation_lock:
        _pending_session_ids.discard(session_id)


async def _shutdown_failed_session(
    session: EngineSession, session_id: str
) -> None:
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
    profile: str = "default"
    agent: Optional[str] = None
    identity: Optional[str] = None
    system_prompt: Optional[str] = None
    workspace: Optional[str] = None
    approval_mode: str = "confirm_all"
    mcp_servers: List[str] = []
    metadata: dict = {}
    credentials: Optional[Credentials] = None
    user_token: Optional[str] = None
    session_id: Optional[str] = None  # caller-supplied ID (proxy pre-generates to enable token injection)


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


def _resolve_agent(name: str) -> Optional[dict]:
    """Load agent definition by name from the agents directory.

    Returns a dict with resolved keys:
      system_prompt: str - fully rendered (trender includes resolved)
      profile: str|None  - agent's preferred profile (may be None)
      mcp_servers: list  - MCP servers declared in agent.json
      tools: list        - native tools declared in agent.json

    Returns None if the agent is not found.
    """
    try:
        from kollabor_agent.agent_manager import (
            Agent,
            AgentManager,
            get_global_agents_dir,
            get_local_agents_dir,
        )
    except ImportError:
        logger.warning("kollabor_agent not available — cannot resolve agent")
        return None

    try:
        # Search global then local agents dirs (local overrides global)
        agent_obj = None
        for agents_dir_fn in [get_global_agents_dir, get_local_agents_dir]:
            try:
                agents_dir = agents_dir_fn()
            except Exception:
                continue
            if not agents_dir or not agents_dir.exists():
                continue
            agent_dir = agents_dir / name
            if agent_dir.is_dir():
                candidate = Agent.from_directory(agent_dir)
                if candidate:
                    agent_obj = candidate

        if not agent_obj:
            return None

        am = AgentManager()
        system_prompt = agent_obj.get_full_system_prompt(agent_manager=am)

        # Read mcp_servers from agent.json directly (not parsed by Agent.from_directory)
        mcp_servers: list = []
        config_file = agent_obj.directory / "agent.json" if agent_obj.directory else None
        if config_file and config_file.exists():
            try:
                import json as _json
                cfg = _json.loads(config_file.read_text())
                mcp_servers = list(cfg.get("mcp_servers", []) or [])
            except Exception:
                pass

        return {
            "system_prompt": system_prompt,
            "profile": getattr(agent_obj, "profile", None),
            "mcp_servers": mcp_servers,
            "tools": list(getattr(agent_obj, "tools", []) or []),
        }
    except Exception as e:
        logger.warning(f"Failed to resolve agent '{name}': {e}")
        return None


@router.post("")
async def create_session(body: CreateSessionRequest, request: Request):
    registry = get_session_registry()

    # Resolve user_token: body takes priority, header is fallback (proxy injects it)
    user_token = body.user_token or request.headers.get("x-mentiko-session-token")

    # Resolve agent definition if requested
    agent_data = None
    if body.agent:
        agent_data = _resolve_agent(body.agent)
        if agent_data is None:
            raise HTTPException(status_code=404, detail=f"Agent '{body.agent}' not found")

    # Determine effective system prompt:
    # agent system_prompt wins over caller-supplied (already rendered, safe to use as-is)
    if agent_data:
        effective_system_prompt = agent_data["system_prompt"] or body.system_prompt
    else:
        # Sanitize user-provided system prompt to block command injection
        effective_system_prompt = _sanitize_system_prompt(body.system_prompt)

    safe_system_prompt = effective_system_prompt

    # Determine effective profile:
    # caller's profile wins; agent profile used only when caller sends "default"
    effective_profile = body.profile
    if agent_data and agent_data.get("profile") and body.profile == "default":
        effective_profile = agent_data["profile"]

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
            name="app-inline",
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
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Spawning the daemon is the slow part of session creation (plugin
        # discovery plus the hub join). Every failure after construction must
        # stop the candidate daemon before the error leaves this route.
        try:
            await session.initialize()
        except asyncio.CancelledError:
            await _shutdown_failed_session(session, session_id)
            raise
        except ValueError as e:
            await _shutdown_failed_session(session, session_id)
            raise HTTPException(status_code=409, detail=str(e)) from e
        except Exception as e:
            logger.exception("Session %s daemon failed to start", session_id)
            await _shutdown_failed_session(session, session_id)
            raise HTTPException(
                status_code=503,
                detail=f"daemon failed to start: {e}",
            ) from e

        registry[session_id] = session
        logger.info("Session %s created", session_id)
        return session.to_dict()
    finally:
        _release_session_id(session_id)


async def _reap_dead_sessions() -> None:
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
        except Exception as e:
            logger.warning("reaping dead session %s: %s", session_id, e)


@router.get("")
async def list_sessions():
    await _reap_dead_sessions()
    registry = get_session_registry()
    return {"sessions": [s.to_dict() for s in registry.values()]}


@router.get("/{session_id}")
async def get_session(session_id: str):
    await _reap_dead_sessions()
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


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

    # Keep the engine list response in sync with the daemon snapshot. The
    # profile manager here is only a redacted mirror; the daemon remains the
    # authority for the active provider/model.
    try:
        from kollabor_ai import ProfileManager

        selected = ProfileManager().get_profile(body.name)
        if selected is not None:
            session.profile = selected
            if body.model:
                selected.model = body.model
    except Exception as exc:
        logger.debug("Session %s profile mirror update failed: %s", session_id, exc)

    result = getattr(snapshot, "to_dict", None)
    return {
        "ok": True,
        "session_id": session_id,
        "profile": result() if callable(result) else snapshot,
        **session.to_dict(),
    }


@router.patch("/{session_id}")
async def patch_session(session_id: str, request: Request):
    """Update mutable session fields. Currently supports: user_token."""
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
            ns  = claims.get("ns", ns)
            org = claims.get("org", org)
        except Exception as e:
            logger.error(f"Session {session_id}: JWT decode failed (possible forged token): {e}")
            raise HTTPException(status_code=403, detail="Token signature verification failed")

    internal_service_secret = os.environ.get("INTERNAL_SERVICE_SECRET", "")
    if not internal_service_secret:
        raise HTTPException(status_code=503, detail="INTERNAL_SERVICE_SECRET not set in engine env")

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
    await session.shutdown()
    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/history")
async def get_history(session_id: str, limit: Optional[int] = None):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # The daemon owns the conversation; pull a fresh copy rather than trusting
    # the local mirror, which only refreshes on turn_complete.
    history = await session.refresh_history()
    if limit:
        history = history[-limit:]
    return {"session_id": session_id, "history": history}


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
