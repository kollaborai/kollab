"""Daemon-owned harness Hub tools and correlated ask waits.

The MCP child only transports calls. Identity, routing, thread ownership,
network policy and the lifetime of waits stay in the hosting Hub plugin.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AskWait:
    target: str
    thread: str
    request_id: str
    future: asyncio.Future[dict[str, Any]]
    earlier: list[str] = field(default_factory=list)


class ACPHubTools:
    def __init__(self, hub: Any):
        self.hub = hub
        self.threads: OrderedDict[str, dict[str, str]] = OrderedDict()
        self.waits: dict[str, AskWait] = {}
        self.expired: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.questions: dict[tuple[str, str], AskWait] = {}

    @property
    def waiting(self) -> bool:
        return bool(self.waits)

    def observe(self, message: Any) -> None:
        if not message.thread_id:
            return
        peers = self.threads.setdefault(message.thread_id, {})
        peers[message.from_identity or message.from_agent] = message.id
        self.threads.move_to_end(message.thread_id)
        while len(self.threads) > 1000:
            self.threads.popitem(last=False)

    def consume(self, message: Any) -> bool:
        if (message.metadata or {}).get("acp_waiting"):
            llm = self.hub.event_bus.get_service("llm_service")
            display = getattr(llm, "message_display_service", None)
            if display is not None:
                display.display_system_message(message.content)
            return True
        sender = message.from_identity or message.from_agent
        frame = (message.metadata or {}).get("turn_end")
        if (message.thread_id, message.reply_to or "") in self.expired:
            return bool(frame)
        for wait in tuple(self.waits.values()):
            if sender != wait.target:
                continue
            matching = message.thread_id == wait.thread and message.reply_to == wait.request_id
            if matching and frame:
                outcome = frame.get("outcome") or ("error" if frame.get("failed") else "end_turn")
                status = "answered" if outcome == "end_turn" else "busy" if outcome == "busy" else "ended"
                text = wait.earlier[-1] if wait.earlier and status == "answered" else message.content
                self._settle(wait, status, text, wait.thread,
                             wait.earlier[:-1] if status == "answered" else wait.earlier)
                return True
            if matching:
                wait.earlier.append(message.content)
                return True
            if (not message.reply_to or (message.metadata or {}).get("acp_question")) and message.action == "message" and message.to not in {"all", "*", "everyone"}:
                self.questions[(sender, message.thread_id)] = wait
                self._settle(wait, "question", message.content, message.thread_id, wait.earlier)
                return True
        return False

    def _settle(self, wait: AskWait, status: str, text: str, thread: str, earlier: list[str]) -> None:
        if not wait.future.done():
            wait.future.set_result({"status": status, "from": wait.target, "thread": thread,
                                    "text": text, "earlier": list(earlier)})

    def _publish_waiting(self) -> None:
        if self.hub._identity:
            self.hub._identity.tags["acp_ask_waiting"] = bool(self.waits)
            if self.hub._presence:
                self.hub._presence.publish()

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "hub_agents":
            return [row.get("identity") for row in self.hub._acp_roster() if row.get("identity")]
        if name == "hub_status":
            return self.hub._acp_roster()
        if name == "hub_spawn":
            result = await self.hub._handle_hub_spawn_tool({"id": uuid.uuid4().hex,
                "input": {"name": arguments.get("name"), "task": arguments.get("task"),
                          "llm": arguments.get("llm")}})
            return {"success": result.success, "text": result.output, "error": result.error}
        if name not in {"hub_msg", "hub_ask"}:
            raise ValueError(f"unknown Hub tool {name}")
        target = str(arguments.get("to") or "").strip()
        from .device_names import format_handle, parse_handle
        handle = parse_handle(target)
        if handle is not None:
            target = format_handle(*handle)
        elif target == "coordinator":
            target = self.hub._get_coordinator_identity() or target
        content = str(arguments.get("question" if name == "hub_ask" else "message") or "")
        if not target or not content:
            raise ValueError("an agent and nonempty message are required")
        if target == self.hub._identity.identity:
            raise ValueError("an agent cannot ask or message itself through this bridge")
        thread = str(arguments.get("thread") or "")
        if thread and thread not in self.threads:
            raise ValueError("thread is not owned by this agent")
        thread = thread or uuid.uuid4().hex
        peers = self.threads.setdefault(thread, {})
        request_id = uuid.uuid4().hex
        reply_to = peers.get(target, "")
        is_ask = name == "hub_ask"
        peer = next((row for row in self.hub._acp_roster() if row.get("identity") == target), {})
        active = getattr(getattr(self.hub.event_bus.get_service("acp_orchestrator"), "runner", None), "_active_context", None)
        requester = (getattr(active, "origin", None) or {}).get("sender_identity")
        busy_peer = bool((peer.get("tags") or {}).get("acp_ask_waiting")) and target != requester
        # A question back is answered on its received thread. An unrelated ask
        # is a fresh request, even if the caller supplied an existing thread.
        original = self.questions.pop((target, thread), None)
        if is_ask and original is None:
            reply_to = ""
        future = asyncio.get_running_loop().create_future()
        wait = AskWait(target, thread, request_id, future)
        if original is not None and is_ask:
            wait.thread = original.thread
            wait.request_id = original.request_id
        if is_ask:
            self.waits[request_id] = wait
            self._publish_waiting()
        try:
            result = await self.hub._handle_hub_msg_tool({
                "id": request_id, "to": target, "message": content,
                "thread_id": thread, "reply_to": reply_to,
                "_acp_hub": True, "_message_id": request_id,
                "_acp_ask": is_ask and not reply_to,
            })
            if not result.success:
                return {"status": "ended", "from": target, "thread": thread,
                        "text": result.error or result.output, "earlier": []}
            if is_ask and busy_peer:
                return {"status": "busy", "from": target, "thread": thread,
                        "text": "The agent is waiting on another ask; your request is queued.", "earlier": []}
            if reply_to and original is not None:
                # Answering a question back finishes the other agent's wait;
                # then continue waiting for the original thread's final turn.
                from .messenger import HubMessage, MessageScope
                await self.hub._route_message(HubMessage(
                    action="message", from_agent=self.hub._identity.agent_id,
                    from_identity=self.hub._identity.identity, to=target,
                    content="The asking agent answered your question.", scope=MessageScope.DIRECT.value,
                    thread_id=thread, reply_to=reply_to,
                    metadata={"turn_end": {"replies": 1, "failed": False, "outcome": "end_turn"}},
                ))
            if not is_ask:
                return {"status": "sent", "thread": thread}
            timeout = min(600, max(1, float(arguments.get("timeout", 300))))
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except asyncio.TimeoutError:
                return {"status": "timeout", "from": target, "thread": thread,
                        "text": "The agent did not finish before the deadline.", "earlier": wait.earlier}
            except asyncio.CancelledError:
                return {"status": "stopped", "from": target, "thread": thread,
                        "text": "The asking turn stopped.", "earlier": wait.earlier}
        finally:
            self.waits.pop(request_id, None)
            self._publish_waiting()
            if is_ask and (not future.done() or future.cancelled() or future.result().get("status") != "question"):
                self.expired[(wait.thread, wait.request_id)] = time.monotonic()
                while len(self.expired) > 1000:
                    self.expired.popitem(last=False)

    def stop(self) -> None:
        for wait in tuple(self.waits.values()):
            self._settle(wait, "stopped", "The asking session stopped.", wait.thread, wait.earlier)
        self.waits.clear()
        self._publish_waiting()
