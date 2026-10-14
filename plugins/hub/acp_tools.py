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


@dataclass(frozen=True)
class QuestionBack:
    target: str
    thread: str
    message_id: str
    original: AskWait
    expires_at: float


class ACPHubTools:
    def __init__(self, hub: Any):
        self.hub = hub
        self.threads: OrderedDict[str, dict[str, str]] = OrderedDict()
        self.waits: dict[str, AskWait] = {}
        self.expired: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.questions: OrderedDict[tuple[str, str], QuestionBack] = OrderedDict()
        self._answering_questions: set[tuple[str, str]] = set()
        self._consumed_returns: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._epoch = 0

    @property
    def waiting(self) -> bool:
        self._prune_questions()
        return bool(self.waits) or any(
            not question.original.future.done() for question in self.questions.values()
        )

    def observe(self, message: Any) -> None:
        metadata = message.metadata or {}
        if (
            not message.thread_id or message.action != "message"
            or metadata.get("turn_end") or metadata.get("acp_waiting")
            or self._return_key(message) in self._consumed_returns
        ):
            return
        peers = self.threads.setdefault(message.thread_id, {})
        peers[message.from_identity or message.from_agent] = message.id
        self.threads.move_to_end(message.thread_id)
        while len(self.threads) > 1000:
            self.threads.popitem(last=False)

    def _return_key(self, message: Any) -> tuple[str, str, str] | None:
        message_id = getattr(message, "id", "")
        if not message_id:
            return None
        action = "turn_end" if (message.metadata or {}).get("turn_end") else message.action
        return (message.from_identity or message.from_agent, message_id, action)

    def _remember_return(self, message: Any) -> None:
        key = self._return_key(message)
        if key is not None:
            self._consumed_returns[key] = None
            while len(self._consumed_returns) > 1000:
                self._consumed_returns.popitem(last=False)

    def consume(self, message: Any) -> bool:
        # A store write can fail after consumption, or a leased relay event can
        # be delivered again. Reply and end projections of one result are
        # separate actions even when they share the source event's ID.
        if self._return_key(message) in self._consumed_returns:
            return True
        self._prune_questions()
        if (message.metadata or {}).get("acp_waiting"):
            self._remember_return(message)
            llm = self.hub.event_bus.get_service("llm_service")
            display = getattr(llm, "message_display_service", None)
            if display is not None:
                display.display_system_message(message.content)
            return True
        sender = message.from_identity or message.from_agent
        frame = (message.metadata or {}).get("turn_end")
        if (message.thread_id, message.reply_to or "") in self.expired:
            return bool(frame)
        is_question = (
            message.action == "message"
            and not frame
            and message.to not in {"all", "*", "everyone"}
            and (not message.reply_to or (message.metadata or {}).get("acp_question"))
        )
        candidates = tuple(self.waits.values())
        if (message.metadata or {}).get("acp_question"):
            # Typed relay questions refer to the original request, whereas a
            # local question back is a fresh request on its own thread.
            candidates = tuple(sorted(candidates, key=lambda wait: not (
                message.thread_id == wait.thread and message.reply_to == wait.request_id
            )))
        for wait in candidates:
            if sender != wait.target or wait.future.done():
                continue
            if is_question:
                self._remember_return(message)
                original = AskWait(
                    wait.target, wait.thread, wait.request_id,
                    asyncio.get_running_loop().create_future(), list(wait.earlier),
                )
                key = (sender, message.thread_id)
                previous = self.questions.pop(key, None)
                if previous is not None and previous.original is not wait:
                    self._retire_question(previous)
                self.questions[key] = QuestionBack(
                    sender, message.thread_id, message.id, original,
                    time.monotonic() + 600,
                )
                self._prune_questions()
                self._settle(wait, "question", message.content, message.thread_id, wait.earlier)
                return True
            if self._consume_reply(wait, message):
                return True
        if not is_question:
            # The original final can arrive while the harness prepares its
            # answer to a question. Keep it for a subsequent hub_ask continuation.
            for question in self.questions.values():
                if sender == question.target and self._consume_reply(question.original, message):
                    return True
        return False

    def _consume_reply(self, wait: AskWait, message: Any) -> bool:
        if message.thread_id != wait.thread or message.reply_to != wait.request_id:
            return False
        frame = (message.metadata or {}).get("turn_end")
        if wait.future.done():
            return bool(frame)
        self._remember_return(message)
        if frame:
            outcome = frame.get("outcome") or ("error" if frame.get("failed") else "end_turn")
            status = "answered" if outcome == "end_turn" else "busy" if outcome == "busy" else "ended"
            text = wait.earlier[-1] if wait.earlier and status == "answered" else message.content
            self._settle(wait, status, text, wait.thread,
                         wait.earlier[:-1] if status == "answered" else wait.earlier)
            self._expire_wait(wait)
        else:
            wait.earlier.append(message.content)
        return True

    def _expire_wait(self, wait: AskWait) -> None:
        self.expired[(wait.thread, wait.request_id)] = time.monotonic()
        while len(self.expired) > 1000:
            self.expired.popitem(last=False)

    def _retire_question(self, question: QuestionBack) -> None:
        wait = question.original
        self._settle(wait, "ended", "The question continuation expired.", wait.thread, wait.earlier)
        self._expire_wait(wait)

    def _prune_questions(self) -> None:
        now = time.monotonic()
        for key, question in tuple(self.questions.items()):
            if question.expires_at <= now:
                self.questions.pop(key, None)
                self._retire_question(question)
        while len(self.questions) > 1000:
            _, question = self.questions.popitem(last=False)
            self._retire_question(question)

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
        self._prune_questions()
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
        question_key = (target, thread)
        question = self.questions.get(question_key)
        if question_key in self._answering_questions:
            raise ValueError("this question is already being answered")
        if thread and thread not in self.threads and question is None:
            raise ValueError("thread is not owned by this agent")
        thread = thread or uuid.uuid4().hex
        peers = self.threads.setdefault(thread, {})
        request_id = uuid.uuid4().hex
        reply_to = question.message_id if question is not None else peers.get(target, "")
        is_ask = name == "hub_ask"
        peer = next((row for row in self.hub._acp_roster() if row.get("identity") == target), {})
        active = getattr(getattr(self.hub.event_bus.get_service("acp_orchestrator"), "runner", None), "_active_context", None)
        requester = (getattr(active, "origin", None) or {}).get("sender_identity")
        busy_peer = (
            question is None
            and bool((peer.get("tags") or {}).get("acp_ask_waiting"))
            and target != requester
        )
        # A question back is answered on its received thread. An unrelated ask
        # is a fresh request, even if the caller supplied an existing thread.
        if is_ask and question is None:
            reply_to = ""
        timeout = min(600, max(1, float(arguments.get("timeout", 300))))
        wait = question.original if question is not None else AskWait(
            target, thread, request_id, asyncio.get_running_loop().create_future()
        )
        future = wait.future
        epoch = self._epoch
        answer_completed = False
        try:
            if question is not None:
                self._answering_questions.add(question_key)
            if is_ask:
                self.waits[request_id] = wait
                self._publish_waiting()
            result = await self.hub._handle_hub_msg_tool({
                "id": request_id, "to": target, "message": content,
                "thread_id": thread, "reply_to": reply_to,
                "_acp_hub": True, "_message_id": request_id,
                "_acp_ask": is_ask and not reply_to,
                "_acp_question_answer": question is not None,
            })
            if epoch != self._epoch:
                return {"status": "stopped", "from": target, "thread": thread,
                        "text": "The asking session stopped.", "earlier": list(wait.earlier)}
            if not result.success:
                return {"status": "ended", "from": target, "thread": thread,
                        "text": result.error or result.output, "earlier": []}
            if is_ask and busy_peer:
                return {"status": "busy", "from": target, "thread": thread,
                        "text": "The agent is waiting on another ask; your request is queued.", "earlier": []}
            if question is not None:
                # Answering a question back finishes the other agent's wait;
                # then continue waiting for the original thread's final turn.
                from .models import HubMessage, MessageScope
                rejected = await self.hub._route_message(HubMessage(
                    action="turn_end", from_agent=self.hub._identity.agent_id,
                    from_identity=self.hub._identity.identity, to=target,
                    content="The asking agent answered your question.", scope=MessageScope.DIRECT.value,
                    thread_id=thread, reply_to=reply_to,
                    metadata={"acp_harness": True,
                              "turn_end": {"replies": 1, "failed": False, "outcome": "end_turn"}},
                ))
                if epoch != self._epoch:
                    return {"status": "stopped", "from": target, "thread": thread,
                            "text": "The asking session stopped.", "earlier": list(wait.earlier)}
                if rejected:
                    return {"status": "ended", "from": target, "thread": thread,
                            "text": "The question's completion frame was refused.",
                            "earlier": list(wait.earlier)}
                answer_completed = True
                if self.questions.get(question_key) is question:
                    self.questions.pop(question_key, None)
                if not is_ask:
                    self._retire_question(question)
            if not is_ask:
                return {"status": "sent", "thread": thread}
            if question is not None:
                timeout = min(timeout, max(0, question.expires_at - time.monotonic()))
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except asyncio.TimeoutError:
                return {"status": "timeout", "from": target, "thread": thread,
                        "text": "The agent did not finish before the deadline.", "earlier": wait.earlier}
        except asyncio.CancelledError:
            if question is not None and self.questions.get(question_key) is question:
                self.questions.pop(question_key, None)
                self._settle(wait, "stopped", "The asking turn stopped.", wait.thread, wait.earlier)
                self._expire_wait(wait)
            return {"status": "stopped", "from": target, "thread": thread,
                    "text": "The asking turn stopped.", "earlier": list(wait.earlier)}
        finally:
            self.waits.pop(request_id, None)
            self._answering_questions.discard(question_key)
            self._publish_waiting()
            retryable_answer = (
                question is not None and not answer_completed
                and epoch == self._epoch and self.questions.get(question_key) is question
            )
            if is_ask and not retryable_answer and (
                not future.done() or future.cancelled() or future.result().get("status") != "question"
            ):
                self._expire_wait(wait)

    def stop(self) -> None:
        self._epoch += 1
        for wait in tuple(self.waits.values()):
            self._settle(wait, "stopped", "The asking session stopped.", wait.thread, wait.earlier)
            self._expire_wait(wait)
        for question in self.questions.values():
            wait = question.original
            self._settle(wait, "stopped", "The asking session stopped.", wait.thread, wait.earlier)
            self._expire_wait(wait)
        self.waits.clear()
        self.questions.clear()
        self._answering_questions.clear()
        self.threads.clear()
        self._publish_waiting()
