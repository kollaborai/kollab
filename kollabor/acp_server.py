"""Kollab as an ACP v1 agent, backed by the existing headless daemon.

Owned sessions stop their daemon on EOF. Attached sessions only detach. Each
prompt and cancel carries an owner request ID through the daemon's turn queue.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import acp
from acp.exceptions import RequestError
from acp.schema import (
    AgentCapabilities, AgentMessageChunk, AgentThoughtChunk, CloseSessionResponse,
    Implementation, InitializeResponse, LoadSessionResponse, NewSessionResponse,
    PermissionOption, PromptResponse, ResumeSessionResponse, SessionMode,
    SessionModeState, SetSessionModeResponse, TextContentBlock, ToolCallStart,
    ToolCallProgress, ToolCallUpdate, SessionNotification, UserMessageChunk,
)

from .acp_stdio import acp_streams, isolated_stdout
from .llm.acp_state import ACPClientSessionStore
from .version import __version__


@dataclass
class ClientSession:
    id: str
    engine: Any
    workspace: str
    attached: bool
    requests: list[str] = field(default_factory=list)
    permission_tasks: dict[tuple[str, str], asyncio.Task] = field(default_factory=dict)


class KollabACPAgent:
    def __init__(self, *, attach: str | None = None, profile: str | None = None):
        self.attach = attach
        self.profile = profile
        self.sessions: dict[str, ClientSession] = {}
        self.connection: Any = None
        self.initialized = False
        self.saved = ACPClientSessionStore()

    def on_connect(self, connection: Any) -> None:
        self.connection = connection

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        self.initialized = True
        return InitializeResponse(
            protocolVersion=1,
            agentInfo=Implementation(name="kollab", version=__version__),
            agentCapabilities=AgentCapabilities(
                loadSession=not bool(self.attach),
                sessionCapabilities={"resume": {}, "close": {}},
            ),
        )

    def _session(self, session_id: str) -> ClientSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        return session

    @staticmethod
    def _modes(current: str = "confirm_all") -> SessionModeState:
        return SessionModeState(currentModeId=current, availableModes=[
            SessionMode(id="confirm_all", name="Confirm All"),
            SessionMode(id="default", name="Default"),
            SessionMode(id="auto_approve_edits", name="Approve Edits"),
            SessionMode(id="trust_all", name="Trust All"),
        ])

    async def new_session(self, cwd: str, mcp_servers: list[Any] | None = None, **kwargs: Any) -> NewSessionResponse:
        if not self.initialized:
            raise RequestError.invalid_params({"reason": "initialize is required"})
        workspace = os.path.realpath(cwd)
        if not os.path.isabs(cwd) or not Path(workspace).is_dir():
            raise RequestError.invalid_params({"cwd": cwd})
        from kollabor_engine.session import EngineSession
        session_id = uuid.uuid4().hex
        if self.attach:
            if self.sessions:
                raise RequestError.invalid_params({"reason": "one attached session per connection"})
            from kollabor_engine.hub_bridge import HubBridge
            candidates = [row for row in HubBridge().discover_sessions(use_cache=False)
                          if row.get("identity") == self.attach
                          and os.path.realpath(str(row.get("workspace") or "")) == workspace
                          and not row.get("device")]
            if len(candidates) != 1:
                raise RequestError.invalid_params({"reason": "attach needs one matching local agent",
                    "candidates": [{"identity": row.get("identity"), "workspace": row.get("workspace")}
                                   for row in HubBridge().discover_sessions()]})
            engine = await EngineSession.adopt(candidates[0])
            if mcp_servers:
                print("kollab acp: client MCP servers are ignored for an attached daemon: " +
                      ", ".join(str(getattr(server, "name", "server")) for server in mcp_servers), file=sys.stderr)
        else:
            engine = EngineSession(session_id, profile=self.profile, workspace=workspace,
                                   approval_mode="", identity=f"acp-{session_id[:8]}")
            try:
                await engine.initialize()
                await engine.daemon.rpc.call("state.acp_mode", {"mode": "confirm_all"})
                engine.approval_mode = "confirm_all"
                if mcp_servers:
                    await engine.daemon.rpc.call("state.acp_configure_mcp", {"servers": [
                        server.model_dump(by_alias=True) if hasattr(server, "model_dump") else server
                        for server in mcp_servers
                    ]})
            except BaseException:
                await engine.shutdown()
                raise
        self.sessions[session_id] = ClientSession(session_id, engine, workspace, bool(self.attach))
        if not self.attach:
            await self._save(self.sessions[session_id])
        return NewSessionResponse(sessionId=session_id,
                                  modes=None if self.attach else self._modes())

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs: Any) -> LoadSessionResponse:
        if self.attach:
            raise RequestError.method_not_found("session/load")
        if session_id in self.sessions:
            session = self._session(session_id)
            if session.workspace != os.path.realpath(cwd):
                raise RequestError.invalid_params({"reason": "workspace differs"})
            await self._replay_history(session)
            return LoadSessionResponse(modes=self._modes(session.engine.approval_mode))
        # ACP IDs are stable opaque handles; private local state maps them to
        # native conversation IDs in this workspace.
        opened = await self.new_session(cwd, mcp_servers=mcp_servers)
        session = self.sessions.pop(str(opened.sessionId))
        try:
            conversation = self.saved.load(session_id, cwd)
            if not conversation:
                raise ValueError("no saved ACP session in this workspace")
            await session.engine.state.resume_conversation(conversation)
        except Exception:
            await session.engine.shutdown()
            raise RequestError.resource_not_found(session_id)
        session.id = session_id
        self.sessions[session_id] = session
        await self._save(session)
        await self._replay_history(session)
        return LoadSessionResponse(modes=self._modes())

    async def _replay_history(self, session: ClientSession) -> None:
        for message in await session.engine.refresh_history():
            role = message.get("role")
            metadata = message.get("metadata") or {}
            updates = metadata.get("acp_updates") or []
            if role == "assistant" and updates:
                for update in updates:
                    if update.get("kind") == "text":
                        await self._text(session.id, str(update.get("text") or ""))
                        continue
                    payload = update.get("payload") or {}
                    if payload.get("sessionUpdate"):
                        await self._wire_update(session.id, payload)
                continue
            content = message.get("content") or ""
            if isinstance(content, list):
                content = "".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
            if role == "assistant":
                thought = message.get("thinking") or metadata.get("thinking") or ""
                if isinstance(thought, list):
                    thought = "".join(str(part) for part in thought)
                if thought:
                    await self.connection.session_update(session.id, AgentThoughtChunk(
                        sessionUpdate="agent_thought_chunk", content=TextContentBlock(type="text", text=str(thought))))
                for tool in metadata.get("tool_calls") or []:
                    tool_id = str(tool.get("id") or tool.get("tool_call_id") or uuid.uuid4().hex)
                    await self.connection.session_update(session.id, self._tool_start({
                        "tool_id": tool_id, "tool_name": tool.get("name"),
                        "tool_type": tool.get("type"), "input": tool.get("input") or {},
                    }))
                    if "result" in tool or tool.get("status") in {"completed", "failed"}:
                        await self.connection.session_update(session.id, self._tool_result({
                            "tool_id": tool_id, "output": tool.get("result"),
                            "success": not bool(tool.get("is_error")), "metadata": tool.get("metadata") or {},
                        }))
                if content:
                    await self._text(session.id, str(content))
            elif role == "user" and content:
                await self.connection.session_update(session.id, UserMessageChunk(
                    sessionUpdate="user_message_chunk", content=TextContentBlock(type="text", text=str(content))))

    async def _wire_update(self, session_id: str, update: dict[str, Any]) -> None:
        notification = SessionNotification.model_validate({"sessionId": session_id, "update": update})
        await self.connection.session_update(session_id, notification.update)

    async def resume_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs: Any) -> ResumeSessionResponse:
        if session_id in self.sessions:
            if self._session(session_id).workspace != os.path.realpath(cwd):
                raise RequestError.invalid_params({"reason": "workspace differs"})
            return ResumeSessionResponse(modes=None if self.attach else self._modes(self._session(session_id).engine.approval_mode))
        # Resume suppresses replay; load is the explicit replaying operation.
        if self.attach:
            raise RequestError.invalid_params({"reason": "attached session is not owned by this connection"})
        opened = await self.new_session(cwd, mcp_servers=mcp_servers)
        session = self.sessions.pop(str(opened.sessionId))
        try:
            conversation = self.saved.load(session_id, cwd)
            if not conversation:
                raise ValueError("no saved ACP session in this workspace")
            await session.engine.state.resume_conversation(conversation)
        except Exception:
            await session.engine.shutdown()
            raise RequestError.resource_not_found(session_id)
        session.id = session_id
        self.sessions[session_id] = session
        await self._save(session)
        return ResumeSessionResponse(modes=self._modes())

    async def set_session_mode(self, session_id: str, mode_id: str, **kwargs: Any) -> SetSessionModeResponse:
        session = self._session(session_id)
        if session.attached:
            raise RequestError.invalid_params({"reason": "change an attached daemon's mode in Kollab"})
        if mode_id not in {"confirm_all", "default", "auto_approve_edits", "trust_all"}:
            raise RequestError.invalid_params({"modeId": mode_id})
        await session.engine.daemon.rpc.call("state.acp_mode", {"mode": mode_id})
        session.engine.approval_mode = mode_id
        return SetSessionModeResponse()

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        session = self._session(session_id)
        if any(getattr(block, "type", "text") != "text" for block in prompt):
            raise RequestError.invalid_params({"reason": "Kollab ACP prompts currently accept text"})
        text = "".join(str(getattr(block, "text", "")) for block in prompt)
        if not text.strip():
            raise RequestError.invalid_params({"reason": "prompt is empty"})
        request_id = f"acp-client-{uuid.uuid4().hex}"
        session.requests.append(request_id)
        events = session.engine.subscribe()
        streamed = False
        completed = False
        try:
            result = await session.engine.daemon.rpc.call("state.acp_submit", {
                "message": text, "request_id": request_id,
            })
            if not result.get("accepted"):
                raise RequestError.invalid_params({"reason": result.get("reason") or "turn was refused"})
            while True:
                event = await events.get()
                if event.get("type") == "daemon_closed":
                    raise RequestError.internal_error({"reason": "daemon closed during this turn"})
                if event.get("request_id") != request_id:
                    continue
                kind = event.get("type")
                if event.get("acp_passthrough"):
                    continue
                if kind == "token":
                    streamed = True
                    await self._text(session_id, event.get("text") or "")
                elif kind == "response" and not streamed:
                    await self._text(session_id, event.get("text") or "")
                elif kind in {"thinking", "thought"}:
                    await self.connection.session_update(session_id, AgentThoughtChunk(
                        sessionUpdate="agent_thought_chunk", content=TextContentBlock(type="text", text=str(event.get("text") or ""))))
                elif kind == "tool_start":
                    await self.connection.session_update(session_id, self._tool_start(event))
                elif kind == "tool_result":
                    await self.connection.session_update(session_id, self._tool_result(event))
                elif kind == "permission_request":
                    tool_id = str(event.get("tool_id") or "")
                    task = asyncio.create_task(self._permission(session, event))
                    session.permission_tasks[(request_id, tool_id)] = task
                elif kind in {"permission_granted", "permission_denied", "permission_cancelled"}:
                    task = session.permission_tasks.pop((request_id, str(event.get("tool_id") or "")), None)
                    if task and not task.done():
                        task.cancel()
                elif kind == "acp_update":
                    await self._wire_update(session_id, event["update"])
                elif kind == "usage_update":
                    await self._wire_update(session_id, event["payload"])
                elif kind == "turn_complete":
                    completed = True
                    await self._save(session)
                    reason = str(event.get("stop_reason") or "end_turn").lower()
                    reason = {"stop": "end_turn", "completed": "end_turn",
                              "tool_calls": "end_turn", "tool_use": "end_turn",
                              "length": "max_tokens", "max_output_tokens": "max_tokens",
                              "content_filter": "refusal", "safety": "refusal"}.get(reason, reason)
                    if reason not in {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}:
                        raise RequestError.internal_error({"reason": event.get("error") or reason})
                    return PromptResponse(stopReason=reason)
        finally:
            if not completed:
                with contextlib.suppress(Exception):
                    await session.engine.daemon.rpc.call("state.acp_cancel", {"request_id": request_id})
            session.engine.unsubscribe(events)
            if request_id in session.requests:
                session.requests.remove(request_id)
            pending = [session.permission_tasks.pop(key) for key in tuple(session.permission_tasks) if key[0] == request_id]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _save(self, session: ClientSession) -> None:
        if session.attached:
            return
        snapshot = await session.engine.state.get_conversation()
        native_id = getattr(snapshot, "session_id", "")
        if native_id:
            self.saved.save(session.id, session.workspace, native_id)

    async def _text(self, session_id: str, text: str) -> None:
        await self.connection.session_update(session_id, AgentMessageChunk(
            sessionUpdate="agent_message_chunk", content=TextContentBlock(type="text", text=str(text))))

    @staticmethod
    def _tool_start(event: dict[str, Any]) -> ToolCallStart:
        tool_type = str(event.get("tool_type") or "")
        kind = "execute" if tool_type == "terminal" else (
            "read" if tool_type in {"file_read", "file_grep"} else
            "delete" if tool_type in {"file_delete", "file_rmdir"} else
            "move" if tool_type == "file_move" else
            "edit" if tool_type.startswith("file_") else "other")
        return ToolCallStart(sessionUpdate="tool_call", toolCallId=str(event.get("tool_id") or uuid.uuid4().hex),
            title=str(event.get("tool_name") or event.get("tool_type") or "Tool"), kind=kind,
            status="in_progress", rawInput=event.get("input") or {})

    @staticmethod
    def _tool_result(event: dict[str, Any]) -> ToolCallProgress:
        content = [{"type": "content", "content": {"type": "text", "text": str(event.get("output") or event.get("error") or "")}}]
        diffs = (event.get("metadata") or {}).get("diffs", [])
        for diff in diffs:
            if isinstance(diff, dict) and diff.get("path"):
                content.append({"type": "diff", "path": diff["path"],
                                "oldText": diff.get("old_text"), "newText": diff.get("new_text", "")})
        return ToolCallProgress(sessionUpdate="tool_call_update", toolCallId=str(event.get("tool_id") or ""),
                              status="completed" if event.get("success") else "failed",
                              content=content, rawOutput=event.get("output"))

    async def _permission(self, session: ClientSession, event: dict[str, Any]) -> None:
        tool_id = str(event.get("tool_id") or "")
        options = [PermissionOption(optionId="once", name="Allow Once", kind="allow_once"),
                   PermissionOption(optionId="deny", name="Deny Once", kind="reject_once")]
        if not session.attached:
            options.append(PermissionOption(optionId="session", name=f"Allow {event.get('tool_name') or 'this operation'} for this session", kind="allow_always"))
        vendor_options = (event.get("details") or {}).get("acp_options") or []
        if vendor_options:
            options = [PermissionOption.model_validate(option) for option in vendor_options
                       if not session.attached or option.get("kind") not in {"allow_always", "reject_always"}]
        try:
            response = await asyncio.wait_for(self.connection.request_permission(
                session_id=session.id, tool_call=ToolCallUpdate(toolCallId=tool_id,
                    title=str(event.get("tool_name") or "Tool permission"), status="pending", rawInput=event.get("input") or {}),
                options=options,
            ), timeout=300)
            outcome = response.outcome
            selection = getattr(outcome, "optionId", None)
            control = {"once": "APPROVE_ONCE", "deny": "DENY", "session": "APPROVE_SESSION"}.get(selection, "CANCEL")
            if session.attached and control == "APPROVE_SESSION":
                control = "CANCEL"
            payload = {"tool_id": tool_id, "control": control}
            if vendor_options and selection in {option.optionId for option in options}:
                payload = {"tool_id": tool_id, "option_id": selection}
            await session.engine.daemon.rpc.call("permission.respond", payload)
        except asyncio.TimeoutError:
            await session.engine.daemon.rpc.call("permission.respond", {"tool_id": tool_id, "control": "DENY"})

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self._session(session_id)
        if session.requests:
            await session.engine.daemon.rpc.call("state.acp_cancel", {"request_id": session.requests[0]})

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        session = self._session(session_id)
        for request_id in tuple(session.requests):
            await session.engine.daemon.rpc.call("state.acp_cancel", {"request_id": request_id})
        await self._save(session)
        await session.engine.shutdown()
        self.sessions.pop(session_id, None)
        return CloseSessionResponse()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def shutdown(self) -> None:
        for session_id in tuple(self.sessions):
            with contextlib.suppress(Exception):
                await self.close_session(session_id)


def main(argv: list[str] | None = None, *, protocol: Any = None) -> None:
    parser = argparse.ArgumentParser(prog="kollab acp")
    parser.add_argument("--attach")
    parser.add_argument("--llm")
    args = parser.parse_args(argv)
    protocol = protocol or isolated_stdout()
    agent = KollabACPAgent(attach=args.attach, profile=args.llm)

    async def run() -> None:
        reader, writer = await acp_streams(protocol)
        try:
            await acp.run_agent(agent, input_stream=writer, output_stream=reader)
        finally:
            await agent.shutdown()
            writer.close()

    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"kollab acp: startup/connection failed ({type(exc).__name__})", file=sys.stderr)
        raise SystemExit(1)
