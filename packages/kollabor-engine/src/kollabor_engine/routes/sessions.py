"""Session lifecycle routes."""

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request  # type: ignore[import-not-found]
from pydantic import BaseModel

from kollabor_ai import LLMProfile

from ..server import get_session_registry
from ..session import EngineSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])

# Pattern to detect <trender> tags (security: block user-controlled dynamic content)
# Matches: <trender>...</trender> and <trender type="..." ... />
TRENDER_PATTERN = re.compile(
    r"<trender\b[^>]*>.*?</trender>|<trender\b[^>]*/?>", re.DOTALL | re.IGNORECASE
)


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
    system_prompt: Optional[str] = None
    workspace: Optional[str] = None
    approval_mode: str = "confirm_all"
    mcp_servers: List[str] = []
    metadata: dict = {}
    credentials: Optional[Credentials] = None
    user_token: Optional[str] = None
    session_id: Optional[str] = None  # caller-supplied ID (proxy pre-generates to enable token injection)


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

        # Debug: log received credentials
        import sys

        print("[ENGINE-DEBUG] Creating session with credentials:", file=sys.stderr)
        print(f"[ENGINE-DEBUG]   provider={creds.provider}", file=sys.stderr)
        print(f"[ENGINE-DEBUG]   model={creds.model}", file=sys.stderr)
        print(f"[ENGINE-DEBUG]   base_url={base_url}", file=sys.stderr)
        print(f"[ENGINE-DEBUG]   max_tokens={creds.max_tokens}", file=sys.stderr)
        print(
            f"[ENGINE-DEBUG]   api_key=sk-*** (length={len(creds.api_key)})",
            file=sys.stderr,
        )

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

    session_id = body.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    try:
        session = EngineSession(
            session_id=session_id,
            profile=profile,
            approval_mode=body.approval_mode,
            workspace=body.workspace,
            system_prompt=safe_system_prompt,
            mcp_server_names=effective_mcp_servers,
            user_token=user_token,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ok = await session.initialize()
    if not ok:
        logger.warning(f"Session {session_id} initialized with provider errors")

    registry[session_id] = session
    logger.info(f"Session {session_id} created")
    return session.to_dict()


@router.get("")
async def list_sessions():
    registry = get_session_registry()
    return {"sessions": [s.to_dict() for s in registry.values()]}


@router.get("/{session_id}")
async def get_session(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


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

    history = session.history
    if limit:
        history = history[-limit:]
    return {"session_id": session_id, "history": history}


@router.delete("/{session_id}/history")
async def clear_history(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Keep system prompt if present
    if session.history and session.history[0].get("role") == "system":
        session.history = session.history[:1]
    else:
        session.history = []

    return {"ok": True, "session_id": session_id}


@router.get("/{session_id}/system-prompt")
async def get_system_prompt(session_id: str):
    registry = get_session_registry()
    session = registry.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Return session.system_prompt if set, otherwise extract from history
    prompt = session.system_prompt
    if not prompt and session.history and session.history[0].get("role") == "system":
        prompt = session.history[0].get("content", "")

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

    # Check if a turn is in progress - warn about potential race condition
    if session._active_turn_task and not session._active_turn_task.done():
        logger.warning(
            f"Session {session_id}: rebuild_system_prompt called during active turn. "
            "History modification may cause inconsistent state."
        )

    # Get raw prompt
    raw_prompt = session.system_prompt or ""
    if (
        not raw_prompt
        and session.history
        and session.history[0].get("role") == "system"
    ):
        raw_prompt = session.history[0].get("content", "")

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

    # Update storage and history
    session.system_prompt = rendered
    if session.history and session.history[0].get("role") == "system":
        session.history[0]["content"] = rendered

    return {"session_id": session_id, "system_prompt": rendered}


# Session-specific MCP endpoints
# These are in the sessions router (not mcp router) to avoid double /mcp prefix


@router.get("/{session_id}/mcp")
async def get_session_mcp(session_id: str):
    """Get MCP connection status for a specific session."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    mcp = session.mcp_integration
    connections = mcp.server_connections
    tools = mcp.tool_registry

    result: Dict[str, Any] = {"session_id": session_id, "servers": {}}

    for server_name, server_config in mcp.mcp_servers.items():
        conn = connections.get(server_name)
        server_tools = [
            name for name, info in tools.items() if info.get("server") == server_name
        ]

        if conn and conn.initialized:
            result["servers"][server_name] = {
                "status": "connected",
                "tool_count": len(server_tools),
                "tools": server_tools,
            }
        elif server_config.get("enabled", True):
            result["servers"][server_name] = {
                "status": "disconnected",
                "error": "Connection not attempted or failed",
            }
        else:
            result["servers"][server_name] = {
                "status": "disconnected",
                "error": "Server disabled in configuration",
            }

    result["total_tools"] = len(tools)
    return result


@router.post("/{session_id}/mcp/{server_name}/connect")
async def connect_server(session_id: str, server_name: str):
    """Connect to an MCP server for a specific session."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    mcp = session.mcp_integration

    if server_name not in mcp.mcp_servers:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' not found in configuration",
        )

    if server_name in mcp.server_connections:
        conn = mcp.server_connections[server_name]
        if conn.initialized:
            raise HTTPException(
                status_code=409, detail=f"Server '{server_name}' is already connected"
            )

    server_config = mcp.mcp_servers[server_name]
    if not server_config.get("enabled", True):
        raise HTTPException(
            status_code=400, detail=f"Server '{server_name}' is disabled"
        )

    command = server_config.get("command")
    if not command:
        raise HTTPException(
            status_code=400, detail=f"Server '{server_name}' has no command configured"
        )

    tools = await mcp._connect_and_list_tools(server_name, command)
    conn = mcp.server_connections.get(server_name)
    if not conn or not conn.initialized:
        raise HTTPException(
            status_code=502, detail=f"Failed to connect MCP server '{server_name}'"
        )

    return {
        "ok": True,
        "server_name": server_name,
        "status": "connected",
        "tool_count": len(tools),
        "tools": [t.get("name") for t in tools if t.get("name")],
        "session_id": session_id,
    }


@router.post("/{session_id}/mcp/{server_name}/disconnect")
async def disconnect_server(session_id: str, server_name: str):
    """Disconnect from an MCP server for a session."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    mcp = session.mcp_integration

    if server_name not in mcp.server_connections:
        raise HTTPException(
            status_code=404, detail=f"No active connection to '{server_name}'"
        )

    # Count tools to be removed
    tools_to_remove = [
        name
        for name, info in mcp.tool_registry.items()
        if info.get("server") == server_name
    ]

    # Close connection
    await mcp.server_connections[server_name].close()
    del mcp.server_connections[server_name]

    # Remove tools
    for tool_name in tools_to_remove:
        del mcp.tool_registry[tool_name]

    return {
        "ok": True,
        "server_name": server_name,
        "status": "disconnected",
        "tools_removed": len(tools_to_remove),
        "session_id": session_id,
    }


@router.get("/{session_id}/mcp/{server_name}/tools")
async def list_server_tools(session_id: str, server_name: str):
    """List tools provided by a connected MCP server."""
    registry = get_session_registry()
    session = registry.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    mcp = session.mcp_integration

    tools = []
    for tool_name, tool_info in mcp.tool_registry.items():
        if tool_info.get("server") == server_name:
            definition = tool_info.get("definition", {})
            tools.append(
                {
                    "name": tool_name,
                    "description": definition.get("description", ""),
                    "parameters": definition.get(
                        "parameters", definition.get("inputSchema", {})
                    ),
                }
            )

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

    mcp = session.mcp_integration
    conn = mcp.server_connections.get(server_name)

    server_tools = [
        name
        for name, info in mcp.tool_registry.items()
        if info.get("server") == server_name
    ]

    if conn and conn.initialized:
        return {
            "server_name": server_name,
            "status": "connected",
            "connected_at": None,
            "uptime_seconds": 0,
            "tool_count": len(server_tools),
            "tools": server_tools,
            "process": {
                "pid": conn.process.pid if conn.process else None,
                "command": conn.command,
            },
            "error": None,
        }
    else:
        mcp.mcp_servers.get(server_name, {})
        return {
            "server_name": server_name,
            "status": "disconnected",
            "error": "Not connected",
            "connected_at": None,
            "uptime_seconds": 0,
            "tool_count": 0,
            "tools": [],
        }
