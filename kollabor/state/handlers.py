"""Daemon-side RPC handlers for StateService.

Every StateService method gets a matching handler registered on the
daemon's RpcServer under the "state.<method>" namespace. Each handler
is a thin wrapper around a LocalStateService method - the daemon's
LocalStateService is the single source of truth for state, and the
RPC handlers just serialize its outputs to dicts.

The attach client's RemoteStateService calls these same method names,
so the whole thing forms a mirror: local and remote see the same
interface, only the transport differs.
"""

from __future__ import annotations

import logging
from typing import Any

from .local import LocalStateService

logger = logging.getLogger(__name__)


def register_state_handlers(rpc_server: Any, state_service: LocalStateService) -> None:
    """Register all StateService RPC handlers on an RpcServer instance.

    Handlers are idempotent with respect to double-registration: if a
    handler is already registered on the server under the same name,
    the ValueError from RpcServer.register is caught and logged as debug.
    This lets the same wiring code run from multiple call sites
    (application.py init path and hub plugin init path) without risk of
    crashing whichever runs second.

    Args:
        rpc_server: kollabor_rpc.RpcServer instance.
        state_service: LocalStateService instance (the daemon's single
            source of truth).

    Registered methods:
        state.get_conversation
        state.save_conversation
        state.get_session_stats
        state.get_active_profile
        state.list_profiles
        state.get_permission_state
        state.get_mcp_state
        state.get_hub_state
        state.get_processing_state
        state.get_system_info
        state.set_active_profile      (phase 4 write)
        state.set_approval_mode       (phase 4 write)
        state.get_active_agent        (phase 4.5 read)
        state.list_agents             (phase 4.5 read)
        state.set_agent               (phase 4.5 write)
        state.clear_agent             (phase 4.5 write)
        state.list_skills             (phase 4.5 read)
        state.activate_skill          (phase 4.5 write)
        state.deactivate_skill        (phase 4.5 write)
        state.get_system_prompt       (phase 4.5 read)
        state.set_system_prompt       (phase 4.5 write)
        state.list_contexts           (phase 4.5 step 6 read)
        state.get_active_context      (phase 4.5 step 6 read)
        state.create_context          (phase 4.5 step 6 write)
        state.attach_to_context       (phase 4.5 step 6 write)
        state.archive_context         (phase 4.5 step 6 write)
        state.restart_session         (phase 4.5 step 7 write)
        state.enable_mcp_server       (phase 4.5 step 7 write)
        state.disable_mcp_server      (phase 4.5 step 7 write)
        state.test_mcp_server         (phase 4.5 step 7 read)
        state.get_mcp_tools           (phase 4.5 step 7 read)
        state.reload_mcp_servers      (explicit MCP hot-reload)
        state.clear_session_approvals (phase 4.5 step 7 write)
        state.clear_project_approvals (phase 4.5 step 7 write)
        state.list_project_approvals  (phase 4.5 step 7 read)
        state.resume_conversation     (phase 4.5 step 7 write)
    """

    async def _get_conversation(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_conversation()
        return snapshot.to_dict()

    async def _save_conversation(params: dict[str, Any]) -> dict[str, Any]:
        format_name = params.get("format", "transcript")
        try:
            content = await state_service.save_conversation(format_name)
        except ValueError as e:
            # Surface as a dict with an explicit error so the remote side
            # can distinguish "bad format" (client error) from "handler
            # crashed" (which the RpcServer would turn into error_kind=
            # "handler"). Phase 3+ may refine this with a proper error
            # taxonomy; for phase 2 a dict with an "error" key is enough.
            return {"error": str(e)}
        return {"content": content}

    async def _get_session_stats(params: dict[str, Any]) -> dict[str, Any]:
        stats = await state_service.get_session_stats()
        return stats.to_dict()

    async def _get_active_profile(params: dict[str, Any]) -> dict[str, Any]:
        profile = await state_service.get_active_profile()
        return profile.to_dict()

    async def _list_profiles(params: dict[str, Any]) -> dict[str, Any]:
        listing = await state_service.list_profiles()
        return listing.to_dict()

    async def _get_permission_state(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_permission_state()
        return snapshot.to_dict()

    async def _get_mcp_state(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_mcp_state()
        return snapshot.to_dict()

    async def _get_hub_state(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_hub_state()
        return snapshot.to_dict()

    async def _get_processing_state(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_processing_state()
        return snapshot.to_dict()

    async def _get_system_info(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_system_info()
        return snapshot.to_dict()

    # === Writes (phase 4) ===
    # Both write handlers catch ValueError and return an {"error": ...}
    # envelope instead of letting the exception bubble up to the
    # RpcServer's generic handler. This keeps "bad input" (unknown
    # profile name, unknown approval mode) on the client-error track
    # so the client can surface a friendly message instead of
    # error_kind="handler".

    async def _set_active_profile(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "profile name is required"}
        persist = bool(params.get("persist", False))
        persist_local = bool(params.get("persist_local", False))
        try:
            snapshot = await state_service.set_active_profile(
                name.strip(), persist=persist, persist_local=persist_local
            )
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _set_approval_mode(params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode", "")
        if not isinstance(mode, str) or not mode.strip():
            return {"error": "approval mode is required"}
        try:
            snapshot = await state_service.set_approval_mode(mode.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    # === Phase 4.5: agents / skills / system prompt ===

    async def _get_active_agent(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_active_agent()
        return snapshot.to_dict()

    async def _list_agents(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.list_agents()
        return snapshot.to_dict()

    async def _set_agent(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "agent name is required"}
        try:
            snapshot = await state_service.set_agent(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _clear_agent(params: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = await state_service.clear_agent()
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _list_skills(params: dict[str, Any]) -> dict[str, Any]:
        agent_name = params.get("agent_name", "")
        if not isinstance(agent_name, str):
            agent_name = ""
        snapshot = await state_service.list_skills(agent_name)
        return snapshot.to_dict()

    async def _activate_skill(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "skill name is required"}
        try:
            snapshot = await state_service.activate_skill(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _deactivate_skill(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "skill name is required"}
        try:
            snapshot = await state_service.deactivate_skill(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _get_system_prompt(params: dict[str, Any]) -> dict[str, Any]:
        snapshot = await state_service.get_system_prompt()
        return snapshot.to_dict()

    async def _set_system_prompt(params: dict[str, Any]) -> dict[str, Any]:
        content = params.get("content", "")
        source = params.get("source", "file")
        path = params.get("path", "")
        if not isinstance(content, str):
            return {"error": "system prompt content must be a string"}
        if not isinstance(source, str):
            source = "file"
        if not isinstance(path, str):
            path = ""
        try:
            snapshot = await state_service.set_system_prompt(
                content, source=source, path=path
            )
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    # === Phase 4.5 step 6: contexts ===

    async def _list_contexts(params: dict[str, Any]) -> dict[str, Any]:
        include_archived = bool(params.get("include_archived", False))
        snapshot = await state_service.list_contexts(include_archived=include_archived)
        return snapshot.to_dict()

    async def _get_active_context(params: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = await state_service.get_active_context()
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _create_context(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str):
            return {"error": "context name must be a string"}
        profile_name = params.get("profile_name", "") or ""
        agent_name = params.get("agent_name", "") or ""
        system_prompt = params.get("system_prompt", "") or ""
        for fname, val in (
            ("profile_name", profile_name),
            ("agent_name", agent_name),
            ("system_prompt", system_prompt),
        ):
            if not isinstance(val, str):
                return {"error": f"{fname} must be a string"}
        try:
            snapshot = await state_service.create_context(
                name,
                profile_name=profile_name,
                agent_name=agent_name,
                system_prompt=system_prompt,
            )
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _attach_to_context(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "context name is required"}
        try:
            snapshot = await state_service.attach_to_context(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        except RuntimeError as e:
            # Turn-in-progress is a transient "try again in a moment" error.
            # Wrap it in the same envelope so the client can surface a
            # friendly message without crashing.
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _archive_context(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "context name is required"}
        try:
            snapshot = await state_service.archive_context(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _restart_session(params: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = await state_service.restart_session()
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _enable_mcp_server(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "server name is required"}
        try:
            snapshot = await state_service.enable_mcp_server(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _disable_mcp_server(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "server name is required"}
        try:
            snapshot = await state_service.disable_mcp_server(name.strip())
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _test_mcp_server(params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if not isinstance(name, str) or not name.strip():
            return {"error": "server name is required"}
        try:
            status = await state_service.test_mcp_server(name.strip())
        except ValueError as e:
            return {"error": str(e), "found": False}
        # status is a plain dict already (mirrors MCPManager.get_server_status);
        # return it verbatim.
        return status

    async def _get_mcp_tools(params: dict[str, Any]) -> dict[str, Any]:
        server_filter = params.get("server_filter")
        if server_filter is not None and not isinstance(server_filter, str):
            return {"error": "server_filter must be a string"}
        try:
            tools = await state_service.get_mcp_tools(server_filter)
        except ValueError as e:
            return {"error": str(e)}
        # Wrap under "tools" so the envelope can carry metadata later.
        return {"tools": tools}

    async def _reload_mcp_servers(params: dict[str, Any]) -> dict[str, Any]:
        try:
            return await state_service.reload_mcp_servers()
        except ValueError as e:
            return {"error": str(e)}

    async def _clear_session_approvals(params: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = await state_service.clear_session_approvals()
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _clear_project_approvals(params: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = await state_service.clear_project_approvals()
        except ValueError as e:
            return {"error": str(e)}
        return snapshot.to_dict()

    async def _list_project_approvals(params: dict[str, Any]) -> dict[str, Any]:
        try:
            approvals = await state_service.list_project_approvals()
        except ValueError as e:
            return {"error": str(e)}
        return {"approvals": approvals}

    async def _resume_conversation(params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("session_id", "")
        if not isinstance(session_id, str) or not session_id.strip():
            return {"error": "session_id is required"}
        try:
            return await state_service.resume_conversation(session_id.strip())
        except ValueError as e:
            return {"error": str(e)}

    async def _get_hub_status_text(params: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await state_service.get_hub_status_text()
        except ValueError as e:
            return {"error": str(e)}
        return {"text": text}

    async def _get_hub_whoami_text(params: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await state_service.get_hub_whoami_text()
        except ValueError as e:
            return {"error": str(e)}
        return {"text": text}

    async def _get_hub_work_text(params: dict[str, Any]) -> dict[str, Any]:
        try:
            text = await state_service.get_hub_work_text()
        except ValueError as e:
            return {"error": str(e)}
        return {"text": text}

    async def _hub_send_msg(params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("target", "")
        content = params.get("content", "")
        if not target or not content:
            return {"error": "target and content are required"}
        try:
            text = await state_service.hub_send_msg(target, content)
        except (ValueError, Exception) as e:
            return {"error": str(e)}
        return {"text": text}

    async def _hub_broadcast(params: dict[str, Any]) -> dict[str, Any]:
        content = params.get("content", "")
        force = params.get("force", False)
        if not content:
            return {"error": "content is required"}
        try:
            text = await state_service.hub_broadcast(content, force=bool(force))
        except (ValueError, Exception) as e:
            return {"error": str(e)}
        return {"text": text}

    async def _cancel_current_request(params: dict[str, Any]) -> dict[str, Any]:
        try:
            return await state_service.cancel_current_request()
        except Exception as e:
            return {"error": str(e)}

    handlers: dict[str, Any] = {
        "state.get_conversation": _get_conversation,
        "state.save_conversation": _save_conversation,
        "state.get_session_stats": _get_session_stats,
        "state.get_active_profile": _get_active_profile,
        "state.list_profiles": _list_profiles,
        "state.get_permission_state": _get_permission_state,
        "state.get_mcp_state": _get_mcp_state,
        "state.get_hub_state": _get_hub_state,
        "state.get_processing_state": _get_processing_state,
        "state.get_system_info": _get_system_info,
        "state.set_active_profile": _set_active_profile,
        "state.set_approval_mode": _set_approval_mode,
        # Phase 4.5: agents / skills / system prompt
        "state.get_active_agent": _get_active_agent,
        "state.list_agents": _list_agents,
        "state.set_agent": _set_agent,
        "state.clear_agent": _clear_agent,
        "state.list_skills": _list_skills,
        "state.activate_skill": _activate_skill,
        "state.deactivate_skill": _deactivate_skill,
        "state.get_system_prompt": _get_system_prompt,
        "state.set_system_prompt": _set_system_prompt,
        # Phase 4.5 step 6: conversation contexts
        "state.list_contexts": _list_contexts,
        "state.get_active_context": _get_active_context,
        "state.create_context": _create_context,
        "state.attach_to_context": _attach_to_context,
        "state.archive_context": _archive_context,
        # Phase 4.5 step 7: session management
        "state.restart_session": _restart_session,
        # Phase 4.5 step 7: MCP writes (file-only, no hot-reload)
        "state.enable_mcp_server": _enable_mcp_server,
        "state.disable_mcp_server": _disable_mcp_server,
        "state.test_mcp_server": _test_mcp_server,
        "state.get_mcp_tools": _get_mcp_tools,
        "state.reload_mcp_servers": _reload_mcp_servers,
        # Phase 4.5 step 7: permission writes + project reads
        "state.clear_session_approvals": _clear_session_approvals,
        "state.clear_project_approvals": _clear_project_approvals,
        "state.list_project_approvals": _list_project_approvals,
        # Phase 4.5 step 7: resume
        "state.resume_conversation": _resume_conversation,
        # Phase 4.5 step 8: hub reads (cheap wins)
        "state.get_hub_status_text": _get_hub_status_text,
        "state.get_hub_whoami_text": _get_hub_whoami_text,
        "state.get_hub_work_text": _get_hub_work_text,
        # Phase 4.6: hub writes (msg/broadcast from attach client)
        "state.hub_send_msg": _hub_send_msg,
        "state.hub_broadcast": _hub_broadcast,
        # Phase 4.6: cancel (ESC in attach mode)
        "state.cancel_current_request": _cancel_current_request,
    }

    registered: list[str] = []
    for method_name, handler in handlers.items():
        try:
            rpc_server.register(method_name, handler)
        except ValueError:
            # Already registered by another caller - that's fine.
            logger.debug(
                "state rpc handler %s already registered, skipping", method_name
            )
            continue
        registered.append(method_name)

    if registered:
        logger.info("registered state rpc handlers: %s", ", ".join(registered))
    else:
        logger.debug("all state rpc handlers already registered, no-op")
